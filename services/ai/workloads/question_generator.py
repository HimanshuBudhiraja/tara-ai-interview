"""QuestionGenerator — skills and tasks in, interview questions out.

The boundary that closes the product's honest gap. Today a skill the authored
pool cannot answer is shown to the recruiter in amber and simply is not
assessed; this is what fills that hole.

Two boundaries it does NOT cross, both of them load-bearing (rules 4-7):

  * **It runs before the interview, never during it.** Main questions are
    generated at design time, reviewed by a recruiter, and frozen into a
    published version. Nothing generates a main question while a candidate is
    on the line — that would be an autonomous interviewer, and a candidate
    could not be compared with the one before them.
  * **It produces `QuestionSpec`s, not conversation.** The output goes into an
    `InterviewDefinition` and is selected by the same deterministic policy as an
    authored question. The orchestrator cannot tell the difference, which is the
    entire point of the contract.

Status: implemented and callable, not yet wired into a recruiter screen. The
generation UI is a later phase — see BUILD_STATUS.md.
"""
from __future__ import annotations

import json
import re
from typing import Any

from packages.schemas import QUESTION_SET
from packages.types.definition import QuestionSpec, SkillSpec, TaskSpec
from services.ai.gateway import Workload, get_gateway

SYSTEM = """You write interview questions for a structured, spoken job interview conducted by an AI interviewer.

You are given a role, the skills to assess, and the concrete tasks the person does in the job.

Return STRICT JSON: {"questions": [ ... ]}. For each question:
- "question_text": the question as it will be SPOKEN ALOUD. One or two sentences, conversational,
  no bullet points, no "Part A / Part B". Ground it in a task from the list wherever you can —
  a question about the actual work beats a question about the abstract skill.
- "skill": the exact skill name it assesses, copied from the skills given.
- "task": the exact task description it is grounded in, copied from the tasks given, or "".
- "difficulty": "easy" | "medium" | "hard". Include at least one easy question per skill: a
  candidate should warm up before meeting the hard scenario.
- "expected_signal": one sentence naming what a strong answer demonstrates.
- "looking_for": 3-5 SHORT concrete cues a strong answer contains. These are read back to a human
  reviewer, so write them as observable things ("names a specific trade-off they made"), never as
  scores or adjectives ("good judgement").
- "evaluation_criteria": 2-4 statements a reviewer could agree or disagree with about an answer.
- "probe_eligible": true unless the question has a single factual answer.
- "probe_bank": 2-3 authored follow-ups for this question, each one sentence ending in a question
  mark. These are the fallback when a generated follow-up is rejected, so they must stand alone.
- "clarify": one sentence restating what the question is after, for a candidate who asks what you
  mean. It must NOT answer the question or list the cues.
- "time_budget_sec": how long a full spoken answer reasonably takes.

Never write a question that touches age, family or marital status, pregnancy, religion, ethnicity,
nationality, immigration status, disability, health, sexual orientation, politics, salary history,
or criminal record — not as a main question and not as a follow-up.
Assess only what predicts performance in the role."""


def _slug(text: str, prefix: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return f"{prefix}-{base[:32] or 'q'}"


def generate(
    role_title: str,
    skills: list[SkillSpec],
    tasks: list[TaskSpec],
    *,
    questions_per_skill: int = 2,
    difficulty: str = "medium",
    language: str = "en",
    session_id: str = "_system",
) -> list[QuestionSpec]:
    """Questions for the given skills, as definition-ready specs.

    Raises `AIError` when no usable set comes back. A caller wiring this into a
    screen should treat that as "generation failed, keep the authored pool",
    never as "publish an interview with no questions for this skill".
    """
    by_name = {s.name.lower(): s for s in skills}
    user = json.dumps(
        {
            "role_title": role_title,
            "language": language,
            "overall_difficulty": difficulty,
            "questions_per_skill": questions_per_skill,
            "skills": [
                {
                    "name": s.name,
                    "priority": s.priority,
                    "required_proficiency": s.proficiency_target,
                    "assessment_scope": s.assessment_scope,
                }
                for s in skills
            ],
            "tasks": [
                {"description": t.description, "outcome": t.outcome} for t in tasks
            ],
        },
        ensure_ascii=False,
    )

    result = get_gateway().generate_structured(
        Workload.QUESTION_GENERATOR,
        SYSTEM,
        user,
        QUESTION_SET,
        schema_name="question_set",
        session_id=session_id,
    )

    out: list[QuestionSpec] = []
    seen: set[str] = set()
    for n, raw in enumerate((result.data or {}).get("questions", [])):
        skill = by_name.get((raw.get("skill") or "").strip().lower())
        if skill is None:
            # A question mapped to a skill nobody asked for cannot be scored
            # against anything. Dropping it beats inventing a skill for it.
            continue
        qid = _slug(raw["question_text"], f"gen{n:02d}")
        if qid in seen:
            continue
        seen.add(qid)
        task = next(
            (t for t in tasks if t.description == (raw.get("task") or "").strip()), None
        )
        out.append(
            QuestionSpec(
                id=qid,
                question_text=raw["question_text"].strip(),
                competency=skill.question_bank or skill.id,
                skill_id=skill.id,
                task_id=task.id if task else "",
                difficulty=(raw.get("difficulty") or difficulty),
                expected_signal=(raw.get("expected_signal") or "").strip(),
                looking_for=[c.strip() for c in raw.get("looking_for", []) if c.strip()],
                evaluation_criteria=[
                    c.strip() for c in raw.get("evaluation_criteria", []) if c.strip()
                ],
                probe_eligible=bool(raw.get("probe_eligible", True)),
                max_probes=2,
                time_budget_sec=int(raw.get("time_budget_sec") or 90),
                probe_bank=[p.strip() for p in raw.get("probe_bank", []) if p.strip()],
                clarify=(raw.get("clarify") or "").strip(),
                type="scenario",
                source="generated",
            )
        )
    return out


def review_flags(questions: list[QuestionSpec]) -> dict[str, list[str]]:
    """What a recruiter should look at before publishing generated questions.

    Generation is a proposal, and the review screen is where it becomes a
    decision. These are the things worth surfacing rather than silently fixing.
    """
    flags: dict[str, list[str]] = {}
    for q in questions:
        problems: list[str] = []
        if q.probe_eligible and not q.probe_bank:
            problems.append("No authored fallback follow-up if a generated one is rejected.")
        if len(q.looking_for) < 2:
            problems.append("Fewer than two cues — a reviewer has little to check against.")
        if len(q.question_text.split()) > 70:
            problems.append("Long for a spoken question; a candidate has to hold it in their head.")
        if not q.clarify:
            problems.append("No restatement, so 'what do you mean?' has no authored answer.")
        if problems:
            flags[q.id] = problems
    return flags
