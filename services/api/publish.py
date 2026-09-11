"""Publishing an assessment, and inviting candidates to it.

The one-way door. Everything before it is a draft a recruiter can change;
everything after it is fixed, because a candidate is judged against the
interview as it was when they were invited.

Two things this module is careful about:

  * **Publishing is idempotent.** A double-click, a browser retry and a network
    retry are the same publish. Three versions of one assessment would make
    "which one did this candidate sit?" unanswerable for no reason.
  * **Nothing half-published exists.** Validation runs first and refuses the
    whole thing; the version row is written atomically. There is no state where
    an interview is partly published.

Email delivery is NOT configured in this build. Invitations are created
server-side and their links are shown to the recruiter to send themselves. An
address given for an email invitation is recorded, and the response says plainly
that nothing was sent.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.assessment import blueprint as bp
from services.assessment.blueprint import Blueprint
from services.assessment.publication import (
    snapshot,
    summarise,
    validate_for_publish,
)
from services.data import audit, interviews, invites, versions
from services.data.interviews import InterviewConfig

router = APIRouter(tags=["publish"])


class PublishRequest(BaseModel):
    notes: str = ""


class InviteRequest(BaseModel):
    """Email invitations, or a plain link the recruiter sends themselves."""

    candidates: list[str] = Field(default_factory=list)   # names or emails
    channel: str = "link"
    note: str = ""
    ttl_days: int = Field(default=14, ge=1, le=365)


class OpenLinkRequest(BaseModel):
    enabled: bool = True
    ttl_days: int = Field(default=90, ge=1, le=365)


def _load(interview_id: str) -> InterviewConfig:
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    return cfg


def _plan(cfg: InterviewConfig) -> Blueprint | None:
    if cfg.blueprint:
        return Blueprint.from_dict(cfg.blueprint)
    try:
        return bp.build(interviews.build_definition(cfg))
    except Exception:  # noqa: BLE001 — the validator reports it properly
        return None


def _invite_payload(invite: invites.Invite, base: str = "") -> dict[str, Any]:
    return {
        "token": invite.token,
        "link": f"{base}/?invite={invite.token}",
        "candidate_name": invite.candidate_name,
        "recipient": invite.recipient,
        "channel": invite.channel,
        "open_link": invite.open_link,
        "status": invite.effective_status,
        "interview_version": invite.interview_version,
        "created_at": invite.created_at,
        "expires_at": invite.expires_at,
        "opened_at": invite.opened_at,
        "completed_at": invite.completed_at,
        "note": invite.note,
    }


# --------------------------------------------------------------------------- #
#  Publish
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/publish/check")
async def publish_check(interview_id: str) -> dict[str, Any]:
    """What the recruiter sees before pressing Publish.

    The same validator the publish endpoint runs, so the confirmation screen
    cannot say "ready" about something the publish would refuse.
    """
    cfg = _load(interview_id)
    definition = interviews.build_definition(cfg)
    plan = _plan(cfg)
    check = validate_for_publish(definition, plan)
    published = versions.latest_published(interview_id)
    return {
        "interview_id": interview_id,
        "ready": check.ok,
        "check": check.as_dict(),
        # From persisted values, never estimated. A recruiter making something
        # immutable is entitled to the real numbers.
        "summary": summarise(definition, plan),
        "published_version": published.version if published else 0,
        "has_unpublished_changes": bool(
            published and published.checksum != definition.checksum()
        ),
    }


@router.post("/interviews/{interview_id}/publish")
async def publish(interview_id: str, body: PublishRequest) -> dict[str, Any]:
    """Freeze the draft as an immutable version.

    Validate, snapshot, write, audit. If validation fails nothing is published;
    if an identical version already exists it is returned rather than duplicated.
    """
    cfg = _load(interview_id)
    definition = interviews.build_definition(cfg)
    plan = _plan(cfg)

    audit.product(
        audit.INTERVIEW_PUBLISH_STARTED, actor="recruiter",
        subject_type="interview", subject_id=interview_id,
        questions=len(definition.questions), skills=len(definition.skills),
    )

    check = validate_for_publish(definition, plan)
    if not check.ok:
        audit.product(
            audit.INTERVIEW_PUBLISH_FAILED, actor="recruiter",
            subject_type="interview", subject_id=interview_id,
            problems=[p["area"] for p in check.problems], count=len(check.problems),
        )
        raise HTTPException(
            422,
            detail={
                "message": "This interview can't be published yet.",
                "check": check.as_dict(),
            },
        )

    before = versions.latest_published(interview_id)
    # `validate=False`: `validate_for_publish` above is a strict superset of the
    # definition's own check, and has already run.
    row = versions.publish(
        interview_id, snapshot(definition),
        published_by="recruiter", notes=body.notes, validate=False,
    )
    created = before is None or before.version != row.version

    cfg.status = "published"
    cfg.published_version = row.version
    interviews.save(cfg)

    if created:
        audit.product(
            audit.INTERVIEW_PUBLISHED, actor="recruiter",
            subject_type="interview", subject_id=interview_id,
            version=row.version, checksum=row.checksum,
            questions=len(definition.questions), outcome="published",
        )

    return {
        "interview_id": interview_id,
        "version": row.version,
        "checksum": row.checksum,
        "published_at": row.published_at,
        # False when an identical version already existed — a retry, not a
        # second publish.
        "created": created,
        "summary": summarise(definition, plan),
    }


@router.get("/interviews/{interview_id}/versions")
async def list_versions(interview_id: str) -> dict[str, Any]:
    """Published history. The draft is not in it — it is not a version yet."""
    _load(interview_id)
    rows = versions.summary(interview_id)
    linked = invites.for_interview(interview_id)
    for row in rows:
        for_version = [i for i in linked if i.interview_version == row["version"]]
        row["invitations"] = len(for_version)
        row["completed"] = sum(
            1 for i in for_version if i.effective_status == "complete"
        )
    return {"interview_id": interview_id, "versions": rows}


# --------------------------------------------------------------------------- #
#  Invitations
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/invitations")
async def list_invitations(interview_id: str, base: str = "") -> dict[str, Any]:
    cfg = _load(interview_id)
    published = versions.latest_published(interview_id)
    rows = invites.for_interview(interview_id)
    return {
        "interview_id": interview_id,
        "published_version": published.version if published else 0,
        "invitations": [_invite_payload(i, base) for i in rows if not i.open_link],
        "open_link": (
            _invite_payload(
                invites.open_link_for(interview_id, published.version), base
            ) if published and invites.open_link_for(interview_id, published.version)
            else None
        ),
        # Stated rather than implied. Nothing in this build sends an email.
        "email_delivery_configured": False,
        "max_emails_per_batch": invites.MAX_EMAILS_PER_BATCH,
    }


@router.post("/interviews/{interview_id}/invitations", status_code=201)
async def create_invitations(
    interview_id: str, body: InviteRequest, base: str = ""
) -> dict[str, Any]:
    """Mint invitations against the CURRENT published version.

    The version is captured now, not when the candidate opens the link —
    otherwise publishing v2 on Tuesday changes the interview for someone invited
    on Monday.
    """
    cfg = _load(interview_id)
    published = versions.latest_published(interview_id)
    if published is None:
        raise HTTPException(
            409,
            detail={"message": (
                "Publish this interview before inviting anyone. A candidate can only "
                "sit a published version."
            )},
        )

    names = [c.strip() for c in body.candidates if c.strip()]
    if not names:
        raise HTTPException(
            422, detail={"errors": {"candidates": "Add at least one candidate."}}
        )
    if len(names) > invites.MAX_EMAILS_PER_BATCH:
        raise HTTPException(
            422,
            detail={"errors": {"candidates": (
                f"Up to {invites.MAX_EMAILS_PER_BATCH} at a time."
            )}},
        )

    created: list[invites.Invite] = []
    for entry in names:
        looks_like_email = "@" in entry
        invite = invites.create(
            entry.split("@")[0] if looks_like_email else entry,
            cfg.role, cfg.id, body.ttl_days,
            interview_version=published.version,
            channel="email" if looks_like_email else "link",
            recipient=entry if looks_like_email else "",
            note=body.note,
        )
        created.append(invite)
        audit.product(
            audit.INVITATION_CREATED, actor="recruiter",
            subject_type="invitation",
            # The token is a credential. Only its prefix goes on the trail.
            subject_id=f"{invite.token[:8]}…",
            interview_id=cfg.id, version=published.version, channel=invite.channel,
        )

    return {
        "created": [_invite_payload(i, base) for i in created],
        "email_delivery_configured": False,
        "message": (
            "Invitations created. Email delivery isn't configured in this build — "
            "copy the links and send them yourself."
        ),
    }


@router.post("/interviews/{interview_id}/invitations/open-link")
async def set_open_link(
    interview_id: str, body: OpenLinkRequest, base: str = ""
) -> dict[str, Any]:
    """Enable or withdraw a reusable link for the published version.

    Bound to a specific version, never to "whatever is published now" — an open
    link that silently follows the latest publish would let two candidates
    clicking the same URL a week apart sit different interviews.
    """
    cfg = _load(interview_id)
    published = versions.latest_published(interview_id)
    if published is None:
        raise HTTPException(
            409, detail={"message": "Publish the interview before enabling a link."}
        )

    existing = invites.open_link_for(interview_id, published.version)

    if not body.enabled:
        if existing:
            invites.revoke(existing.token)
            audit.product(
                audit.INVITATION_REVOKED, actor="recruiter",
                subject_type="invitation", subject_id=f"{existing.token[:8]}…",
                interview_id=cfg.id, version=published.version, open_link=True,
            )
        return {"open_link": None}

    if existing:
        return {"open_link": _invite_payload(existing, base)}

    invite = invites.create(
        "Open link", cfg.role, cfg.id, body.ttl_days,
        interview_version=published.version, channel="link", open_link=True,
    )
    audit.product(
        audit.INVITATION_CREATED, actor="recruiter", subject_type="invitation",
        subject_id=f"{invite.token[:8]}…", interview_id=cfg.id,
        version=published.version, open_link=True,
    )
    return {"open_link": _invite_payload(invite, base)}


@router.delete("/interviews/{interview_id}/invitations/{token}")
async def revoke_invitation(interview_id: str, token: str) -> dict[str, Any]:
    """Withdraw one invitation.

    A session already under way is not ended: pulling the interview out from
    under someone mid-answer is worse than letting it finish.
    """
    _load(interview_id)
    invite = invites.get(token)
    if invite is None or invite.interview_id != interview_id:
        raise HTTPException(404, "No such invitation.")
    invites.revoke(token)
    audit.product(
        audit.INVITATION_REVOKED, actor="recruiter", subject_type="invitation",
        subject_id=f"{token[:8]}…", interview_id=interview_id,
        version=invite.interview_version,
    )
    return {"token": token, "status": "revoked"}
