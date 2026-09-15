"""Interview versions — the immutability guarantee, implemented.

The rule (§8, rule 9) in one sentence: **a candidate is judged against the
interview as it was when they were invited, whatever the recruiter has done to
it since.**

    Interview "iv_ab12"
      ├── v1  published 3 Mar   ← Candidate A sat this
      └── v2  published 9 Mar   ← Candidate B sat this

Candidate A must never receive v2's questions, and their answers must never be
re-scored against v2's criteria. Without that, two candidates who "took the same
interview" took different ones, and neither the comparison screen nor the audit
trail means anything.

How it holds:

  * `publish()` freezes an `InterviewDefinition` into a new numbered version and
    stores it whole, with a content checksum. Nothing rewrites a published row.
  * An `Invitation` records the version number at the moment it is minted.
  * A `SessionState` records the version it started on, and the orchestrator
    resolves its questions from that version — never from the live draft.

Publishing an unchanged definition returns the existing version rather than
minting a duplicate: a recruiter pressing Publish twice has not created a second
interview, and version numbers that move for no reason make the history
unreadable.
"""
from __future__ import annotations

import json
import time
from typing import Any

from packages.types import InterviewDefinition, InterviewVersion
from services import config
from services.data import audit

_PATH = config.DATA_DIR / "interview_versions.json"


def _key(interview_id: str, version: int) -> str:
    return f"{interview_id}@v{version}"


def _read_all() -> dict[str, InterviewVersion]:
    if not _PATH.exists():
        return {}
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    out: dict[str, InterviewVersion] = {}
    for k, v in raw.items():
        known = set(InterviewVersion.__dataclass_fields__)
        out[k] = InterviewVersion(**{kk: vv for kk, vv in v.items() if kk in known})
    return out


def _write_all(rows: dict[str, InterviewVersion]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        k: {f: getattr(v, f) for f in InterviewVersion.__dataclass_fields__}
        for k, v in rows.items()
    }
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_PATH)


# --------------------------------------------------------------------------- #
#  Reading
# --------------------------------------------------------------------------- #
def get(interview_id: str, version: int) -> InterviewVersion | None:
    return _read_all().get(_key(interview_id, version))


def list_for(interview_id: str, include_draft: bool = False) -> list[InterviewVersion]:
    rows = [
        v for v in _read_all().values()
        if v.interview_id == interview_id
        and (include_draft or v.version != DRAFT_VERSION)
    ]
    return sorted(rows, key=lambda v: v.version)


def latest_published(interview_id: str) -> InterviewVersion | None:
    published = [v for v in list_for(interview_id) if v.status == "published"]
    return published[-1] if published else None


def definition_for(interview_id: str, version: int) -> InterviewDefinition | None:
    """The exact contract a session must run under. Never the live draft."""
    row = get(interview_id, version)
    return InterviewDefinition.from_dict(row.definition) if row else None


def next_version(interview_id: str) -> int:
    rows = list_for(interview_id)
    return (rows[-1].version + 1) if rows else 1


# --------------------------------------------------------------------------- #
#  Writing
# --------------------------------------------------------------------------- #
def publish(
    interview_id: str,
    definition: InterviewDefinition,
    *,
    published_by: str = "recruiter",
    notes: str = "",
    validate: bool = True,
) -> InterviewVersion:
    """Freeze `definition` as the next published version of `interview_id`.

    **Idempotent.** Publishing a definition identical to the latest published
    version returns that version rather than minting another. A double-click, a
    browser retry and a network retry are all the same publish, and three
    versions of one assessment would make "which one did this candidate sit?"
    unanswerable for no reason.

    **Atomic.** The row is written through a temp file and renamed, so a crash
    partway through leaves either the old history or the new one — never a
    half-written version file that no candidate could be resolved against.

    `validate=False` is for callers that have already run the fuller
    `publication.validate_for_publish`, which is a superset of this check.
    """
    if validate:
        definition.require_valid()

    latest = latest_published(interview_id)
    # Compared on the CONTENT checksum, which excludes the version number — so
    # "the same assessment" is decided by what it assesses, not by what it is
    # numbered.
    if latest and latest.checksum == definition.checksum():
        return latest

    number = next_version(interview_id)
    definition.interview_id = interview_id
    definition.version = number
    row = InterviewVersion(
        interview_id=interview_id,
        version=number,
        definition=definition.to_dict(),
        status="published",
        checksum=definition.checksum(),
        published_at=time.time(),
        published_by=published_by,
        notes=notes,
    )
    rows = _read_all()
    # Never overwrite an existing published version. If this key is already
    # taken, two publishes raced and the other one won; the caller gets that
    # version rather than clobbering it.
    existing = rows.get(_key(interview_id, number))
    if existing is not None and existing.status == "published":
        return existing
    rows[_key(interview_id, number)] = row
    _write_all(rows)

    audit.product(
        audit.INTERVIEW_PUBLISHED,
        actor=published_by,
        subject_type="interview",
        subject_id=interview_id,
        version=number,
        checksum=row.checksum,
        questions=len(definition.questions),
        skills=len(definition.skills),
        notes=notes,
    )
    return row


#: A draft always sits at this number: version 0 is "the thing being worked on",
#: and published versions start at 1. Keeping the draft out of the numbered
#: sequence means editing a draft never advances a number that candidates are
#: pinned to, and there is never a v3 draft that later becomes a different v3.
DRAFT_VERSION = 0


def save_draft(
    interview_id: str,
    definition: InterviewDefinition,
    *,
    notes: str = "",
) -> InterviewVersion:
    """Store (or replace) the working draft. Deliberately does NOT validate.

    A draft is allowed to be incomplete — that is what makes it a draft. An
    interview designed from a job description has `questions = []` until the
    Question Generator runs, and refusing to save it would mean a recruiter
    could not leave the room between two phases of work.

    `publish()` is where `require_valid()` bites, and it still does.
    """
    definition.interview_id = interview_id
    definition.version = DRAFT_VERSION
    row = InterviewVersion(
        interview_id=interview_id,
        version=DRAFT_VERSION,
        definition=definition.to_dict(),
        status="draft",
        checksum=definition.checksum(),
        published_at=None,
        published_by="",
        notes=notes,
    )
    rows = _read_all()
    rows[_key(interview_id, DRAFT_VERSION)] = row
    _write_all(rows)
    return row


def get_draft(interview_id: str) -> InterviewVersion | None:
    return get(interview_id, DRAFT_VERSION)


def draft_definition(interview_id: str) -> InterviewDefinition | None:
    row = get_draft(interview_id)
    return InterviewDefinition.from_dict(row.definition) if row else None


def summary(interview_id: str) -> list[dict[str, Any]]:
    """Version history for the recruiter's review screen."""
    return [
        {
            "version": v.version,
            "status": v.status,
            "checksum": v.checksum,
            "published_at": v.published_at,
            "published_by": v.published_by,
            "notes": v.notes,
            "questions": len(v.definition.get("questions") or []),
            "skills": len(v.definition.get("skills") or []),
        }
        for v in list_for(interview_id)
    ]
