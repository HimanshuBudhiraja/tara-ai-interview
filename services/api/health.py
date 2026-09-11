"""Liveness and readiness — two different questions.

    GET /api/health   is this process alive?
    GET /api/ready    can it actually serve a request?

The split is not ceremony. A load balancer that restarts a container because a
dependency is down turns a five-minute database blip into a crash loop, and a
platform that routes traffic to a process which cannot reach its storage turns
it into a wall of 500s. So:

**Liveness answers 200 whenever the process can run Python.** It touches no
database, no Redis and — emphatically — no AI provider. An OpenRouter outage,
or an exhausted key, must never make Tara look dead. Nor may a health check
spend money: a liveness probe that costs a token per call costs real money at
one call every ten seconds.

**Readiness checks the things a request genuinely needs**: the data directory is
writable, the question pool loaded, at least one account exists if
authentication is required, and production configuration is sane. It reports
`degraded` — 200, with detail — when something is impaired but requests can
still be served, and `unavailable` — 503 — when they cannot.

The AI provider appears in readiness as **information, never as a verdict**. Its
configuration is reported; whether it is reachable is not tested, for the same
reason liveness does not test it. An interview can start, a candidate can answer,
and an evaluation can be requested and queued while the provider is down; only
the evaluation's execution is blocked, and that has its own failure state.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response

from services import config

router = APIRouter(tags=["health"])

#: When the process started, so a probe can say how long it has been up. Set at
#: import, which is as close to "process start" as this module can observe.
_STARTED_AT = time.time()

OK = "ok"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


@router.get("/api/health")
async def health() -> dict[str, Any]:
    """Liveness. Cheap, dependency-free, and always 200 while the process runs.

    Deliberately says nothing about who is deployed or what is configured
    beyond the version: a liveness endpoint is reachable without credentials,
    so it is not the place to describe the deployment.
    """
    return {
        "ok": True,
        "service": "tara-api",
        "version": config.SERVICE_VERSION,
        "uptime_sec": round(time.time() - _STARTED_AT, 1),
    }


def _check_storage() -> dict[str, Any]:
    """Can this process actually write where it keeps state?

    An API that accepts a turn and then cannot persist it is worse than one that
    refuses the turn, so this is a readiness gate rather than a warning. The
    probe writes and removes a file rather than checking a permission bit,
    because a read-only mount and a full disk both pass the permission check.
    """
    probe = config.DATA_DIR / ".readiness"
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text(str(time.time()), encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return {"status": UNAVAILABLE,
                "detail": f"data directory not writable ({type(exc).__name__})"}
    # The PATH is not reported. Readiness is reachable without credentials, and
    # `PRODUCTION_SECURITY_MATRIX.md` §8 rules out internal filesystem paths in
    # any response — a probe is not an exception to that.
    return {"status": OK, "detail": "writable"}


def _check_question_pool() -> dict[str, Any]:
    try:
        from services.api.candidate import pool

        if not pool.items:
            return {"status": DEGRADED, "detail": "the authored pool is empty"}
        return {"status": OK, "detail": f"{len(pool.items)} authored items"}
    except Exception as exc:  # noqa: BLE001 — a probe must not raise
        return {"status": UNAVAILABLE, "detail": f"pool failed to load ({type(exc).__name__})"}


def _check_accounts() -> dict[str, Any]:
    """Somebody has to be able to sign in, or the recruiter API is closed."""
    try:
        from services.data import accounts

        if accounts.any_user_exists():
            return {"status": OK, "detail": "at least one account"}
    except Exception as exc:  # noqa: BLE001
        return {"status": UNAVAILABLE, "detail": f"account store unreadable ({type(exc).__name__})"}
    # No accounts is fatal only where the deployment says the recruiter API must
    # be closed without them — otherwise it is a first-boot state.
    status = UNAVAILABLE if config.RECRUITER_AUTH_REQUIRED else DEGRADED
    return {"status": status, "detail": "no accounts exist; create one with tools.make_user"}


def _check_configuration() -> dict[str, Any]:
    """Production configuration is valid.

    The problems themselves are printed at startup, into the deployment's own
    logs, and are not repeated here: they name origins, settings and sometimes
    an invitation token, and this endpoint has no credential in front of it. The
    count is enough to know something is wrong and where to look.
    """
    problems = config.require_production_configuration()
    if problems:
        return {"status": UNAVAILABLE,
                "detail": f"{len(problems)} production configuration problem(s); "
                          "see the startup log"}
    return {"status": OK, "detail": "valid"}


def _check_database() -> dict[str, Any]:
    """Reported, not required.

    Core state is file-backed today (see DATA_LIFECYCLE.md §1 and
    DEPLOYMENT.md). `DATABASE_URL` is read so the wiring has one home; nothing
    connects to it yet, and readiness must not claim a dependency the
    application does not actually have.
    """
    if not config.DATABASE_URL:
        return {"status": OK, "detail": "not configured; core state is file-backed"}
    return {"status": OK, "detail": "configured but unused by this build"}


def _check_provider() -> dict[str, Any]:
    """Information only — never a verdict, and never a call.

    Two reasons. A provider outage must not take Tara out of rotation: an
    interview still runs, and an evaluation still queues. And a readiness probe
    that calls a model spends money every few seconds forever.
    """
    return {
        "status": OK,
        "detail": "configured" if config.llm_is_live() else "not configured (mock)",
        "checked": False,
    }


#: name → probe. Order is the order they are reported in.
CHECKS = {
    "storage": _check_storage,
    "question_pool": _check_question_pool,
    "accounts": _check_accounts,
    "configuration": _check_configuration,
    "database": _check_database,
    "ai_provider": _check_provider,
}


@router.get("/api/ready")
async def ready(response: Response) -> dict[str, Any]:
    """Readiness. 200 when requests can be served, 503 when they cannot."""
    checks = {name: probe() for name, probe in CHECKS.items()}
    statuses = {c["status"] for c in checks.values()}

    if UNAVAILABLE in statuses:
        overall = UNAVAILABLE
        response.status_code = 503
    elif DEGRADED in statuses:
        overall = DEGRADED
    else:
        overall = OK

    # `environment` and `version` are here on purpose: an operator needs to know
    # which build a failing probe belongs to, and neither is a secret. Nothing
    # else about the deployment is named.
    return {
        "status": overall,
        "service": "tara-api",
        "version": config.SERVICE_VERSION,
        "environment": config.ENVIRONMENT,
        "uptime_sec": round(time.time() - _STARTED_AT, 1),
        "checks": checks,
    }
