"""Sign in, sign out, and say who you are. The only unauthenticated recruiter routes.

    POST /api/auth/login    email + password  →  an HttpOnly session cookie
    POST /api/auth/logout   revokes this session, server-side
    GET  /api/auth/me       the principal, or 401

Three properties worth stating because each closes a specific hole:

  * **The cookie is the only credential the browser holds**, it is HttpOnly, and
    the session behind it lives on disk where it can be revoked. Logging out is
    not "delete the cookie and hope".
  * **Every failure is the same failure.** Unknown address, wrong password and
    disabled account all return one 401 with one message, and an unknown address
    still pays for a password verification so the timing does not answer what
    the status code refuses to.
  * **Nothing here reads an organization from the request.** The organization
    comes from the user record found by the credential, so a client cannot ask
    to be in one.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services.data import accounts, audit
from services.security import principal as P
from services.security import ratelimit

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


@router.post("/api/auth/login")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    _: None = Depends(ratelimit.limiter("login")),
) -> dict[str, Any]:
    user = P.authenticator().login(body.email, body.password)
    if user is None:
        # One answer for every kind of failure, and one audit line that names
        # the address attempted without the password that was tried.
        audit.product(
            audit.LOGIN_FAILED, actor="anonymous", subject_type="user",
            subject_id=_masked(body.email), auth=P.authenticator().name,
        )
        raise HTTPException(401, "Those credentials are not valid.")

    session = accounts.start_session(user, request.headers.get("user-agent", ""))
    response.set_cookie(
        P.SESSION_COOKIE, session.token,
        max_age=accounts.ABSOLUTE_TIMEOUT_SEC, **P.candidate_cookie_kwargs(),
    )
    audit.product(
        audit.LOGIN_SUCCEEDED, actor=user.user_id, subject_type="user",
        subject_id=user.user_id, org_id=user.organization_id,
        role=user.role, auth=P.authenticator().name,
    )
    return {"user": user.public(), "organization": _org(user.organization_id)}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    """Always succeeds. Signing out must not depend on being signed in."""
    token = request.cookies.get(P.SESSION_COOKIE, "")
    if token:
        session = accounts.get_session(token)
        accounts.revoke_session(token)
        if session is not None:
            audit.product(
                audit.LOGOUT, actor=session.user_id, subject_type="user",
                subject_id=session.user_id, org_id=session.organization_id,
            )
    response.delete_cookie(P.SESSION_COOKIE, path="/")
    return {"signed_out": True}


@router.get("/api/auth/me")
async def me(
    who: P.AuthenticatedPrincipal = Depends(P.current_principal),
) -> dict[str, Any]:
    return {"user": who.public(), "organization": _org(who.organization_id)}


def _org(organization_id: str) -> dict[str, Any]:
    org = accounts.get_organization(organization_id)
    return {
        "organization_id": organization_id,
        "name": org.name if org else "",
        "status": org.status if org else "unknown",
    }


def _masked(email: str) -> str:
    """Enough of an address to investigate a burst, not enough to harvest one."""
    address = (email or "").strip().lower()
    if "@" not in address:
        return "invalid"
    local, _, domain = address.partition("@")
    return f"{local[:2]}…@{domain}"
