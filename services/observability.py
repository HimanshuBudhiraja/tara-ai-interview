"""Structured logs and a request id that survives the whole journey.

An operator has to be able to answer nine questions without reading a
transcript: is it up, can recruiters sign in, can candidates start, are turns
succeeding, are evaluations completing, are reports readable, is the provider
failing, is the database reachable, are errors rising. All nine are answerable
from these log lines plus the audit trail.

## The correlation id

One id per request, generated at the edge or taken from the client's
`X-Request-ID`, put in a contextvar, echoed on the response, and attached to
every log line and every AI-gateway call made while handling it. The evaluation
path carries it further: the id that requested an evaluation is stored on the
record, so a report that never appeared can be traced from the recruiter's click
to the worker's failure without joining on timestamps.

    browser → API → handler → evaluation record → worker → gateway → provider

## What is never logged

Passwords, authorization headers, session cookies, candidate tokens, session
grants, transcripts, answers, evidence quotes, provider keys, and candidate
names. Not "redacted after the fact" — they are never passed in. `_SAFE_QUERY`
is an allow-list rather than a deny-list, because a deny-list is a list of the
leaks somebody already thought of.

The format is JSON lines to stdout, which is what every platform's log shipper
expects, and human-readable text when `TARA_LOG_FORMAT=text` for local work.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from services import config

#: The current request's id, for anything that wants to name it without being
#: handed it. Read by the log formatter and by the AI gateway's telemetry.
_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="")

#: Query parameters safe to record. An allow-list: `?token=` is how a candidate
#: authenticates, and a deny-list would eventually miss one.
_SAFE_QUERY = frozenset({
    "interview_id", "pilot_run_id", "lifecycle", "limit", "dry_run", "q", "tab",
})

#: Stable error categories. The same vocabulary the AI gateway already uses for
#: provider failures, extended to the HTTP surface, so "what kind of thing went
#: wrong" is one word an operator can count rather than a message they must read.
AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
CONFLICT = "CONFLICT"
RATE_LIMITED = "RATE_LIMITED"
DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"

_BY_STATUS = {
    400: VALIDATION_ERROR,
    401: AUTHENTICATION_ERROR,
    403: AUTHORIZATION_ERROR,
    404: NOT_FOUND,
    409: CONFLICT,
    410: NOT_FOUND,
    422: VALIDATION_ERROR,
    429: RATE_LIMITED,
    503: DEPENDENCY_ERROR,
}


def error_category(status: int) -> str:
    if status < 400:
        return ""
    return _BY_STATUS.get(status, INTERNAL_ERROR if status >= 500 else VALIDATION_ERROR)


def request_id() -> str:
    """The current request's id, or empty outside a request."""
    return _REQUEST_ID.get()


def new_request_id(supplied: str = "") -> str:
    """Accept the client's id when it looks like one, otherwise mint one.

    Accepting a client-supplied value is what makes a trace span the browser and
    the server. It is bounded and filtered because it ends up in log lines: an
    unbounded string from an untrusted client is a log-injection vector.
    """
    clean = "".join(c for c in (supplied or "") if c.isalnum() or c in "-_")[:64]
    return clean or "req_" + uuid.uuid4().hex[:16]


# --------------------------------------------------------------------------- #
#  Formatting
# --------------------------------------------------------------------------- #
class JsonFormatter(logging.Formatter):
    """One JSON object per line. Nothing multi-line, so nothing splits."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname.lower(),
            "service": "tara-api",
            "environment": config.ENVIRONMENT,
            "version": config.SERVICE_VERSION,
            "message": record.getMessage(),
        }
        rid = getattr(record, "request_id", "") or request_id()
        if rid:
            payload["request_id"] = rid
        for key, value in getattr(record, "fields", {}).items():
            payload[key] = value
        if record.exc_info:
            # The TYPE, never the traceback: a traceback in a shipped log is
            # both noise and, when it quotes a payload, a leak.
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """For a terminal. Same fields, laid out for eyes."""

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        rid = getattr(record, "request_id", "") or request_id()
        tail = " ".join(f"{k}={v}" for k, v in fields.items())
        head = f"{record.levelname.lower():7} {record.getMessage()}"
        return f"{head}  {tail}" + (f"  [{rid}]" if rid else "")


_CONFIGURED = False


def configure() -> logging.Logger:
    """Install the handler once, idempotently."""
    global _CONFIGURED
    logger = logging.getLogger("tara")
    if _CONFIGURED:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    text = config.LOG_FORMAT == "text"
    handler.setFormatter(TextFormatter() if text else JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(config.LOG_LEVEL)
    logger.propagate = False
    _CONFIGURED = True
    return logger


def log(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """One structured line. `event` is the stable name; fields are the detail."""
    configure().log(level, event, extra={"fields": fields, "request_id": request_id()})


# --------------------------------------------------------------------------- #
#  The middleware
# --------------------------------------------------------------------------- #
def _safe_query(request: Any) -> dict[str, str]:
    return {k: v for k, v in request.query_params.items() if k in _SAFE_QUERY}


def _route(request: Any) -> str:
    """The route TEMPLATE, not the path.

    `/api/recruiter/sessions/{session_id}` rather than the session id itself:
    a per-id log line is unaggregatable, and a resource id does not belong in
    an unfiltered log stream.

    Built by substituting the matched path parameters back out of the real
    path, rather than reading `scope["route"].path`. An included router's route
    object carries only its own suffix (`/interviews`), so trusting it would
    label `/api/recruiter/interviews` and `/api/admin/interviews` identically
    and lose the namespace — which is the first thing you want when the
    recruiter API is misbehaving.
    """
    path = request.url.path
    for name, value in (request.path_params or {}).items():
        text = str(value)
        if text:
            path = path.replace(text, "{" + name + "}")
    return path


async def request_logger(request: Any, call_next: Any):
    """Wrap every request: correlate it, time it, and record the outcome."""
    rid = new_request_id(request.headers.get("X-Request-ID", ""))
    token = _REQUEST_ID.set(rid)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    except Exception:
        # Logged here and re-raised: the framework turns it into a 500 whose
        # body says nothing, and this line is the only place the type is kept.
        log("request.failed", logging.ERROR,
            method=request.method, route=_route(request),
            status=500, error_category=INTERNAL_ERROR,
            duration_ms=round((time.perf_counter() - started) * 1000, 1))
        raise
    finally:
        duration = round((time.perf_counter() - started) * 1000, 1)
        if status != 500:
            level = logging.WARNING if status >= 400 else logging.INFO
            log("request", level,
                method=request.method,
                route=_route(request),
                status=status,
                duration_ms=duration,
                **({"error_category": error_category(status)} if status >= 400 else {}),
                **({"query": _safe_query(request)} if _safe_query(request) else {}))
        _REQUEST_ID.reset(token)
