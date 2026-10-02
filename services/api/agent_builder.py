"""Agent Builder API: brief → agent → edit → test on Retell → publish.

    GET  /agent-builder/options                     voices, choices, what is configured
    POST /agent-builder/drafts                      a brief → a new agent (server-sent events)
    GET  /agent-builder/agents                      this organization's agents
    GET  /agent-builder/agents/{agent_id}           one agent, with its readiness and call config
    PUT  /agent-builder/agents/{agent_id}           save the review screen
    POST /agent-builder/agents/{agent_id}/revise    "Ask Tara to change something"
    POST /agent-builder/agents/{agent_id}/questions/generate   three more questions
    POST /agent-builder/agents/{agent_id}/test-chat            a typed rehearsal, on the real prompt
    POST /agent-builder/agents/{agent_id}/test-call            a Retell web call → access token
    POST /agent-builder/agents/{agent_id}/score                score a test against the rubric
    POST /agent-builder/agents/{agent_id}/publish              freeze a version
    POST /agent-builder/agents/{agent_id}/invites              an access code + link for one participant
    GET  /agent-builder/agents/{agent_id}/sessions             who took it, and how they scored

    POST /api/agent-builder/retell-webhook          Retell's call events (signed; public)

Mounted under /api/recruiter behind `builder_scope`, like the Scenario Builder:
the recruiter guard, plus the local-prototype exception that is off by default.
Responses carry the rubric, which is the point of an authoring surface. The
Retell payload never does, and `leaked_cues` refuses any payload that would.

There is ONE Retell agent (`RETELL_AGENT_BUILDER_AGENT_ID`). Nothing here creates
another. A built agent reaches it as per-call variables and `agent_override`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services import config
from services.ai.brain import LLMError
from services.ai.workloads import agent_builder as ab
from services.assessment import agent_builder_retell as rx
from services.data import built_agents as store
from services.security import ratelimit

router = APIRouter(prefix="/agent-builder", tags=["agent-builder"])
#: Retell calls this one. It has no principal: its proof is the signature.
webhook_router = APIRouter(tags=["agent-builder"])

RETELL = "https://api.retellai.com"


# --------------------------------------------------------------------------- #
#  Ownership
# --------------------------------------------------------------------------- #
def _org(request: Request) -> str:
    """The caller's organization. "local" only under the local-prototype
    exception, where there is no principal at all."""
    from services.security import principal as security

    p = security.optional_principal(request)
    return p.organization_id if p else "local"


def _row(agent_id: str, request: Request) -> dict[str, Any]:
    row = store.load(agent_id)
    # Another organization's agent is indistinguishable from one that does not
    # exist, so an id cannot be probed for.
    if row is None or row.get("org_id") != _org(request):
        raise HTTPException(404, "No such agent.")
    return row


# --------------------------------------------------------------------------- #
#  Shape
# --------------------------------------------------------------------------- #
def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _one(v: Any, options: tuple[str, ...], default: str) -> str:
    return v if v in options else default


def clean_cfg(cfg: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    base = base or {}
    cfg = {**base, **(cfg or {})}
    v = cfg.get("voice") if cfg.get("voice") in rx.VOICE_KEYS else rx.DEFAULT_VOICE
    return {
        "tone": _one(cfg.get("tone"), rx.DIFFICULTIES, "Realistic"),
        "depth": _one(cfg.get("depth"), rx.DEPTHS, "Probing"),
        "followups": bool(cfg.get("followups", True)),
        # Every agent is a voice conversation: no video, no typed chat.
        "format": rx.FORMATS[0],
        "speaker": _one(cfg.get("speaker"), rx.SPEAKERS, rx.SPEAKERS[0]),
        "sound": _one(cfg.get("sound"), rx.SOUNDS, rx.SOUNDS[0]),
        "ending": _one(cfg.get("ending"), rx.ENDINGS, rx.ENDINGS[0]),
        "voice": v,
        # The language follows the voice. Voice is chosen in Persona, and a
        # Hindi voice told to speak French would be a configuration nobody meant.
        "language": rx.voice(v).language,
        "consent": bool(cfg.get("consent", True)),
        # Proctoring only ever meant turning the camera on, and there is no video.
        "proctoring": "Off",
    }


def clean_agent(agent: dict[str, Any]) -> dict[str, Any]:
    persona = agent.get("persona") or {}
    rubric = []
    for r in agent.get("rubric") or []:
        try:
            w = max(0, min(100, int(round(float(r.get("weight") or 0)))))
        except (TypeError, ValueError):
            w = 0
        rubric.append({"name": _s(r.get("name"))[:120], "anchor": _s(r.get("anchor"))[:600], "weight": w})
    return {
        "title": _s(agent.get("title"))[:120] or "New agent",
        "type_label": _s(agent.get("type_label"))[:80],
        "persona": {k: _s(persona.get(k))[:160] for k in ("name", "role", "style")},
        "description": _s(agent.get("description"))[:2000],
        "instructions": _s(agent.get("instructions"))[:6000],
        "opening_line": _s(agent.get("opening_line"))[:600],
        "closing_line": _s(agent.get("closing_line"))[:600],
        "questions": [{"text": _s(q.get("text"))[:600], "tag": _s(q.get("tag"))[:120]}
                      for q in (agent.get("questions") or [])[:30] if _s(q.get("text"))],
        "rubric": rubric[:10],
        "depth": _one(agent.get("depth"), rx.DEPTHS, "Probing"),
        "voice": agent.get("voice") if agent.get("voice") in rx.VOICE_KEYS else rx.DEFAULT_VOICE,
    }


def clean_fields(fields: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    out = {k: _s((fields or {}).get(k))[:400] for k in ("type", "role", "persona", "skills")}
    if out["type"] not in ("Role-play", "Assessment"):
        out["type"] = "Role-play"
    out["difficulty"] = cfg["tone"]
    out["length"] = f"{rx.lengths(cfg)[0]} min"
    return out


def readiness(row: dict[str, Any]) -> list[dict[str, Any]]:
    a, f = row["agent"], row["fields"]
    total = sum(r["weight"] for r in a["rubric"])
    return [
        {"id": "scenario", "label": "Scenario details", "done": all(f.get(k) for k in ("type", "role", "skills"))},
        {"id": "desc", "label": "Description", "done": bool(a["description"])},
        {"id": "context", "label": "Instructions", "done": bool(a["instructions"])},
        {"id": "persona", "label": "Persona", "done": bool(a["persona"]["name"])},
        {"id": "rubric", "label": "Skills", "done": bool(row.get("reviewed")) and total == 100 and len(a["rubric"]) > 0},
    ]


def call_body(row: dict[str, Any]) -> dict[str, Any]:
    webhook = f"{config.PUBLIC_URL}/api/agent-builder/retell-webhook" if config.PUBLIC_URL else ""
    return rx.web_call_body(row, config.RETELL_AGENT_BUILDER_AGENT_ID or "RETELL_AGENT_BUILDER_AGENT_ID", webhook)


def public(row: dict[str, Any]) -> dict[str, Any]:
    body = call_body(row)
    target, cap = rx.lengths(row["cfg"])
    return {
        "agent_id": row["agent_id"],
        "brief": row.get("brief", ""),
        "mode": "roleplay" if row.get("mode", "roleplay") == "interview" else row.get("mode", "roleplay"),
        "fields": row["fields"],
        "agent": row["agent"],
        "cfg": row["cfg"],
        "reviewed": bool(row.get("reviewed")),
        "version": int(row.get("version") or 0),
        "published_version": int((row.get("published") or {}).get("version") or 0),
        "readiness": readiness(row),
        "length": {"target_minutes": target, "cap_minutes": cap},
        "call_config": body,
        "leaked_cues": rx.leaked_cues(body, row),
        "tests": (row.get("tests") or [])[-10:],
        "candidate_link": next(({"code": i["code"], "path": f"/participant?code={i['code']}"}
                                for i in row.get("invites") or [] if i.get("shared")), None),
        "updated_at": row.get("updated_at", 0),
    }


def _new_row(p: dict[str, Any], c: dict[str, Any], brief: str, mode: str, org: str) -> dict[str, Any]:
    agent = clean_agent({**p["agent"], **c})
    cfg = clean_cfg({"tone": p["fields"]["difficulty"], "depth": agent["depth"], "voice": agent["voice"]})
    return {
        "agent_id": store.new_id(agent["title"]),
        "org_id": org,
        "brief": brief,
        "mode": mode,
        "fields": clean_fields(p["fields"], cfg),
        "agent": agent,
        "cfg": cfg,
        "reviewed": False,
        "version": 0,
        "tests": [],
    }


# --------------------------------------------------------------------------- #
#  Options
# --------------------------------------------------------------------------- #
@router.get("/options")
def options() -> dict[str, Any]:
    from services.ai.gateway import get_gateway

    return {
        "voices": [{"key": v.key, "label": v.label, "language": v.language, "locale": v.locale}
                   for v in rx.VOICES],
        "difficulties": rx.DIFFICULTIES, "depths": rx.DEPTHS, "depth_minutes": rx.DEPTH_MINUTES,
        "endings": rx.ENDINGS, "speakers": rx.SPEAKERS, "formats": rx.FORMATS, "sounds": rx.SOUNDS,
        "model_configured": get_gateway().live,
        "voice_configured": bool(config.RETELL_API_KEY and config.RETELL_AGENT_BUILDER_AGENT_ID),
    }


# --------------------------------------------------------------------------- #
#  Draft
# --------------------------------------------------------------------------- #
class DraftBody(BaseModel):
    brief: str = Field(min_length=1, max_length=4000)
    mode: str = "roleplay"


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/drafts")
def draft(
    body: DraftBody,
    request: Request,
    _: None = Depends(ratelimit.limiter("generation")),
) -> StreamingResponse:
    """Build an agent from a brief, streamed in two real stages.

    `plan` arrives once the scenario, persona and lines exist; `content` once
    the questions and rubric do; `done` carries the saved agent. Screen 2 shows
    each as it lands.
    """
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(422, "Describe the agent you want to build.")
    mode = body.mode if body.mode in ("roleplay", "assessment") else "roleplay"
    org = _org(request)

    def events():
        try:
            p, by1 = ab.plan(brief, mode)
            yield _sse("plan", {**p, "drafted_by": by1})
            c, by2 = ab.content(p)
            yield _sse("content", {**c, "drafted_by": by2})
            row = store.save(_new_row(p, c, brief, mode, org))
            yield _sse("done", public(row))
        except Exception as exc:  # noqa: BLE001 — the stream must end with a reason
            yield _sse("error", {"message": "Tara couldn't finish the draft. Try again.",
                                 "detail": type(exc).__name__})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- #
#  Read and save
# --------------------------------------------------------------------------- #
@router.get("/agents")
def list_agents(request: Request) -> dict[str, Any]:
    org = _org(request)
    return {"agents": [
        {"agent_id": r["agent_id"], "title": r["agent"]["title"], "type_label": r["agent"]["type_label"],
         "persona": ((r["agent"].get("persona") or {}).get("name") or ""),
         "description": r["agent"].get("description") or "",
         "minutes": rx.DEPTH_MINUTES.get((r.get("cfg") or {}).get("depth") or "", 0),
         "published_version": int((r.get("published") or {}).get("version") or 0),
         "updated_at": r.get("updated_at", 0)}
        for r in sorted(store.list_all(), key=lambda r: r.get("updated_at", 0), reverse=True)
        if r.get("org_id") == org
    ]}


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    return public(_row(agent_id, request))


class SaveBody(BaseModel):
    fields: dict[str, Any]
    agent: dict[str, Any]
    cfg: dict[str, Any]
    reviewed: bool = False


@router.put("/agents/{agent_id}")
def save_agent(agent_id: str, body: SaveBody, request: Request) -> dict[str, Any]:
    row = _row(agent_id, request)
    row["agent"] = clean_agent(body.agent)
    row["cfg"] = clean_cfg(body.cfg)
    row["agent"]["depth"], row["agent"]["voice"] = row["cfg"]["depth"], row["cfg"]["voice"]
    row["fields"] = clean_fields(body.fields, row["cfg"])
    row["reviewed"] = body.reviewed
    row["version"] = int(row.get("version") or 0) + 1
    return public(store.save(row))


# --------------------------------------------------------------------------- #
#  Ask Tara, and more questions
# --------------------------------------------------------------------------- #
class ReviseBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=1500)


def _no_model() -> HTTPException:
    return HTTPException(503, "No model is configured, so Tara can't do this. Set OPENROUTER_API_KEY.")


@router.post("/agents/{agent_id}/revise")
def revise(agent_id: str, body: ReviseBody, request: Request,
           _: None = Depends(ratelimit.limiter("generation"))) -> dict[str, Any]:
    row = _row(agent_id, request)
    try:
        out = ab.revise(row, body.instruction.strip())
    except LLMError as exc:
        raise _no_model() from exc
    row["cfg"] = clean_cfg(out["cfg"])
    row["agent"] = clean_agent({**out["agent"], "depth": row["cfg"]["depth"], "voice": row["cfg"]["voice"]})
    row["fields"] = clean_fields(out["fields"], row["cfg"])
    # A changed rubric is an unreviewed rubric.
    row["reviewed"] = False
    row["version"] = int(row.get("version") or 0) + 1
    return {**public(store.save(row)), "summary": out["summary"]}


@router.get("/skill-master")
def skill_master_catalogue() -> dict[str, Any]:
    """The Skill Master picker: domains by category, each with readable skills."""
    from services.data import skill_master

    lib = json.loads((config.ROOT_DIR / "content" / "skill_library.json").read_text())["skills"]
    cat = skill_master.catalogue()
    return {"version": cat["version"], "categories": cat["categories"], "domains": [
        {"id": d["id"], "label": d["label"], "category": d["category"],
         "description": d.get("description", ""), "skills": lib.get(d["id"], [])}
        for d in cat["domains"]
    ]}


class SkillNames(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=12)


@router.post("/agents/{agent_id}/skills/draft")
def draft_skills(agent_id: str, body: SkillNames, request: Request,
                 _: None = Depends(ratelimit.limiter("generation"))) -> dict[str, Any]:
    """Anchors and questions for skills just added from the Skill Master."""
    row = _row(agent_id, request)
    have = {r["name"].lower(): r for r in row["agent"]["rubric"]}
    names = [n.strip() for n in body.names if n.strip().lower() in have][:12]
    if not names:
        return {**public(row), "added": 0}
    try:
        out = ab.skill_details(row, names)
    except LLMError as exc:
        raise _no_model() from exc
    for name, anchor in out["anchors"].items():
        r = have[name.lower()]
        if not (r.get("anchor") or "").strip():
            r["anchor"] = anchor
    row["agent"]["questions"] += out["questions"]
    row["reviewed"] = False
    row["version"] = int(row.get("version") or 0) + 1
    return {**public(store.save(row)), "added": len(out["questions"])}


@router.post("/agents/{agent_id}/questions/generate")
def generate_questions(agent_id: str, request: Request,
                       _: None = Depends(ratelimit.limiter("generation"))) -> dict[str, Any]:
    row = _row(agent_id, request)
    try:
        added = ab.more_questions(row)
    except LLMError as exc:
        raise _no_model() from exc
    row["agent"]["questions"] += added
    row["version"] = int(row.get("version") or 0) + 1
    return {**public(store.save(row)), "added": len(added)}


# --------------------------------------------------------------------------- #
#  Testing
# --------------------------------------------------------------------------- #
class Turn(BaseModel):
    role: str = "user"          # "agent" | "user"
    text: str = Field(default="", max_length=4000)


class ChatBody(BaseModel):
    messages: list[Turn] = Field(default_factory=list, max_length=80)


@router.post("/agents/{agent_id}/test-chat")
def test_chat(agent_id: str, body: ChatBody, request: Request) -> dict[str, Any]:
    row = _row(agent_id, request)
    try:
        reply = ab.test_reply(row, [m.model_dump() for m in body.messages])
    except LLMError as exc:
        raise _no_model() from exc
    return {"reply": reply}


@router.post("/agents/{agent_id}/test-call")
async def test_call(agent_id: str, request: Request) -> dict[str, Any]:
    """A Retell web call on the one agent, configured for this agent."""
    row = _row(agent_id, request)
    if not (config.RETELL_API_KEY and config.RETELL_AGENT_BUILDER_AGENT_ID):
        raise HTTPException(503, "Voice testing isn't configured. Set RETELL_API_KEY and "
                                 "RETELL_AGENT_BUILDER_AGENT_ID.")
    body = call_body(row)
    leaks = rx.leaked_cues(body, row)
    if leaks:
        # Refuse rather than strip: a payload carrying the rubric means the
        # instructions restate it, and the recruiter should see where.
        raise HTTPException(422, {"message": "The instructions or questions repeat a skill description, "
                                             "so this can't be sent to the voice agent.", "leaked": leaks})
    from services.assessment import slots

    running, limit = slots.retell_concurrency()
    if limit and running >= limit:
        raise HTTPException(503, f"All {limit} voice lines are in use right now. Try again in a few minutes.")
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(f"{RETELL}/v2/create-web-call", json=body,
                                headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Couldn't reach the voice service.") from exc
    if r.status_code >= 400:
        raise HTTPException(502, "The voice service refused the call.")
    out = r.json()
    call_id = str(out.get("call_id") or "")
    row.setdefault("tests", []).append({"call_id": call_id, "kind": "voice", "at": time.time(),
                                        "version": int(row.get("version") or 0)})
    store.save(row)
    return {"access_token": out.get("access_token", ""), "call_id": call_id}


class ScoreBody(BaseModel):
    transcript: list[Turn] = Field(default_factory=list, max_length=200)
    call_id: str = ""


async def _retell_transcript(call_id: str) -> list[dict[str, str]]:
    import httpx

    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(f"{RETELL}/v2/get-call/{call_id}",
                           headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"})
    if r.status_code >= 400:
        raise HTTPException(502, "Couldn't fetch the call from the voice service.")
    return transcript_of(r.json())


def transcript_of(call: dict[str, Any]) -> list[dict[str, str]]:
    return [{"role": "agent" if t.get("role") == "agent" else "user", "text": _s(t.get("content"))}
            for t in call.get("transcript_object") or [] if _s(t.get("content"))]


@router.post("/agents/{agent_id}/score")
async def score(agent_id: str, body: ScoreBody, request: Request) -> dict[str, Any]:
    row = _row(agent_id, request)
    if body.call_id:
        test = next((t for t in row.get("tests") or [] if t.get("call_id") == body.call_id), None)
        if test is None:
            raise HTTPException(404, "No such test call for this agent.")
        if test.get("result"):
            return test["result"]
        transcript = await _retell_transcript(body.call_id)
    else:
        transcript = [m.model_dump() for m in body.transcript]
    if sum(1 for t in transcript if t["role"] == "user") < 2:
        raise HTTPException(422, "The participant needs to answer at least twice before this can be scored.")
    try:
        result = ab.score(row, transcript)
    except LLMError as exc:
        raise _no_model() from exc
    result["transcript"] = transcript
    if body.call_id:
        test["result"] = result
        store.save(row)
    return result


# --------------------------------------------------------------------------- #
#  Publish
# --------------------------------------------------------------------------- #
@router.post("/agents/{agent_id}/publish")
def publish(agent_id: str, request: Request) -> dict[str, Any]:
    row = _row(agent_id, request)
    # Re-apply today's rules before freezing: an agent saved under older ones
    # (video, chat, "Interview") must not publish them.
    row["cfg"] = clean_cfg(row["cfg"])
    row["fields"] = clean_fields(row["fields"], row["cfg"])
    missing = [i["label"] for i in readiness(row) if not i["done"]]
    if missing:
        raise HTTPException(422, {"message": "Finish these before publishing.", "missing": missing})
    body = call_body(row)
    leaks = rx.leaked_cues(body, row)
    if leaks:
        raise HTTPException(422, {"message": "The instructions or questions repeat a skill description.", "leaked": leaks})
    version = int((row.get("published") or {}).get("version") or 0) + 1
    row["published"] = {"version": version, "at": time.time(), "fields": row["fields"],
                        "agent": row["agent"], "cfg": row["cfg"]}
    # One shareable candidate link per agent, made on first publish and kept
    # across republishes: whoever opens it sits the latest published version.
    if not any(i.get("shared") for i in row.get("invites") or []):
        row.setdefault("invites", []).append({"code": _unique_code(), "shared": True, "created_at": time.time()})
    return {**public(store.save(row)), "published": True}


# --------------------------------------------------------------------------- #
#  Participants: invites and results
# --------------------------------------------------------------------------- #
class InviteBody(BaseModel):
    name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=200)


def _code() -> str:
    import secrets
    import string

    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    tail = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(2))
    return f"RP-{digits}-{tail}"


def _unique_code() -> str:
    codes = {i["code"] for r in store.list_all() for i in r.get("invites") or []}
    code = _code()
    while code in codes:
        code = _code()
    return code


@router.post("/agents/{agent_id}/invites")
def invite(agent_id: str, body: InviteBody, request: Request) -> dict[str, Any]:
    """One access code per participant, for the PUBLISHED version."""
    row = _row(agent_id, request)
    if not row.get("published"):
        raise HTTPException(409, "Publish the agent before inviting anyone.")
    code = _unique_code()
    entry = {"code": code, "name": _s(body.name), "email": _s(body.email).lower(), "created_at": time.time()}
    row.setdefault("invites", []).append(entry)
    store.save(row)
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    return {"code": code, "link": f"{base}/participant?code={code}", "version": row["published"]["version"]}


@router.get("/agents/{agent_id}/sessions")
def participant_sessions(agent_id: str, request: Request) -> dict[str, Any]:
    from services.data import agent_sessions

    _row(agent_id, request)
    return {"sessions": [
        {"session_id": r["session_id"], "name": r.get("name", ""), "email": r.get("email", ""),
         "status": r["status"], "version": r["version"], "early": bool(r.get("early")),
         "elapsed_sec": r.get("elapsed_sec", 0), "ended_at": r.get("ended_at"),
         "result": r.get("result"), "result_error": r.get("result_error"),
         "feedback": r.get("feedback"), "transcript": r.get("transcript") or [],
         "booking": r.get("booking")}
        for r in agent_sessions.for_agent(agent_id)
    ]}


# --------------------------------------------------------------------------- #
#  Retell webhook
# --------------------------------------------------------------------------- #
def verify_signature(raw: bytes, header: str, api_key: str, now_ms: int | None = None) -> bool:
    """Retell's `x-retell-signature`: `v=<ms>,d=<hex>` where hex is
    HMAC-SHA256(key=api key, msg=raw body + timestamp). Five-minute window."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, digest = parts["v"], parts["d"]
        age = abs((now_ms if now_ms is not None else int(time.time() * 1000)) - int(ts))
    except (KeyError, ValueError):
        return False
    if age > 5 * 60 * 1000:
        return False
    expected = hmac.new(api_key.encode(), raw + ts.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)


def _score_in_background(agent_id: str, call_id: str, transcript: list[dict[str, str]]) -> None:
    row = store.load(agent_id)
    if row is None:
        return
    test = next((t for t in row.get("tests") or [] if t.get("call_id") == call_id), None)
    if test is None or test.get("result"):
        return
    test["transcript"] = transcript
    if sum(1 for t in transcript if t["role"] == "user") >= 2:
        try:
            test["result"] = {**ab.score(row, transcript), "transcript": transcript}
        except Exception:  # noqa: BLE001 — scoring can be retried from the page
            pass
    store.save(row)


@webhook_router.post("/api/agent-builder/retell-webhook")
async def retell_webhook(request: Request, background: BackgroundTasks) -> dict[str, Any]:
    raw = await request.body()
    if not config.RETELL_API_KEY or not verify_signature(
        raw, request.headers.get("x-retell-signature", ""), config.RETELL_API_KEY
    ):
        raise HTTPException(401, "Bad signature.")
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Not JSON.") from exc
    call = event.get("call") or {}
    meta = call.get("metadata") or {}
    if event.get("event") not in ("call_ended", "call_analyzed") or meta.get("source") != "tara-agent-builder":
        return {"ok": True, "ignored": True}
    call_id = _s(call.get("call_id"))
    # The agent comes from OUR record of the call, never from metadata alone:
    # metadata is what we sent, but only the binding proves we placed the call.
    row = store.by_call(call_id)
    if row is None:
        return {"ok": True, "ignored": True}
    background.add_task(_score_in_background, row["agent_id"], call_id, transcript_of(call))
    return {"ok": True}
