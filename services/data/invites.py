"""Invitations — the candidate's way in.

The candidate never signs up and never picks a role. They open a link that
already knows who they are and what they're interviewing for. This module is
the stand-in for whatever the recruiter console / ATS will own in production;
the contract the candidate app depends on is just `get()`.

Seeded from data/invites.json, created on demand in dev so a fresh clone has a
working link immediately.
"""
from __future__ import annotations

import functools
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from services import config
from services.data import jsonfile

_PATH = config.DATA_DIR / "invites.json"


#: Invitation lifecycle. Every transition is server-authoritative — a browser
#: cannot declare itself COMPLETE or EXPIRED, because the two states a candidate
#: would most like to control are exactly those.
#:
#:   created  → active   the link exists and is usable
#:   active   → opened   the candidate loaded the welcome screen
#:   opened   → in_progress   a session started
#:   in_progress → complete   the ORCHESTRATOR closed the interview
#:   any      → expired  the clock, not the candidate
#:   any      → revoked  the recruiter withdrew it
STATUSES = (
    "created", "active", "opened", "in_progress", "complete", "expired", "revoked",
)

#: States from which a NEW session may begin. `complete` is absent on purpose:
#: an interview that has been sat cannot be sat again on the same link.
CAN_START = ("created", "active", "opened")

#: Email delivery is not configured in this build. Invitations are created and
#: their links are shown to the recruiter to send themselves — see BUILD_STATUS.
CHANNELS = ("link", "email")

#: The bounded batch the recruiter UI offers.
MAX_EMAILS_PER_BATCH = 10


@dataclass
class Invite:
    #: Opaque, cryptographically random, and carrying nothing. Not derived from
    #: the candidate or the interview, and reversible into neither — everything
    #: about an invitation is looked up server-side from this string alone.
    token: str
    candidate_name: str
    candidate_id: str
    role: str
    interview_id: str = ""
    # The published version this candidate was invited to sit. Captured when the
    # link is minted, not when it is opened — otherwise publishing v2 on Tuesday
    # changes the interview for someone invited on Monday.
    interview_version: int = 0
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    session_id: str | None = None
    status: str = "created"
    #: How it was issued. "link" is a link the recruiter copies and sends
    #: themselves; "email" records an address we were asked to send to and
    #: could not, because no provider is configured.
    channel: str = "link"
    recipient: str = ""
    #: True for a reusable open link. A single-use invitation belongs to one
    #: person and is spent once they sit it.
    open_link: bool = False
    opened_at: float | None = None
    completed_at: float | None = None
    revoked_at: float | None = None
    note: str = ""

    # ------------------------------------------------------------------ #
    #  Data lifecycle
    #
    #  Separate from `status` on purpose, and the distinction is not
    #  pedantic: `status` is about whether the INVITATION can be used
    #  (created → active → in_progress → complete, or revoked), while
    #  `lifecycle` is about whether the candidate's DATA still exists. A
    #  completed interview is `status="complete"` for as long as the record
    #  is kept and then `lifecycle="deleted"` afterwards; collapsing the two
    #  would make "this candidate finished" and "this candidate's transcript
    #  is gone" the same fact, which they are not.
    #
    #  The invitation is the anchor because it is the one row that exists
    #  across the whole candidate lifecycle — before a session, after one,
    #  and after the session is erased. See DATA_LIFECYCLE.md.
    # ------------------------------------------------------------------ #
    lifecycle: str = "active"
    #: When an authorized recruiter asked for erasure, or the sweep marked it.
    deletion_requested_at: float | None = None
    #: When erasure completed AND verification confirmed nothing remained.
    deleted_at: float | None = None
    #: How many times erasure has been attempted. A retry increments it, so a
    #: record that keeps failing is visible rather than quietly stuck.
    deletion_attempts: int = 0
    #: Why the last attempt failed, in operator language. Never candidate
    #: content — see `erasure._failure_reason`.
    deletion_error: str = ""
    #: Which locations still held data at the last verification. Location
    #: names only, never what was in them.
    deletion_remaining: list[str] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and time.time() > self.expires_at)

    @property
    def effective_status(self) -> str:
        """The status a reader should trust.

        Expiry is a fact about the clock, not a stored state someone remembered
        to write — so it is computed rather than looked up, and an invitation
        cannot be kept alive by a row that was never updated.
        """
        if self.status in ("revoked", "complete"):
            return self.status
        if self.expired:
            return "expired"
        return self.status

    def can_start_session(self) -> tuple[bool, str]:
        """May a NEW session begin on this invitation?

        The reason is for the audit trail and the server log. What the candidate
        sees is the existing Problem screen's wording, which deliberately says
        less (§16).
        """
        status = self.effective_status
        if status == "revoked":
            return False, "the invitation was withdrawn"
        if status == "expired":
            return False, "the invitation has expired"
        if status == "complete" and not self.open_link:
            return False, "this invitation has already been used"
        if not self.open_link and status not in CAN_START and status != "in_progress":
            return False, f"the invitation is {status}"
        return True, ""


def _read_all() -> dict[str, Invite]:
    if not _PATH.exists():
        return {}
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    known = set(Invite.__dataclass_fields__)
    return {
        k: Invite(**{kk: vv for kk, vv in v.items() if kk in known}) for k, v in raw.items()
    }


def _write_all(invites: dict[str, Invite]) -> None:
    jsonfile.write_atomic(_PATH, {k: asdict(v) for k, v in invites.items()})


def _serialised(fn):
    """Hold this file's lock for the whole read-modify-write.

    Three concurrent interviews each update their own invitation twice — once at
    session start, once at completion — and without this the second write of an
    interleaved pair silently discarded the first one's row. Re-entrant, so
    `mark_opened` calling `update` is fine.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any):
        with jsonfile.guarded(_PATH):
            return fn(*args, **kwargs)

    return wrapper


@_serialised
def all_invites() -> list[Invite]:
    """Every invitation as an object, in ONE read of the file.

    `list_all()` plus `get()` per row re-reads a multi-megabyte file once per
    invitation, which turns a retention sweep over a few thousand candidates
    into minutes of pointless I/O. Anything that walks the whole set uses this.
    """
    return list(_read_all().values())


@_serialised
def create(
    candidate_name: str,
    role: str = config.ROLE,
    interview_id: str = "",
    ttl_days: int = 14,
    interview_version: int = 0,
    *,
    channel: str = "link",
    recipient: str = "",
    open_link: bool = False,
    note: str = "",
) -> Invite:
    invites = _read_all()
    invite = Invite(
        # 32 bytes of CSPRNG. Opaque and unguessable: an invitation link is the
        # only credential a candidate has, so a token someone could enumerate is
        # a way into another person's interview.
        token=secrets.token_urlsafe(32),
        candidate_name=candidate_name.strip(),
        candidate_id="cand_" + secrets.token_hex(4),
        role=role,
        interview_id=interview_id,
        interview_version=interview_version,
        expires_at=time.time() + ttl_days * 86400,
        status="active",
        channel=channel if channel in CHANNELS else "link",
        recipient=recipient.strip(),
        open_link=open_link,
        note=note.strip(),
    )
    invites[invite.token] = invite
    _write_all(invites)
    return invite


def get(token: str) -> Invite | None:
    return _read_all().get(token)


@_serialised
def update(invite: Invite) -> None:
    invites = _read_all()
    invites[invite.token] = invite
    _write_all(invites)


def list_all() -> list[dict[str, Any]]:
    return [asdict(v) for v in _read_all().values()]


def for_interview(interview_id: str) -> list[Invite]:
    return sorted(
        (i for i in _read_all().values() if i.interview_id == interview_id),
        key=lambda i: i.created_at,
        reverse=True,
    )


def open_link_for(interview_id: str, version: int) -> Invite | None:
    """The reusable link for one published version, if one is enabled."""
    return next(
        (
            i for i in _read_all().values()
            if i.interview_id == interview_id
            and i.interview_version == version
            and i.open_link
            and i.effective_status not in ("revoked", "expired")
        ),
        None,
    )


@_serialised
def mark_opened(invite: Invite) -> Invite:
    """The candidate loaded the welcome screen."""
    if invite.opened_at is None:
        invite.opened_at = time.time()
    if invite.effective_status in ("created", "active"):
        invite.status = "opened"
        update(invite)
    return invite


@_serialised
def revoke(token: str) -> Invite | None:
    """Withdraw an invitation. Sessions already under way are NOT ended —
    pulling the interview out from under someone mid-answer would be worse than
    letting it finish."""
    invite = get(token)
    if invite is None:
        return None
    invite.status = "revoked"
    invite.revoked_at = time.time()
    update(invite)
    return invite


@_serialised
def ensure_demo_invite(interview_id: str = "iv_default", interview_version: int = 0) -> Invite:
    """A ready-to-open link on first boot, so the prototype is never a dead end."""
    for invite in _read_all().values():
        if invite.candidate_id == "cand_demo":
            changed = False
            if not invite.interview_id:
                invite.interview_id = interview_id
                changed = True
            if not invite.interview_version and interview_version:
                invite.interview_version = interview_version
                changed = True
            if changed:
                update(invite)
            return invite
    invites = _read_all()
    invite = Invite(
        token="demo",
        candidate_name="Priya Sharma",
        candidate_id="cand_demo",
        role=config.ROLE,
        interview_id=interview_id,
        interview_version=interview_version,
        expires_at=None,
        status="active",
    )
    invites[invite.token] = invite
    _write_all(invites)
    return invite
