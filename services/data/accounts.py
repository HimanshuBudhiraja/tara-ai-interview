"""Organizations, users, and login sessions — the identity the product had none of.

Everything recruiter-side used to be reachable by anyone who could reach the
port. This module is the smallest durable identity that fixes that:

    Organization ─┬─ User ─── login session (opaque, revocable, expiring)
                  └─ Job ─── Interview ─┬─ InterviewVersion
                                        ├─ Invitation ─── candidate session
                                        └─ Evaluation

Three deliberate choices:

  * **Server-side login sessions, not JWTs.** A session is a random 32-byte
    token in an HttpOnly cookie and a row on disk. Logging out, disabling a user
    or rotating an organization all take effect on the next request, which a
    self-contained signed token cannot promise without a revocation list — and a
    revocation list is the row this already is.
  * **Passwords hashed with `scrypt`,** from the standard library, per-user
    salt, parameters recorded with the hash so they can be raised later without
    invalidating anyone. No new dependency, and no home-made hashing.
  * **Candidates are not users.** A candidate never has an account: their access
    is the invitation token and the session grant minted from it. Identity for
    someone who answers eight questions once would be data collected for the
    product's convenience rather than theirs.

The files are `data/organizations.json`, `data/users.json` and
`data/auth_sessions.json`, written through `jsonfile` — atomic, and serialised
per file so two concurrent logins cannot lose each other's row.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from services import config
from services.data import jsonfile

# --------------------------------------------------------------------------- #
#  Roles
#
#  Three, because the product has three genuinely different needs and a fourth
#  would be invented rather than observed. `admin` is an ORGANIZATION admin —
#  there is no cross-organization superuser, and adding one would be the first
#  thing an attacker looked for.
# --------------------------------------------------------------------------- #
ADMIN = "admin"
RECRUITER = "recruiter"
VIEWER = "viewer"
ROLES = (ADMIN, RECRUITER, VIEWER)

#: What each role may do, as a set of coarse capabilities the authorization
#: layer checks. Deliberately coarse: a permission matrix nobody can hold in
#: their head is a matrix nobody notices a hole in.
CAPABILITIES: dict[str, frozenset[str]] = {
    ADMIN: frozenset({"read", "write", "publish", "invite", "delete", "audit", "admin"}),
    RECRUITER: frozenset({"read", "write", "publish", "invite"}),
    VIEWER: frozenset({"read"}),
}

ACTIVE = "active"
DISABLED = "disabled"

#: How long a login lasts without activity, and at most in total. Short enough
#: that a laptop left open in a coffee shop is not a standing grant; long enough
#: that a recruiter designing an interview is not logged out mid-form.
IDLE_TIMEOUT_SEC = 12 * 3600
ABSOLUTE_TIMEOUT_SEC = 7 * 24 * 3600

#: scrypt parameters. Recorded with every hash so they can be raised without
#: invalidating existing passwords.
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


class AccountError(RuntimeError):
    """Something about an account request that cannot be satisfied."""


# --------------------------------------------------------------------------- #
#  Records
# --------------------------------------------------------------------------- #
@dataclass
class Organization:
    organization_id: str
    name: str
    status: str = ACTIVE
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class User:
    user_id: str
    organization_id: str
    email: str
    role: str = RECRUITER
    status: str = ACTIVE
    #: `scrypt$n$r$p$salt$hash`, all hex. Never leaves this module.
    password_hash: str = ""
    created_at: float = field(default_factory=time.time)
    last_login_at: float | None = None

    @property
    def capabilities(self) -> frozenset[str]:
        return CAPABILITIES.get(self.role, frozenset())

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public(self) -> dict[str, Any]:
        """What may cross the wire. Never the hash."""
        return {
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "capabilities": sorted(self.capabilities),
        }


@dataclass
class LoginSession:
    #: The cookie value. 32 bytes of CSPRNG, and the only credential the browser
    #: holds after login.
    token: str
    user_id: str
    organization_id: str
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + ABSOLUTE_TIMEOUT_SEC)
    revoked_at: float | None = None
    #: Recorded so a suspicious session can be recognised. Never a full header.
    user_agent: str = ""

    @property
    def live(self) -> bool:
        now = time.time()
        return (
            self.revoked_at is None
            and now < self.expires_at
            and (now - self.last_seen_at) < IDLE_TIMEOUT_SEC
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
#  Storage
# --------------------------------------------------------------------------- #
def _path(name: str):
    return config.DATA_DIR / name


def _read(name: str, cls):
    path = _path(name)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = set(cls.__dataclass_fields__)
    return {k: cls(**{kk: vv for kk, vv in v.items() if kk in known}) for k, v in raw.items()}


def _write(name: str, rows: dict) -> None:
    jsonfile.write_atomic(_path(name), {k: asdict(v) for k, v in rows.items()})


def _serialised(name: str):
    """Hold this file's lock for a whole read-modify-write."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            with jsonfile.guarded(_path(name)):
                return fn(*args, **kwargs)

        return wrapper

    return decorate


ORGS = "organizations.json"
USERS = "users.json"
SESSIONS = "auth_sessions.json"


# --------------------------------------------------------------------------- #
#  Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    if len(password) < 12:
        raise AccountError("A password must be at least 12 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${hash}".format(
        salt=salt.hex(), hash=digest.hex(), **_SCRYPT
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time comparison against a stored hash.

    A malformed or empty stored hash verifies nothing rather than everything —
    the failure mode of a truthy default here is every password working.
    """
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        computed = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest)),
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(computed, bytes.fromhex(digest))


# --------------------------------------------------------------------------- #
#  Organizations
# --------------------------------------------------------------------------- #
@_serialised(ORGS)
def create_organization(name: str, organization_id: str = "") -> Organization:
    rows = _read(ORGS, Organization)
    org = Organization(
        organization_id=organization_id or "org_" + secrets.token_hex(6),
        name=name.strip() or "Unnamed organization",
    )
    rows[org.organization_id] = org
    _write(ORGS, rows)
    return org


def get_organization(organization_id: str) -> Organization | None:
    return _read(ORGS, Organization).get(organization_id)


def list_organizations() -> list[Organization]:
    return sorted(_read(ORGS, Organization).values(), key=lambda o: o.created_at)


DEFAULT_ORG_ID = "org_default"


@_serialised(ORGS)
def ensure_default_organization() -> Organization:
    """The organization everything that predates tenancy belongs to.

    A single-tenant deployment and a fresh clone both need one organization to
    exist before anything can be owned, and the alternative — resources with no
    owner — is the hole this whole module closes.
    """
    rows = _read(ORGS, Organization)
    existing = rows.get(DEFAULT_ORG_ID)
    if existing:
        return existing
    org = Organization(organization_id=DEFAULT_ORG_ID, name="Default organization")
    rows[org.organization_id] = org
    _write(ORGS, rows)
    return org


# --------------------------------------------------------------------------- #
#  Users
# --------------------------------------------------------------------------- #
def _normalise(email: str) -> str:
    return email.strip().lower()


@_serialised(USERS)
def create_user(
    organization_id: str,
    email: str,
    password: str,
    *,
    role: str = RECRUITER,
    status: str = ACTIVE,
) -> User:
    if role not in ROLES:
        raise AccountError(f"Role must be one of {', '.join(ROLES)}.")
    if get_organization(organization_id) is None:
        raise AccountError("No such organization.")
    address = _normalise(email)
    if "@" not in address:
        raise AccountError("That is not an email address.")
    rows = _read(USERS, User)
    if any(u.email == address for u in rows.values()):
        raise AccountError("A user with that email already exists.")
    user = User(
        user_id="usr_" + secrets.token_hex(6),
        organization_id=organization_id,
        email=address,
        role=role,
        status=status,
        password_hash=hash_password(password),
    )
    rows[user.user_id] = user
    _write(USERS, rows)
    return user


def get_user(user_id: str) -> User | None:
    return _read(USERS, User).get(user_id)


def find_user(email: str) -> User | None:
    address = _normalise(email)
    return next((u for u in _read(USERS, User).values() if u.email == address), None)


def list_users(organization_id: str = "") -> list[User]:
    rows = _read(USERS, User).values()
    if organization_id:
        rows = [u for u in rows if u.organization_id == organization_id]
    return sorted(rows, key=lambda u: u.created_at)


def any_user_exists() -> bool:
    return bool(_read(USERS, User))


@_serialised(USERS)
def set_status(user_id: str, status: str) -> User | None:
    rows = _read(USERS, User)
    user = rows.get(user_id)
    if user is None:
        return None
    user.status = status
    _write(USERS, rows)
    return user


@_serialised(USERS)
def _stamp_login(user_id: str) -> None:
    rows = _read(USERS, User)
    user = rows.get(user_id)
    if user is not None:
        user.last_login_at = time.time()
        _write(USERS, rows)


# --------------------------------------------------------------------------- #
#  Login sessions
# --------------------------------------------------------------------------- #
def authenticate(email: str, password: str) -> User | None:
    """Verify a password. Returns the user, or None for every kind of failure.

    One return value for "no such user", "wrong password" and "disabled
    account", because three distinguishable answers are an account-enumeration
    oracle. The caller turns all of them into one 401.

    The password is still verified against a dummy hash when the user does not
    exist, so the response time does not answer the question the return value
    refuses to.
    """
    user = find_user(email)
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    if user.status != ACTIVE:
        return None
    if (org := get_organization(user.organization_id)) is None or org.status != ACTIVE:
        return None
    _stamp_login(user.user_id)
    return user


#: A real hash of a random password, computed once, so a login attempt for an
#: unknown address costs the same as one for a known address.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


@_serialised(SESSIONS)
def start_session(user: User, user_agent: str = "") -> LoginSession:
    rows = _read(SESSIONS, LoginSession)
    session = LoginSession(
        token=secrets.token_urlsafe(32),
        user_id=user.user_id,
        organization_id=user.organization_id,
        user_agent=(user_agent or "")[:120],
    )
    rows[session.token] = session
    _write(SESSIONS, _prune(rows))
    return session


def get_session(token: str) -> LoginSession | None:
    if not token:
        return None
    return _read(SESSIONS, LoginSession).get(token)


@_serialised(SESSIONS)
def touch_session(token: str) -> LoginSession | None:
    """Confirm a session is live and slide its idle window."""
    rows = _read(SESSIONS, LoginSession)
    session = rows.get(token)
    if session is None or not session.live:
        return None
    session.last_seen_at = time.time()
    _write(SESSIONS, rows)
    return session


@_serialised(SESSIONS)
def revoke_session(token: str) -> None:
    rows = _read(SESSIONS, LoginSession)
    session = rows.get(token)
    if session is not None and session.revoked_at is None:
        session.revoked_at = time.time()
        _write(SESSIONS, rows)


@_serialised(SESSIONS)
def revoke_sessions_for(user_id: str) -> int:
    """Every login this user holds. What "disable this account" has to mean."""
    rows = _read(SESSIONS, LoginSession)
    count = 0
    for session in rows.values():
        if session.user_id == user_id and session.revoked_at is None:
            session.revoked_at = time.time()
            count += 1
    if count:
        _write(SESSIONS, rows)
    return count


def _prune(rows: dict[str, LoginSession]) -> dict[str, LoginSession]:
    """Drop sessions that are long past use.

    Kept for a day after expiry so "why was I logged out" is answerable, then
    removed — an unbounded table of dead credentials is a file that only ever
    grows and only ever hurts.
    """
    cutoff = time.time() - 86400
    return {
        token: row for token, row in rows.items()
        if row.live or max(row.expires_at, row.revoked_at or 0, row.last_seen_at) > cutoff
    }
