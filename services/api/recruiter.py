"""Recruiter-side API.

Separate router, separate `/api/admin` prefix, on purpose: the candidate app and
the recruiter console are different units with different audiences, and the
namespace is where authentication goes when this stops being a prototype.

⚠ KNOWN SECURITY GAP — there is NO authentication on this router. Anyone who can
reach the server can read every transcript. That is acceptable for local
development and unacceptable anywhere else. It is tracked, not forgotten:
`config.RECRUITER_AUTH_REQUIRED` exists so the gap is a visible setting, the
namespace is already separate so auth has exactly one place to go, and
BUILD_STATUS.md lists it as a release blocker.

What it deliberately does NOT expose: a score, a verdict, or a recommendation.
Scoring is a separate unit that this build doesn't include. What a reviewer gets
is evidence — the transcript, which cues the answers evidenced, and the full
decision trail — so a person can judge. That's the whole point of the split.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from packages.types import DefinitionError
from services import config
from services.ai.brain import LLMError
from services.ai.workloads import interview_designer as jd_extract
from services.ai.workloads.interview_designer import PROFICIENCY_LABELS, Skill
from services.data import audit, interviews, invites, versions
from services.data import sessions as store
from services.data.interviews import CompanyInfo, InterviewConfig
from services.data.jobs import SUPPORTED_LANGUAGES
from services.evaluation import analytics, scoring
from services.orchestrator.pool import Pool, get_pool
from services.orchestrator.state import SessionState
from services.security import authz
from services.security.principal import AuthenticatedPrincipal

# No prefix here on purpose: `services.api.app` mounts this router at
# /api/recruiter and, for continuity, at the /api/admin address the console
# shipped on. One router, two addresses, one implementation.
router = APIRouter(tags=["recruiter"])
pool = get_pool()


# --------------------------------------------------------------------------- #
#  Models
# --------------------------------------------------------------------------- #
class CreateInterview(BaseModel):
    title: str = ""
    role: str = config.ROLE


class ExtractRequest(BaseModel):
    """The recruiter's job details, and the JD to infer the rest from."""

    jd_text: str
    role_title: str = ""
    company_name: str = ""
    company_about: str = ""
    role_context: str = ""
    experience_from: int = Field(default=0, ge=0, le=50)
    experience_to: int = Field(default=0, ge=0, le=50)
    skills_evaluated: int = Field(default=6, ge=1, le=12)


class SkillPatch(BaseModel):
    competency_id: str
    name: str | None = None
    priority: str | None = None  # high | medium | low
    proficiency_target: int | None = Field(default=None, ge=0, le=4)
    evaluated: bool | None = None
    pool_competency: str | None = None


class UpdateInterview(BaseModel):
    title: str | None = None
    status: str | None = None
    role_title: str | None = None
    company_name: str | None = None
    company_about: str | None = None
    role_context: str | None = None
    experience_from: int | None = None
    experience_to: int | None = None
    jd_text: str | None = None
    skills: list[SkillPatch] | None = None
    remove_skills: list[str] | None = None
    add_skill: str | None = None
    skills_evaluated: int | None = None
    question_budget: int | None = None
    max_probes_per_item: int | None = None
    allow_generated_probes: bool | None = None


class CreateInvite(BaseModel):
    candidate_name: str
    interview_id: str
    ttl_days: int = Field(default=14, ge=1, le=365)


class TestRunRequest(BaseModel):
    """A dry run of the configured interview, with no candidate involved."""

    # "strong" answers substantively (few follow-ups); "thin" answers in
    # platitudes (probes on every item). Both are the same scripted answers the
    # test harness uses, so a test run reproduces a known result.
    persona: str = "strong"


class PublishRequest(BaseModel):
    notes: str = ""   # what changed, for the version history


class CompareRequest(BaseModel):
    session_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  The authored pool — what a recruiter has to choose from
# --------------------------------------------------------------------------- #
@router.get("/pool")
async def read_pool() -> dict[str, Any]:
    return {
        "role": pool.role,
        "role_title": pool.role_title,
        "competencies": [
            {"id": c.id, "label": c.label, "weight": c.weight, "min_items": c.min_items,
             "items": sum(1 for i in pool.items if i.competency == c.id)}
            for c in pool.competencies
        ],
        "items": [
            {
                "id": i.id,
                "type": i.type,
                "competency": i.competency,
                "competency_label": pool.competency_label(i.competency),
                "difficulty": i.difficulty,
                "prompt": i.prompt,
                "probe_eligible": i.probe_eligible,
                "probe_bank": i.probe_bank,
                "clarify": i.clarify,
                "looking_for": i.looking_for,
                "time_estimate_sec": i.time_estimate_sec,
            }
            for i in pool.items
        ],
    }


# --------------------------------------------------------------------------- #
#  Interviews
# --------------------------------------------------------------------------- #
def _who(request: Request) -> AuthenticatedPrincipal:
    """The principal the router's guard already verified.

    `recruiter_scope` runs as a router dependency and stashes it, so a handler
    reads it rather than re-deriving identity — one verification per request,
    and no handler that could forget to do it.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:  # pragma: no cover — the guard cannot be bypassed
        raise HTTPException(401, "Sign in to use the recruiter API.")
    return principal


def _interview_payload(cfg: InterviewConfig, with_preview: bool = False) -> dict[str, Any]:
    linked = [i for i in invites.list_all() if i.get("interview_id") == cfg.id]
    payload: dict[str, Any] = {
        **cfg.to_dict(),
        "pool_role_title": pool.role_title,
        "extracted": cfg.extracted,
        "proficiency_labels": PROFICIENCY_LABELS,
        "covered_count": len(cfg.covered_skills()),
        "uncovered_count": len(cfg.uncovered_skills()),
        "published_version": cfg.published_version,
        "language": cfg.language,
        "language_label": SUPPORTED_LANGUAGES.get(cfg.language, cfg.language),
        "role_title": cfg.role_title or cfg.title,
        "experience_from": cfg.experience_from,
        "experience_to": cfg.experience_to,
        "interview_type": cfg.interview_type,
        "difficulty": cfg.difficulty,
        "recommended_duration_min": cfg.recommended_duration_min,
        "skill_count": len(cfg.skills),
        "high_priority_count": sum(1 for s in cfg.skills if s.priority == "high"),
        "task_count": len(cfg.tasks),
        # Questions do not exist until the Question Generator phase. Reported as
        # a number rather than implied, so the overview cannot look like it has
        # assessment content it does not have.
        "question_count": 0,
        "designed": bool(cfg.skills) and bool(cfg.job_id),
        "versions": len(versions.list_for(cfg.id)),
        # True when the draft has moved on from what candidates are sitting, so
        # the console can say "you have unpublished changes" rather than leaving
        # a recruiter to guess whether their edit is live.
        "has_unpublished_changes": _has_unpublished_changes(cfg),
        "candidates": {
            "total": len(linked),
            "pending": sum(1 for i in linked if i["status"] == "pending"),
            "in_progress": sum(1 for i in linked if i["status"] == "in_progress"),
            "complete": sum(1 for i in linked if i["status"] == "complete"),
        },
    }
    if with_preview:
        plan = pool.plan(cfg)
        order = pool.running_order(plan)
        by_comp: dict[str, int] = {}
        for item in order:
            by_comp[item.competency] = by_comp.get(item.competency, 0) + 1

        payload["preview"] = {
            "items": [
                {
                    "id": i.id,
                    "position": n + 1,
                    "competency": i.competency,
                    "competency_label": pool.competency_label(i.competency),
                    "skills": [
                        s.name for s in cfg.evaluated_skills() if s.pool_competency == i.competency
                    ],
                    "difficulty": i.difficulty,
                    "prompt": i.prompt,
                    "probe_eligible": i.probe_eligible,
                }
                for n, i in enumerate(order)
            ],
            "estimated_minutes": _estimate_minutes(order, cfg),
            "skills": [
                {
                    "competency_id": s.competency_id,
                    "name": s.name,
                    "priority": s.priority,
                    "proficiency_target": s.proficiency_target,
                    "pool_competency": s.pool_competency,
                    "pool_competency_label": (
                        pool.competency_label(s.pool_competency) if s.pool_competency else ""
                    ),
                    "questions_planned": by_comp.get(s.pool_competency, 0) if s.pool_competency else 0,
                }
                for s in cfg.evaluated_skills()
            ],
            "warnings": _warnings(cfg, order),
        }
    return payload


def _has_unpublished_changes(cfg: InterviewConfig) -> bool:
    """Does the draft differ from the published contract candidates are sitting?

    Compared by content checksum rather than by timestamp: a recruiter who opens
    the builder, changes nothing, and leaves has not created a pending change.
    """
    published = versions.latest_published(cfg.id)
    if published is None:
        return bool(cfg.skills)
    try:
        return interviews.build_definition(cfg, pool).checksum() != published.checksum
    except Exception:  # noqa: BLE001 — a half-built draft is simply "changed"
        return True


def _estimate_minutes(order, cfg: InterviewConfig) -> int:
    seconds = sum(i.time_estimate_sec for i in order)
    # Assume roughly half the items draw one follow-up in a typical interview.
    seconds += len(order) * 0.5 * cfg.max_probes_per_item * 45
    return max(5, round((seconds + 90) / 60))  # +90s for the greeting and close


def _warnings(cfg: InterviewConfig, order) -> list[str]:
    """What a recruiter should see before publishing, in plain language."""
    out: list[str] = []
    if not cfg.extracted:
        out.append("No job description analysed yet — add one to work out what this role needs.")
        return out
    if not order:
        out.append(
            "No questions will be asked. None of the skills you're evaluating has a question "
            "bank behind it."
        )
        return out

    uncovered = cfg.uncovered_skills()
    if uncovered:
        names = ", ".join(s.name for s in uncovered[:3])
        more = f" and {len(uncovered) - 3} more" if len(uncovered) > 3 else ""
        out.append(
            f"{len(uncovered)} skill{'s' if len(uncovered) > 1 else ''} you're evaluating "
            f"({names}{more}) has no authored questions, so it won't be assessed. "
            f"Either drop it from the evaluated set or map it to a question bank."
        )

    asked: dict[str, int] = {}
    for item in order:
        asked[item.competency] = asked.get(item.competency, 0) + 1

    wanted = cfg.pool_min_items()
    short: list[str] = []
    for s in cfg.covered_skills():
        got = asked.get(s.pool_competency, 0)
        if got == 0:
            out.append(
                f"{s.name} won't come up at all — the question budget runs out before it does. "
                f"Raise the budget or evaluate fewer skills."
            )
        elif got < wanted.get(s.pool_competency, 1):
            short.append(s.name)

    if short:
        # High-priority skills ask for two questions each. If the recruiter marks
        # everything high, the floors can exceed the budget — say so with the
        # arithmetic, because "raise the budget" alone doesn't tell them by how much.
        need = sum(wanted.values())
        out.append(
            f"{', '.join(short[:3])}{' and others' if len(short) > 3 else ''} "
            f"get one question instead of two. These skills want {need} questions in total but "
            f"the budget is {cfg.question_budget} — raise it, or drop some skills to medium "
            f"priority so they only need one each."
        )

    if len(order) < cfg.question_budget:
        out.append(
            f"The budget is {cfg.question_budget} questions but only {len(order)} are available "
            f"from the banks these skills map to."
        )
    if cfg.question_budget < 4:
        out.append("Under four questions is a very short interview — coverage will be thin.")
    if not cfg.allow_generated_probes:
        out.append(
            "Follow-ups are limited to the authored bank. Tara will still probe, but the "
            "questions won't adapt to what the candidate actually said."
        )
    return out


@router.get("/interviews")
async def list_interviews(
    request: Request, q: str = "",
) -> dict[str, Any]:
    """The AI Interviews overview, for one organization.

    `q` searches job title and role title. Filtering happens here rather than in
    the browser because the list is the recruiter's whole working set and will
    outgrow a page long before it outgrows a query.

    The organization filter is the other half of tenant isolation: an ownership
    check on the detail route means nothing if the list hands out every id.
    """
    rows = authz.visible_interviews(_who(request))
    needle = (q or "").strip().lower()
    if needle:
        rows = [
            c for c in rows
            if needle in (c.title or "").lower() or needle in (c.role_title or "").lower()
        ]
    return {
        "interviews": [_interview_payload(c) for c in rows],
        "query": q,
        "total": len(authz.visible_interviews(_who(request))),
    }


@router.post("/interviews", status_code=201)
async def create_interview(request: Request, body: CreateInterview) -> dict[str, Any]:
    who = _who(request)
    cfg = interviews.create(
        body.title, body.role,
        organization_id=who.organization_id, created_by=who.user_id,
    )
    audit.product(
        audit.INTERVIEW_CREATED, actor=who.user_id, org_id=who.organization_id,
        subject_type="interview", subject_id=cfg.id, title=cfg.title, role=cfg.role,
    )
    return _interview_payload(cfg, with_preview=True)


@router.get("/interviews/{interview_id}")
async def read_interview(interview_id: str) -> dict[str, Any]:
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    return _interview_payload(cfg, with_preview=True)


@router.post("/interviews/{interview_id}/extract")
async def extract_from_jd(interview_id: str, body: ExtractRequest) -> dict[str, Any]:
    """Analyse the job description: outcomes → tasks → skills.

    Overwrites any previous extraction, so re-running after editing the JD gives
    a clean read rather than merging two analyses. Recruiter edits made after
    this point survive because they're saved through PATCH, not here.
    """
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    if not body.jd_text.strip():
        raise HTTPException(400, "Paste a job description first — that's what the analysis reads.")

    cfg.jd_text = body.jd_text
    cfg.role_title = body.role_title.strip() or cfg.role_title
    cfg.company = CompanyInfo(
        name=body.company_name.strip(),
        about=body.company_about.strip(),
        role_context=body.role_context.strip(),
    )
    cfg.experience_from = body.experience_from
    cfg.experience_to = body.experience_to
    cfg.skills_evaluated = body.skills_evaluated

    try:
        outcomes, tasks, skills = await asyncio.to_thread(
            jd_extract.extract,
            body.jd_text,
            cfg.role_title or pool.role_title,
            cfg.company.name,
            cfg.company.about,
            cfg.company.role_context,
            body.experience_to,
            body.skills_evaluated,
            pool,
        )
    except LLMError as exc:
        raise HTTPException(
            503,
            "Couldn't analyse the job description — the language model is unavailable. "
            f"({exc})",
        ) from exc

    cfg.outcomes = outcomes
    cfg.tasks = tasks
    cfg.skills = skills
    cfg.extracted_at = time.time()
    interviews.save(cfg)
    audit.product(
        audit.INTERVIEW_GENERATED, actor="recruiter",
        subject_type="interview", subject_id=cfg.id,
        skills=len(skills), tasks=len(tasks), outcomes=len(outcomes),
        uncovered=len(cfg.uncovered_skills()),
    )
    return _interview_payload(cfg, with_preview=True)


@router.patch("/interviews/{interview_id}")
async def update_interview(interview_id: str, body: UpdateInterview) -> dict[str, Any]:
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")

    if body.title is not None:
        cfg.title = body.title.strip() or cfg.title
    if body.status == "draft":
        # Unpublishing stops new invitations. It does NOT retract published
        # versions: candidates already sitting one keep the interview they
        # started, which is the whole point of freezing it.
        cfg.status = "draft"
    elif body.status == "published":
        # Publishing is a version event, not a field assignment. Route it
        # through the endpoint that freezes a contract and writes the audit row.
        raise HTTPException(
            400,
            "Publish through POST /interviews/{id}/publish — publishing freezes a "
            "version, and setting a flag would leave candidates running a draft "
            "that can still change under them.",
        )
    if body.role_title is not None:
        cfg.role_title = body.role_title.strip()
    if body.jd_text is not None:
        cfg.jd_text = body.jd_text
    if body.company_name is not None:
        cfg.company.name = body.company_name.strip()
    if body.company_about is not None:
        cfg.company.about = body.company_about.strip()
    if body.role_context is not None:
        cfg.company.role_context = body.role_context.strip()
    if body.experience_from is not None:
        cfg.experience_from = max(0, min(50, body.experience_from))
    if body.experience_to is not None:
        cfg.experience_to = max(0, min(50, body.experience_to))

    # --- skill edits: the "TARA inferred it, I adjusted it" half ---
    valid_banks = {c.id for c in pool.competencies}
    if body.skills:
        for patch in body.skills:
            skill = cfg.skill(patch.competency_id)
            if skill is None:
                continue
            if patch.name is not None and patch.name.strip():
                skill.name = patch.name.strip()
            if patch.priority in jd_extract.PRIORITY_RANK:
                skill.priority = patch.priority
            if patch.proficiency_target is not None:
                skill.proficiency_target = patch.proficiency_target
            if patch.evaluated is not None:
                skill.evaluated = patch.evaluated
            if patch.pool_competency is not None:
                skill.pool_competency = (
                    patch.pool_competency if patch.pool_competency in valid_banks else ""
                )

    if body.remove_skills:
        drop = set(body.remove_skills)
        cfg.skills = [s for s in cfg.skills if s.competency_id not in drop]
        for task in cfg.tasks:
            task.required_skills = [r for r in task.required_skills if r not in drop]

    if body.add_skill and body.add_skill.strip():
        name = body.add_skill.strip()
        cid = jd_extract.slug(name)
        if not cfg.skill(cid):
            new = Skill(name=name, competency_id=cid, priority="medium", evaluated=True)
            jd_extract.map_to_pool([new], pool)
            cfg.skills.append(new)

    if body.skills_evaluated is not None:
        cfg.skills_evaluated = max(1, min(12, body.skills_evaluated))
    if body.question_budget is not None:
        cfg.question_budget = max(1, min(body.question_budget, len(pool.items)))
    if body.max_probes_per_item is not None:
        cfg.max_probes_per_item = max(0, min(body.max_probes_per_item, 4))
    if body.allow_generated_probes is not None:
        cfg.allow_generated_probes = body.allow_generated_probes

    interviews.save(cfg)
    audit.product(
        audit.INTERVIEW_EDITED, actor="recruiter",
        subject_type="interview", subject_id=cfg.id,
        fields=[k for k, v in body.model_dump(exclude_none=True).items()],
    )
    return _interview_payload(cfg, with_preview=True)


@router.post("/interviews/{interview_id}/publish")
async def publish_interview(interview_id: str, body: PublishRequest) -> dict[str, Any]:
    """Freeze this configuration as the next published version.

    Everything the interview will ask, and everything it will be judged on, is
    copied into an immutable version here. Candidates invited afterwards sit
    that version; candidates already sitting an earlier one are untouched.

    Re-publishing an unchanged configuration returns the existing version rather
    than minting a duplicate — pressing the button twice has not created a
    second interview.
    """
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    try:
        version = interviews.publish(cfg, pool, published_by="recruiter", notes=body.notes)
    except DefinitionError as exc:
        # Everything wrong at once, in the recruiter's language. Publish time is
        # the right moment to find this out; a candidate on the line is not.
        raise HTTPException(400, f"This interview can't be published yet. {exc}") from exc

    payload = _interview_payload(interviews.get(interview_id), with_preview=True)
    payload["published_version"] = version.version
    return payload


@router.post("/interviews/{interview_id}/reset_skills")
async def reset_skills(interview_id: str) -> dict[str, Any]:
    """Put priorities and proficiency targets back to what TARA inferred.

    An undo for the review screen. Without it, a recruiter who has adjusted ten
    sliders has no way back short of re-running the whole analysis.
    """
    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")
    if not cfg.skills:
        raise HTTPException(400, "Nothing to reset — no job description has been analysed yet.")
    jd_extract.mark_evaluated(cfg.skills, cfg.skills_evaluated)
    jd_extract.spread_targets(cfg.skills, cfg.experience_to)
    interviews.save(cfg)
    return _interview_payload(cfg, with_preview=True)


@router.delete("/interviews/{interview_id}")
async def delete_interview(interview_id: str) -> dict[str, Any]:
    linked = [i for i in invites.list_all() if i.get("interview_id") == interview_id]
    if any(i["status"] != "pending" for i in linked):
        raise HTTPException(
            409,
            "Candidates have already started this interview. Unpublish it instead of deleting — "
            "deleting would orphan their transcripts.",
        )
    if not interviews.delete(interview_id):
        raise HTTPException(404, "No such interview.")
    return {"deleted": interview_id}


# --------------------------------------------------------------------------- #
#  Candidates
# --------------------------------------------------------------------------- #
def _candidate_row(raw: dict[str, Any]) -> dict[str, Any]:
    state = store.try_load(raw["session_id"]) if raw.get("session_id") else None
    answered = sum(1 for r in state.records.values() if r.closed_at) if state else 0
    return {
        **raw,
        "link": f"/?invite={raw['token']}",
        "answered": answered,
        "asked": len(state.asked_item_ids) if state else 0,
        "last_activity": state.updated_at if state else raw.get("created_at"),
        "duration_sec": (
            round((state.completed_at or state.updated_at) - state.created_at) if state else None
        ),
    }


@router.get("/candidates")
async def list_candidates(
    request: Request, interview_id: str | None = None
) -> dict[str, Any]:
    """Everyone invited to this organization's interviews, and nobody else's.

    An invitation carries no tenant id of its own; it is owned by the interview
    it belongs to, so the filter is over the interviews this principal can see.
    """
    mine = authz.visible_interview_ids(_who(request))
    rows = [r for r in invites.list_all() if r.get("interview_id") in mine]
    if interview_id:
        rows = [r for r in rows if r.get("interview_id") == interview_id]
    rows = [_candidate_row(r) for r in rows]
    rows.sort(key=lambda r: r.get("last_activity") or 0, reverse=True)
    return {"candidates": rows}


@router.post("/candidates", status_code=201)
async def create_candidate(request: Request, body: CreateInvite) -> dict[str, Any]:
    """Invite one candidate, naming the interview in the body.

    Which is why ownership is resolved here: the router's guard walks path
    parameters, and this id never appears in the path. Unchecked, it would let
    any signed-in recruiter mint a working invitation into another
    organization's interview.
    """
    who = _who(request)
    cfg = authz.interview(who, body.interview_id)
    if cfg.status != "published":
        raise HTTPException(
            409, "Publish the interview before inviting candidates — a draft can still change."
        )
    published = versions.latest_published(cfg.id)
    if published is None:
        raise HTTPException(
            409,
            "This interview has no published version behind it. Publish it once, so the "
            "candidate is invited to a fixed set of questions rather than a moving one.",
        )
    if not body.candidate_name.strip():
        raise HTTPException(400, "A candidate name is required — the link greets them by it.")
    invite = invites.create(
        body.candidate_name, cfg.role, cfg.id, body.ttl_days,
        interview_version=published.version,
    )
    audit.product(
        audit.INVITATION_CREATED,
        actor=who.user_id,
        org_id=who.organization_id,
        subject_type="invitation",
        subject_id=invite.token,
        interview_id=cfg.id,
        version=published.version,
    )
    return _candidate_row(asdict(invite))


# --------------------------------------------------------------------------- #
#  Session review — the evidence, not a verdict
# --------------------------------------------------------------------------- #
@router.get("/sessions/{session_id}")
async def read_session(session_id: str) -> dict[str, Any]:
    state = store.try_load(session_id)
    if state is None:
        raise HTTPException(404, "No such session.")

    cfg = interviews.get(state.interview_id) if state.interview_id else None

    # A review must reconstruct the interview the candidate ACTUALLY sat, not
    # whatever the interview looks like now. Reviewing v1 answers against v2's
    # questions is how a reviewer ends up reading an exchange that never
    # happened.
    defn = (
        versions.definition_for(state.interview_id, state.interview_version)
        if state.interview_version
        else None
    )
    if defn is not None:
        session_pool = Pool.from_definition(defn)
        plan = session_pool.plan_from_definition(defn)
    else:
        session_pool = pool
        plan = pool.plan(cfg)

    def skills_for(competency: str) -> list[str]:
        if defn is not None:
            return [s.name for s in defn.skills_for_bank(competency)]
        if not cfg:
            return []
        return [s.name for s in cfg.evaluated_skills() if s.pool_competency == competency]

    items = []
    for item_id in state.asked_item_ids:
        record = state.records.get(item_id)
        if record is None:
            continue
        item = session_pool.item(item_id)
        # Interleave what was asked with what was said, so a reviewer reads the
        # exchange in order instead of reassembling it from two lists.
        exchange: list[dict[str, Any]] = [{"role": "question", "text": item.prompt}]
        for n, answer in enumerate(record.answers):
            exchange.append({"role": "answer", "text": answer})
            if n < len(record.probes_asked):
                exchange.append({"role": "probe", "text": record.probes_asked[n]})
        items.append({
            "item_id": item_id,
            "competency": item.competency,
            "competency_label": session_pool.competency_label(item.competency),
            "skills": skills_for(item.competency),
            "difficulty": item.difficulty,
            "prompt": item.prompt,
            "exchange": exchange,
            "probes_asked": record.probes_asked,
            "reasks": record.reasks,
            "clarifies": record.clarifies,
            "evidenced": record.covered,
            "not_evidenced": record.missing,
            "looking_for": item.looking_for,
            "answered": bool(record.answers),
            "closed": bool(record.closed_at),
            "seconds": (round(record.closed_at - record.asked_at) if record.closed_at else None),
        })

    return {
        "session_id": state.session_id,
        "candidate_name": state.candidate_name,
        "interview_id": state.interview_id,
        "interview_title": cfg.title if cfg else None,
        "interview_version": state.interview_version,
        "role_title": (defn.role_title if defn else (cfg.role_title if cfg and cfg.role_title else pool.role_title)),
        "phase": state.phase,
        "channel": state.channel,
        "accommodations": state.accommodations,
        "consent_recording": state.consent_recording,
        "started_at": state.created_at,
        "completed_at": state.completed_at,
        "duration_sec": round((state.completed_at or state.updated_at) - state.created_at),
        "coverage": session_pool.coverage(state.asked_item_ids, plan),
        "items": items,
        "transcript": [
            {"speaker": u.speaker, "text": u.text, "at": u.at, "kind": u.kind}
            for u in state.transcript
        ],
    }


@router.get("/sessions/{session_id}/trail")
async def read_trail(session_id: str) -> dict[str, Any]:
    """The decision trail: what was chosen, what was generated, what was blocked.

    This is the auditability story. Every item selection, every generated probe
    with its guardrail verdict, every fallback, every silence and unanswered
    item — appended, never rewritten.
    """
    events = store.read_audit(session_id)
    if not events:
        raise HTTPException(404, "No trail for that session.")
    return {
        "session_id": session_id,
        "events": events,
        "summary": {
            "items_selected": sum(1 for e in events if e["event"] == "item_selected"),
            "probes_generated": sum(1 for e in events if e["event"] == "probe_generated"),
            "probes_blocked": sum(
                1
                for e in events
                if e["event"] == "probe_generated" and not (e.get("guardrail") or {}).get("ok", True)
            ),
            "probes_from_bank": sum(1 for e in events if e["event"] == "probe_fallback"),
            "silences": sum(1 for e in events if e["event"] == "silence"),
            "repeats": sum(1 for e in events if e["event"] == "repeated"),
            "clarifications": sum(1 for e in events if e["event"] == "clarified"),
            "unanswered": sum(1 for e in events if e["event"] == "item_unanswered"),
        },
    }


# --------------------------------------------------------------------------- #
#  Score — the assessment engine's view of one session
# --------------------------------------------------------------------------- #
@router.get("/sessions/{session_id}/score")
async def read_score(session_id: str) -> dict[str, Any]:
    """Levels, confidence, and a band — for a person to read, never to act on
    automatically. See scoring.py for what the engine refuses to look at."""
    state = store.try_load(session_id)
    if state is None:
        raise HTTPException(404, "No such session.")
    return scoring.score_session(state, pool).to_dict()


# --------------------------------------------------------------------------- #
#  Results, comparison, fairness
# --------------------------------------------------------------------------- #
@router.get("/interviews/{interview_id}/results")
async def read_results(interview_id: str) -> dict[str, Any]:
    if interviews.get(interview_id) is None:
        raise HTTPException(404, "No such interview.")
    return analytics.results(pool, interview_id)


@router.post("/compare")
async def compare(request: Request, body: CompareRequest) -> dict[str, Any]:
    """Compare candidates — after checking that all of them are ours.

    The session ids arrive in the body, which no path-parameter guard can see,
    so this is one of the few handlers that has to ask for itself. A single
    foreign id refuses the whole request rather than being dropped quietly: a
    comparison missing a candidate the caller asked for is worse than an error.
    """
    if len(body.session_ids) < 2:
        raise HTTPException(400, "Pick at least two candidates to compare.")
    who = _who(request)
    for session_id in body.session_ids:
        authz.session(who, session_id)
    return analytics.compare(pool, body.session_ids)


@router.get("/fairness")
async def read_fairness(
    request: Request, interview_id: str | None = None
) -> dict[str, Any]:
    """The fairness view. Aggregate, and still organization-scoped.

    `interview_id` arrives as a QUERY parameter, which the router's ownership
    guard cannot see — it reads path parameters. So this handler resolves it
    itself. Without that, `?interview_id=` on someone else's interview would
    return their blocked probes, candidate names and all.

    No interview named: the aggregate is computed over this organization's
    sessions only, because an average across tenants is both a leak and
    meaningless.
    """
    who = _who(request)
    if interview_id:
        authz.interview(who, interview_id)
        return analytics.fairness(pool, interview_id)
    session_ids = [s.session_id for s in authz.visible_sessions(who)]
    return analytics.fairness(pool, None, session_ids=session_ids)


# --------------------------------------------------------------------------- #
#  Test run — try the interview before anyone is invited
# --------------------------------------------------------------------------- #
@router.post("/interviews/{interview_id}/test_run")
async def test_run(interview_id: str, body: TestRunRequest) -> dict[str, Any]:
    """Run the whole configured interview against a scripted candidate.

    The point is not the transcript — it is that a recruiter can see what their
    configuration actually does end to end (including how often TARA probes and
    what a resulting score looks like) without exposing a real candidate to a
    configuration nobody has tried.

    It runs through the real orchestrator on a throwaway session, then deletes
    it, so a test run never appears in results or candidate counts.
    """
    import uuid

    from services.orchestrator.engine import Orchestrator
    from tests.personas import PERSONAS, PROBE_REPLIES

    cfg = interviews.get(interview_id)
    if cfg is None:
        raise HTTPException(404, "No such interview.")

    persona = body.persona if body.persona in PERSONAS else "strong"
    answers = PERSONAS[persona]
    probe_replies = PROBE_REPLIES[persona]

    orch = Orchestrator()
    state = SessionState.new(
        candidate_name="Test run",
        candidate_id="test_" + uuid.uuid4().hex[:6],
        role=cfg.role,
        invite_token="",
        interview_id=cfg.id,
        # Deliberately version 0: a test run exercises the DRAFT on screen, which
        # is the entire reason to run one before publishing.
        interview_version=0,
    )
    state.consent_recording = True
    state.channel = "text"

    transcript: list[dict[str, Any]] = []
    reply = await asyncio.to_thread(orch.start, state)
    transcript.append({"speaker": "tara", "kind": reply.kind, "text": reply.text})

    probe_i = 0
    turns = 0
    while not reply.ends and turns < 40:
        if reply.kind == "probe":
            said = probe_replies[probe_i % len(probe_replies)]
            probe_i += 1
        else:
            said = answers.get(reply.item_id or "", "I'd handle it the way I described.")
        transcript.append({"speaker": "candidate", "kind": None, "text": said})
        reply = await asyncio.to_thread(orch.on_answer, state, said)
        transcript.append({"speaker": "tara", "kind": reply.kind, "text": reply.text})
        turns += 1

    score = scoring.score_session(state, pool)
    probes = sum(1 for t in transcript if t["kind"] == "probe")

    # Throw the session away. A dry run must never show up as a candidate.
    for path in (
        config.SESSION_DIR / f"{state.session_id}.json",
        config.AUDIT_DIR / f"{state.session_id}.jsonl",
    ):
        path.unlink(missing_ok=True)

    return {
        "persona": persona,
        "turns": turns,
        "questions_asked": len(state.asked_item_ids),
        "follow_ups": probes,
        "transcript": transcript,
        "score": score.to_dict(),
    }


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    """The numbers on the console's landing page, for one organization."""
    who = _who(request)
    all_interviews = authz.visible_interviews(who)
    mine = {c.id for c in all_interviews}
    all_invites = [i for i in invites.list_all() if i.get("interview_id") in mine]
    complete = [i for i in all_invites if i["status"] == "complete"]

    durations = []
    for row in complete:
        state = store.try_load(row["session_id"]) if row.get("session_id") else None
        if state and state.completed_at:
            durations.append(state.completed_at - state.created_at)

    return {
        "interviews": len(all_interviews),
        "published": sum(1 for c in all_interviews if c.status == "published"),
        "candidates": len(all_invites),
        "in_progress": sum(1 for i in all_invites if i["status"] == "in_progress"),
        "complete": len(complete),
        "not_started": sum(1 for i in all_invites if i["status"] == "pending"),
        "median_minutes": (
            round(sorted(durations)[len(durations) // 2] / 60) if durations else None
        ),
        "llm": config.llm_is_live(),
        "generated_at": time.time(),
    }
