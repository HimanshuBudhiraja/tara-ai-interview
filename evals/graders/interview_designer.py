"""Grading the interview designer.

The load-bearing check is not "did it find good skills" — it is **can the output
become a runnable interview**. So the grader builds a real `InterviewDefinition`
from the model's response and runs the Phase-0 `validate()` over it. A design
that cannot be published is not a design.
"""
from __future__ import annotations

from typing import Any

from evals.graders.base import PROTECTED_TERMS, Grade, mentions_any
from packages.types.definition import (
    BankSpec,
    InterviewDefinition,
    QuestionSpec,
    SkillSpec,
    TaskSpec,
)


def build_payload(case: dict[str, Any], banks: str = "") -> str:
    """The exact user payload the production designer sends."""
    return (
        f"Role title: {case['role_title']}\n"
        f"Company:  — \n"
        f"Role context: \n"
        f"Target experience: up to {case.get('experience_to', 0)} years\n\n"
        f"QUESTION BANKS available (id: label):\n{banks or '(none)'}\n\n"
        f"Job description:\n{case['jd_text'].strip()}\n"
    )


def _as_definition(output: dict[str, Any]) -> InterviewDefinition:
    """Turn a raw design into the contract, the way the product would."""
    from services.ai.workloads.interview_designer import slug

    skills = [
        SkillSpec(
            id=slug(s.get("name", "")),
            name=s.get("name", ""),
            priority=(s.get("priority") or "medium"),
            proficiency_target=int(s.get("proficiency_target") or 2),
            assessment_scope=s.get("assessment_scope", "") or "",
            # Every skill is given a bank here so the definition is *runnable*.
            # In the product an unmapped skill is shown amber and not assessed;
            # this grader is asking whether the STRUCTURE is sound.
            question_bank=slug(s.get("name", "")),
        )
        for s in output.get("skills", [])
        if s.get("name")
    ]
    by_name = {s.name.lower(): s.id for s in skills}
    tasks = [
        TaskSpec(
            id=f"task_{n}",
            description=t.get("description", ""),
            outcome=t.get("outcome", "") or "",
            skill_ids=[
                by_name[r.strip().lower()]
                for r in (t.get("required_skills") or [])
                if r and r.strip().lower() in by_name
            ],
        )
        for n, t in enumerate(output.get("tasks", []))
        if t.get("description")
    ]
    return InterviewDefinition(
        role_title="eval",
        skills=skills,
        tasks=tasks,
        banks=[BankSpec(id=s.id, label=s.name) for s in skills],
        # One placeholder question per skill: this grader is judging the design,
        # and a definition with no questions fails validation for a reason that
        # has nothing to do with the design.
        questions=[
            QuestionSpec(id=f"q_{s.id}", question_text=f"Placeholder for {s.name}",
                         competency=s.id, skill_id=s.id, probe_bank=["And then what?"])
            for s in skills
        ],
    )


def grade(case: dict[str, Any], output: dict[str, Any] | None) -> Grade:
    g = Grade()
    if not output:
        g.add("returned_valid_output", False, "no parsed output")
        return g

    expect = case.get("expect", {})
    skills = output.get("skills") or []
    tasks = output.get("tasks") or []
    outcomes = output.get("outcomes") or []

    g.add("has_outcomes", len(outcomes) >= 1, f"{len(outcomes)} outcomes")
    g.add("skill_count", len(skills) >= expect.get("min_skills", 1),
          f"{len(skills)} skills, wanted >= {expect.get('min_skills', 1)}")
    g.add("task_count", len(tasks) >= expect.get("min_tasks", 1),
          f"{len(tasks)} tasks, wanted >= {expect.get('min_tasks', 1)}")

    # --- task → skill mapping ---
    skill_names = {(s.get("name") or "").strip().lower() for s in skills}
    mapped = {
        r.strip().lower()
        for t in tasks
        for r in (t.get("required_skills") or [])
        if r
    }
    unknown = mapped - skill_names
    g.add("tasks_reference_real_skills", not unknown,
          f"tasks require skills that were never returned: {sorted(unknown)[:4]}")

    covered = skill_names & mapped
    share = len(covered) / len(skill_names) if skill_names else 0
    # A skill no task requires is a skill nobody can point at a piece of the job
    # and justify. That is exactly the flat list tasks exist to replace.
    g.add("skills_grounded_in_tasks", share >= 0.6,
          f"{len(covered)}/{len(skill_names)} skills are required by at least one task")

    # --- priority is a judgement, not a rubber stamp ---
    priorities = [(s.get("priority") or "").lower() for s in skills]
    highs = priorities.count("high")
    g.add("priority_discriminates", 0 < highs < len(skills),
          f"{highs}/{len(skills)} marked high — if everything is essential, nothing is")

    # --- protected characteristics ---
    blob = " ".join(
        [str(s.get("name", "")) + " " + str(s.get("assessment_scope", "") or "") for s in skills]
        + [str(t.get("description", "")) for t in tasks]
        + [str(o) for o in outcomes]
    )
    forbidden = case.get("forbidden_terms") or PROTECTED_TERMS
    hit = mentions_any(blob, forbidden)
    g.add("no_protected_characteristics", not hit,
          f"repeated {hit!r} from the job description" if hit else "", critical=bool(hit))

    # --- did it find the role, or a different one? ---
    likes = expect.get("expect_skill_like") or []
    if likes:
        lowered = blob.lower()
        found = [k for k in likes if k in lowered]
        g.add("recognised_the_role", len(found) >= 2,
              f"matched {found}" if found else f"none of {likes[:5]} appeared")

    task_likes = expect.get("expect_task_like") or []
    if task_likes:
        task_blob = " ".join(str(t.get("description", "")) for t in tasks).lower()
        found = [k for k in task_likes if k in task_blob]
        g.add("tasks_match_the_jd", len(found) >= 2, f"matched {found}")

    # --- the check that matters: is it runnable? ---
    try:
        problems = _as_definition(output).validate()
    except Exception as exc:  # noqa: BLE001
        problems = [f"could not be built into a definition: {exc}"]
    g.add("builds_a_valid_definition", not problems, "; ".join(problems[:2]))

    # --- duration, when the model offered one ---
    lo, hi = (expect.get("duration_range") or [0, 10_000])
    if "recommended_duration_min" in output:
        duration = output["recommended_duration_min"]
        g.add("duration_sensible", lo <= duration <= hi,
              f"{duration} min, expected {lo}-{hi}")

    return g
