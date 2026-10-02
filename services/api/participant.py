"""The participant side of a published Agent Builder agent.

    POST /api/participant/sign-in                          access code + name + email + consent → a session
    GET  /api/participant/session/{session_id}             what the screens show
    GET  /api/participant/session/{session_id}/slots       the times they can book, with places left
    POST /api/participant/session/{session_id}/booking     book (or move) their slot
    POST /api/participant/session/{session_id}/call        a Retell web call (or a resumed one), only in their slot
    POST /api/participant/session/{session_id}/chat        the next line, for a Chat-format agent
    POST /api/participant/session/{session_id}/complete    submit; scoring runs on the server
    POST /api/participant/session/{session_id}/feedback    the participant's rating of the experience

Sign-in is the one public route, because it is the call that MINTS the session
grant. Everything after it needs that grant (an HttpOnly cookie) and
`participant_scope` checks it. A session that is not yours reads as one that
does not exist.

The participant sees what the published agent shows them: persona, title,
description, the names of the skills, the length and the format. They never
see the skill descriptors, the weights, the instructions, the question bank or
their score. The call goes to the ONE Retell agent, configured from the
published snapshot, with their first name and, if a call dropped, what was
already said.
"""
from __future__ import annotations


import secrets
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services import config
from services.ai.brain import LLMError
from services.ai.workloads import agent_builder as ab
from services.assessment import agent_builder_retell as rx
from services.assessment import slots
from services.data import agent_sessions as sessions
from services.data import built_agents as agents
from services.security import principal as security

router = APIRouter(prefix="/api/participant", tags=["participant"])

COOKIE = "tara_participant"
RETELL = "https://api.retellai.com"
#: Who reviews the conversation, in the participant's words. "The hiring team"
#: only when the scenario is about hiring; any other role-play or assessment
#: is reviewed by whoever invited the participant.
HIRING_LABEL = "the hiring team"
ORG_LABEL = "the team that invited you"


def purpose_of(row: dict[str, Any]) -> str:
    """The session's purpose: as configured when it was published, else a guess from its text."""
    from services.evaluation import simulation as sim

    snap = row.get("snapshot") or row
    p = (snap.get("cfg") or {}).get("purpose")
    if p in sim.PURPOSES:
        return p
    a, f = snap.get("agent") or {}, snap.get("fields") or {}
    return sim.infer_purpose(a.get("title", ""), a.get("type_label", ""), a.get("description", ""), f.get("role", ""))


# --------------------------------------------------------------------------- #
#  The session guard
# --------------------------------------------------------------------------- #
def participant_scope(request: Request, session_id: str) -> dict[str, Any]:
    """Prove this browser owns this session. Named so the access matrix can
    see it in the route's wiring."""
    row = sessions.load(session_id)
    grant = request.cookies.get(COOKIE, "")
    if row is None or not grant or not row.get("grant_hash") or sessions.hash_grant(grant) != row["grant_hash"]:
        raise HTTPException(404, "No such session.")
    return row


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """The published agent this session sits, in the shape the Retell export reads."""
    return {"agent_id": row["agent_id"], "version": row["version"], **row["snapshot"]}


# --------------------------------------------------------------------------- #
#  Voice samples: the real voice, from Retell's catalogue
# --------------------------------------------------------------------------- #
_PREVIEWS: dict[str, str] = {}


def voice_sample(voice_id: str) -> str:
    if not _PREVIEWS and config.RETELL_API_KEY:
        try:
            import httpx

            r = httpx.get(f"{RETELL}/list-voices", timeout=10,
                          headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"})
            if r.status_code < 400:
                for v in r.json():
                    if v.get("voice_id") and v.get("preview_audio_url"):
                        _PREVIEWS[v["voice_id"]] = v["preview_audio_url"]
        except Exception:  # noqa: BLE001 — no sample is a degraded overview, not an error
            pass
    return _PREVIEWS.get(voice_id, "")


def _persona_only(agent: dict[str, Any], text: str) -> str:
    return rx.no_interview(rx._TARA.sub(rx.persona_first((agent.get("persona") or {}).get("name")), text or ""))


def view(row: dict[str, Any]) -> dict[str, Any]:
    """Everything the participant's screens show, and nothing the scorer uses."""
    snap = row["snapshot"]
    a, cfg = snap["agent"], snap["cfg"]
    v = rx.voice(cfg.get("voice") or rx.DEFAULT_VOICE)
    target, cap = rx.lengths(cfg)
    fmt = cfg.get("format") or rx.FORMATS[0]
    return {
        "session_id": row["session_id"],
        "status": row["status"],
        "participant": {"name": row["name"], "email": row["email"]},
        "agent": {
            # The participant only meets the persona: any "Tara" in what they
            # read becomes the persona's first name, as it does in the call.
            "title": _persona_only(a, a["title"]), "type_label": _persona_only(a, a["type_label"]),
            "persona": {"name": a["persona"]["name"], "role": a["persona"]["role"]},
            "description": _persona_only(a, a["description"]),
            "skills": [r["name"] for r in a["rubric"]],
            "voice": v.label.split(" —")[0], "language": v.language,
        },
        "call": {
            "target_minutes": target, "cap_minutes": cap, "ending": cfg.get("ending"),
            "format": fmt, "speaker": cfg.get("speaker"), "sound": cfg.get("sound") or "None",
            "camera_required": False,
            "camera_used": False,
            "recording_consent": bool(cfg.get("consent", True)),
            "voice_configured": bool(config.RETELL_API_KEY and config.RETELL_AGENT_BUILDER_AGENT_ID),
        },
        "voice_sample_url": voice_sample(v.voice_id),
        # For the proctoring suite to read and apply; this page does not act on it.
        "proctoring": row.get("proctoring") or {"image_proctoring": bool(cfg.get("image_proctoring")),
                                                "safe_browser": bool(cfg.get("safe_browser"))},
        "org_label": HIRING_LABEL if purpose_of(row) == "Hiring" else ORG_LABEL,
        "hiring": purpose_of(row) == "Hiring",
        "purpose": purpose_of(row),
        "attempt": int(row.get("attempt") or 1),
        "attempts": (snap.get("cfg") or {}).get("attempts") or "1",
        "calls": len(row.get("calls") or []),
        "submitted_at": row.get("ended_at"),
        "booking": booking_view(row),
    }


def booking_view(row: dict[str, Any]) -> dict[str, Any] | None:
    b = row.get("booking")
    if not b:
        return None
    opens, closes, ends = slots.window(b)
    return {"start": b["start"], "minutes": b["cap_minutes"], "join_opens": slots.iso(opens),
            "join_closes": slots.iso(closes), "can_join_now": slots.can_join(b, resuming=bool(row.get("calls")))}


# --------------------------------------------------------------------------- #
#  Sign-in
# --------------------------------------------------------------------------- #
class SignIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200)
    consent: bool = False


def _find_invite(code: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    want = code.strip().upper()
    for row in agents.list_all():
        for inv in row.get("invites") or []:
            if inv.get("code", "").upper() == want and not inv.get("revoked"):
                return row, inv
    return None


@router.post("/sign-in")
def sign_in(body: SignIn, response: Response) -> dict[str, Any]:
    if not body.consent:
        raise HTTPException(422, {"error": "consent", "message": "Please agree to continue."})
    found = _find_invite(body.code)
    if found is None:
        raise HTTPException(404, {"error": "authFail", "message": "This access code isn't valid."})
    agent_row, invite = found
    published = agent_row.get("published")
    if not published:
        raise HTTPException(409, {"error": "authFail", "message": "This conversation isn't open yet."})

    email = body.email.strip().lower()
    # Every attempt this person has made with this code: a shared link is one
    # person per email; a personal code is its one invitee.
    mine = [r for r in sessions.for_agent(agent_row["agent_id"]) if r.get("invite_code") == invite["code"]
            and (r.get("email") == email or not invite.get("shared"))]
    row = next((r for r in mine if r["status"] != "complete"), None)  # a dropped tab rejoins
    grant = secrets.token_urlsafe(32)
    if row is None:
        # A new attempt, if the scenario's attempt policy allows one.
        limit = (published.get("cfg") or {}).get("attempts") or "1"
        if limit != "Unlimited" and len(mine) >= int(limit):
            raise HTTPException(409, {"error": "completed", "message":
                                      "This conversation has already been submitted." if limit == "1"
                                      else f"You've used all {limit} attempts for this conversation."})
        row = {
            "session_id": sessions.new_id(), "agent_id": agent_row["agent_id"], "org_id": agent_row["org_id"],
            "version": published["version"], "invite_code": invite["code"],
            "snapshot": {k: published[k] for k in ("fields", "agent", "cfg")},
            "status": "signed_in", "calls": [], "created_at": time.time(),
            "attempt": len(mine) + 1,
            # For the proctoring suite: what this invitation (or the open link) asked for.
            "proctoring": invite.get("proctoring") or {"image_proctoring": bool(published["cfg"].get("image_proctoring")),
                                                       "safe_browser": bool(published["cfg"].get("safe_browser"))},
        }
        if not invite.get("shared"):
            invite["session_id"] = row["session_id"]
            agents.save(agent_row)
    row.update(name=body.name.strip(), email=email, consent_at=time.time(),
               grant_hash=sessions.hash_grant(grant))
    sessions.save(row)
    response.set_cookie(COOKIE, grant, max_age=7 * 24 * 3600, **security.candidate_cookie_kwargs())
    return view(row)


@router.get("/session/{session_id}")
def get_session(row: dict[str, Any] = Depends(participant_scope)) -> dict[str, Any]:
    return view(row)


# --------------------------------------------------------------------------- #
#  The call
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Slots
# --------------------------------------------------------------------------- #
def _cap_minutes(row: dict[str, Any]) -> int:
    return rx.lengths(row["snapshot"]["cfg"])[1]


@router.get("/session/{session_id}/slots")
def list_slots(row: dict[str, Any] = Depends(participant_scope)) -> dict[str, Any]:
    return {"timezone": slots.TZ, "slot_minutes": slots.SLOT_MINUTES, "length_minutes": _cap_minutes(row),
            "join_early_minutes": slots.JOIN_EARLY_MIN, "join_late_minutes": slots.JOIN_LATE_MIN,
            "slots": slots.available(sessions.all_bookings(), _cap_minutes(row), session_id=row["session_id"]),
            "booking": booking_view(row)}


class BookingBody(BaseModel):
    start: str = Field(min_length=10, max_length=40)


@router.post("/session/{session_id}/booking")
def book(body: BookingBody, row: dict[str, Any] = Depends(participant_scope)) -> dict[str, Any]:
    if row["status"] == "complete":
        raise HTTPException(409, "This conversation has already been submitted.")
    if row.get("calls"):
        raise HTTPException(409, {"error": "started", "message": "Your conversation has started, so the time can't change."})
    try:
        start = slots.parse(body.start)
    except ValueError as exc:
        raise HTTPException(422, "That time isn't valid.") from exc
    if slots.iso(start) not in {slots.iso(t) for t in slots.offered()}:
        raise HTTPException(422, {"error": "unavailable", "message": "That time isn't offered. Pick another."})
    with slots.LOCK:
        # Re-read under the lock: the check and the write must see the same bookings.
        fresh = sessions.load(row["session_id"]) or row
        left = next((s["left"] for s in slots.available(sessions.all_bookings(), _cap_minutes(fresh),
                                                        session_id=fresh["session_id"]) if s["start"] == slots.iso(start)), 0)
        if left <= 0:
            raise HTTPException(409, {"error": "full", "message": "That time just filled up. Pick another."})
        fresh["booking"] = {"start": slots.iso(start), "cap_minutes": _cap_minutes(fresh), "booked_at": time.time()}
        sessions.save(fresh)
    return view(fresh)


class CallBody(BaseModel):
    speed: str = "normal"


async def _transcripts(call_ids: list[str]) -> list[dict[str, str]]:
    from services.api.agent_builder import _retell_transcript

    out: list[dict[str, str]] = []
    for cid in call_ids:
        try:
            out += await _retell_transcript(cid)
        except HTTPException:
            continue
    return out


@router.post("/session/{session_id}/call")
async def call(body: CallBody, row: dict[str, Any] = Depends(participant_scope)) -> dict[str, Any]:
    if row["status"] == "complete":
        raise HTTPException(409, "This conversation has already been submitted.")
    if row["snapshot"]["cfg"].get("format") == "Chat":
        raise HTTPException(409, "This conversation is typed, not spoken.")
    if not (config.RETELL_API_KEY and config.RETELL_AGENT_BUILDER_AGENT_ID):
        raise HTTPException(503, "Voice isn't available right now.")
    if len(row.get("calls") or []) >= 5:
        raise HTTPException(429, "Too many reconnects for one conversation. Contact the hiring team.")
    # Only in the booked slot: that is what keeps live calls within Retell's limit.
    if not slots.can_join(row.get("booking"), resuming=bool(row.get("calls"))):
        raise HTTPException(409, {"error": "slot", "message": "Your conversation opens at your booked time."
                                  if row.get("booking") else "Book a time for your conversation first."})
    running, limit = slots.retell_concurrency()
    if limit and running >= limit:
        raise HTTPException(503, {"error": "busy", "message": "All sessions are in use."})
    snap = _snapshot(row)
    # A second call on the same session is a reconnect: the agent is told what
    # was already said, and picks up there instead of starting over.
    resume = await _transcripts(row["calls"]) if row.get("calls") else None
    body_ = rx.web_call_body(
        snap, config.RETELL_AGENT_BUILDER_AGENT_ID,
        f"{config.PUBLIC_URL}/api/agent-builder/retell-webhook" if config.PUBLIC_URL else "",
        candidate_name=row["name"], resume=resume or None, speed=body.speed,
        metadata={"participant_session": row["session_id"]},
    )
    if rx.leaked_cues(body_, snap):
        raise HTTPException(409, "This conversation isn't available right now. Contact the hiring team.")
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(f"{RETELL}/v2/create-web-call", json=body_,
                                headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Couldn't reach the voice service.") from exc
    if r.status_code == 429:
        raise HTTPException(503, {"error": "busy", "message": "All sessions are in use."})
    if r.status_code >= 400:
        raise HTTPException(502, "The voice service refused the call.")
    out = r.json()
    row.setdefault("calls", []).append(str(out.get("call_id") or ""))
    row["status"] = "in_call"
    row.setdefault("started_at", time.time())
    sessions.save(row)
    return {"access_token": out.get("access_token", ""), "call_id": out.get("call_id", ""),
            "resumed": bool(resume)}


class Turn(BaseModel):
    role: str = "user"
    text: str = Field(default="", max_length=4000)


class ChatBody(BaseModel):
    messages: list[Turn] = Field(default_factory=list, max_length=120)


@router.post("/session/{session_id}/chat")
def chat(body: ChatBody, row: dict[str, Any] = Depends(participant_scope)) -> dict[str, Any]:
    if row["status"] == "complete":
        raise HTTPException(409, "This conversation has already been submitted.")
    if row["snapshot"]["cfg"].get("format") != "Chat":
        raise HTTPException(409, "This conversation is spoken, not typed.")
    try:
        reply = ab.test_reply(_snapshot(row), [m.model_dump() for m in body.messages], candidate_name=row["name"])
    except LLMError as exc:
        raise HTTPException(503, "Tara can't reply right now. Try again in a moment.") from exc
    row["transcript"] = [m.model_dump() for m in body.messages] + [{"role": "agent", "text": reply}]
    row["status"] = "in_call"
    row.setdefault("started_at", time.time())
    sessions.save(row)
    return {"reply": reply}


# --------------------------------------------------------------------------- #
#  Submit, score, feedback
# --------------------------------------------------------------------------- #
class CompleteBody(BaseModel):
    early: bool = False
    elapsed_sec: int = Field(default=0, ge=0, le=6 * 3600)


async def _score(session_id: str) -> None:
    """Gather the authoritative transcript and score it. Voice transcripts come
    from Retell, not from the browser: what the participant's page reports is
    shown to them, never scored."""
    import asyncio

    row = sessions.load(session_id)
    if row is None:
        return
    transcript = row.get("transcript") or []
    if row.get("calls"):
        for attempt in range(4):  # Retell finalises a transcript a few seconds after hang-up
            transcript = await _transcripts(row["calls"])
            if transcript:
                break
            await asyncio.sleep(3 * (attempt + 1))
    row = sessions.load(session_id) or row
    row["transcript"] = transcript
    evaluate_session(row)
    sessions.save(row)


def evaluate_session(row: dict[str, Any]) -> None:
    """Run the evaluation engine once per set of calls; store a valid result or why not.

    Idempotent: a second completion, a retried webhook or a page refresh with the
    same calls never produces a second result.
    """
    from services.assessment.agent_builder_flow import FLOW
    from services.evaluation import simulation as sim

    key = ",".join(row.get("calls") or []) or "typed"
    if (row.get("evaluation") or {}).get("calls_key") == key:
        return
    transcript = row.get("transcript") or []
    if sum(1 for t in transcript if t.get("role") == "user") < 2:
        row["evaluation_error"] = {"problems": ["the conversation was too short to evaluate"], "calls_key": key}
        return
    snap = _snapshot(row)
    snap["cfg"] = {**snap["cfg"], "purpose": purpose_of(row)}
    try:
        result = sim.evaluate(snap, transcript, complete=ab._complete, flow=FLOW)
    except sim.EvaluationError as exc:  # failed the integrity check: never saved as a result
        row["evaluation_error"] = {"problems": exc.problems, "calls_key": key}
        return
    except Exception as exc:  # noqa: BLE001 — model unavailable; can be re-run later
        row["evaluation_error"] = {"problems": [type(exc).__name__], "calls_key": ""}
        return
    result["calls_key"] = key
    result["attempt"] = int(row.get("attempt") or 1)
    row["evaluation"] = result
    row.pop("evaluation_error", None)


@router.post("/session/{session_id}/complete")
def complete(body: CompleteBody, background: BackgroundTasks,
             row: dict[str, Any] = Depends(participant_scope)) -> dict[str, Any]:
    if row["status"] != "complete":
        row.update(status="complete", early=body.early, elapsed_sec=body.elapsed_sec, ended_at=time.time())
        sessions.save(row)
        background.add_task(_score, row["session_id"])
    return view(row)


class FeedbackBody(BaseModel):
    rating: int = Field(ge=1, le=5)
    answers: dict[str, str] = Field(default_factory=dict)
    comment: str = Field(default="", max_length=2000)


@router.post("/session/{session_id}/feedback")
def feedback(body: FeedbackBody, row: dict[str, Any] = Depends(participant_scope)) -> dict[str, Any]:
    row["feedback"] = {"rating": body.rating, "answers": {k[:40]: v[:40] for k, v in list(body.answers.items())[:10]},
                       "comment": body.comment.strip(), "at": time.time()}
    sessions.save(row)
    return {"ok": True}
