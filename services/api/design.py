"""The recruiter's interview-creation flow.

    job details  →  AI Interview Designer  →  recommended interview  →  edits  →  draft

Mounted alongside the rest of the recruiter API, so these live at
`/api/recruiter/interviews/...` (and, for continuity, `/api/admin/...`).

Two properties this module exists to hold:

  * **Nothing here publishes.** Generation and every edit write the *draft*
    version. A candidate can only ever sit a published version, so no amount of
    designing or editing can reach someone mid-interview.

  * **Nothing invalid is persisted.** The designer's output passes schema
    validation, then mapping validation, then domain coercion, before anything
    is written. A generation that cannot be trusted becomes a retry state for
    the recruiter and an audit row for us — never a half-built assessment.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from packages.types import (
    DURATION_BANDS,
    SPEECH_RATE_RANGE,
    clamp_speech_rate,
    duration_for,
)
from packages.types.definition import INTERVIEW_TYPES, PRIORITY_RANK
from services.ai.workloads import interview_design
from services.ai.workloads.interview_design import Design, DesignError
from packages.types import new_id
from services.ai.workloads.interview_designer import Skill, Task
from services.api import questions as questions_api
from services.api import progress
from services.data import audit, interviews, jobs, skill_master, versions
from services.data.interviews import InterviewConfig
from services.data.jobs import SUPPORTED_LANGUAGES, ValidationError

from services.security import ratelimit  # noqa: E402

router = APIRouter(tags=["interview-design"])


def _who(request: Request):
    """The principal `recruiter_scope` verified for this request."""
    principal = getattr(request.state, "principal", None)
    if principal is None:  # pragma: no cover — the router guard runs first
        raise HTTPException(401, "Sign in to use the recruiter API.")
    return principal


# --------------------------------------------------------------------------- #
#  Request models
#
#  Pydantic types the payload; `services.data.jobs.validate` decides whether it
#  is acceptable. Keeping the rules there rather than in the annotations means
#  the same validation runs whether the call arrives from the form, a script, or
#  a regeneration replaying a stored job.
# --------------------------------------------------------------------------- #
class GenerateRequest(BaseModel):
    title: str = ""
    experience_from: Any = None
    experience_to: Any = None
    language: str = "en"
    #: Where in the hiring process this interview sits. Sets the starting
    #: interview type and difficulty — see `jobs.STAGE_SHAPE`.
    funnel_stage: str = "technical"
    job_description: str = ""
    additional_information: str = ""
    #: A client-generated id used only to look up progress on this generation.
    #: Not a credential and not an identifier of anything: the progress
    #: endpoint checks the caller's organization before answering. Blank means
    #: the client does not want progress, and reporting is skipped entirely.
    progress_token: str = ""


class SkillPatch(BaseModel):
    id: str
    name: str | None = None
    priority: str | None = None
    description: str | None = None
    assessment_scope: str | None = None
    #: A skill master domain id, or "" to clear it back to unset.
    domain: str | None = None
    #: Whether this skill is actually interviewed. The designer infers every
    #: skill the job description implies — fifteen or more for a senior role —
    #: and interviewing all of them in twenty minutes would mean one shallow
    #: question each. So inference and assessment are separate: `evaluated`
    #: is the smaller set that questions are written for, and the rest stay
    #: on the record as what the JD asked for.
    evaluated: bool | None = None
    #: 0-4, the level the role genuinely requires. Set from priority and
    #: seniority by `spread_targets`, and correctable here — a recruiter knows
    #: their own bar better than a band table does.
    proficiency_target: int | None = None


class TaskPatch(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None
    priority: str | None = None
    skills_assessed: list[str] | None = None   # skill ids


class DraftPatch(BaseModel):
    title: str | None = None
    #: The one shape decision the recruiter makes here. Difficulty and duration
    #: are NOT on this model, and their absence is the point: difficulty comes
    #: from the hiring stage and the duration comes from the type, so neither
    #: can be set to something that contradicts what it is derived from. An
    #: unknown field on a Pydantic model is ignored, so an old client sending
    #: either one changes nothing rather than half-applying.
    interview_type: str | None = None
    #: How fast Tara speaks, 0.75–1.1. Unlike duration and difficulty this IS
    #: the recruiter's to set: nothing else determines it, and the right pace
    #: for a role is a judgement about the candidates, not something derivable
    #: from the interview type.
    speech_rate: float | None = None
    skills: list[SkillPatch] | None = None
    tasks: list[TaskPatch] | None = None
    remove_skills: list[str] | None = Field(default=None)
    remove_tasks: list[str] | None = Field(default=None)


# --------------------------------------------------------------------------- #
#  Payloads
# --------------------------------------------------------------------------- #
def _skill_payload(cfg: InterviewConfig, skill: Skill) -> dict[str, Any]:
    return {
        "id": skill.competency_id,
        "name": skill.name,
        "priority": skill.priority,
        "description": skill.description,
        "assessment_scope": skill.assessment_scope,
        # The catalogued half of a skill. `domain_label` travels with it so the
        # review screen can render a chosen domain before the picker's
        # catalogue has loaded, and so an id that has since left the master
        # still shows as something rather than as a bare `dom_…`.
        "domain": skill.domain,
        "domain_label": skill_master.label_of(skill.domain),
        "evaluated": skill.evaluated,
        "proficiency_target": skill.proficiency_target,
    }


def _task_payload(cfg: InterviewConfig, task: Task) -> dict[str, Any]:
    by_id = {s.competency_id: s.name for s in cfg.skills}
    return {
        "id": task.ensure_id(),
        "name": task.label,
        "description": task.description,
        "priority": task.priority,
        "skills_assessed": [
            {"id": sid, "name": by_id.get(sid, sid)}
            for sid in task.required_skills
            if sid in by_id
        ],
    }


def _questions_by_skill(cfg: InterviewConfig) -> dict[str, int]:
    """skill id → how many questions are written for it."""
    counts: dict[str, int] = {}
    for raw in cfg.questions:
        skill_id = (raw.get("primary_skill_id") or raw.get("skill_id") or "") if isinstance(raw, dict) else ""
        if skill_id:
            counts[skill_id] = counts.get(skill_id, 0) + 1
    return counts


def _draft_payload(cfg: InterviewConfig) -> dict[str, Any]:
    """Everything the Recommended Interview screen renders."""
    job = jobs.get(cfg.job_id) if cfg.job_id else None
    skills = [_skill_payload(cfg, s) for s in cfg.skills]
    published = versions.latest_published(cfg.id)
    low, high = DURATION_BANDS.get(cfg.interview_type, (0, 0))
    return {
        "id": cfg.id,
        "title": cfg.title,
        "role_title": cfg.role_title or cfg.title,
        "status": cfg.status,
        "language": cfg.language,
        "language_label": SUPPORTED_LANGUAGES.get(cfg.language, cfg.language),
        "experience_from": cfg.experience_from,
        "experience_to": cfg.experience_to,
        "funnel_stage": cfg.funnel_stage,
        "funnel_stage_label": jobs.FUNNEL_STAGES.get(cfg.funnel_stage, ""),
        "job": {
            "id": job.id,
            "description": job.description,
            "additional_information": job.additional_information,
        } if job else None,
        "assessment": {
            "interview_type": cfg.interview_type,
            "difficulty": cfg.difficulty,
            "recommended_duration_min": cfg.recommended_duration_min,
            "duration_band": {"min": low, "max": high},
            "speech_rate": cfg.speech_rate,
            "speech_rate_range": {"min": SPEECH_RATE_RANGE[0], "max": SPEECH_RATE_RANGE[1]},
        },
        "rationale": cfg.design_rationale,
        "design_failed": cfg.design_failed,
        # False until the designer has actually run. The screen shows the retry
        # state on this, not on an empty skill list, which could equally mean a
        # recruiter deleted them all.
        "designed": bool(cfg.skills) and not cfg.design_failed,
        "skills": skills,
        # Derived from priority, never a second model call — a separate one
        # could disagree with the priorities it had just set.
        "high_priority_skill_ids": [s["id"] for s in skills if s["priority"] == "high"],
        # The set questions are written for and the interview actually probes.
        # Separate from the high-priority list because the two can legitimately
        # differ: a recruiter may add a medium-priority skill they care about,
        # or drop a high-priority one another round already covers.
        "evaluated_skill_ids": [s["id"] for s in skills if s["evaluated"]],
        "tasks": [_task_payload(cfg, t) for t in cfg.tasks],
        # Questions do not exist yet, and the screen says so rather than
        # showing a placeholder that could be mistaken for assessment content.
        # The questions themselves live on the pool screen; what the review
        # screen needs is whether they exist and how they are spread, because
        # a skill with none is a skill the interview cannot actually assess.
        "questions": [],
        "questions_generated": bool(cfg.questions),
        "question_count": len(cfg.questions),
        "questions_by_skill": _questions_by_skill(cfg),
        "published_version": published.version if published else 0,
        "created_at": cfg.created_at,
        "updated_at": cfg.updated_at,
    }


#: A safety net, not a target. The assessed set is the high-priority skills —
#: however many of those there are — and this only tops it up when the
#: designer marked almost nothing essential, because an interview that probes
#: one skill is a screening question. It never trims: three high-priority
#: skills means three, and eight means eight.
EVALUATED_FLOOR = 3


def _seed_evaluated(cfg: InterviewConfig) -> None:
    """Choose the starting assessed set from the inferred one.

    The designer infers every skill the JD implies — fifteen or more for a
    senior role — and that whole list is worth keeping: it is the evidence
    that the role was read properly, and the recruiter may well want one of
    the others. What it is not is an interview plan. So the high-priority
    skills become the assessed set, topped up by rank if the designer marked
    too few, and the rest stay on the record unassessed.

    Only called when nobody has chosen yet. Once a recruiter has edited the
    set, their choice stands — `_count_evaluated` recounts and refuses an
    empty set, and does not quietly add skills back.
    """
    ranked = sorted(
        cfg.skills,
        key=lambda s: (PRIORITY_RANK.get(s.priority, 2), s.proficiency_target),
        reverse=True,
    )
    for skill in cfg.skills:
        skill.evaluated = skill.priority == "high"
    floor = min(EVALUATED_FLOOR, len(cfg.skills))
    for skill in ranked:
        if sum(1 for s in cfg.skills if s.evaluated) >= floor:
            break
        skill.evaluated = True
    cfg.skills_evaluated = sum(1 for s in cfg.skills if s.evaluated)


def _count_evaluated(cfg: InterviewConfig) -> None:
    """Keep the stored count equal to the flags it is supposed to describe.

    Two sources of truth for "how many skills does this interview assess" is
    one too many, and the flags are the authoritative one — `evaluated_skills`
    reads them, and so do the blueprint and the question generator.
    """
    cfg.skills_evaluated = sum(1 for s in cfg.skills if s.evaluated)


def _persist_design(cfg: InterviewConfig, result: Design) -> InterviewConfig:
    """Write a validated design onto the draft.

    Skill ids are slugs of the skill name, so a task's `skills_assessed` (which
    the model gives as names) becomes a real foreign key. Collisions were already
    removed upstream, where duplicate names would have made the mapping
    ambiguous.
    """
    # Opaque ids, minted once. Derived-from-name ids were stable in practice
    # (nothing re-slugged on rename) but only by convention; an opaque id makes
    # it structural, and two similarly-named skills can no longer collide.
    by_name = {s.name: new_id("skl") for s in result.skills}
    cfg.skills = [
        Skill(
            name=s.name,
            competency_id=by_name[s.name],
            priority=s.priority,
            # Set properly by `_seed_evaluated` below, once every skill exists
            # and they can be ranked against each other.
            evaluated=False,
            description=s.description,
            assessment_scope=s.assessment_scope,
            # No question bank yet: nothing has been generated. The gap is real
            # and is shown to the recruiter rather than papered over.
            pool_competency="",
        )
        for s in result.skills
    ]
    cfg.tasks = [
        Task(
            id=new_id("tsk"),
            name=t.name,
            description=t.description,
            priority=t.priority,
            required_skills=[by_name[n] for n in t.skills_assessed if n in by_name],
        )
        for t in result.tasks
    ]
    # The recruiter's funnel stage outranks the designer's guess. They said
    # this is a prescreen; the model reading the JD has no way to know that,
    # and it was quietly proposing a 20-minute medium interview for all three
    # stages. The designer still decides everything else.
    shape = jobs.STAGE_SHAPE.get(cfg.funnel_stage)
    if shape:
        cfg.interview_type, cfg.difficulty = shape
    else:
        cfg.interview_type = result.interview_type
        cfg.difficulty = result.difficulty
    # Derived, not taken from the model and corrected. The designer does return
    # a `recommended_duration_min`, and it is discarded: the stage above may
    # have just moved the type to `short` under a number picked for a `deep`
    # interview, and a duration that has to be clamped back into its band was
    # never carrying information the type did not already carry.
    cfg.recommended_duration_min = duration_for(cfg.interview_type)
    cfg.design_rationale = result.rationale
    _seed_evaluated(cfg)
    cfg.extracted_at = time.time()
    interviews.save(cfg)
    versions.save_draft(cfg.id, interviews.build_definition(cfg), notes="Designed from the JD")
    return cfg


async def _run_designer(cfg: InterviewConfig, job: jobs.Job, event: str) -> InterviewConfig:
    """Call the designer, persist on success, audit either way."""
    audit.product(
        audit.INTERVIEW_GENERATION_STARTED,
        actor="recruiter", subject_type="interview", subject_id=cfg.id,
        job_id=job.id, title=job.title,
    )
    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(
            interview_design.design,
            title=job.title,
            experience_from=job.experience_from,
            experience_to=job.experience_to,
            language=job.language,
            job_description=job.description,
            additional_information=job.additional_information,
            session_id="_design",
        )
    except DesignError as exc:
        # The technical detail goes to the audit trail; the recruiter gets a
        # retry state. A raw provider error on screen is neither actionable nor
        # safe to show.
        cfg.design_failed = True
        interviews.save(cfg)
        audit.product(
            audit.INTERVIEW_GENERATION_FAILED,
            actor="recruiter", subject_type="interview", subject_id=cfg.id,
            job_id=job.id, error=str(exc)[:400],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        # No assessment structure was written — that is the part that must never
        # be half-persisted. The job details ARE kept, because making someone
        # re-paste a job description when a provider had a bad minute is a worse
        # trade than an interview sitting in the list waiting to be retried.
        raise HTTPException(
            502,
            detail={
                "message": (
                    "Tara couldn't build a recommendation from this job description. "
                    "Your job details have been saved — reopen the interview to try again."
                ),
                "interview_id": cfg.id,
                "retryable": True,
            },
        ) from exc

    cfg.design_failed = False
    cfg = _persist_design(cfg, result)
    audit.product(
        event,
        actor="recruiter", subject_type="interview", subject_id=cfg.id,
        job_id=job.id,
        skills=len(cfg.skills), tasks=len(cfg.tasks),
        high_priority=sum(1 for s in cfg.skills if s.priority == "high"),
        interview_type=cfg.interview_type, difficulty=cfg.difficulty,
        duration_min=cfg.recommended_duration_min,
        repairs=result.repairs,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return cfg


# --------------------------------------------------------------------------- #
#  Endpoints
# --------------------------------------------------------------------------- #
@router.post("/interviews/generate", status_code=201)
async def generate(request: Request, body: GenerateRequest,
    _: None = Depends(ratelimit.limiter("generation")),
) -> dict[str, Any]:
    """Job details in, a reviewable draft interview out.

    Creates the Job and the Interview, runs the designer, persists skills, tasks
    and their mapping, and writes the draft version. Publishes nothing.
    """
    try:
        clean = jobs.validate(
            title=body.title,
            experience_from=body.experience_from,
            experience_to=body.experience_to,
            language=body.language,
            job_description=body.job_description,
            additional_information=body.additional_information,
            funnel_stage=body.funnel_stage,
        )
    except ValidationError as exc:
        raise HTTPException(422, detail={"errors": exc.errors}) from exc

    who = _who(request)
    token = body.progress_token.strip()[:64]
    progress.start(token, who.organization_id, detail="Reading the job description")

    job = jobs.create(**clean, org_id=who.organization_id, created_by=who.user_id)
    audit.product(audit.JOB_CREATED, actor=who.user_id, org_id=who.organization_id,
                  subject_type="job", subject_id=job.id, title=job.title)

    cfg = interviews.create(
        job.title, role=clean["title"],
        organization_id=who.organization_id, created_by=who.user_id,
    )
    cfg.role_title = job.title
    cfg.job_id = job.id
    cfg.language = job.language
    cfg.jd_text = job.description
    cfg.additional_information = job.additional_information
    cfg.experience_from = job.experience_from
    cfg.experience_to = job.experience_to
    # The stage decides the shape: a prescreen is short and easy, an advanced
    # technical round is deep and hard. The recruiter can still move the type
    # on the review screen; the difficulty follows the stage, because "how hard
    # should this round push" is what choosing the round already answered.
    cfg.funnel_stage = job.funnel_stage
    shape = jobs.STAGE_SHAPE.get(job.funnel_stage)
    if shape:
        cfg.interview_type, cfg.difficulty = shape
        cfg.recommended_duration_min = duration_for(cfg.interview_type)
    interviews.save(cfg)
    audit.product(audit.INTERVIEW_CREATED, actor="recruiter", subject_type="interview",
                  subject_id=cfg.id, title=cfg.title, job_id=job.id)

    # The interview id is known from here on, so the client can navigate the
    # moment the work finishes rather than waiting for the response body.
    progress.update(
        token, stage="designing", interview_id=cfg.id,
        detail="Working out the skills and the tasks behind them",
    )
    cfg = await _run_designer(cfg, job, audit.INTERVIEW_GENERATED)

    # Questions too, in the same call. A recommendation without them is not an
    # interview anybody can look at and judge — it is a skill list plus a
    # button. The recruiter asked for an interview; this is the rest of it.
    #
    # Best-effort on purpose: if the generator fails, the design is already
    # saved and the review screen offers a retry. Losing a good design because
    # the second half of the work failed would be the worse trade.
    if not cfg.design_failed:
        try:
            await questions_api.write_questions(cfg, progress_token=token)
        except Exception as exc:  # noqa: BLE001 — the design must survive it
            audit.product(
                audit.QUESTION_GENERATION_FAILED, actor=who.user_id,
                org_id=who.organization_id, subject_type="interview",
                subject_id=cfg.id, error=f"{type(exc).__name__}",
            )
        cfg = interviews.get(cfg.id) or cfg

    progress.update(
        token,
        stage="failed" if cfg.design_failed else "complete",
        detail="Couldn't design this interview" if cfg.design_failed
               else f"{len(cfg.questions)} questions ready to review",
    )
    return _draft_payload(cfg)


# NOT `{token}`: that path-param name is reserved — `authz.RESOLVERS` maps it
# to an invitation and ownership-checks it, so this route 404'd on a string
# that was never an invitation in the first place. The guard was right; the
# name was wrong.
@router.get("/interviews/generate/progress/{progress_token}")
async def generation_progress(request: Request, progress_token: str) -> dict[str, Any]:
    """What the generation behind `token` is doing right now.

    Polled by the create screen while it waits. Answers `unknown` rather than
    404 for a token this worker has never seen, because the honest reading of
    that is "no news" — the generation is driven by the POST and is unaffected
    by whether anybody is watching. A client that treated it as failure would
    abandon a run that is still going perfectly well.
    """
    who = _who(request)
    entry = progress.get(progress_token, who.organization_id)
    if entry is None:
        return {"stage": "unknown", "detail": "", "done": 0, "total": 0,
                "interview_id": "", "elapsed_sec": 0}
    return entry.payload()


@router.post("/interviews/{interview_id}/regenerate")
async def regenerate(interview_id: str,
    _: None = Depends(ratelimit.limiter("generation")),
) -> dict[str, Any]:
    """Re-run the designer on the ORIGINAL job details.

    ⚠ This replaces the skills, tasks and assessment structure on the draft,
    including any edits the recruiter has made. It is destructive by design and
    the UI confirms before calling it — the alternative, merging a fresh design
    into hand-edited content, produces a result nobody chose.

    Refused once anything has been published, and that is not a UI nicety.
    The published version is immutable and candidates keep sitting it, so a
    regenerate would not change the live interview — it would leave the
    recruiter reading a screen that no longer describes it. The next publish
    then replaces a live assessment with a design nobody compared against the
    one it replaced, while candidates are part-way through the old one. If the
    role has genuinely changed, that is a new interview, not a new draft of
    this one.
    """
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    published = versions.latest_published(cfg.id)
    if published is not None:
        raise HTTPException(
            409,
            f"This interview is published (v{published.version}) and candidates "
            "may be sitting it. Regenerating would replace the design behind a "
            "live assessment. Create a new interview for the changed role.",
        )
    job = jobs.get(cfg.job_id) if cfg.job_id else None
    if job is None:
        raise HTTPException(
            409,
            "This interview has no job details behind it, so there is nothing to "
            "regenerate from. It predates the Create AI Interview flow.",
        )
    cfg = await _run_designer(cfg, job, audit.INTERVIEW_REGENERATED)
    return _draft_payload(cfg)


@router.patch("/interviews/{interview_id}/draft")
async def edit_draft(interview_id: str, body: DraftPatch) -> dict[str, Any]:
    """Controlled edits to the recommendation.

    A recruiter fixing a typo must not have to regenerate the interview, so
    every field on the review screen is individually editable. What they cannot
    do is leave the draft in an invalid relational state: a task may only assess
    a skill that exists, and removing a skill that a task still references is
    refused with the tasks named.
    """
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")

    errors: dict[str, str] = {}
    changed: list[str] = []

    if body.title is not None:
        title = body.title.strip()
        if not title:
            errors["title"] = "The title can't be empty."
        else:
            cfg.title = cfg.role_title = title
            changed.append("title")

    if body.interview_type is not None:
        if body.interview_type not in INTERVIEW_TYPES:
            errors["interview_type"] = f"Choose one of: {', '.join(INTERVIEW_TYPES)}."
        else:
            cfg.interview_type = body.interview_type
            # The duration is not carried over and clamped — it is replaced,
            # because it was never independent of the type in the first place.
            # Leaving a 40-minute duration on a newly-"short" interview is the
            # contradiction this exists to prevent.
            cfg.recommended_duration_min = duration_for(cfg.interview_type)
            changed.append("interview_type")

    if body.speech_rate is not None:
        low, high = SPEECH_RATE_RANGE
        if not low <= body.speech_rate <= high:
            errors["speech_rate"] = (
                f"Speaking pace runs from {low} to {high}. Outside that the "
                "delivery itself starts to affect how well someone can answer."
            )
        else:
            cfg.speech_rate = clamp_speech_rate(body.speech_rate)
            changed.append("speech_rate")

    by_id = {s.competency_id: s for s in cfg.skills}

    for patch in body.skills or []:
        skill = by_id.get(patch.id)
        if skill is None:
            errors[f"skills.{patch.id}"] = "That skill isn't part of this interview."
            continue
        if patch.name is not None:
            if not patch.name.strip():
                errors[f"skills.{patch.id}.name"] = "A skill needs a name."
            else:
                skill.name = patch.name.strip()
        if patch.priority is not None:
            if patch.priority not in PRIORITY_RANK:
                errors[f"skills.{patch.id}.priority"] = "Priority is high, medium or low."
            else:
                skill.priority = patch.priority
        if patch.description is not None:
            skill.description = patch.description.strip()
        if patch.assessment_scope is not None:
            skill.assessment_scope = patch.assessment_scope.strip()
        if patch.domain is not None:
            candidate = patch.domain.strip()
            if not skill_master.is_valid(candidate):
                # A domain that is not in the master is not a domain. Refusing
                # it here is the whole reason the field is catalogued rather
                # than free text — a typo'd domain aggregates with nothing.
                errors[f"skills.{patch.id}.domain"] = (
                    f"{candidate!r} is not a domain in the skill master."
                )
            else:
                skill.domain = candidate
        if patch.evaluated is not None:
            skill.evaluated = patch.evaluated
        if patch.proficiency_target is not None:
            if not 0 <= patch.proficiency_target <= 4:
                errors[f"skills.{patch.id}.proficiency_target"] = (
                    "Required proficiency runs 0 (novice) to 4 (expert)."
                )
            else:
                skill.proficiency_target = patch.proficiency_target
        changed.append(f"skill:{patch.id}")

    for patch in body.tasks or []:
        index = _task_index(cfg, patch.id)
        if index is None:
            errors[f"tasks.{patch.id}"] = "That task isn't part of this interview."
            continue
        task = cfg.tasks[index]
        if patch.name is not None:
            task.name = patch.name.strip()
        if patch.description is not None:
            if not patch.description.strip():
                errors[f"tasks.{patch.id}.description"] = "A task needs a description."
            else:
                task.description = patch.description.strip()
        if patch.priority is not None:
            if patch.priority not in PRIORITY_RANK:
                errors[f"tasks.{patch.id}.priority"] = "Priority is high, medium or low."
            else:
                task.priority = patch.priority
        if patch.skills_assessed is not None:
            unknown = [sid for sid in patch.skills_assessed if sid not in by_id]
            if unknown:
                errors[f"tasks.{patch.id}.skills_assessed"] = (
                    f"These skills aren't part of this interview: {', '.join(unknown)}."
                )
            elif not patch.skills_assessed:
                errors[f"tasks.{patch.id}.skills_assessed"] = (
                    "A task has to assess at least one skill, or it isn't assessing anything."
                )
            else:
                seen: set[str] = set()
                task.required_skills = [
                    s for s in patch.skills_assessed if not (s in seen or seen.add(s))
                ]
        changed.append(f"task:{patch.id}")

    if body.remove_skills:
        for skill_id in body.remove_skills:
            if skill_id not in by_id:
                errors[f"remove_skills.{skill_id}"] = "That skill isn't part of this interview."
                continue
            # Refuse rather than cascade. Silently stripping a skill out of three
            # tasks changes what those tasks assess, and the recruiter who
            # deleted one row would never know.
            blocking = [
                t.label for t in cfg.tasks
                if skill_id in t.required_skills and len(t.required_skills) == 1
            ]
            if blocking:
                errors[f"remove_skills.{skill_id}"] = (
                    f"These tasks assess only that skill and would be left assessing "
                    f"nothing: {', '.join(blocking[:3])}. Remap or remove them first."
                )
                continue
            cfg.skills = [s for s in cfg.skills if s.competency_id != skill_id]
            for task in cfg.tasks:
                task.required_skills = [s for s in task.required_skills if s != skill_id]
            changed.append(f"-skill:{skill_id}")

    if body.remove_tasks:
        for task_id in sorted(body.remove_tasks, reverse=True):
            index = _task_index(cfg, task_id)
            if index is None:
                errors[f"remove_tasks.{task_id}"] = "That task isn't part of this interview."
                continue
            cfg.tasks.pop(index)
            changed.append(f"-task:{task_id}")

    if errors:
        raise HTTPException(422, detail={"errors": errors})

    if not cfg.skills:
        raise HTTPException(
            422,
            detail={"errors": {"skills": "An interview needs at least one skill to assess."}},
        )

    # Inferred-but-unassessed is a legitimate state for a skill; inferred and
    # assessed by nothing is not an interview. Refused rather than silently
    # topped back up, because a recruiter who unticked the last one meant
    # something by it and should be told why it cannot stand.
    if not any(s.evaluated for s in cfg.skills):
        raise HTTPException(
            422,
            detail={"errors": {"skills": (
                "At least one skill has to be assessed. Everything else can stay "
                "inferred without being interviewed."
            )}},
        )

    _count_evaluated(cfg)
    interviews.save(cfg)
    versions.save_draft(cfg.id, interviews.build_definition(cfg), notes="Recruiter edit")
    audit.product(
        audit.INTERVIEW_EDITED, actor="recruiter", subject_type="interview",
        subject_id=cfg.id, changed=changed,
    )
    return _draft_payload(cfg)


def _task_index(cfg: InterviewConfig, task_id: str) -> int | None:
    """Resolve a task by its stable id.

    Positional ids are still accepted so a draft opened in a stale browser tab
    does not start editing the wrong task — the id is resolved against position
    only when it has the old shape and no stable id matches it.
    """
    for index, task in enumerate(cfg.tasks):
        if task.id == task_id:
            return index
    if task_id.startswith("task_"):
        try:
            index = int(task_id.split("_", 1)[1])
        except ValueError:
            return None
        return index if 0 <= index < len(cfg.tasks) else None
    return None


@router.get("/interviews/{interview_id}/draft")
async def read_draft(interview_id: str) -> dict[str, Any]:
    """Reopen a draft. This is what makes the work resumable."""
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    return _draft_payload(cfg)


@router.get("/interviews/{interview_id}/versions/{version}")
async def read_version(interview_id: str, version: int) -> dict[str, Any]:
    """One version's frozen definition. Version 0 is the working draft."""
    row = versions.get(interview_id, version)
    if row is None:
        raise HTTPException(404, f"No version {version} of that interview.")
    return {
        "interview_id": row.interview_id,
        "version": row.version,
        "status": row.status,
        "checksum": row.checksum,
        "published_at": row.published_at,
        "published_by": row.published_by,
        "notes": row.notes,
        "definition": row.definition,
    }


@router.get("/funnel-stages")
async def read_funnel_stages() -> dict[str, Any]:
    """The hiring stages an interview can be created for, and what each implies.

    Served rather than hard-coded in the form, for the same reason the language
    list is: a dropdown must never offer a stage the backend would reject.
    """
    return {
        "stages": [
            {
                "id": stage_id,
                "label": label,
                "interview_type": jobs.STAGE_SHAPE[stage_id][0],
                "difficulty": jobs.STAGE_SHAPE[stage_id][1],
            }
            for stage_id, label in jobs.FUNNEL_STAGES.items()
        ]
    }


@router.get("/skill-domains")
async def read_skill_domains() -> dict[str, Any]:
    """The skill master, for the domain picker.

    Read-only and the same for every organization: it is shipped content, not
    tenant data, which is why this route is authenticated but not
    organization-scoped.
    """
    return skill_master.catalogue()


@router.get("/languages")
async def read_languages() -> dict[str, Any]:
    """What the runtime can actually interview in.

    Served rather than hard-coded in the form, so the dropdown can never offer a
    language the interview cannot be conducted in.
    """
    return {"languages": [{"code": c, "label": l} for c, l in SUPPORTED_LANGUAGES.items()]}
