"""The pilot: which runs exist, and what a human thought of each result.

Two small file-backed stores, deliberately kept apart from everything they
describe:

    runs.json      one row per pilot run — a label, a start, a stop
    reviews/       one file per (evaluation, reviewer) — agree / disagree / why

Neither is an experiment platform. A pilot run is an ID and a window in time,
so a session, an evaluation, a cost and a latency can all be attributed to the
same batch and compared against the next one. Everything else the pilot needs to
know is already written down somewhere else — the version, the snapshot, the
model, the score — and copying it here would create a second source of truth
that could disagree with the first.

**Reviews never touch the assessment.** A reviewer records agreement,
disagreement and a category; nothing here can change a score, a recommendation
or a piece of evidence, and no code path reads a review back into the evaluator.
That separation is the point: the pilot is collecting calibration data, not
letting one recruiter's opinion retune the product (§13).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from services import config

#: What a reviewer can say about a completed assessment.
VERDICTS = ("agree", "disagree", "needs_review")

#: Why they disagreed. A closed list, because free text cannot be counted and a
#: category nobody can count is a category that never becomes a fix.
DISAGREEMENT_REASONS = (
    "wrong_evidence",
    "wrong_skill",
    "wrong_score",
    "wrong_recommendation",
    "insufficient_coverage",
    "other",
)


# --------------------------------------------------------------------------- #
#  Runs
# --------------------------------------------------------------------------- #
@dataclass
class PilotRun:
    pilot_run_id: str
    label: str = ""
    notes: str = ""
    #: What the run was held against. Recorded at creation because they are the
    #: two things that make two runs incomparable if they change underneath.
    engine_version: str = ""
    configured_model: str = ""
    started_at: float = field(default_factory=time.time)
    stopped_at: float | None = None
    created_by: str = "recruiter"
    #: Which organization's pilot this is. A run is a recruiter-facing object
    #: with a label and notes in it, and "one run open at a time" is a fact
    #: about one organization's pilot, not about the deployment. Empty on rows
    #: that predate tenancy; the API adopts those into the default organization
    #: at startup, the same way untenanted interviews are adopted.
    organization_id: str = ""

    @property
    def active(self) -> bool:
        return self.stopped_at is None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "active": self.active}


def _path():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / "pilot_runs.json"


def _read_all() -> dict[str, PilotRun]:
    path = _path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = set(PilotRun.__dataclass_fields__)
    return {
        k: PilotRun(**{kk: vv for kk, vv in v.items() if kk in known})
        for k, v in raw.items()
    }


def _write_all(rows: dict[str, PilotRun]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({k: asdict(v) for k, v in rows.items()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def start_run(
    label: str = "",
    *,
    notes: str = "",
    engine_version: str = "",
    configured_model: str = "",
    created_by: str = "recruiter",
    organization_id: str = "",
) -> PilotRun:
    """Open a run, stopping whichever one this organization had open.

    One at a time on purpose: two open runs would mean a session could be
    attributed to either, and "which batch was this?" is the only question the
    id exists to answer. Per organization, because one tenant opening a pilot
    must not close another tenant's.
    """
    rows = _read_all()
    now = time.time()
    for row in rows.values():
        if row.stopped_at is None and row.organization_id == organization_id:
            row.stopped_at = now
    run = PilotRun(
        pilot_run_id="pilot_" + uuid.uuid4().hex[:10],
        label=label.strip(),
        notes=notes.strip(),
        engine_version=engine_version,
        configured_model=configured_model,
        created_by=created_by,
        organization_id=organization_id,
    )
    rows[run.pilot_run_id] = run
    _write_all(rows)
    return run


def stop_run(pilot_run_id: str, organization_id: str | None = None) -> PilotRun | None:
    """Close a run. None when there is no such run *for this caller*.

    `organization_id=None` means "no tenant check", for the tools and tests that
    run outside a request. The API always passes one, so another organization's
    run is indistinguishable from one that does not exist.
    """
    rows = _read_all()
    run = rows.get(pilot_run_id)
    if run is None:
        return None
    if organization_id is not None and run.organization_id != organization_id:
        return None
    if run.stopped_at is None:
        run.stopped_at = time.time()
        _write_all(rows)
    return run


def adopt_untenanted_runs(organization_id: str) -> int:
    """Give pre-tenancy runs an owner, once, idempotently.

    Without this a pilot's history becomes invisible the moment runs are scoped
    — and invisible history is worse than history one organization can see.
    """
    rows = _read_all()
    adopted = 0
    for row in rows.values():
        if not row.organization_id:
            row.organization_id = organization_id
            adopted += 1
    if adopted:
        _write_all(rows)
    return adopted


def get_run(pilot_run_id: str) -> PilotRun | None:
    return _read_all().get(pilot_run_id)


def list_runs(organization_id: str | None = None) -> list[PilotRun]:
    """Every run, oldest first — or every run one organization can see.

    `None` means unscoped, for the offline tools. The API always names an
    organization: a run carries a label and notes a recruiter wrote, so a list
    of every tenant's runs is a leak of what other companies are hiring for.
    """
    rows = sorted(_read_all().values(), key=lambda r: r.started_at)
    if organization_id is None:
        return rows
    return [r for r in rows if r.organization_id == organization_id]


def active_run(organization_id: str | None = None) -> PilotRun | None:
    """The run new sessions are stamped with, if there is one.

    `TARA_PILOT_RUN` overrides it, so a scripted batch can name its own run
    without a recruiter opening one in the console first.
    """
    forced = config.PILOT_RUN_ID
    if forced:
        return get_run(forced) or PilotRun(pilot_run_id=forced, label="from TARA_PILOT_RUN")
    return next((r for r in reversed(list_runs(organization_id)) if r.active), None)


def active_run_id(organization_id: str | None = None) -> str:
    run = active_run(organization_id)
    return run.pilot_run_id if run else ""


# --------------------------------------------------------------------------- #
#  Reviews
# --------------------------------------------------------------------------- #
@dataclass
class Review:
    """One human's read of one completed assessment.

    Keyed by evaluation rather than by session: a re-evaluation is a different
    document and deserves its own review, and pinning the review to the exact
    evaluation is what makes "the reviewer disagreed with THIS result" a fact
    rather than an impression.
    """

    evaluation_id: str
    session_id: str
    reviewer: str
    verdict: str
    #: Only meaningful when the verdict is `disagree`. Several may apply.
    reasons: list[str] = field(default_factory=list)
    #: What the reviewer would have said instead. Recorded as their judgement,
    #: never written back into the evaluation.
    recommendation: str = ""
    #: A sentence for the calibration set. Not shown to candidates, not read by
    #: the evaluator, and never a score.
    note: str = ""
    #: What the assessment said at the moment it was reviewed, so a later
    #: re-evaluation cannot silently change what the human was looking at.
    ai_recommendation: str = ""
    ai_total_score: int = 0
    ai_percentage: float = 0.0
    ai_coverage_percentage: float = 0.0
    pilot_run_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReviewError(ValueError):
    """A review that would not mean anything if it were stored."""


def _reviews_dir():
    path = config.DATA_DIR / "pilot_reviews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _review_path(evaluation_id: str, reviewer: str):
    safe = "".join(c for c in reviewer.lower() if c.isalnum() or c in "-_") or "anonymous"
    return _reviews_dir() / f"{evaluation_id}__{safe}.json"


def save_review(review: Review) -> Review:
    if review.verdict not in VERDICTS:
        raise ReviewError(f"verdict must be one of {', '.join(VERDICTS)}")
    bad = [r for r in review.reasons if r not in DISAGREEMENT_REASONS]
    if bad:
        raise ReviewError(f"unknown disagreement reason(s): {', '.join(bad)}")
    if review.verdict == "disagree" and not review.reasons:
        # A disagreement with no category is an opinion nobody can act on.
        raise ReviewError("a disagreement needs at least one reason")
    existing = get_review(review.evaluation_id, review.reviewer)
    if existing is not None:
        review.created_at = existing.created_at
    review.updated_at = time.time()
    path = _review_path(review.evaluation_id, review.reviewer)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(review.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return review


def get_review(evaluation_id: str, reviewer: str) -> Review | None:
    path = _review_path(evaluation_id, reviewer)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    known = set(Review.__dataclass_fields__)
    return Review(**{k: v for k, v in raw.items() if k in known})


def delete_reviews(evaluation_id: str) -> int:
    """Remove every review of one evaluation. Idempotent.

    A review is a recruiter's note, not candidate content — but it is *about*
    one candidate's assessment, and it carries the score that was on screen when
    the reviewer wrote it (`ai_total_score`, `ai_recommendation`). Once the
    assessment is erased, a note recording what it said is a surviving fragment
    of it.
    """
    removed = 0
    for path in _reviews_dir().glob(f"{evaluation_id}__*.json"):
        path.unlink()
        removed += 1
    return removed


def reviews_for_session(session_id: str) -> list[Review]:
    """Every review of every evaluation of one session.

    Keyed by evaluation on disk, so this reads the files rather than deriving
    from the evaluation records — which, by the time erasure verifies, are
    already gone.
    """
    return [r for r in list_reviews() if r.session_id == session_id]


def list_reviews(evaluation_id: str = "") -> list[Review]:
    out: list[Review] = []
    known = set(Review.__dataclass_fields__)
    for path in sorted(_reviews_dir().glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue  # a torn row never hides the rest
        row = Review(**{k: v for k, v in raw.items() if k in known})
        if not evaluation_id or row.evaluation_id == evaluation_id:
            out.append(row)
    return sorted(out, key=lambda r: r.created_at)
