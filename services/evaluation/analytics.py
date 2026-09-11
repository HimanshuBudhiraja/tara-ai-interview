"""Aggregate views over finished interviews: results, comparison, fairness.

One module because all three answer the same shape of question — "across these
candidates, what happened?" — and all three read the same two sources: scored
sessions and the append-only audit trail.

Nothing here recomputes a score. It calls `scoring.score_session`, so the number
on the dashboard, the number in the comparison, and the number on the candidate's
own report are the same number by construction rather than by coincidence.
"""
from __future__ import annotations

from typing import Any

from services.data import interviews, invites
from services.data import sessions as store
from services.evaluation import scoring
from services.orchestrator.pool import Pool


def _scored_sessions(
    pool: Pool,
    interview_id: str | None = None,
    session_ids: list[str] | None = None,
) -> list[tuple[dict, Any]]:
    """Every completed candidate, paired with their score.

    `session_ids`, when given, is an allow-list: the caller has already decided
    which sessions the reader is entitled to, and an aggregate must not quietly
    widen that. Passing None keeps the old behaviour for the interview-scoped
    callers, whose ownership was checked on the way in.
    """
    allowed = set(session_ids) if session_ids is not None else None
    out = []
    for row in invites.list_all():
        if interview_id and row.get("interview_id") != interview_id:
            continue
        if not row.get("session_id"):
            continue
        if allowed is not None and row["session_id"] not in allowed:
            continue
        state = store.try_load(row["session_id"])
        if state is None or state.phase != "complete":
            continue
        out.append((row, scoring.score_session(state, pool)))
    return out


# --------------------------------------------------------------------------- #
#  Results dashboard
# --------------------------------------------------------------------------- #
def results(pool: Pool, interview_id: str) -> dict[str, Any]:
    cfg = interviews.get(interview_id)
    linked = [r for r in invites.list_all() if r.get("interview_id") == interview_id]
    pairs = _scored_sessions(pool, interview_id)

    funnel = {
        "invited": len(linked),
        "started": sum(1 for r in linked if r["status"] in ("in_progress", "complete")),
        "completed": sum(1 for r in linked if r["status"] == "complete"),
    }

    scored = [s for _, s in pairs if s.scored]
    bands: dict[str, int] = {}
    for s in scored:
        bands[s.band] = bands.get(s.band, 0) + 1

    # Per-skill averages across everyone who took it. This is the number that
    # tells a recruiter their bar is wrong: if nobody clears a skill, the target
    # is probably miscalibrated rather than the market being empty.
    per_skill: dict[str, dict[str, Any]] = {}
    for s in scored:
        for sk in s.skills:
            if sk.level is None:
                continue
            entry = per_skill.setdefault(
                sk.competency_id,
                {
                    "competency_id": sk.competency_id,
                    "name": sk.name,
                    "priority": sk.priority,
                    "target": sk.target,
                    "levels": [],
                    "met": 0,
                },
            )
            entry["levels"].append(sk.level)
            if sk.met:
                entry["met"] += 1

    skills = []
    for entry in per_skill.values():
        levels = entry.pop("levels")
        skills.append({
            **entry,
            "candidates": len(levels),
            "average": round(sum(levels) / len(levels), 2),
            "met_rate": round(entry["met"] / len(levels), 2),
        })
    skills.sort(key=lambda s: s["average"])

    durations = []
    for row, _ in pairs:
        state = store.try_load(row["session_id"])
        if state and state.completed_at:
            durations.append(state.completed_at - state.created_at)

    return {
        "interview_id": interview_id,
        "title": cfg.title if cfg else interview_id,
        "funnel": funnel,
        "completion_rate": round(funnel["completed"] / funnel["invited"], 2) if funnel["invited"] else 0.0,
        "scored": len(scored),
        "bands": bands,
        "band_order": [b[0] for b in scoring.BANDS],
        "band_labels": {b[0]: b[1] for b in scoring.BANDS},
        "average_composite": (
            round(sum(s.composite for s in scored) / len(scored), 2) if scored else None
        ),
        "flagged": sum(1 for s in scored if s.confidence < scoring.CONFIDENCE_THRESHOLD),
        "median_minutes": (
            round(sorted(durations)[len(durations) // 2] / 60) if durations else None
        ),
        "skills": skills,
        "candidates": [
            {
                "token": row["token"],
                "session_id": row["session_id"],
                "name": row["candidate_name"],
                "composite": s.composite,
                "percent": s.percent,
                "band": s.band,
                "band_label": s.band_label,
                "confidence": s.confidence,
                "met_ratio": s.met_ratio,
                "flagged": s.confidence < scoring.CONFIDENCE_THRESHOLD,
            }
            for row, s in sorted(pairs, key=lambda p: p[1].composite or -1, reverse=True)
        ],
    }


# --------------------------------------------------------------------------- #
#  Comparison
# --------------------------------------------------------------------------- #
def compare(pool: Pool, session_ids: list[str]) -> dict[str, Any]:
    """Candidates side by side, skill by skill.

    Rows are keyed by the QUESTION BANK, not the skill name. Two recruiters can
    call the same competency different things, and two candidates are only
    genuinely comparable on a skill if they answered the same questions.

    Ordered by the recruiter's priority bands rather than by score, so the skills
    the role depends on sit at the top where the comparison is actually made.
    """
    rows = []
    interview_ids: set[str] = set()
    for sid in session_ids[:6]:  # more than six columns stops being comparable
        state = store.try_load(sid)
        if state is None:
            continue
        interview_ids.add(state.interview_id)
        rows.append(scoring.score_session(state, pool))

    if not rows:
        return {"candidates": [], "skills": [], "cross_interview": False, "note": ""}

    cross = len(interview_ids) > 1
    note = ""
    if cross:
        note = (
            "These candidates sat different interviews, so they answered different questions "
            "against different bars. Compare them skill by skill, not on the overall figure."
        )

    order = {"high": 0, "medium": 1, "low": 2}
    banks: dict[str, tuple[str, str, int]] = {}
    for r in rows:
        for sk in r.skills:
            key = sk.pool_competency or sk.competency_id
            if key not in banks:
                banks[key] = (sk.name, sk.priority, sk.target)
    skill_names = [(k, v[0], v[1], v[2]) for k, v in banks.items()]
    skill_names.sort(key=lambda s: (order.get(s[2], 1), s[1]))

    def cell(r, bank: str) -> dict[str, Any]:
        match = next(
            (s for s in r.skills if (s.pool_competency or s.competency_id) == bank), None
        )
        return {
            "session_id": r.session_id,
            "level": match.level if match else None,
            "met": bool(match and match.met),
            "flagged": bool(match and match.flagged),
            "assessed": match is not None,
        }

    return {
        "cross_interview": cross,
        "note": note,
        "candidates": [
            {
                "session_id": r.session_id,
                "name": r.candidate_name,
                "composite": r.composite,
                "percent": r.percent,
                "band": r.band,
                "band_label": r.band_label,
                "confidence": r.confidence,
                "met_ratio": r.met_ratio,
            }
            for r in rows
        ],
        "skills": [
            {
                "competency_id": bank,
                "name": name,
                "priority": priority,
                "target": target,
                "levels": [cell(r, bank) for r in rows],
            }
            for bank, name, priority, target in skill_names
        ],
    }


# --------------------------------------------------------------------------- #
#  Fairness & audit
# --------------------------------------------------------------------------- #
# What the system is structurally incapable of scoring. Stated as guarantees the
# code actually enforces, not as aspirations — each one maps to a mechanism.
EXCLUSIONS = [
    ("Accent and pronunciation", "Scoring reads the transcript's content only; the audio never reaches the assessment engine."),
    ("Emotion and tone", "Affect is read once, to choose an acknowledgement, and is never passed to anything that assigns a level."),
    ("Speech fluency and fillers", "The read prompt forbids penalising grammar, filler words, or transcription errors."),
    ("Answer length", "Level comes from which authored evidence cues an answer covered, not from how much was said."),
    ("Protected characteristics", "No generated question may touch age, family status, religion, ethnicity, origin, immigration status, health, disability, identity, politics, salary history, or criminal record — enforced before the question can be spoken."),
]


def fairness(
    pool: Pool,
    interview_id: str | None = None,
    session_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The bias-audit view: what was excluded, and what the guardrails caught."""
    pairs = _scored_sessions(pool, interview_id, session_ids)

    blocked: list[dict[str, Any]] = []
    generated = fallback = unanswered = 0
    silence_recoveries = 0
    device_help = 0

    for row, _ in pairs:
        for e in store.read_audit(row["session_id"]):
            event = e.get("event")
            if event == "probe_generated":
                generated += 1
                guard = e.get("guardrail") or {}
                if not guard.get("ok", True):
                    blocked.append({
                        "candidate": row["candidate_name"],
                        "session_id": row["session_id"],
                        "probe": e.get("probe", ""),
                        "gate": guard.get("gate", ""),
                        "reason": guard.get("reason", ""),
                        "at": e.get("at"),
                    })
            elif event == "probe_fallback":
                fallback += 1
            elif event == "item_unanswered":
                unanswered += 1
            elif event == "device_help_offered":
                device_help += 1
            elif event == "repeated":
                silence_recoveries += 1

    # Accommodations, so a reviewer can see they were offered and used — the
    # spec's "accommodations built in" is only true if someone can verify it.
    accommodations: dict[str, int] = {}
    for row, _ in pairs:
        state = store.try_load(row["session_id"])
        for key, value in (state.accommodations if state else {}).items():
            if value:
                accommodations[key] = accommodations.get(key, 0) + 1

    scored = [s for _, s in pairs if s.scored]
    return {
        "interview_id": interview_id,
        "sessions_audited": len(pairs),
        "exclusions": [{"factor": f, "mechanism": m} for f, m in EXCLUSIONS],
        "generated_probes": generated,
        "blocked_probes": len(blocked),
        "block_rate": round(len(blocked) / generated, 3) if generated else 0.0,
        "fallback_probes": fallback,
        "blocked": sorted(blocked, key=lambda b: b.get("at") or 0, reverse=True)[:25],
        "unanswered_items": unanswered,
        # How often Tara had to stop and talk someone through a microphone
        # problem. Was `channel_fallbacks_offered`, when the remedy on offer
        # was a typed answer box; the interview is spoken only now, so what is
        # counted is the help, not a change of channel.
        "device_help_offered": device_help,
        "questions_repeated": silence_recoveries,
        "accommodations": accommodations,
        "low_confidence_scores": sum(
            1 for s in scored if s.confidence < scoring.CONFIDENCE_THRESHOLD
        ),
        "confidence_threshold": scoring.CONFIDENCE_THRESHOLD,
    }
