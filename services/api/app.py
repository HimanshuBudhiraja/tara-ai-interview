"""The HTTP surface — two experiences, one backend.

    /api/*            candidate: invites, sessions, turns   (no auth: the link IS the auth)
    /ws/interview/*   candidate: the live turn socket
    /api/recruiter/*  recruiter: interviews, versions, candidates, review
    /api/admin/*      the same recruiter router, kept mounted for continuity

Why they are separate routers on one app rather than two services: they share
one interview record, one version history and one audit log, and splitting them
across processes would mean either duplicating that state or inventing an
internal API to reach it. What they must NOT share is a namespace, because the
namespace is where authentication goes — and today only one of them needs it.

The candidate app is never served anything from the recruiter namespace. It has
no copy of the question pool, no scoring rules, and no way to ask for either
(rule 10). It finds out what the next question is the same way the candidate
does: by being told.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Running `uvicorn services.api.app:app` from the repo root puts the root on
# sys.path already; this covers being launched from anywhere else.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import config, observability  # noqa: E402
from services.ai.brain import get_llm  # noqa: E402
from services.api import (  # noqa: E402
    auth,
    candidate,
    design,
    evaluation,
    health,
    lifecycle,
    pilot,
    publish,
    questions,
    recruiter,
    retell,
)
from services.data import accounts, interviews, invites, versions  # noqa: E402
from services.data import pilot as pilot_store  # noqa: E402
from services.security import principal as security  # noqa: E402

app = FastAPI(title="Tara AI Interview", version="0.1.0")

# Registered first so it wraps everything below it, including the CORS layer and
# the recruiter guard: a request refused by either still gets a request id and a
# log line, which is exactly when an operator most wants one.
app.middleware("http")(observability.request_logger)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
#  Recruiter authentication — the closed door
# --------------------------------------------------------------------------- #
@app.middleware("http")
async def _guard_recruiter_routes(request: Request, call_next):
    """The last resort behind the per-router dependency.

    Authorization is enforced by `security.recruiter_scope`, mounted on every
    recruiter router, and that is what a request actually meets. This middleware
    exists for one narrower case: a deployment that requires authentication and
    has nobody who can authenticate. Serving the recruiter API to a system with
    no accounts would mean the only thing standing between the internet and
    every transcript is that no login exists — which is not a control.

    It is not the authentication check. It cannot be: middleware runs before
    routing, so it cannot know which resource is being asked for.
    """
    path = request.url.path
    recruiter_namespace = path.startswith("/api/recruiter") or path.startswith("/api/admin")
    if recruiter_namespace and config.RECRUITER_AUTH_REQUIRED and not accounts.any_user_exists():
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Recruiter API is closed: this deployment requires "
                          "authentication and has no accounts yet."
            },
        )
    return await call_next(request)


# Authentication is the one recruiter-side surface that cannot require a
# session, so it is mounted before the guarded routers and outside their prefix.
app.include_router(health.router)
app.include_router(auth.router)

app.include_router(candidate.router)
# Voice transport. Candidate-scoped like the rest of the candidate surface —
# NOT behind RECRUITER_GUARD — and mounted whether or not Retell is configured,
# so the routes have one access class rather than a shape that depends on the
# environment. `create_web_call` answers 503 when it is not configured.
app.include_router(retell.router)

# The design router is mounted FIRST because FastAPI matches in registration
# order: `/interviews/generate` has to be reached before `/interviews/{id}`
# would swallow it as an interview called "generate".
#: Authentication, organization membership, role and resource ownership, for
#: every route in every recruiter router. Mounted here rather than written into
#: each handler so that adding a route cannot accidentally add an open one — and
#: so the list of protected surfaces is this list, readable in one place.
RECRUITER_GUARD = [Depends(security.recruiter_scope)]

app.include_router(design.router, prefix="/api/recruiter", dependencies=RECRUITER_GUARD)
app.include_router(questions.router, prefix="/api/recruiter", dependencies=RECRUITER_GUARD)
app.include_router(publish.router, prefix="/api/recruiter", dependencies=RECRUITER_GUARD)
# Before the recruiter router: `/sessions/{id}/evaluation` has to be matched
# as an evaluation route rather than reaching the session-review handlers.
app.include_router(evaluation.router, prefix="/api/recruiter", dependencies=RECRUITER_GUARD)
# Also before the recruiter router: `/sessions/{id}/review` and the `/pilot/*`
# reporting routes have to be matched before the session-review handlers.
app.include_router(pilot.router, prefix="/api/recruiter", dependencies=RECRUITER_GUARD)
# Before the recruiter router: `/candidates/{token}/data` has to be matched as a
# lifecycle route rather than reaching the candidate handlers.
app.include_router(lifecycle.router, prefix="/api/recruiter", dependencies=RECRUITER_GUARD)
app.include_router(recruiter.router, prefix="/api/recruiter", dependencies=RECRUITER_GUARD)

# The console shipped on /api/admin. Keeping that address alive means an older
# build, a bookmark, or a curl someone has in a runbook keeps working — the
# rename is a rename, not a breaking change. Same router, so there is exactly
# one implementation behind both.
for _router in (design.router, questions.router, publish.router,
                evaluation.router, pilot.router, lifecycle.router, recruiter.router):
    app.include_router(
        _router, prefix="/api/admin", include_in_schema=False,
        dependencies=RECRUITER_GUARD,
    )


def _backfill_skill_domains() -> int:
    """Propose a domain for skills that predate the skill master.

    Resolution runs on save, so an interview nobody has touched since the
    catalogue landed carries no domains — and the publish check would stop a
    recruiter on skills they never had the chance to file. Idempotent, and it
    never overwrites a domain already set, so running it on every boot costs a
    pass over the drafts and changes nothing after the first.
    """
    from services.data import skill_master

    touched = 0
    for row in interviews.list_all():
        before = [(s.competency_id, s.domain) for s in row.skills]
        for skill in row.skills:
            skill_master.apply_to(skill)
        if [(s.competency_id, s.domain) for s in row.skills] != before:
            interviews.save(row)
            touched += 1
    return touched


def _adopt_untenanted_interviews(organization_id: str) -> int:
    """Give every interview that predates tenancy an owner.

    Ownership is derived through `InterviewConfig.organization_id`, so an
    interview without one is unreachable — including the sessions, invitations
    and evaluations beneath it. Rather than leave a pilot's data orphaned or
    make "no owner" mean "anyone", the rows that existed before this phase are
    adopted by the default organization, once, idempotently.
    """
    adopted = 0
    for row in interviews.list_all():
        if not row.organization_id:
            row.organization_id = organization_id
            interviews.save(row)
            adopted += 1
    return adopted


@app.on_event("startup")
async def _startup() -> None:
    # Identity first: everything else needs an owner, including the demo.
    org = accounts.ensure_default_organization()
    adopted = _adopt_untenanted_interviews(org.organization_id)
    adopted_runs = pilot_store.adopt_untenanted_runs(org.organization_id)
    domained = _backfill_skill_domains()

    bootstrapped = None
    if config.BOOTSTRAP_EMAIL and config.BOOTSTRAP_PASSWORD and not accounts.any_user_exists():
        # The first administrator, from configuration, once. Nothing prints the
        # password and nothing stores it beyond the scrypt hash.
        target = org
        if config.BOOTSTRAP_ORG:
            target = accounts.create_organization(config.BOOTSTRAP_ORG)
        try:
            bootstrapped = accounts.create_user(
                target.organization_id, config.BOOTSTRAP_EMAIL,
                config.BOOTSTRAP_PASSWORD, role=accounts.ADMIN,
            )
        except accounts.AccountError as exc:
            print(f"[boot] ⚠ could not create the first administrator: {exc}", flush=True)

    pool = candidate.pool
    default = interviews.ensure_default(pool)
    if not default.organization_id:
        default.organization_id = org.organization_id
        interviews.save(default)
    published = versions.latest_published(default.id)
    # Development convenience only. A well-known, never-expiring token is a
    # credential; production deployments get their candidates from real
    # invitations issued by a signed-in recruiter.
    invite = None
    if not config.is_production():
        invite = invites.ensure_demo_invite(
            default.id, published.version if published else 0
        )
    else:
        # Production REVOKES it rather than merely not creating it.
        #
        # Not creating it was never enough: the invitation lives in the data
        # directory, so a deployment that ran once in development — or was
        # promoted from a dev volume — carries a usable, never-expiring,
        # well-known credential for a real interview forever afterwards, and
        # switching TARA_ENV later does nothing about the row already on disk.
        # This is a real deployment that hit exactly that.
        #
        # `/api/invite/demo` already refuses to serve it on a public host, so
        # this is the second of two independent measures rather than the only
        # one — and it is the one that makes the state on disk match the
        # posture, instead of leaving a live credential that something else
        # happens to be standing in front of.
        stale = invites.get(config.DEMO_TOKEN)
        if stale is not None and stale.effective_status not in ("revoked", "expired"):
            invites.revoke(config.DEMO_TOKEN)
            print("[boot] revoked the well-known 'demo' invitation "
                  "(production)", flush=True)
    print(f"[boot] role      : {pool.role_title} ({len(pool.items)} authored items)", flush=True)
    print(f"[boot] ai        : {get_llm().name}", flush=True)
    print(f"[boot] interview : {default.title} @ v{published.version if published else '—'}",
          flush=True)
    print(f"[boot] org       : {org.name} ({org.organization_id})"
          + (f" · adopted {adopted} untenanted interview(s)" if adopted else "")
          + (f" · adopted {adopted_runs} untenanted pilot run(s)" if adopted_runs else "")
          + (f" · filed skills in {domained} interview(s)" if domained else ""),
          flush=True)
    print(f"[boot] users     : {len(accounts.list_users())}", flush=True)
    if bootstrapped is not None:
        print(f"[boot] created   : {bootstrapped.email} ({bootstrapped.role})", flush=True)
    if invite is not None:
        print(f"[boot] candidate : http://localhost:5173/?invite={invite.token}", flush=True)
    print("[boot] recruiter : http://localhost:5174/recruiter", flush=True)
    if not accounts.any_user_exists():
        print("[boot] ⚠ no accounts yet — create one with "
              "`python -m tools.make_user --email you@example.com --admin`", flush=True)
    for problem in config.require_production_configuration():
        print(f"[boot] ⚠ production configuration: {problem}", flush=True)


# --------------------------------------------------------------------------- #
#  Built frontends
#
#  In development each app runs on its own Vite server and proxies /api here, so
#  none of this is used. In a built deployment one process serves both bundles:
#  the recruiter app under /recruiter, the candidate app at the root.
# --------------------------------------------------------------------------- #
_CANDIDATE_DIST = ROOT / "apps" / "candidate" / "dist"
_RECRUITER_DIST = ROOT / "apps" / "recruiter" / "dist"

if _RECRUITER_DIST.exists():
    app.mount(
        "/recruiter/assets",
        StaticFiles(directory=_RECRUITER_DIST / "assets"),
        name="recruiter-assets",
    )

    @app.get("/recruiter")
    @app.get("/recruiter/{full_path:path}")
    async def recruiter_spa(full_path: str = ""):
        return FileResponse(_RECRUITER_DIST / "index.html")


@app.get("/admin/{full_path:path}")
@app.get("/admin")
async def admin_redirect(full_path: str = ""):
    """`/admin` was the console's address. Send it to its new one."""
    return RedirectResponse(f"/recruiter/{full_path}".rstrip("/"), status_code=308)


if _CANDIDATE_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_CANDIDATE_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def candidate_spa(full_path: str):
        if full_path.startswith(("api/", "ws/", "recruiter")):
            raise HTTPException(404)
        return FileResponse(_CANDIDATE_DIST / "index.html")

else:

    @app.get("/")
    async def dev_notice():
        return JSONResponse(
            {
                "message": "API is up. The two frontends run on their own dev servers.",
                "candidate": "cd apps/candidate && npm run dev  →  http://localhost:5173/?invite=demo",
                "recruiter": "cd apps/recruiter && npm run dev  →  http://localhost:5174/recruiter",
                "health": "/api/health",
            }
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("services.api.app:app", host="0.0.0.0", port=8000, reload=False)
