"""The API access matrix — derived from the running app, not from a document.

Every HTTP and WebSocket route this service exposes falls into exactly one
access class:

    PUBLIC                          no credential, and nothing tenant-specific
    CANDIDATE_TOKEN_SCOPED          an invitation token or a session grant, for
                                    one session and no other
    RECRUITER_AUTHENTICATED         a signed-in principal; the response carries
                                    nothing that belongs to a specific resource
    RECRUITER_ORGANIZATION_SCOPED   a signed-in principal AND ownership of the
                                    resource named in the path
    INTERNAL_ONLY                   not part of the product surface (docs,
                                    schema, the built frontends)

`DECLARED` below is the authoritative matrix, written by hand. `derive()` reads
the actual dependency graph of each mounted route and works out what the code
really enforces. `audit()` compares the two.

That comparison is the point. A matrix in a markdown file is a claim; this one
fails the test suite when a new route appears, when a route's guard is removed,
or when a declared class stops matching the code. The failure mode a document
cannot catch — someone adds `POST /api/recruiter/something` and forgets — is
the failure mode that leaks a tenant's transcripts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

PUBLIC = "PUBLIC"
CANDIDATE_TOKEN_SCOPED = "CANDIDATE_TOKEN_SCOPED"
RECRUITER_AUTHENTICATED = "RECRUITER_AUTHENTICATED"
RECRUITER_ORGANIZATION_SCOPED = "RECRUITER_ORGANIZATION_SCOPED"
INTERNAL_ONLY = "INTERNAL_ONLY"

CLASSES = (
    PUBLIC,
    CANDIDATE_TOKEN_SCOPED,
    RECRUITER_AUTHENTICATED,
    RECRUITER_ORGANIZATION_SCOPED,
    INTERNAL_ONLY,
)

#: Path parameters that carry a tenant-owned resource id. A recruiter route with
#: one of these is organization-scoped by construction, because
#: `authz.enforce_path_ownership` resolves it. Mirrors `authz.RESOLVERS`.
OWNED_PARAMS = ("interview_id", "session_id", "evaluation_id", "token")


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    endpoint: str
    dependencies: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def __str__(self) -> str:                       # for readable failures
        return f"{self.method} {self.path}"


# --------------------------------------------------------------------------- #
#  What the code actually enforces
# --------------------------------------------------------------------------- #
def _dependency_names(dependant: Any, out: set[str]) -> set[str]:
    if dependant is None:
        return out
    for dep in getattr(dependant, "dependencies", ()):  # recursive: sub-deps count
        call = getattr(dep, "call", None)
        if call is not None:
            out.add(getattr(call, "__name__", str(call)))
        _dependency_names(dep, out)
    return out


def routes(app: Any, *, include_aliases: bool = False) -> list[Route]:
    """Every mounted route, with the guards actually attached to it.

    `/api/admin/*` is the same routers mounted a second time for continuity, so
    it is folded out by default: it would double every row without adding a
    surface. `include_aliases=True` keeps it, which is how the alias is tested
    for having identical protection.
    """
    found: dict[tuple[str, str], Route] = {}
    for entry in _candidates(app):
        path, endpoint, dependant, methods = entry
        if not include_aliases and path.startswith("/api/admin"):
            continue
        names = tuple(sorted(_dependency_names(dependant, set())))
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            key = (method, path)
            # Two handlers can share a path (a legacy alias for one of them).
            # The first registration is the one FastAPI matches, so it is the
            # one whose protection matters.
            found.setdefault(key, Route(method, path, endpoint, names))
    return sorted(found.values(), key=lambda r: (r.path, r.method))


def _candidates(app: Any) -> Iterable[tuple[str, str, Any, set[str]]]:
    """Walk the app's route table, including routers mounted as one object.

    FastAPI keeps an included router as a single `_IncludedRouter` entry whose
    real routes are behind `effective_candidates()`, so a naive walk over
    `app.routes` sees eight entries and misses the entire API.
    """
    for route in getattr(app, "routes", ()):
        expand = getattr(route, "effective_candidates", None)
        if callable(expand):
            for ctx in expand():
                endpoint = getattr(ctx, "endpoint", None)
                # A websocket route's context carries no path of its own; the
                # original route does. Missing this silently dropped the
                # candidate turn socket out of the matrix entirely.
                path = getattr(ctx, "path", "") or getattr(
                    getattr(ctx, "original_route", None), "path", "")
                yield (
                    path,
                    getattr(endpoint, "__name__", type(ctx).__name__),
                    getattr(ctx, "dependant", None),
                    set(getattr(ctx, "methods", None) or {"WS"}),
                )
            continue
        if type(route).__name__ == "Mount":
            # A static-file mount: no endpoint, no dependencies, one method.
            yield (getattr(route, "path", ""), "static", None, {"MOUNT"})
            continue
        endpoint = getattr(route, "endpoint", None)
        yield (
            getattr(route, "path", ""),
            getattr(endpoint, "__name__", type(route).__name__),
            getattr(route, "dependant", None),
            set(getattr(route, "methods", None) or {"WS"}),
        )


def derive(route: Route) -> str:
    """The class the route's own wiring implies."""
    if "recruiter_scope" in route.dependencies:
        if any("{" + name + "}" in route.path for name in OWNED_PARAMS):
            return RECRUITER_ORGANIZATION_SCOPED
        return RECRUITER_AUTHENTICATED
    if "current_principal" in route.dependencies:
        return RECRUITER_AUTHENTICATED
    if "candidate_scope" in route.dependencies:
        return CANDIDATE_TOKEN_SCOPED
    if route.path.startswith("/api/"):
        return PUBLIC
    return INTERNAL_ONLY


# --------------------------------------------------------------------------- #
#  The declared matrix
#
#  Written out in full rather than generated, because a generated matrix agrees
#  with the code by definition and therefore proves nothing. Where a row's class
#  is not what `derive()` would say — the routes whose credential is checked
#  inside the handler — `NOTE` says why.
# --------------------------------------------------------------------------- #
DECLARED: dict[tuple[str, str], str] = {
    # ---- Public -------------------------------------------------------------
    ("GET", "/api/health"): PUBLIC,
    # Readiness is public because a load balancer probes it before it has any
    # credential to offer. It is written to be safe unauthenticated: statuses
    # and a version, never a path, an origin, a setting or a secret —
    # `test_neither_probe_leaks_a_path_a_secret_or_a_configuration_value`.
    ("GET", "/api/ready"): PUBLIC,
    ("GET", "/api/demo/prompts"): PUBLIC,
    ("POST", "/api/auth/login"): PUBLIC,
    ("POST", "/api/auth/logout"): PUBLIC,

    # ---- Candidate ----------------------------------------------------------
    ("GET", "/api/invite/{token}"): CANDIDATE_TOKEN_SCOPED,
    ("POST", "/api/invite/{token}/precheck"): CANDIDATE_TOKEN_SCOPED,
    ("POST", "/api/session/{session_id}/voice"): CANDIDATE_TOKEN_SCOPED,
    # Retell connects INBOUND to this, so it is not a route a browser calls and
    # there is no principal to check. It is safe only because the call id in
    # the path resolves against a binding the server made when an authorised
    # candidate minted the call; an unrecognised call id gets no session and
    # therefore no interview. Classified INTERNAL_ONLY because that is what it
    # is: a vendor callback, not a public API.
    ("WS", "/llm-websocket/{call_id}"): INTERNAL_ONLY,
    ("POST", "/api/session/start"): CANDIDATE_TOKEN_SCOPED,
    ("GET", "/api/session/{session_id}"): CANDIDATE_TOKEN_SCOPED,
    ("POST", "/api/session/{session_id}/turn"): CANDIDATE_TOKEN_SCOPED,
    ("WS", "/ws/interview/{session_id}"): CANDIDATE_TOKEN_SCOPED,

    # ---- Recruiter, authenticated but not resource-specific -----------------
    ("GET", "/api/auth/me"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/candidates"): RECRUITER_AUTHENTICATED,
    ("POST", "/api/recruiter/candidates"): RECRUITER_AUTHENTICATED,
    ("POST", "/api/recruiter/compare"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/fairness"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/interviews"): RECRUITER_AUTHENTICATED,
    ("POST", "/api/recruiter/interviews"): RECRUITER_AUTHENTICATED,
    ("POST", "/api/recruiter/interviews/generate"): RECRUITER_AUTHENTICATED,
    # Organization-scoped, not merely authenticated: the token in the path is a
    # client-generated string, so it is a lookup key and not a capability. The
    # handler compares the caller's organization against the one that started
    # the generation, and a token belonging to another tenant reads exactly
    # like one that was never issued.
    ("GET", "/api/recruiter/interviews/generate/progress/{progress_token}"):
        RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/languages"): RECRUITER_AUTHENTICATED,
    # Shipped content, identical for every tenant — authenticated so it is not
    # part of the public surface, but nothing about it is organization-scoped.
    ("GET", "/api/recruiter/skill-domains"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/funnel-stages"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/overview"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/pool"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/pilot/dataset"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/pilot/runs"): RECRUITER_AUTHENTICATED,
    ("POST", "/api/recruiter/pilot/runs"): RECRUITER_AUTHENTICATED,
    ("POST", "/api/recruiter/pilot/runs/{pilot_run_id}/stop"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/pilot/stability"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/pilot/summary"): RECRUITER_AUTHENTICATED,

    # ---- Data lifecycle ------------------------------------------------------
    # The two `{token}` routes are organization-scoped by the resolver; the
    # retention views are aggregates over the caller's own organization, and
    # the sweep is deployment-wide behind the `delete` capability.
    ("GET", "/api/recruiter/retention"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/retention/candidates"): RECRUITER_AUTHENTICATED,
    ("POST", "/api/recruiter/retention/sweep"): RECRUITER_AUTHENTICATED,
    ("GET", "/api/recruiter/candidates/{token}/data"): RECRUITER_ORGANIZATION_SCOPED,
    ("DELETE", "/api/recruiter/candidates/{token}/data"): RECRUITER_ORGANIZATION_SCOPED,

    # ---- Recruiter, resource-owned ------------------------------------------
    ("GET", "/api/recruiter/evaluations/{evaluation_id}"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/evaluations/{evaluation_id}/result"): RECRUITER_ORGANIZATION_SCOPED,
    ("DELETE", "/api/recruiter/interviews/{interview_id}"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}"): RECRUITER_ORGANIZATION_SCOPED,
    ("PATCH", "/api/recruiter/interviews/{interview_id}"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/draft"): RECRUITER_ORGANIZATION_SCOPED,
    ("PATCH", "/api/recruiter/interviews/{interview_id}/draft"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/extract"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/invitations"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/invitations"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/invitations/open-link"): RECRUITER_ORGANIZATION_SCOPED,
    ("DELETE", "/api/recruiter/interviews/{interview_id}/invitations/{token}"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/publish"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/publish/check"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/questions"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/questions"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/questions/generate"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/questions/validate"): RECRUITER_ORGANIZATION_SCOPED,
    ("DELETE", "/api/recruiter/interviews/{interview_id}/questions/{question_id}"): RECRUITER_ORGANIZATION_SCOPED,
    ("PATCH", "/api/recruiter/interviews/{interview_id}/questions/{question_id}"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/questions/{question_id}/regenerate"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/regenerate"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/reset_skills"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/results"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/interviews/{interview_id}/test_run"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/versions"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/interviews/{interview_id}/versions/{version}"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}/evaluation"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/sessions/{session_id}/evaluation"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}/evaluation/evidence"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}/evaluation/result"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}/evaluations"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}/review"): RECRUITER_ORGANIZATION_SCOPED,
    ("POST", "/api/recruiter/sessions/{session_id}/review"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}/score"): RECRUITER_ORGANIZATION_SCOPED,
    ("GET", "/api/recruiter/sessions/{session_id}/trail"): RECRUITER_ORGANIZATION_SCOPED,

    # ---- Not the product surface --------------------------------------------
    ("GET", "/openapi.json"): INTERNAL_ONLY,
    ("GET", "/docs"): INTERNAL_ONLY,
    ("GET", "/docs/oauth2-redirect"): INTERNAL_ONLY,
    ("GET", "/redoc"): INTERNAL_ONLY,
    ("GET", "/admin"): INTERNAL_ONLY,
    # Served only when the candidate bundle is NOT built — it tells a developer
    # where the dev servers are. Mutually exclusive with `GET /{full_path:path}`
    # below, which is the built candidate app at the same address.
    ("GET", "/"): INTERNAL_ONLY,
    ("GET", "/admin/{full_path:path}"): INTERNAL_ONLY,
    ("GET", "/recruiter"): INTERNAL_ONLY,
    ("GET", "/recruiter/{full_path:path}"): INTERNAL_ONLY,
    ("GET", "/{full_path:path}"): INTERNAL_ONLY,
    ("MOUNT", "/assets"): INTERNAL_ONLY,
    ("MOUNT", "/recruiter/assets"): INTERNAL_ONLY,
}

#: Rows where the declared class is deliberately stricter than the wiring can
#: show, because the credential is checked in the handler rather than by a
#: dependency. Each one is covered by a test in `tests/test_security.py`, named
#: here so the exemption is not a place to hide.
HANDLER_ENFORCED: dict[tuple[str, str], str] = {
    ("GET", "/api/invite/{token}"):
        "invites.get(token) — an unguessable 43-byte token IS the credential; "
        "test_a_malformed_or_random_invitation_token_is_refused",
    ("POST", "/api/invite/{token}/precheck"):
        "same token, read-only; test_a_malformed_or_random_invitation_token_is_refused",
    ("POST", "/api/session/start"):
        "invites.can_start(token) before any session is minted; "
        "test_a_revoked_invitation_cannot_start_a_session",
    ("POST", "/api/session/{session_id}/voice"):
        "retell._session_belongs_to_caller — the same grant cookie or "
        "invitation token every other candidate route accepts; "
        "test_a_voice_call_cannot_be_minted_for_someone_elses_session",
    ("WS", "/llm-websocket/{call_id}"):
        "retell.session_for(call_id) — the call/session binding is recorded "
        "server-side when the call is minted, so a forged call id resolves to "
        "no session; test_an_unminted_call_id_reaches_no_interview",
    ("WS", "/ws/interview/{session_id}"):
        "_socket_is_authorised(ws, state) as the first act of the handler; "
        "test_candidate_a_cannot_reach_candidate_bs_session",
    ("POST", "/api/auth/logout"):
        "revokes whatever session the caller presents and always succeeds; "
        "test_logging_out_revokes_the_session_server_side",
    ("GET", "/api/recruiter/interviews/generate/progress/{progress_token}"):
        "progress.get(token, principal.organization_id) — the token is a "
        "client-minted lookup key, not a resource id, so no path resolver "
        "can scope it and the handler compares the organization itself; "
        "test_a_progress_board_is_not_readable_by_another_organization",
}


@dataclass(frozen=True)
class Discrepancy:
    kind: str          # "unclassified" | "stale" | "mismatch"
    route: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.route}: {self.detail}"


#: Routes that exist only in a BUILT deployment, and their counterpart that
#: exists only when the bundles are absent.
#:
#: `services/api/app.py` mounts the two SPAs behind `dist.exists()`, so a fresh
#: clone that has not run `npm run build` serves neither — and the dev notice at
#: `GET /` stands in for the candidate app. Both states are correct, so neither
#: may be reported as a discrepancy: declaring them unconditionally made a
#: freshly cloned repository fail its own security test, which trains people to
#: ignore exactly the test that should never be ignored.
#:
#: Their access class is still checked whenever they ARE mounted. This only
#: permits absence, never a wrong classification.
BUILD_DEPENDENT: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/"),
    ("GET", "/recruiter"),
    ("GET", "/recruiter/{full_path:path}"),
    ("GET", "/{full_path:path}"),
    ("MOUNT", "/assets"),
    ("MOUNT", "/recruiter/assets"),
})


def audit(app: Any) -> list[Discrepancy]:
    """Every way the matrix and the code can disagree."""
    out: list[Discrepancy] = []
    live = routes(app)
    by_key = {r.key: r for r in live}

    for route in live:
        declared = DECLARED.get(route.key)
        if declared is None:
            out.append(Discrepancy(
                "unclassified", str(route),
                f"not in the access matrix (code enforces {derive(route)}). "
                "Add it to services/security/matrix.py DECLARED.",
            ))
            continue
        derived = derive(route)
        if derived == declared or route.key in HANDLER_ENFORCED:
            continue
        out.append(Discrepancy(
            "mismatch", str(route),
            f"declared {declared}, code enforces {derived} "
            f"(dependencies: {', '.join(route.dependencies) or 'none'})",
        ))

    for key in DECLARED:
        if key not in by_key and key not in BUILD_DEPENDENT:
            out.append(Discrepancy(
                "stale", f"{key[0]} {key[1]}",
                "declared in the access matrix but not mounted. Remove the row.",
            ))
    return out


def table(app: Any) -> str:
    """The matrix as markdown, for PRODUCTION_SECURITY_MATRIX.md."""
    lines = [
        "| Method | Path | Access class | Enforced by |",
        "| --- | --- | --- | --- |",
    ]
    for route in routes(app):
        declared = DECLARED.get(route.key, "UNCLASSIFIED")
        if route.key in HANDLER_ENFORCED:
            enforced = "handler: " + HANDLER_ENFORCED[route.key].split(";")[0].strip()
        elif route.dependencies:
            enforced = "`" + "`, `".join(route.dependencies) + "`"
        else:
            enforced = "— (no credential)"
        lines.append(f"| {route.method} | `{route.path}` | {declared} | {enforced} |")
    return "\n".join(lines)
