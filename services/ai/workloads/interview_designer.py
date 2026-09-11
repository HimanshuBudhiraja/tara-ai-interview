"""InterviewDesigner — job description → outcomes → tasks → skills.

The first AI workload in the product, and the one a recruiter meets first.

The recruiter's starting point is a job description, not a list of competencies.
One LLM call turns it into three linked things:

  outcomes — 3-6 results the person achieves when they do this role well
  tasks    — 8-12 concrete things they do to reach those outcomes, each tagged
             with the skills it needs and the outcome it drives
  skills   — 15-20 competencies, each with a priority (how essential to THIS
             job) and a required proficiency (0-4)

Tasks are the hinge. A flat skill list is easy to argue with and impossible to
verify; "you will de-escalate an angry renewal call, and that needs empathy and
policy judgment" is something a hiring manager can confirm or correct. It also
grounds the interview in the work rather than in abstract competency names.

Two things this module deliberately does NOT trust the model on:

  * `priority` is used as given, but the top few become the evaluated set by
    rank rather than by the model volunteering which to interview.
  * `proficiency_target` is overwritten by `spread_targets`. Left to itself the
    extractor marks nearly every competency "expert" for anything titled senior,
    which produces an unpassable profile.

Extraction is one-shot and recruiter-editable. Nothing here is final: the whole
point of the review screen is "TARA inferred it, I adjusted it".
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from packages.types import new_id
from services.ai.brain import LLMError, get_llm

PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}
PROFICIENCY_LABELS = ["Novice", "Advanced beginner", "Competent", "Proficient", "Expert"]


@dataclass
class Skill:
    """One competency inferred from the JD."""

    name: str
    #: Opaque and immutable — `skl_…` for anything created since stable ids
    #: landed. Older drafts carry a slug of the name they had at creation; both
    #: are stable, which is what matters, so they are migrated in place rather
    #: than rewritten (rewriting would break the tasks pointing at them).
    competency_id: str
    priority: str = "medium"  # high | medium | low — how essential to THIS job
    proficiency_target: int = 2  # 0-4, the level the role genuinely requires
    evaluated: bool = False  # in the interviewed set
    tasks: list[str] = field(default_factory=list)  # back-filled from tagged tasks
    # One sentence on what the skill means for THIS role.
    description: str = ""
    # What specifically gets probed within the skill — the niche limitation.
    # The field that stops a skill list being a list of nouns, and the one a
    # recruiter is most likely to want to correct.
    assessment_scope: str = ""
    # Which authored pool competency answers this skill, if any. Empty means no
    # questions exist for it yet — where a Question Generation Engine would go.
    pool_competency: str = ""
    #: Which domain of the skill master this skill belongs to. The NAME stays
    #: free text in the JD's own words; the DOMAIN is catalogued, so skills can
    #: be counted and compared across interviews instead of each one being an
    #: island. Proposed by `skill_master.resolve` and overridable by the
    #: recruiter — "" means nothing matched well enough to guess, which the
    #: configuration screen asks them to fix.
    domain: str = ""

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 2)


@dataclass
class Task:
    """A concrete task inferred from the JD, tagged with what it needs.

    `id` is opaque and immutable. Tasks used to be addressed by their position
    in the list (`task_0`), which was survivable while nothing else pointed at
    them — but a question referencing `task_3` would silently start assessing a
    different piece of work the moment someone deleted `task_1`.
    """

    description: str
    id: str = ""
    required_skills: list[str] = field(default_factory=list)  # competency_ids
    outcome: str = ""
    # A short label, so a review screen has a heading that is not the whole
    # sentence.
    name: str = ""
    priority: str = "medium"

    @property
    def label(self) -> str:
        return self.name or self.description[:60]

    def ensure_id(self) -> str:
        """Backfill an id for a task stored before ids existed."""
        if not self.id:
            self.id = new_id("tsk")
        return self.id


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return s or "skill"


# --------------------------------------------------------------------------- #
#  The extraction call
# --------------------------------------------------------------------------- #
_SYSTEM = """You are an expert technical recruiter and I/O psychologist. From a job description, produce THREE things.

Return STRICT JSON with exactly these keys: {"outcomes": [...], "skills": [...], "tasks": [...]}

1) outcomes: 3-6 concrete job OUTCOMES — what the person actually achieves when they perform this
   role well. Results, not activities. Each a short sentence.

2) skills: the 15-20 skills/competencies a structured interview should assess. Name them from the
   JOB DESCRIPTION's own language and the tasks it describes. The question-bank list below exists
   only for the mapping field — never rename a skill to match a bank label, or the recruiter stops
   recognising their own role in the output. For each:
   - "name": a concise competency name (2-4 words), in the JD's vocabulary
   - "priority": "high" | "medium" | "low" — how ESSENTIAL the skill is to performing THIS job and
     its tasks. Reserve "high" for the 5-7 skills most required to do the job well; those are the
     ones worth interviewing.
   - "proficiency_target": integer 0-4 (0 novice, 1 advanced beginner, 2 competent, 3 proficient,
     4 expert) = the level the role genuinely requires.
   - "assessment_scope": ONE sentence naming what specifically would be probed within this skill
     FOR THIS ROLE — the narrow thing an interviewer would actually ask about. Not a definition of
     the skill and not a restatement of its name. "Reconciliation" is a noun; "Spotting a mismatch
     between the ledger and a provider settlement file, and deciding what to do about it" is a
     thing you can ask a question about. Ground it in this job description's own work.
   - "question_bank": the id of the question bank whose questions could genuinely assess this
     skill, chosen from the QUESTION BANKS list given in the user message, or "" if none of them
     fits. Match on what the questions actually probe, not on wording — "problem diagnosis" and
     "root cause analysis" both belong to a troubleshooting bank. Do NOT force a match; "" is the
     correct answer for a skill no bank covers, and the recruiter is shown that gap.

3) tasks: 8-12 concrete TASKS the person does to achieve those outcomes. For each:
   - "description": a specific behavioural description of the work
   - "outcome": the outcome it drives, reusing your outcome text verbatim
   - "required_skills": the SKILL NAMES from your own skills list, matched exactly — usually 2-4
     per task.
   Cover the list so EVERY skill you returned is required by at least one task.

Assess only what predicts performance in the role. Never infer or return anything about age, family
status, religion, ethnicity, nationality, immigration status, health, disability, or any other
protected characteristic, even if the job description mentions it."""


# Required proficiency by priority, banded by the role's seniority in years.
# (max_years, {priority: target}, share of evaluated competencies allowed at Expert)
SENIORITY_BANDS: list[tuple[int, dict[str, int], float]] = [
    (2, {"high": 2, "medium": 2, "low": 1}, 0.0),  # junior
    (5, {"high": 3, "medium": 2, "low": 2}, 0.0),  # mid
    (9, {"high": 3, "medium": 3, "low": 2}, 0.34),  # senior
    (99, {"high": 4, "medium": 3, "low": 2}, 0.50),  # lead / principal
]


def spread_targets(skills: list[Skill], experience_to: int = 0) -> None:
    """Set required proficiency from priority and seniority, not from the model.

    Left to itself the extractor marks nearly every competency "expert" for
    anything titled senior. That produces an unpassable profile — a candidate
    has to clear every target, so a genuinely strong performer still lands on
    the wrong side of the bar. Spreading the targets keeps the profile
    discriminating: a few competencies sit at the top of the scale and the rest
    sit where a good hire actually clears them.

    The Expert cap applies to the EVALUATED set, and excess Experts are demoted
    lowest-priority first, so the ones that stay at Expert are the ones the role
    most depends on.
    """
    targets, expert_share = next(
        (t, share) for max_yrs, t, share in SENIORITY_BANDS if (experience_to or 0) <= max_yrs
    )
    for s in skills:
        s.proficiency_target = targets.get(s.priority, 2)

    evaluated = [s for s in skills if s.evaluated]
    if not evaluated:
        return
    allowed = int(len(evaluated) * expert_share) if expert_share else 0
    if expert_share and allowed == 0:
        allowed = 1  # a senior role should stretch somewhere

    by_essential = sorted(evaluated, key=lambda s: s.priority_rank, reverse=True)
    for i, s in enumerate(by_essential):
        if i < allowed:
            s.proficiency_target = 4
        elif s.proficiency_target >= 4:
            s.proficiency_target = 3


def mark_evaluated(skills: list[Skill], top_n: int) -> None:
    """Interview the highest-priority skills, capped at top_n.

    Ties break on proficiency target, so the more demanding skill wins.
    """
    ranked = sorted(skills, key=lambda s: (s.priority_rank, s.proficiency_target), reverse=True)
    keep = {id(s) for s in ranked[:top_n]}
    for s in skills:
        s.evaluated = id(s) in keep


def extract(
    jd_text: str,
    role_title: str,
    company_name: str = "",
    company_about: str = "",
    role_context: str = "",
    experience_to: int = 0,
    skills_evaluated: int = 6,
    pool: Any = None,
) -> tuple[list[str], list[Task], list[Skill]]:
    """One call → (outcomes, tasks, skills). Raises LLMError with no key.

    The pool's competencies go into the same call so the model can say which
    question bank answers each skill. Doing it here rather than in a second pass
    is both cheaper and better: matching "problem diagnosis" to a
    troubleshooting bank is a semantic judgement, and the model already has the
    job description in front of it.
    """
    banks = ""
    if pool is not None:
        banks = "\n".join(f"- {c.id}: {c.label}" for c in pool.competencies)
    user = (
        f"Role title: {role_title}\n"
        f"Company: {company_name} — {company_about}\n"
        f"Role context: {role_context}\n"
        f"Target experience: up to {experience_to} years\n\n"
        f"QUESTION BANKS available (id: label):\n{banks or '(none)'}\n\n"
        f"Job description:\n{jd_text.strip()}\n"
    )
    data = get_llm().complete_json(_SYSTEM, user, max_tokens=4000)
    if not data or not data.get("skills"):
        raise LLMError("extraction returned no skills")

    # --- skills ---
    skills: list[Skill] = []
    name_to_cid: dict[str, str] = {}
    seen: set[str] = set()
    for raw in data.get("skills", []):
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        cid = slug(name)
        if cid in seen:
            continue
        seen.add(cid)
        name_to_cid[name.lower()] = cid
        priority = (raw.get("priority") or "medium").strip().lower()
        if priority not in PRIORITY_RANK:
            priority = "medium"
        bank = (raw.get("question_bank") or "").strip()
        # The question generator writes from the scope, and publication refuses
        # a skill without one — "Reconciliation" alone does not say what to ask.
        # Trimmed rather than truncated mid-word, and capped because a model
        # occasionally answers with a paragraph where a sentence was asked for.
        scope = " ".join((raw.get("assessment_scope") or "").split())[:280]
        skills.append(
            Skill(
                name=name, competency_id=cid, priority=priority,
                pool_competency=bank, assessment_scope=scope,
            )
        )

    # --- tasks: map skill names back to the ids we actually kept ---
    tasks: list[Task] = []
    for raw in data.get("tasks", []):
        desc = (raw.get("description") or "").strip()
        if not desc:
            continue
        required: list[str] = []
        for rs in raw.get("required_skills") or []:
            cid = name_to_cid.get((rs or "").strip().lower())
            if cid and cid not in required:
                required.append(cid)
        tasks.append(
            Task(description=desc, required_skills=required, outcome=(raw.get("outcome") or "").strip())
        )

    # Each skill's grounding tasks, so question selection and the review screen
    # can both show why a skill is being assessed.
    for s in skills:
        s.tasks = [t.description for t in tasks if s.competency_id in t.required_skills][:6]

    outcomes = [o.strip() for o in (data.get("outcomes") or []) if o.strip()]

    if pool is not None:
        # Drop bank ids the model invented, then fall back to word matching for
        # any skill it left blank.
        valid = {c.id for c in pool.competencies}
        for s in skills:
            if s.pool_competency not in valid:
                s.pool_competency = ""
        map_to_pool([s for s in skills if not s.pool_competency], pool)

    mark_evaluated(skills, skills_evaluated)
    spread_targets(skills, experience_to)
    return outcomes, tasks, skills


# --------------------------------------------------------------------------- #
#  Mapping inferred skills onto the authored question pool
# --------------------------------------------------------------------------- #
_MATCH_STOPWORDS = {"skill", "skills", "ability", "and", "the", "of", "for", "with"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _MATCH_STOPWORDS}


def map_to_pool(skills: list[Skill], pool) -> None:
    """Point each inferred skill at the pool competency that can answer it.

    This build has no Question Generation Engine, so the questions come from the
    authored pool. A skill the pool doesn't cover isn't an error — it's the
    honest gap, shown to the recruiter as "no questions authored yet", and
    exactly where a QGE would plug in.

    Matching is by id, then by word overlap between the skill name and the
    competency label. Crude, but it's a suggestion the recruiter can override,
    not a decision.
    """
    candidates = [(c.id, c.label, _tokens(c.label) | _tokens(c.id)) for c in pool.competencies]

    for s in skills:
        s.pool_competency = ""
        if any(cid == s.competency_id for cid, _, _ in candidates):
            s.pool_competency = s.competency_id
            continue
        skill_tokens = _tokens(s.name)
        best, best_score = "", 0
        for cid, _label, comp_tokens in candidates:
            score = len(skill_tokens & comp_tokens)
            # Substring hits catch "de-escalation" vs "deescalation".
            flat = re.sub(r"[^a-z]", "", s.name.lower())
            if flat and (flat in cid or cid in flat):
                score += 2
            if score > best_score:
                best, best_score = cid, score
        if best_score >= 1:
            s.pool_competency = best


def to_dict(obj: Any) -> dict:
    return asdict(obj)
