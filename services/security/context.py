"""Who is making the current request, for the code that must not have to ask.

The audit trail has to name the authenticated actor and their organization on
every recruiter-side line. Threading a principal through forty handlers and
into every `audit.product(...)` call would touch every one of them and be
forgotten on the forty-first, so the verified principal is published here — a
context variable set once by the authorization guard and read by the audit
writer as a *default*.

Two rules keep this from becoming an ambient-authority mistake:

  * **It is never an authorization input.** Nothing in `authz` reads it. A
    handler decides what a caller may do from the principal it was given, not
    from a global.
  * **Explicit always wins.** A candidate-side audit line passes its own actor
    and keeps it; this only fills in what the caller did not say.
"""
from __future__ import annotations

import contextvars
from typing import Any

_CURRENT: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "tara_principal", default=None
)


def set_principal(principal: Any) -> None:
    _CURRENT.set(principal)


def clear() -> None:
    _CURRENT.set(None)


def principal() -> Any | None:
    return _CURRENT.get()


def actor() -> tuple[str, str, str]:
    """`(actor_type, actor_id, organization_id)` for the audit writer."""
    who = _CURRENT.get()
    if who is None:
        return "", "", ""
    return "user", who.user_id, who.organization_id
