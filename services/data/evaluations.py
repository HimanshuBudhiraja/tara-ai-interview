"""Evaluation records — one durable row per (session, version, engine).

An evaluation is not a view over a session; it is a document, produced at a
moment, by a named methodology, from a frozen snapshot. Recomputing it on
request would mean a recruiter and the reviewer who read the same page a month
apart could see different scores with nothing to explain the difference. So it
is written down.

    evaluation_id      what to link to
    session_id         who
    interview_version  the immutable contract they sat
    engine_version     the methodology that produced it
    snapshot_checksum  what it read, hashed
    status             pending → running → completed | failed

One file per evaluation, written atomically, following the session store rather
than the single-JSON repositories: these carry a whole snapshot each, and a
per-row file means a large one never rewrites its neighbours.

Nothing is ever mutated into a different result. A re-evaluation is a NEW record
that supersedes the old one, and the old one stays on disk — "what did we
decide, and what did we decide it from" has to survive the decision changing.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from services import config

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STATUSES = (PENDING, RUNNING, COMPLETED, FAILED)

#: Why a run failed. Kept apart because they call for different responses: a
#: model failure is worth retrying, and a validation failure means the output
#: was not safe to keep and retrying it unchanged will fail the same way.
MODEL_FAILURE = "model"
VALIDATION_FAILURE = "validation"
#: The provider, not the model: an exhausted budget, a revoked key, a throttled
#: account, a timeout, a network failure. Separated because the two lead an
#: operator to completely different places — "the model answered badly" is a
#: calibration question, and "the account is out of credit" is a billing one.
PROVIDER_FAILURE = "provider"


@dataclass
class EvaluationRecord:
    evaluation_id: str
    session_id: str
    interview_id: str
    interview_version: int
    engine_version: str
    snapshot_checksum: str
    status: str = PENDING
    #: 1 for the first run of a session, incremented by each explicit re-run.
    attempt: int = 1
    #: True once a newer record has replaced this one as the current answer.
    superseded: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    requested_by: str = "system"
    #: Copied from the session, not re-derived: an evaluation belongs to the
    #: batch the INTERVIEW was part of, however long afterwards it was run.
    pilot_run_id: str = ""
    error: str = ""
    error_kind: str = ""
    #: Where it stopped: evidence_extraction | skill_assessment | integrity_gate
    #: | result_assembly. The error message says what went wrong; this says at
    #: which stage, which is the part you need to know whether to retry it.
    failed_stage: str = ""
    #: The frozen inputs. Present from creation, so a pending evaluation is
    #: already reproducible and a worker needs nothing but this row.
    snapshot: dict[str, Any] = field(default_factory=dict)
    #: The engine's output, in the canonical contract shape.
    result: dict[str, Any] = field(default_factory=dict)
    #: Validated evidence, and what was refused on the way in.
    evidence: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    #: Constraints the engine had to apply to the model's output.
    adjustments: list[str] = field(default_factory=list)
    #: Values code corrected without guessing — currently only a criterion the
    #: model named with a dimension's word. Visible so a rising repair rate is
    #: noticed rather than absorbed.
    repairs: list[dict[str, Any]] = field(default_factory=list)
    #: Provider, model and cost. Never a credential — see `jobs._model_meta`.
    model_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "EvaluationRecord":
        known = set(EvaluationRecord.__dataclass_fields__)
        return EvaluationRecord(**{k: v for k, v in raw.items() if k in known})

    @property
    def is_current(self) -> bool:
        return not self.superseded


def _dir():
    # Read at call time, not import time: tests redirect DATA_DIR, and a path
    # captured at import would write into the real data directory.
    path = config.DATA_DIR / "evaluations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(evaluation_id: str):
    return _dir() / f"{evaluation_id}.json"


def new_id() -> str:
    return "ev_" + uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
#  Writing
# --------------------------------------------------------------------------- #
def save(record: EvaluationRecord) -> EvaluationRecord:
    record.updated_at = time.time()
    path = _path(record.evaluation_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return record


def create(
    *,
    session_id: str,
    interview_id: str,
    interview_version: int,
    engine_version: str,
    snapshot: dict[str, Any],
    snapshot_checksum: str,
    requested_by: str = "system",
    attempt: int = 1,
    pilot_run_id: str = "",
) -> EvaluationRecord:
    return save(EvaluationRecord(
        evaluation_id=new_id(),
        session_id=session_id,
        interview_id=interview_id,
        interview_version=interview_version,
        engine_version=engine_version,
        snapshot_checksum=snapshot_checksum,
        snapshot=snapshot,
        requested_by=requested_by,
        attempt=attempt,
        pilot_run_id=pilot_run_id,
    ))


def supersede(record: EvaluationRecord) -> EvaluationRecord:
    record.superseded = True
    return save(record)


# --------------------------------------------------------------------------- #
#  Reading
# --------------------------------------------------------------------------- #
def get(evaluation_id: str) -> EvaluationRecord | None:
    path = _path(evaluation_id)
    if not path.exists():
        return None
    try:
        return EvaluationRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return None


def delete(evaluation_id: str) -> bool:
    """Remove one evaluation document. Idempotent.

    This is the record that holds the frozen snapshot — a complete second copy
    of the transcript — plus the verbatim evidence quotes and the result. It is
    the file that would be missed by an erasure that only deleted the session.
    """
    path = _path(evaluation_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def list_all() -> list[EvaluationRecord]:
    rows = []
    for path in _dir().glob("ev_*.json"):
        try:
            rows.append(EvaluationRecord.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            ))
        except json.JSONDecodeError:
            continue  # a torn row never hides the rest
    return sorted(rows, key=lambda r: (r.created_at, r.evaluation_id))


def list_for_session(session_id: str) -> list[EvaluationRecord]:
    """Every run for one session, oldest first. History, not just the answer."""
    return [r for r in list_all() if r.session_id == session_id]


def current_for(session_id: str, engine_version: str) -> EvaluationRecord | None:
    """The one record that is this session's evaluation under this engine."""
    rows = [
        r for r in list_for_session(session_id)
        if r.engine_version == engine_version and not r.superseded
    ]
    return rows[-1] if rows else None


def list_pending(limit: int = 50) -> list[EvaluationRecord]:
    return [r for r in list_all() if r.status == PENDING and not r.superseded][:limit]
