"""Deterministic validation for generated assessment content.

Three separate things, kept separate on purpose (§16):

    schema validity   the shape is right                 — packages/schemas
    safety validity   nothing here may reach a candidate — this module
    semantic quality  is it a GOOD question              — a person, on the review screen

The middle one is what this module owns, and it does not ask a model. A prompt
instructing a generator not to ask about age is a request; a regex that rejects
the question is a control. Both exist, and only one of them is testable with the
provider switched off.

Legality reuses the production guardrail patterns — `guardrails.check_legality`
is the same function that gates a live probe, so a question and a follow-up are
held to one standard rather than two that drift.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from packages.types.definition import (
    DIFFICULTIES,
    QUESTION_TYPES,
    InterviewDefinition,
    QuestionSpec,
)
from services.orchestrator import guardrails

#: Cues and criteria outside this range stop being usable: too few and a
#: reviewer has nothing to check against, too many and the classifier is asked
#: to make distinctions no spoken answer supports.
MIN_CUES, MAX_CUES = 3, 5
MIN_CRITERIA, MAX_CRITERIA = 2, 5
MIN_PROBES = 2

#: Two questions this similar are the same question. Measured on content-word
#: overlap rather than characters, because a re-wording shares almost no
#: characters with its original.
#:
#: This is lexical, and its limit is worth stating: "de-escalated an angry
#: customer" and "calmed an angry customer" score 0.5 and would pass, because
#: catching synonym-level paraphrase needs embeddings. Duplicate detection here
#: catches re-orderings and near-copies; the review screen catches the rest,
#: which is one of the things the review screen is for.
NEAR_DUPLICATE_THRESHOLD = 0.65

#: Model exhaust. A question containing any of this has leaked its own scaffolding.
_META_MARKERS = re.compile(
    r"\b(as an ai|language model|i cannot|i'm sorry|system prompt|"
    r"looking_for|evaluation_criteria|assistant:|user:|json|schema|rubric)\b",
    re.I,
)

#: A question that hands over its own answer measures nothing.
_LEADING = re.compile(
    r"\b(don't you|wouldn't you|isn't it important|would you agree|"
    r"the right answer|obviously you|you should always)\b",
    re.I,
)


@dataclass
class Problem:
    code: str
    message: str
    fatal: bool = True


@dataclass
class Verdict:
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(p.fatal for p in self.problems)

    @property
    def messages(self) -> list[str]:
        return [p.message for p in self.problems]

    def add(self, code: str, message: str, fatal: bool = True) -> None:
        self.problems.append(Problem(code, message, fatal))

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": [
                {"code": p.code, "message": p.message, "fatal": p.fatal}
                for p in self.problems
            ],
        }


def _content_words(text: str) -> set[str]:
    """Six-character stems, matching the production relevance guardrail.

    The same stemming, for the same reason it exists there: exact-token matching
    once threw away a good follow-up because the model wrote "apologizing" where
    the candidate said "apologise".
    """
    stop = {
        "about", "would", "could", "there", "their", "which", "where", "when",
        "what", "your", "have", "that", "this", "with", "from", "tell", "describe",
        "walk", "time", "give", "example", "situation", "candidate", "them", "they",
    }
    words = re.findall(r"[a-z']{4,}", (text or "").lower())
    return {w[:6] for w in words if w not in stop}


def similarity(a: str, b: str, *, ignore: set[str] | None = None) -> float:
    """Jaccard overlap of content stems. 1.0 is the same question.

    `ignore` drops vocabulary the two texts share by construction — the skill
    and task they are both about — so what is measured is how differently they
    ask, not how similar their subject is.
    """
    wa, wb = _content_words(a), _content_words(b)
    if ignore:
        wa, wb = wa - ignore, wb - ignore
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def contains(haystack: str, needle: str) -> float:
    """How much of `needle`'s content appears in `haystack`.

    Asymmetric on purpose. "Does this restatement repeat that cue?" is not the
    same question as "are these two sentences alike": a long clarification that
    happens to contain a short cue verbatim has leaked it, however different
    their overall lengths make them look to a symmetric measure.
    """
    hay, need = _content_words(haystack), _content_words(needle)
    if not need:
        return 0.0
    return len(hay & need) / len(need)


# --------------------------------------------------------------------------- #
#  One question
# --------------------------------------------------------------------------- #
def validate_question(
    question: QuestionSpec,
    definition: InterviewDefinition,
    *,
    existing: list[QuestionSpec] | None = None,
) -> Verdict:
    """Everything checkable about one item without a person reading it.

    Applied identically to generated and hand-written questions. Human-edited
    content is not automatically trusted — a recruiter can type an illegal
    question as easily as a model can generate one, and the candidate on the
    other end cannot tell which happened.
    """
    v = Verdict()
    text = (question.question_text or "").strip()

    if not text:
        v.add("empty", "The question has no text.")
        return v
    if len(text.split()) < 4:
        v.add("too_short", f"“{text}” is too short to be an interview question.")
    if len(text.split()) > 90:
        v.add("too_long",
              "This is too long to hold in your head when it is spoken aloud.")

    leaked = _META_MARKERS.search(text)
    if leaked:
        v.add("meta_text",
              f"The question contains model or system text: “{leaked.group(0)}”.")

    leading = _LEADING.search(text)
    if leading:
        v.add("leading",
              f"“{leading.group(0)}” tells the candidate the answer you want.")

    # --- legality: the production gate, not a copy of it ---
    legality = guardrails.check_legality(text)
    if not legality.ok:
        # The guardrail's reason already reads "touches age".
        v.add("illegal", f"This question {legality.reason}.")

    # --- mapping ---
    skill_ids = {s.id for s in definition.skills}
    task_ids = {t.id for t in definition.tasks}
    if not question.skill_id:
        v.add("no_primary_skill",
              "Every question needs one primary skill, or nothing knows what it measures.")
    elif question.skill_id not in skill_ids:
        v.add("unknown_skill",
              f"The primary skill {question.skill_id} is not part of this interview.")

    unknown_secondary = [s for s in question.secondary_skill_ids if s not in skill_ids]
    if unknown_secondary:
        v.add("unknown_secondary_skill",
              f"These secondary skills are not part of this interview: "
              f"{', '.join(unknown_secondary)}.")
    if question.skill_id in question.secondary_skill_ids:
        v.add("duplicate_skill",
              "The primary skill is also listed as a secondary skill.")

    if question.task_id and question.task_id not in task_ids:
        v.add("unknown_task",
              f"The task {question.task_id} is not part of this interview.")

    if question.difficulty not in DIFFICULTIES:
        v.add("bad_difficulty", f"{question.difficulty!r} is not a difficulty.")
    if question.question_type not in QUESTION_TYPES:
        v.add("bad_type", f"{question.question_type!r} is not a question type.")

    # --- looking_for: what the classifier actually reads ---
    cues = [c.strip() for c in question.looking_for if c and c.strip()]
    if not cues:
        v.add("no_cues",
              "Without expected signals, the runtime cannot tell a good answer from a bad one.")
    elif not MIN_CUES <= len(cues) <= MAX_CUES:
        v.add("cue_count",
              f"{len(cues)} expected signals — keep it between {MIN_CUES} and {MAX_CUES} "
              f"so each one can be judged separately.")
    vague = [c for c in cues if len(c.split()) < 3]
    if vague:
        v.add("vague_cue",
              f"These signals are too vague to observe in an answer: {', '.join(vague[:2])}.",
              fatal=False)

    # --- rubric ---
    criteria = [c for c in question.evaluation_criteria if (c.label or "").strip()]
    if not criteria:
        v.add("no_criteria", "The question has no evaluation criteria.")
    elif not MIN_CRITERIA <= len(criteria) <= MAX_CRITERIA:
        v.add("criteria_count",
              f"{len(criteria)} criteria — keep it between {MIN_CRITERIA} and {MAX_CRITERIA}.")

    # --- clarification ---
    clarify = (question.clarify or "").strip()
    if not clarify:
        v.add("no_clarify",
              "Without a restatement, “what do you mean?” has no authored answer.")
    else:
        # A clarification that recites the cues hands the candidate the answer
        # key at the exact moment they said they were struggling.
        for cue in cues:
            if contains(clarify, cue) >= 0.75:
                v.add("clarify_leaks_rubric",
                      f"The restatement repeats an expected signal (“{cue}”), which tells "
                      f"the candidate what to say.")
                break
        if _META_MARKERS.search(clarify):
            v.add("clarify_meta", "The restatement contains model or system text.")

    # --- probes: through the production guardrails ---
    if question.probe_eligible:
        probes = [p.strip() for p in question.probe_bank if p and p.strip()]
        if len(probes) < MIN_PROBES:
            v.add("thin_probe_bank",
                  f"Only {len(probes)} authored follow-up(s). If a generated one is "
                  f"rejected at runtime there has to be something else to ask.")
        for probe in probes:
            # `validate_probe` needs an answer to judge relevance against, and
            # there is none at authoring time — so the two gates that CAN be
            # judged are applied, and relevance is left to runtime where the
            # candidate's words exist.
            fmt = guardrails.check_format(probe)
            if not fmt.ok:
                v.add("bad_probe_format", f"Follow-up “{probe[:48]}”: {fmt.reason}")
            legal = guardrails.check_legality(probe)
            if not legal.ok:
                v.add("illegal_probe", f"Follow-up “{probe[:48]}” {legal.reason}.")
        for i, a in enumerate(probes):
            for b in probes[i + 1:]:
                if similarity(a, b) >= NEAR_DUPLICATE_THRESHOLD:
                    v.add("duplicate_probe",
                          f"Two follow-ups ask the same thing: “{a[:40]}”.", fatal=False)

    # --- duplicates against the rest of the pool ---
    #
    # Compared with the shared subject removed. Two questions about the same
    # task inevitably share the task's words — "walk me through reconciling
    # settlements" and "what breaks when you reconcile settlements" are eighty
    # per cent the same vocabulary and completely different questions. Stripping
    # the skill and task names measures whether the QUESTION differs, which is
    # the thing that matters.
    subject = _content_words(
        " ".join(filter(None, [
            (definition.skill(question.skill_id).name
             if definition.skill(question.skill_id) else ""),
            next((t.label + " " + t.description for t in definition.tasks
                  if t.id == question.task_id), ""),
        ]))
    )
    for other in existing or []:
        if other.id == question.id:
            continue
        score = (
            similarity(text, other.question_text, ignore=subject)
            if other.skill_id == question.skill_id and other.task_id == question.task_id
            else similarity(text, other.question_text)
        )
        if score >= 0.95:
            v.add("duplicate", f"This is the same question as “{other.question_text[:48]}”.")
            break
        if score >= NEAR_DUPLICATE_THRESHOLD:
            v.add("near_duplicate",
                  f"This is very close to “{other.question_text[:48]}” — a candidate would "
                  f"be answering the same thing twice.")
            break

    return v


# --------------------------------------------------------------------------- #
#  The whole pool
# --------------------------------------------------------------------------- #
def validate_question_pool_coverage(
    definition: InterviewDefinition, blueprint: Any
) -> Verdict:
    """Does the pool actually assess what the recruiter approved?

    This becomes the publication gate. Today it drives the review screen, which
    is the same question asked earlier: an interview that cannot be published is
    one the recruiter should find out about while they are still looking at it.
    """
    v = Verdict()
    questions = definition.questions

    if not questions:
        v.add("empty_pool", "No questions have been generated yet.")
        return v

    skill_ids = {s.id for s in definition.skills}
    task_ids = {t.id for t in definition.tasks}

    orphans = [q.id for q in questions if q.skill_id not in skill_ids]
    if orphans:
        v.add("orphan_questions",
              f"{len(orphans)} question(s) point at a skill that no longer exists.")

    bad_tasks = [q.id for q in questions if q.task_id and q.task_id not in task_ids]
    if bad_tasks:
        v.add("orphan_task_mapping",
              f"{len(bad_tasks)} question(s) point at a task that no longer exists.")

    # --- floors, per skill ---
    counts: dict[str, int] = {}
    for q in questions:
        counts[q.skill_id] = counts.get(q.skill_id, 0) + 1

    for target in getattr(blueprint, "coverage", []):
        have = counts.get(target.skill_id, 0)
        if have < target.min_items:
            v.add(
                "below_floor",
                f"{target.skill_name} has {have} question(s) but needs at least "
                f"{target.min_items} — a {target.priority}-priority skill shouldn't rest "
                f"on {'one answer' if have == 1 else 'nothing'}.",
                fatal=target.priority == "high",
            )
        elif have < target.target_question_count:
            v.add(
                "below_target",
                f"{target.skill_name} has {have} of {target.target_question_count} "
                f"planned questions.",
                fatal=False,
            )

    # --- the pool has to fill the interview ---
    budget = getattr(blueprint, "live_item_budget", 0)
    if budget and len(questions) < budget:
        v.add("pool_too_small",
              f"The interview asks up to {budget} questions but the pool only has "
              f"{len(questions)}. It would run out and close early.",
              fatal=False)

    # --- difficulty ---
    difficulties = {q.difficulty for q in questions}
    if "easy" not in difficulties:
        v.add("no_warm_up",
              "Every interview opens with an easy question, and there isn't one in the "
              "pool. The first thing a nervous candidate meets would be a hard scenario.")

    # --- task coverage ---
    if task_ids:
        covered_tasks = {q.task_id for q in questions if q.task_id}
        if not covered_tasks:
            v.add("no_task_grounding",
                  "No question is grounded in a task, so the interview asks about skills "
                  "in the abstract rather than the work.", fatal=False)

    # --- probes ---
    probeable = [q for q in questions if q.probe_eligible]
    thin = [q.id for q in probeable if len(q.probe_bank) < MIN_PROBES]
    if thin:
        v.add("thin_probe_coverage",
              f"{len(thin)} question(s) can be probed but have fewer than {MIN_PROBES} "
              f"authored follow-ups to fall back on.", fatal=False)

    return v


def coverage_summary(definition: InterviewDefinition, blueprint: Any) -> dict[str, Any]:
    """The numbers at the top of the Question Pool screen."""
    questions = definition.questions
    counts: dict[str, int] = {}
    for q in questions:
        counts[q.skill_id] = counts.get(q.skill_id, 0) + 1
    by_difficulty: dict[str, int] = {}
    for q in questions:
        by_difficulty[q.difficulty] = by_difficulty.get(q.difficulty, 0) + 1
    by_type: dict[str, int] = {}
    for q in questions:
        by_type[q.question_type] = by_type.get(q.question_type, 0) + 1

    return {
        "pool_size": len(questions),
        "live_item_budget": getattr(blueprint, "live_item_budget", 0),
        "target_duration_min": getattr(blueprint, "target_duration_min", 0),
        "skills_covered": len([s for s in definition.skills if counts.get(s.id)]),
        "skills_total": len(definition.skills),
        "tasks_covered": len({q.task_id for q in questions if q.task_id}),
        "tasks_total": len(definition.tasks),
        "difficulty_distribution": by_difficulty,
        "type_distribution": by_type,
        "per_skill": [
            {
                "skill_id": t.skill_id,
                "skill_name": t.skill_name,
                "priority": t.priority,
                "have": counts.get(t.skill_id, 0),
                "target": t.target_question_count,
                "min_items": t.min_items,
            }
            for t in getattr(blueprint, "coverage", [])
        ],
    }
