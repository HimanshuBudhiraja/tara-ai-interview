"""Grading generated questions.

"Is this a good interview question?" is not fully checkable without a person,
and this grader does not pretend otherwise. What it checks is everything that
IS objective, including the one that decides whether the generator is usable at
all: does the output satisfy the runtime's question contract, authored fallback
probe and clarify line included? Those are what stop a rejected follow-up
becoming silence, and a generator that omits them cannot be published.

The generated questions themselves are printed into MODEL_EVALUATION.md so a
person can read them and make the last judgement.
"""
from __future__ import annotations

import json
from typing import Any

from evals.graders.base import PROTECTED_TERMS, Grade, mentions_any, stems
from packages.types.definition import (
    BankSpec,
    InterviewDefinition,
    QuestionSpec,
    SkillSpec,
)

#: Stems of questions that could be asked about any job at all. A generator that
#: produces these has not read the task it was given.
GENERIC_OPENERS = [
    "tell me about yourself",
    "what are your strengths",
    "what are your weaknesses",
    "where do you see yourself",
    "why do you want this job",
    "why do you want to work here",
    "describe your experience",
    "tell me about your background",
]

#: A question containing its own answer measures nothing.
LEADING_MARKERS = [
    "don't you", "wouldn't you", "isn't it important", "would you agree",
    "do you think it's important", "you should", "the right answer",
    "obviously", "of course you would",
]


def build_payload(case: dict[str, Any]) -> str:
    """The exact user payload the production generator sends."""
    return json.dumps(
        {
            "role_title": case["role_title"],
            "language": case.get("language", "en"),
            "overall_difficulty": case.get("difficulty", "medium"),
            "questions_per_skill": case.get("questions_per_skill", 2),
            "skills": [
                {
                    "name": s["name"],
                    "priority": s.get("priority", "medium"),
                    "required_proficiency": s.get("proficiency_target", 2),
                    "assessment_scope": s.get("assessment_scope", ""),
                }
                for s in case["skills"]
            ],
            "tasks": [
                {"description": t["description"], "outcome": t.get("outcome", "")}
                for t in case["tasks"]
            ],
        },
        ensure_ascii=False,
    )


def grade(case: dict[str, Any], output: dict[str, Any] | None) -> Grade:
    g = Grade()
    questions = (output or {}).get("questions") or []
    if not questions:
        g.add("produced_questions", False, "no questions returned")
        return g
    g.add("produced_questions", True, f"{len(questions)} questions")

    skills = case["skills"]
    skill_names = {s["name"].lower() for s in skills}
    expected = case.get("expect", {})

    # --- every question maps to exactly one requested skill ---
    mapped = [(q.get("skill") or "").strip().lower() for q in questions]
    unknown = [m for m in mapped if m not in skill_names]
    g.add("targets_the_requested_skill", not unknown,
          f"mapped to skills nobody asked for: {unknown[:3]}")

    wanted = expected.get("targets_skills") or (
        [expected["targets_skill"]] if "targets_skill" in expected else []
    )
    if wanted:
        got = {m for m in mapped}
        missing = [w for w in wanted if w.lower() not in got]
        g.add("covers_every_requested_skill", not missing, f"no question for {missing}")

    # --- grounded in the task, not the abstract skill ---
    if expected.get("expect_grounded_in_task"):
        task_stems: set[str] = set()
        for t in case["tasks"]:
            task_stems |= stems(t["description"])
        grounded = sum(
            1 for q in questions if len(stems(q.get("question_text", "")) & task_stems) >= 2
        )
        g.add("grounded_in_the_task", grounded >= max(1, len(questions) // 2),
              f"{grounded}/{len(questions)} questions share content with a task")

    texts = [q.get("question_text", "") for q in questions]
    blob = " ".join(texts).lower()

    # --- not generic, not leading ---
    generic = [o for o in GENERIC_OPENERS if o in blob]
    g.add("not_generic", not generic, f"could be asked of any job: {generic}")

    leading = [m for m in LEADING_MARKERS if m in blob]
    g.add("not_leading", not leading, f"contains its own answer: {leading}")

    # --- protected characteristics ---
    everything = blob + " " + " ".join(
        " ".join(q.get("looking_for", []) or []) + " " + " ".join(q.get("probe_bank", []) or [])
        for q in questions
    ).lower()
    hit = mentions_any(everything, PROTECTED_TERMS)
    g.add("no_protected_characteristics", not hit, f"mentions {hit!r}" if hit else "",
          critical=bool(hit))

    # --- expected signal ---
    with_signal = sum(1 for q in questions if (q.get("expected_signal") or "").strip())
    g.add("has_expected_signal", with_signal == len(questions),
          f"{with_signal}/{len(questions)} say what a strong answer shows")

    with_cues = sum(1 for q in questions if len(q.get("looking_for") or []) >= 2)
    g.add("has_reviewable_cues", with_cues == len(questions),
          f"{with_cues}/{len(questions)} give a reviewer at least two cues")

    # --- difficulty ---
    asked = case.get("difficulty", "medium")
    difficulties = {(q.get("difficulty") or "").lower() for q in questions}
    g.add("respects_difficulty", asked in difficulties or not difficulties,
          f"asked for {asked}, got {sorted(difficulties)}")

    # --- spoken aloud, not read ---
    too_long = [t for t in texts if len(t.split()) > 70]
    g.add("speakable", not too_long,
          f"{len(too_long)} question(s) too long to hold in your head when spoken")

    # --- the decisive one: is it usable by the runtime unchanged? ---
    specs = [
        QuestionSpec(
            id=f"q{n}",
            question_text=q.get("question_text", ""),
            competency="bank",
            looking_for=list(q.get("looking_for") or []),
            evaluation_criteria=list(q.get("evaluation_criteria") or []),
            probe_eligible=bool(q.get("probe_eligible", True)),
            probe_bank=list(q.get("probe_bank") or []),
            clarify=q.get("clarify", "") or "",
        )
        for n, q in enumerate(questions)
    ]
    defn = InterviewDefinition(
        skills=[SkillSpec(id="s", name=skills[0]["name"], question_bank="bank")],
        banks=[BankSpec(id="bank", label="bank")],
        questions=specs,
    )
    problems = defn.validate()
    g.add("satisfies_the_runtime_contract", not problems, "; ".join(problems[:2]))

    with_clarify = sum(1 for q in questions if (q.get("clarify") or "").strip())
    g.add("has_clarify_line", with_clarify == len(questions),
          f"{with_clarify}/{len(questions)} answer 'what do you mean?'")

    return g
