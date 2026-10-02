"""Scenario Builder API — describe a role-play in chat, configure it, publish it.

    POST /roleplay-builder/draft                 chat turn → draft (new or revised)
    POST /roleplay-builder/check                 a hand-edited draft → problems + Retell payload
    POST /roleplay-builder/save                  keep the working draft
    POST /roleplay-builder/publish               freeze a version the engine can run
    GET  /roleplay-builder/scenarios             everything built here
    GET  /roleplay-builder/scenarios/{id}        one, draft and published
    GET  /roleplay-builder/retell-prompt         the one generic Retell prompt
    GET  /roleplay-builder/knowledge             knowledge bases a scenario can ground on

Mounted under /api/recruiter behind `builder_scope`: the recruiter guard, plus
a local-prototype exception that is off by default. This is an authoring
surface: every response carries the scoring key, which is exactly what the
subject-facing /api/roleplay routes are built never to return.

The Retell payload is computed on every check so the configurer sees, while
editing, precisely what the voice agent will be told — and, by its absence,
what it will not.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services import config
from services.ai.workloads import scenario_builder as builder
from services.assessment import retell_export
from services.data import built_scenarios as store
from services.security import ratelimit

router = APIRouter(prefix="/roleplay-builder", tags=["roleplay-builder"])

#: Loopback addresses. A request from anywhere else never skips the sign-in.
_LOCAL = {"127.0.0.1", "::1", "localhost"}


def builder_scope(request: Request) -> None:
    """The recruiter guard, with one local-prototype exception.

    With `TARA_LOCAL_NO_LOGIN` on, outside production and staging, a request
    from this machine that carries no session is let through, so the page opens
    on the chat rather than a login form nobody has a password for. Every other
    request — a signed-in one, a remote one, any deployed one — meets
    `recruiter_scope` exactly as the rest of the recruiter namespace does.
    """
    from services.security import principal as security

    host = request.client.host if request.client else ""
    # TARA_BUILDER_OPEN (demo): the Agent Builder answers without a sign-in.
    if (config.BUILDER_OPEN and "/agent-builder" in request.url.path
            and security.optional_principal(request) is None):
        return None
    if (config.LOCAL_NO_LOGIN and not config.is_production() and host in _LOCAL
            and security.optional_principal(request) is None):
        return None
    security.recruiter_scope(request)
    return None


PROMPT_PATH = config.ROOT_DIR / "content" / "retell" / "generic_roleplay_prompt.md"


class Message(BaseModel):
    role: str = "user"
    text: str = Field(default="", max_length=6000)


class DraftBody(BaseModel):
    messages: list[Message]
    scenario: dict[str, Any] | None = None


class ScenarioBody(BaseModel):
    scenario: dict[str, Any]
    messages: list[Message] = Field(default_factory=list)


def _kb(scenario: dict[str, Any]):
    from services.api import roleplay

    kb_id = scenario.get("knowledge_base_id") or ""
    return roleplay._content()[1].get(kb_id) if kb_id else None


def _retell(scenario: dict[str, Any]) -> dict[str, Any] | None:
    try:
        defn = builder.to_definition(scenario)
        body = retell_export.web_call_body(
            defn, config.RETELL_ROLEPLAY_AGENT_ID or "<generic role-play agent id>", kb=_kb(scenario),
        )
    except (TypeError, ValueError, KeyError):
        return None
    body["leaked_cues"] = retell_export.leaked_cues(body, defn)
    return body


def _checked(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "problems": builder.problems(scenario),
        "retell": _retell(scenario),
    }


@router.post("/draft")
def draft(
    body: DraftBody,
    _: None = Depends(ratelimit.limiter("generation")),
) -> dict[str, Any]:
    if not body.messages or not body.messages[-1].text.strip():
        raise HTTPException(422, "Describe the role-play you want to build.")
    out = builder.draft([m.model_dump() for m in body.messages], body.scenario)
    return {**_checked(out["scenario"]), "reply": out["reply"],
            "changed": out["changed"], "drafted_by": out["drafted_by"]}


@router.post("/check")
def check(body: ScenarioBody) -> dict[str, Any]:
    return _checked(builder.normalise(body.scenario, previous=body.scenario))


@router.post("/save")
def save(body: ScenarioBody) -> dict[str, Any]:
    scenario = builder.normalise(body.scenario, previous=body.scenario)
    scenario["scenario_id"] = scenario["scenario_id"] or store.new_id(scenario["title"])
    row = store.load(scenario["scenario_id"]) or {"scenario_id": scenario["scenario_id"]}
    row["draft"] = scenario
    row["messages"] = [m.model_dump() for m in body.messages][-40:]
    store.save(row)
    return {**_checked(scenario), "saved": True}


@router.post("/publish")
def publish(body: ScenarioBody) -> dict[str, Any]:
    scenario = builder.normalise(body.scenario, previous=body.scenario)
    problems = builder.problems(scenario)
    if problems:
        raise HTTPException(422, {"problems": problems})
    scenario["scenario_id"] = scenario["scenario_id"] or store.new_id(scenario["title"])
    row = store.load(scenario["scenario_id"]) or {"scenario_id": scenario["scenario_id"]}
    version = int((row.get("published") or {}).get("version") or 0) + 1
    scenario["version"] = version
    row["draft"] = scenario
    row["published"] = scenario
    row["messages"] = [m.model_dump() for m in body.messages][-40:]
    store.save(row)

    from services.api import roleplay

    roleplay.register(builder.to_definition(scenario))
    return {
        **_checked(scenario),
        "published": True,
        "version": version,
        "play_url": f"/roleplay/practice?scenario={scenario['scenario_id']}",
    }


@router.get("/scenarios")
def list_built() -> dict[str, Any]:
    return {"scenarios": [
        {
            "scenario_id": r["scenario_id"],
            "title": (r.get("draft") or {}).get("title", ""),
            "agent_type": (r.get("draft") or {}).get("agent_type", ""),
            "purpose": (r.get("draft") or {}).get("purpose", ""),
            "published_version": (r.get("published") or {}).get("version", 0),
            "updated_at": r.get("updated_at", 0),
        }
        for r in store.list_all()
    ]}


@router.get("/scenarios/{scenario_id}")
def get_built(scenario_id: str) -> dict[str, Any]:
    row = store.load(scenario_id)
    if row is None:
        raise HTTPException(404, "no such scenario")
    return {**_checked(row["draft"]), "messages": row.get("messages", []),
            "published_version": (row.get("published") or {}).get("version", 0)}


@router.get("/retell-prompt")
def retell_prompt() -> dict[str, Any]:
    text = PROMPT_PATH.read_text()
    return {
        "prompt": text.split("\n---\n", 1)[-1].strip(),
        "variables": list(retell_export.VARIABLES),
        "begin_message": "{{opening_line}}",
        "agent_id_configured": bool(config.RETELL_ROLEPLAY_AGENT_ID),
    }


@router.get("/knowledge")
def knowledge_bases() -> dict[str, Any]:
    from services.api import roleplay

    return {"knowledge_bases": [
        {"kb_id": kb.kb_id, "name": kb.name, "description": kb.description,
         "sources": [{"id": s.id, "title": s.title, "passages": len(s.passages)} for s in kb.sources]}
        for kb in roleplay._content()[1].values()
    ]}
