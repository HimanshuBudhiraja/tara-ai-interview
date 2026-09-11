"""The authenticated principal, and where it is allowed to come from.

    cookie / bearer token ─► login session on disk ─► user ─► AuthenticatedPrincipal

Nothing else establishes identity. Not a request body, not a query parameter,
not a header naming an organization, not anything the browser can set other
than the opaque session token it was given at login. That is the whole point of
the type: a handler that receives an `AuthenticatedPrincipal` knows the identity
was verified, because there is no other way to construct one from a request.

**Provider-neutral by construction.** `Authenticator` is the seam: today
`PasswordAuthenticator` checks a password against `services.data.accounts`, and
an OIDC / SAML / workspace-SSO integration replaces that one class without any
route, dependency or authorization rule changing. What must not happen is the
rest of the product learning that passwords exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException, Request

from services import config
from services.data import accounts
from services.security import context

#: The recruiter's login cookie. HttpOnly, so no script can read it; SameSite
#: Lax, so a cross-site form post cannot ride it.
SESSION_COOKIE = "tara_session"

#: The candidate's session grant, minted when their interview starts. Also
#: HttpOnly — the candidate app never needs to read it, only to send it.
CANDIDATE_COOKIE = "tara_candidate"

#: Accepted for API clients that cannot hold cookies (curl, the persona driver,
#: a future worker). Same token, same session row, same expiry — it is a
#: transport detail, not a second credential.
BEARER_PREFIX = "Bearer "


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Who is making this request. Constructed only from a verified session."""

    user_id: str
    organization_id: str
    role: str
    email: str

    def can(self, capability: str) -> bool:
        return capability in accounts.CAPABILITIES.get(self.role, frozenset())

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "role": self.role,
            "email": self.email,
            "capabilities": sorted(accounts.CAPABILITIES.get(self.role, frozenset())),
        }


# --------------------------------------------------------------------------- #
#  The authentication seam
# --------------------------------------------------------------------------- #
class Authenticator(Protocol):
    """Turn a credential into a user, or None. The only thing that may."""

    name: str

    def login(self, email: str, password: str) -> accounts.User | None: ...


class PasswordAuthenticator:
    """Local passwords, hashed with scrypt in `services.data.accounts`.

    The implementation an installation gets when it has no identity provider.
    It deliberately does no session handling, no cookie handling and no
    authorization — those belong to this module and to `authz`, so swapping this
    class out for an IdP touches nothing else.
    """

    name = "password"

    def login(self, email: str, password: str) -> accounts.User | None:
        return accounts.authenticate(email, password)


def authenticator() -> Authenticator:
    """The configured authentication mechanism.

    A single function so an installation with an identity provider has one place
    to change, and so nothing else in the product refers to a mechanism by name.
    """
    return PasswordAuthenticator()


# --------------------------------------------------------------------------- #
#  Reading the principal off a request
# --------------------------------------------------------------------------- #
def _token_from(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX):].strip()
    return request.cookies.get(SESSION_COOKIE, "")


def optional_principal(request: Request) -> AuthenticatedPrincipal | None:
    """The principal if this request carries a live session, else None.

    Sliding the idle window is a write, so this is not free — it is called once
    per request through the dependencies below rather than opportunistically.
    """
    token = _token_from(request)
    if not token:
        return None
    session = accounts.touch_session(token)
    if session is None:
        return None
    user = accounts.get_user(session.user_id)
    if user is None or user.status != accounts.ACTIVE:
        return None
    if user.organization_id != session.organization_id:
        # The user was moved between organizations after this session started.
        # The session is stale rather than wrong: refuse it and make them log in.
        return None
    principal = AuthenticatedPrincipal(
        user_id=user.user_id, organization_id=user.organization_id,
        role=user.role, email=user.email,
    )
    request.state.principal = principal
    # Published for the audit writer, which must name the actor on every line
    # without every handler having to pass one. Never read for authorization.
    context.set_principal(principal)
    return principal


def current_principal(request: Request) -> AuthenticatedPrincipal:
    """AUTHENTICATED. 401 when there is no verified session."""
    principal = optional_principal(request)
    if principal is None:
        raise HTTPException(
            401,
            "Sign in to use the recruiter API.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


#: Which capability an HTTP method implies. Coarse on purpose: the roles are
#: coarse, and a per-route permission table is a table that drifts from the
#: routes. `DELETE` is separated because destroying a hiring record is the one
#: recruiter-side action worth restricting to an organization's admins.
_METHOD_CAPABILITY = {
    "GET": "read", "HEAD": "read", "OPTIONS": "read",
    "POST": "write", "PUT": "write", "PATCH": "write",
    "DELETE": "delete",
}


def recruiter_scope(request: Request) -> AuthenticatedPrincipal:
    """The whole recruiter namespace, in one dependency.

    AUTHENTICATED, then ORGANIZATION_MEMBER, then ROLE_ALLOWED for the method,
    then RESOURCE_OWNER for every recognised id in the path. Mounted on the
    routers rather than written into sixty handlers, so a new route is protected
    the moment it exists and an unprotected one has to be added deliberately
    somewhere visible.
    """
    from services.security import authz

    principal = current_principal(request)
    authz.organization_member(principal)
    authz.role_allowed(principal, _METHOD_CAPABILITY.get(request.method, "write"))
    authz.enforce_path_ownership(principal, dict(request.path_params))
    return principal


def require_capability(capability: str):
    """A dependency for the few routes that need more than their method implies."""

    def dependency(request: Request) -> AuthenticatedPrincipal:
        from services.security import authz

        principal = current_principal(request)
        authz.organization_member(principal)
        authz.role_allowed(principal, capability)
        authz.enforce_path_ownership(principal, dict(request.path_params))
        return principal

    return dependency


# --------------------------------------------------------------------------- #
#  CANDIDATE_INVITATION_SCOPE
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CandidateScope:
    """A candidate's authority: exactly one session, and nothing else.

    Not a principal. A candidate has no user, no organization and no role — they
    have an invitation and the session it started. Keeping the two types apart
    is what stops a candidate scope ever being accepted where a recruiter
    principal is required.
    """

    session_id: str
    invite_token: str

    @property
    def public(self) -> dict[str, Any]:
        return {"session_id": self.session_id}


def candidate_scope(request: Request, session_id: str) -> CandidateScope:
    """Prove this request may act on this session, or 404.

    Two acceptable proofs, and both are checked against the session on disk:

      * the `tara_candidate` cookie, minted when this session started. What the
        browser uses, and what a re-opened link re-establishes;
      * the invitation token, as `X-Candidate-Token` or `?token=`. What an
        HTTP client without a cookie jar uses — the same secret the candidate
        already holds in their link, so it grants nothing new.

    A missing or wrong proof answers 404 with the same message an unknown
    session gets. A candidate who guesses another candidate's session id must
    not learn that they guessed correctly.
    """
    from services.data import sessions as session_store
    from services.security.authz import NOT_FOUND

    state = session_store.try_load(session_id)
    if state is None:
        raise HTTPException(404, NOT_FOUND)

    grant = request.cookies.get(CANDIDATE_COOKIE, "")
    if grant and state.session_grant and grant == state.session_grant:
        return CandidateScope(session_id=session_id, invite_token=state.invite_token)

    supplied = (
        request.headers.get("x-candidate-token", "")
        or request.query_params.get("token", "")
    ).strip()
    if supplied and state.invite_token and supplied == state.invite_token:
        return CandidateScope(session_id=session_id, invite_token=state.invite_token)

    raise HTTPException(404, NOT_FOUND)


def candidate_cookie_kwargs() -> dict[str, Any]:
    """How both cookies are set. One place, so neither can drift insecure."""
    return {
        "httponly": True,
        "samesite": "lax",
        # Secure in any deployment that is not a laptop. Left off for plain
        # HTTP local development, where a Secure cookie would simply not be
        # sent and nothing would work.
        "secure": config.COOKIES_SECURE,
        "path": "/",
    }
