"""The assessment engine — evidence in, a level and a confidence out.

A separate unit from the orchestrator on purpose. The orchestrator decides
*flow* and must never be influenced by how well someone is doing; this decides
*level* and must never be able to change what gets asked. They share only the
recorded session.

It runs off the interview path entirely — nothing here is in the latency budget
of a live turn, so it can be re-run against a finished session as often as you
like and always produces the same answer.

Three things it will not do, and the reasons matter:

  * It never scores affect, accent, fluency, or answer length. It counts which
    of an item's authored evidence cues the answer actually demonstrated. That
    is the only input.
  * It never decides. Every output carries a confidence, and anything below the
    threshold is flagged for a human rather than quietly averaged in.
  * It never penalises an unanswered item. A question the candidate never heard,
    or was released from, is excluded from the denominator — scoring someone
    zero for a question that was never put to them is the single most unfair
    thing an interview system can do.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from services import config
from services.data import interviews
from services.data.interviews import InterviewConfig
from services.ai.workloads.interview_designer import PRIORITY_RANK, PROFICIENCY_LABELS
from services.orchestrator.pool import Pool
from services.orchestrator.state import SessionState

# Demonstrated level runs on the same 0-4 scale as `proficiency_target`, so a
# level can be compared directly against the bar the role asked for.
LEVEL_LABELS = PROFICIENCY_LABELS

# Coverage of an item's authored cues → demonstrated level.
# Deliberately generous at the bottom: answering at all is worth more than
# silence, and a candidate who covers most of what a question was looking for
# has demonstrated the competency even if they missed a cue.
_COVERAGE_BANDS: list[tuple[float, int]] = [
    (0.85, 4),   # covered essentially everything the question looked for
    (0.60, 3),   # covered most of it
    (0.35, 2),   # covered some of it
    (0.01, 1),   # engaged with it but showed little of what was wanted
    (0.00, 0),   # answered, but none of the cues
]

# Below this, a score is flagged and excluded from any auto-advance.
CONFIDENCE_THRESHOLD = float(config.__dict__.get("CONFIDENCE_THRESHOLD", 0.55))

# Recommendation bands. Fixed, not per-interview: a recruiter who can move the
# hire bar for their own req breaks comparability across candidates, which is
# the whole reason for running a structured interview.
BANDS = [
    ("strong_hire", "Strong indication", 3.30, 0.80),
    ("hire", "Positive indication", 2.80, 0.60),
    ("borderline", "Mixed", 2.20, 0.00),
    ("no_hire", "Weak indication", 0.00, 0.00),
]


@dataclass
class ItemScore:
    item_id: str
    competency: str
    prompt: str
    answered: bool
    level: int | None  # 0-4, or None when it was never answered
    coverage: float  # share of authored cues evidenced
    evidenced: list[str] = field(default_factory=list)
    not_evidenced: list[str] = field(default_factory=list)
    probes: int = 0
    words: int = 0
    confidence: float = 0.0
    excluded_reason: str = ""


@dataclass
class SkillScore:
    competency_id: str
    name: str
    # The question bank behind it. Two candidates are only comparable on a skill
    # if they answered the same bank, whatever the recruiter named it.
    pool_competency: str
    priority: str
    target: int
    target_label: str
    level: float | None
    level_label: str
    met: bool
    confidence: float
    flagged: bool
    items: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class SessionScore:
    session_id: str
    candidate_name: str
    interview_id: str
    scored: bool
    composite: float | None  # 0-4, priority-weighted
    percent: int | None  # composite normalised to 0-100, for the dashboard
    band: str
    band_label: str
    met_ratio: float
    confidence: float
    flagged_skills: list[str]
    skills: list[SkillScore]
    items: list[ItemScore]
    excluded_items: int
    note: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["level_labels"] = LEVEL_LABELS
        return d


# --------------------------------------------------------------------------- #
#  Item level
# --------------------------------------------------------------------------- #
def _level_from_coverage(coverage: float) -> int:
    for threshold, level in _COVERAGE_BANDS:
        if coverage >= threshold:
            return level
    return 0


def _item_confidence(coverage: float, words: int, cue_count: int) -> float:
    """How much to trust this item's level.

    Three things erode it: a very short answer (little to read), an item with
    few authored cues (coarse resolution), and a coverage figure sitting right
    on a band boundary (a single cue either way would move the level).
    """
    if cue_count == 0:
        return 0.0
    conf = 1.0
    if words < 25:
        conf -= 0.35
    elif words < 50:
        conf -= 0.15
    if cue_count < 3:
        conf -= 0.20
    # Distance from the nearest band edge, normalised — near an edge is fragile.
    edges = [t for t, _ in _COVERAGE_BANDS if 0 < t < 1]
    margin = min(abs(coverage - e) for e in edges) if edges else 0.5
    if margin < 0.08:
        conf -= 0.20
    return max(0.0, min(1.0, conf))


def score_items(state: SessionState, pool: Pool) -> list[ItemScore]:
    out: list[ItemScore] = []
    for item_id in state.asked_item_ids:
        record = state.records.get(item_id)
        if record is None:
            continue
        item = pool.item(item_id)
        cues = item.looking_for or []
        words = sum(len(a.split()) for a in record.answers)

        if not record.answers:
            out.append(
                ItemScore(
                    item_id=item_id,
                    competency=item.competency,
                    prompt=item.prompt,
                    answered=False,
                    level=None,
                    coverage=0.0,
                    not_evidenced=list(cues),
                    probes=len(record.probes_asked),
                    words=0,
                    confidence=0.0,
                    excluded_reason="no answer recorded — excluded, not counted against the candidate",
                )
            )
            continue

        evidenced = [c for c in record.covered if c in cues]
        coverage = (len(evidenced) / len(cues)) if cues else 0.0
        out.append(
            ItemScore(
                item_id=item_id,
                competency=item.competency,
                prompt=item.prompt,
                answered=True,
                level=_level_from_coverage(coverage),
                coverage=round(coverage, 3),
                evidenced=evidenced,
                not_evidenced=[c for c in cues if c not in evidenced],
                probes=len(record.probes_asked),
                words=words,
                confidence=round(_item_confidence(coverage, words, len(cues)), 2),
            )
        )
    return out


# --------------------------------------------------------------------------- #
#  Skill level
# --------------------------------------------------------------------------- #
def score_skills(cfg: InterviewConfig | None, items: list[ItemScore], pool: Pool) -> list[SkillScore]:
    """Roll item levels up to the skills the recruiter actually chose.

    A skill's level is the mean of the items in the question bank behind it.
    Several skills can share a bank, in which case they share a level — which is
    honest: the same questions produced the same evidence.
    """
    scored = [i for i in items if i.answered and i.level is not None]
    by_comp: dict[str, list[ItemScore]] = {}
    for i in scored:
        by_comp.setdefault(i.competency, []).append(i)

    skills = cfg.evaluated_skills() if cfg else []
    if not skills:
        # No configured interview — fall back to the pool's own competencies so
        # a legacy session still produces something readable.
        return [
            SkillScore(
                competency_id=c.id,
                name=c.label,
                pool_competency=c.id,
                priority="medium",
                target=3,
                target_label=LEVEL_LABELS[3],
                level=_mean([i.level for i in by_comp.get(c.id, [])]),
                level_label=_label(_mean([i.level for i in by_comp.get(c.id, [])])),
                met=(_mean([i.level for i in by_comp.get(c.id, [])]) or 0) >= 3,
                confidence=round(_mean([i.confidence for i in by_comp.get(c.id, [])]) or 0, 2),
                flagged=False,
                items=[i.item_id for i in by_comp.get(c.id, [])],
            )
            for c in pool.competencies
            if by_comp.get(c.id)
        ]

    out: list[SkillScore] = []
    for s in skills:
        bucket = by_comp.get(s.pool_competency, []) if s.pool_competency else []
        level = _mean([i.level for i in bucket])
        confidence = round(_mean([i.confidence for i in bucket]) or 0.0, 2)
        # One item is one data point. A skill the role depends on, judged on a
        # single answer, is worth saying out loud rather than burying.
        if len(bucket) == 1:
            confidence = round(confidence * 0.8, 2)

        note = ""
        if not s.pool_competency:
            note = "No questions behind this skill — it wasn't assessed."
        elif not bucket:
            note = "The question budget ran out before this skill came up."
        elif len(bucket) == 1:
            note = "Judged on one answer."

        out.append(
            SkillScore(
                competency_id=s.competency_id,
                name=s.name,
                pool_competency=s.pool_competency,
                priority=s.priority,
                target=s.proficiency_target,
                target_label=LEVEL_LABELS[max(0, min(4, s.proficiency_target))],
                level=None if level is None else round(level, 2),
                level_label=_label(level),
                met=bool(level is not None and level >= s.proficiency_target),
                confidence=confidence,
                flagged=bool(bucket) and confidence < CONFIDENCE_THRESHOLD,
                items=[i.item_id for i in bucket],
                note=note,
            )
        )
    return out


def _mean(values: list[float | int | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _label(level: float | None) -> str:
    if level is None:
        return "Not assessed"
    return LEVEL_LABELS[max(0, min(4, round(level)))]


# --------------------------------------------------------------------------- #
#  Session
# --------------------------------------------------------------------------- #
def score_session(state: SessionState, pool: Pool) -> SessionScore:
    cfg = interviews.get(state.interview_id) if state.interview_id else None
    items = score_items(state, pool)
    skills = score_skills(cfg, items, pool)

    assessed = [s for s in skills if s.level is not None]
    excluded = sum(1 for i in items if not i.answered)

    if not assessed:
        return SessionScore(
            session_id=state.session_id,
            candidate_name=state.candidate_name,
            interview_id=state.interview_id,
            scored=False,
            composite=None,
            percent=None,
            band="not_scored",
            band_label="Not enough evidence",
            met_ratio=0.0,
            confidence=0.0,
            flagged_skills=[],
            skills=skills,
            items=items,
            excluded_items=excluded,
            note="No skill produced a level — the interview was too short, or nothing was answered.",
        )

    # Priority-weighted, because a recruiter said which skills the role depends
    # on and a flat mean would throw that away.
    weights = [PRIORITY_RANK.get(s.priority, 2) for s in assessed]
    composite = sum(s.level * w for s, w in zip(assessed, weights)) / sum(weights)
    met_ratio = sum(1 for s in assessed if s.met) / len(assessed)
    confidence = sum(s.confidence for s in assessed) / len(assessed)

    band, band_label = "no_hire", "Weak indication"
    for key, label, min_composite, min_met in BANDS:
        if composite >= min_composite and met_ratio >= min_met:
            band, band_label = key, label
            break

    flagged = [s.name for s in assessed if s.flagged]
    note = ""
    if confidence < CONFIDENCE_THRESHOLD:
        note = (
            "Overall confidence is low — read the evidence before relying on this. "
            "Short answers or thin question coverage make the level unreliable."
        )
    elif flagged:
        note = f"{len(flagged)} skill{'s' if len(flagged) > 1 else ''} scored with low confidence."

    return SessionScore(
        session_id=state.session_id,
        candidate_name=state.candidate_name,
        interview_id=state.interview_id,
        scored=True,
        composite=round(composite, 2),
        percent=round(composite / 4 * 100),
        band=band,
        band_label=band_label,
        met_ratio=round(met_ratio, 2),
        confidence=round(confidence, 2),
        flagged_skills=flagged,
        skills=skills,
        items=items,
        excluded_items=excluded,
        note=note,
    )
