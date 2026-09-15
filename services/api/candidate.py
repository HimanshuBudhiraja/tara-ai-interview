"""Candidate-side API.

Endpoints the candidate app talks to — and nothing else. There is no recruiter
surface here, no scoring endpoint, and no way to read a verdict: the candidate
app presents items and captures work, and that is all it is allowed to do.

  GET  /api/invite/{token}          who this link is for, and where they left off
  POST /api/session/start           consent + accommodations in, first turn out
  GET  /api/session/{id}            resume payload (transcript + progress)
  POST /api/session/{id}/turn       one turn over HTTP (text mode, tests, curl)
  WS   /ws/interview/{id}           one turn over a socket (what the UI uses)
  GET  /api/health                  boot diagnostics

Voice never appears in this file. The browser does STT and TTS; the server deals
in text. Swapping in a vendor for the mouth and ears changes the client only.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from packages.types.evaluation import CRITERIA
from services import config
from services.ai.brain import get_llm
from services.assessment import runtime_adapter
from services.api import retell as retell_api
from services.data import audit, interviews, invites, pilot, versions
from services.evaluation import analytics
from services.data import sessions as store
from services.orchestrator.engine import Orchestrator
from services.orchestrator.pool import Pool, get_pool
from services.orchestrator.state import SessionState
from services.security import principal as security
from services.security import ratelimit

router = APIRouter(tags=["candidate"])

orch = Orchestrator()
pool = get_pool()


def _published_definition(interview_id: str, version: int):
    """The contract a candidate is actually sitting.

    Resolved from the pinned version wherever there is one, so the welcome
    screen promises the same interview the orchestrator will run. A welcome
    screen that says eight questions while the session asks five is a small lie
    that costs trust at exactly the wrong moment.
    """
    if interview_id and version:
        defn = versions.definition_for(interview_id, version)
        if defn is not None:
            return defn
    cfg = interviews.get(interview_id) if interview_id else None
    return interviews.build_definition(cfg, pool) if cfg else None


# --------------------------------------------------------------------------- #
#  Models
# --------------------------------------------------------------------------- #
class StartRequest(BaseModel):
    token: str
    consent_recording: bool = False
    #: No `channel`. A candidate interview is spoken, and the channel is not
    #: something a request gets to assert: accepting `"text"` here would put
    #: the session in a mode the candidate app has no way to serve, and a
    #: written interview is a different assessment rather than a setting.
    #: `SessionState.channel` still exists because a recruiter's own scripted
    #: test run of a draft is genuinely text — see `recruiter.test_run`.
    accommodations: dict[str, Any] = Field(default_factory=dict)
    #: What Tara should call them. Address only — the invitation's
    #: `candidate_name` stays the record, so this cannot be used to sit
    #: someone else's interview under a different name. Trimmed and bounded
    #: here because it is candidate-supplied text that ends up being spoken.
    preferred_name: str = ""


class PrecheckReport(BaseModel):
    """What the candidate's device check concluded, before any session exists.

    Three states and nothing else: `ready` (the mic heard something and the
    speaker test played), `text` (they chose to type, or the browser cannot do
    voice), `failed` (they could not get past it). It carries no device
    identifiers and no audio — a count of how many people the check stopped is
    the whole point, and anything more would be collecting hardware
    fingerprints from candidates to answer a question about our own funnel.
    """

    token: str
    outcome: str  # ready | text | failed
    reason: str = ""


class TurnRequest(BaseModel):
    # Exactly one of these describes the turn.
    said: str | None = None
    action: str | None = None  # "silence" | "repeat" | "end"
    #: Optional client-minted id for this turn, unique per turn and stable
    #: across retries. When it is present the turn is applied at most once
    #: however many times it is delivered; when it is absent behaviour is
    #: exactly what it always was, so an older client keeps working.
    turn_id: str | None = None


# Health and readiness live in `services/api/health.py`. They were here, as one
# endpoint that described the whole deployment; they are two now, because
# liveness and readiness answer different questions and a probe that conflates
# them turns a dependency blip into a crash loop.


# --------------------------------------------------------------------------- #
#  Demo prompter
# --------------------------------------------------------------------------- #
@router.get("/api/demo/prompts")
async def demo_prompts() -> dict[str, Any]:
    """Teleprompter lines for the voice demo (`?demo=1` on the candidate link).

    A live voice demo doesn't fail because the technology doesn't work — it
    fails because the person holding the microphone freezes, waffles, and Tara
    probes them three times in front of an audience. These are short lines the
    presenter reads ALOUD, so the demo stays a real voice interview in both
    directions while never stalling.

    Nothing here reaches the orchestrator. It's presenter-facing copy, served to
    the demo overlay only.
    """
    # These are model answers to the AUTHORED items of the default interview,
    # keyed by item id. Locally that is a teleprompter; on a production
    # deployment it would be an answer key that any candidate sitting that
    # interview could fetch without signing in. So production has no demo.
    path = config.CONTENT_DIR / "demo_answers.json"
    if config.is_production() or not path.exists():
        return {"answers": {}, "probe_replies": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {"answers": raw.get("answers", {}), "probe_replies": raw.get("probe_replies", [])}


# --------------------------------------------------------------------------- #
#  Invite
# --------------------------------------------------------------------------- #
def _demo_invite_allowed() -> bool:
    """May the well-known `demo` invitation be served at all?

    Only on a local development host, or when an operator has deliberately
    opted in with TARA_DEMO_INVITE=1 for a demo deployment they intend to be
    open. A fresh clone on localhost still gets its ready-to-open link — that
    convenience is real and is why the demo invitation exists — but it stops at
    the edge of the machine.
    """
    if config.DEMO_INVITE_PUBLIC:
        return True
    host = (config.PUBLIC_URL or "").lower()
    if not host:
        # No public URL configured is the signature of local development.
        return True
    return "localhost" in host or "127.0.0.1" in host


@router.get("/api/invite/{token}")
async def read_invite(
    token: str, _: None = Depends(ratelimit.limiter("invitation")),
) -> dict[str, Any]:
    """What the welcome screen renders. Read-only — never mints a session."""
    if token == config.DEMO_TOKEN and not _demo_invite_allowed():
        # The same 404 an unknown token gets. `demo` is a well-known,
        # never-expiring credential, and on a public host it is an open door
        # into a real interview — every turn of which costs provider credit and
        # voice minutes.
        #
        # Gated HERE, not only where it is created. The row is persisted, so a
        # deployment that ran once in development keeps serving it forever
        # afterwards no matter what the environment is later set to: switching
        # TARA_ENV stops it being re-created and does nothing about the one
        # already on disk. That gap is the whole reason this check exists.
        #
        # And gated on REACHABILITY rather than on the environment label,
        # because the danger is being reachable from the internet, not being
        # labelled "production". A public deployment left in development is
        # exactly the case that needs protecting, and it is the one an
        # `is_production()` check misses.
        raise HTTPException(404, "This interview link isn't valid. Check that you "
                                 "copied all of it, exactly as you received it.")

    invite = invites.get(token)
    if invite is None:
        # Channel-neutral: Tara sends no email. An invitation is a link the
        # recruiter passes on however they like — an ATS, a portal, a message —
        # so "check your email" sends a candidate looking for something that
        # may never have existed.
        raise HTTPException(404, "This interview link isn't valid. Check that you "
                                 "copied all of it, exactly as you received it.")

    # The candidate is told the link is not usable; they are NOT told which of
    # the several reasons applies. "Already used" and "withdrawn" are facts
    # about someone else's decisions, and spelling them out tells whoever holds
    # a guessed token more than they should learn (§16).
    usable, reason = invite.can_start_session()
    resumable_session = store.try_load(invite.session_id) if invite.session_id else None
    mid_interview = bool(
        resumable_session and resumable_session.phase not in ("complete", "abandoned")
    )
    if not usable and not mid_interview:
        if invite.expired:
            raise HTTPException(
                410, "This interview link has expired. Ask your recruiter for a new one."
            )
        raise HTTPException(
            410,
            "This interview link isn't available any more. "
            "Get in touch with whoever invited you.",
        )

    invites.mark_opened(invite)

    resumable = False
    answered = 0
    if invite.session_id:
        state = store.try_load(invite.session_id)
        if state and state.phase not in ("complete", "abandoned"):
            import time as _t

            window = config.REJOIN_WINDOW_SEC
            pinned = versions.definition_for(state.interview_id, state.interview_version) \
                if state.interview_version else None
            if pinned is not None:
                window = pinned.runtime.rejoin_window_sec
            resumable = (_t.time() - state.updated_at) < window
            answered = sum(1 for r in state.records.values() if r.closed_at)

    # What the candidate is told comes from the version they were invited to,
    # not from server defaults — otherwise the welcome screen promises eight
    # questions and Tara asks five.
    #
    # `candidate_safe_summary` decides what may cross this boundary: how long it
    # takes and roughly what it covers. Never the questions, the expected
    # signals, the criteria, the priorities or the task mapping (§20, §23).
    defn = _published_definition(invite.interview_id, invite.interview_version)
    if defn is not None:
        summary = runtime_adapter.candidate_safe_summary(defn)
        role_title = summary["role_title"] or pool.role_title
        question_count = summary["question_count"]
        estimated = summary["estimated_minutes"] or max(10, question_count * 2)
        competencies = summary["competencies"]
        # A delivery setting, not assessment content: it says how fast Tara
        # speaks, nothing about what she asks. Read from the PUBLISHED
        # definition so two candidates sitting the same version hear it the
        # same way, and so changing the draft cannot alter an interview
        # somebody is part-way through.
        speech_rate = defn.runtime.speech_rate
    else:
        plan = pool.plan(None)
        role_title = pool.role_title
        question_count = min(plan.budget, len(plan.allowed))
        estimated = max(10, question_count * 2)
        competencies = [c["label"] for c in pool.coverage([], plan)]
        speech_rate = 0.9

    return {
        "token": invite.token,
        "candidate_name": invite.candidate_name,
        # What the assessment does and does not look at. Served rather than
        # written into the candidate app, because both lists are enforced
        # elsewhere in this codebase and a reassurance the code has stopped
        # honouring is worse than no reassurance at all: `CRITERIA` is what the
        # evaluator scores, and `EXCLUSIONS` is what it is structurally
        # incapable of scoring, each paired with the mechanism that makes it so.
        # Which voice path this deployment can actually run. Served rather than
        # sniffed in the browser: whether Retell is configured is a server fact,
        # and the candidate app must not have to guess (or carry a key to find
        # out). "browser" is a working path, not a degraded one.
        "voice_mode": "retell" if retell_api.enabled() else "browser",
        "speech_rate": speech_rate,
        "assessed_on": list(CRITERIA),
        "not_assessed": [factor for factor, _mechanism in analytics.EXCLUSIONS],
        "role": invite.role,
        "role_title": role_title,
        "status": invite.effective_status,
        "question_count": question_count,
        "estimated_minutes": estimated,
        "competencies": competencies,
        "resumable": resumable,
        "session_id": invite.session_id if resumable else None,
        "answered": answered,
    }


#: What a device check can conclude. `"text"` was here while a failed check
#: could divert into a typed interview; it cannot now — a check either passes
#: or the candidate cannot sit the interview on that device.
PRECHECK_OUTCOMES = ("ready", "failed")


@router.post("/api/invite/{token}/precheck")
async def report_precheck(
    token: str, body: PrecheckReport,
    _: None = Depends(ratelimit.limiter("invitation")),
) -> dict[str, Any]:
    """Record how the device check went. Never a gate.

    The server does not decide whether a candidate may start on the strength of
    this — `start_session` gates on consent and on the invitation, both of which
    are facts the server owns. Without it the funnel simply stops at "opened the
    link", and a pilot cannot tell somebody who walked away from somebody whose
    microphone never worked.
    """
    invite = invites.get(token)
    if invite is None:
        raise HTTPException(404, "Unknown invite token.")
    outcome = body.outcome if body.outcome in PRECHECK_OUTCOMES else "failed"
    audit.product(
        audit.SYSTEM_CHECK_REPORTED,
        actor="candidate", subject_type="invitation",
        subject_id=f"{invite.token[:8]}…",
        interview_id=invite.interview_id,
        version=invite.interview_version,
        outcome=outcome,
        # A short, closed-vocabulary reason from the client. Truncated because
        # nothing on the far side of this call is trusted.
        reason=(body.reason or "")[:80],
        pilot_run=pilot.active_run_id(_owning_organization(invite.interview_id)),
    )
    return {"recorded": True, "outcome": outcome}


# --------------------------------------------------------------------------- #
#  Session
# --------------------------------------------------------------------------- #
@router.post("/api/session/start")
async def start_session(
    body: StartRequest, request: Request, response: Response,
    _: None = Depends(ratelimit.limiter("invitation")),
) -> dict[str, Any]:
    invite = invites.get(body.token)
    if invite is None:
        raise HTTPException(404, "Unknown invite token.")

    # Resuming an interview already under way is always allowed; STARTING a new
    # one is what the lifecycle gates. Every transition here is decided by the
    # server: a browser cannot declare an invitation complete or expired, which
    # are exactly the two states a candidate would most like to control.
    existing = store.try_load(invite.session_id) if invite.session_id else None
    resuming = bool(existing and existing.phase not in ("complete", "abandoned"))
    if not resuming:
        usable, reason = invite.can_start_session()
        if not usable:
            audit.product(
                audit.INVITATION_EXPIRED if invite.expired else audit.INVITATION_REVOKED,
                actor="candidate", subject_type="invitation",
                subject_id=f"{invite.token[:8]}…", reason=reason,
            )
            raise HTTPException(410, "This interview link isn't available any more.")
    if not body.consent_recording:
        raise HTTPException(400, "The interview can't start without consent to record.")

    # Resume rather than restart if a live session is still inside the window.
    if resuming and existing is not None:
        reply = await asyncio.to_thread(orch.resume, existing)
        # Re-issue the grant: the candidate has proved they hold the invitation,
        # and a reopened link on a new device has no cookie yet.
        _grant(existing, response)
        return {"session_id": existing.session_id, "resumed": True, "reply": reply.as_dict()}

    # Pin the version the moment the session is created, and never move it.
    # An invitation minted before versioning (or against a since-republished
    # interview) resolves to whatever is published right now — once. After this
    # line, publishing v(n+1) cannot reach this candidate.
    version = invite.interview_version
    if invite.interview_id and not version:
        latest = versions.latest_published(invite.interview_id)
        version = latest.version if latest else 0

    state = SessionState.new(
        candidate_name=invite.candidate_name,
        candidate_id=invite.candidate_id,
        role=invite.role,
        invite_token=invite.token,
        interview_id=invite.interview_id,
        interview_version=version,
    )
    # Which pilot batch this belongs to, decided once, here. Nothing about the
    # interview changes because of it — it is an attribution, not a mode.
    state.pilot_run_id = pilot.active_run_id(
        _owning_organization(invite.interview_id))
    state.consent_recording = True
    state.accommodations = body.accommodations
    # One line, letters and spaces — it is read aloud, so newlines and markup
    # have nowhere sensible to go, and an unbounded string would let a
    # candidate put a paragraph into Tara's mouth.
    state.preferred_name = " ".join(body.preferred_name.split())[:40]
    state.channel = "voice"

    _grant(state, response)
    reply = await asyncio.to_thread(orch.start, state)

    invite.session_id = state.session_id
    invite.status = "in_progress"
    invites.update(invite)

    audit.product(
        audit.SESSION_CREATED, actor="candidate", subject_type="session",
        subject_id=state.session_id, interview_id=state.interview_id,
        version=state.interview_version, invitation=f"{invite.token[:8]}…",
        candidate_id=state.candidate_id, pilot_run=state.pilot_run_id,
    )

    return {"session_id": state.session_id, "resumed": False, "reply": reply.as_dict()}


def _socket_is_authorised(ws: WebSocket, state: SessionState) -> bool:
    """The same proof the HTTP routes require, over a socket.

    Cookies are sent on the WebSocket handshake, so the browser needs nothing
    extra; a client without a cookie jar may present the invitation token as a
    query parameter, which is the secret it already holds.
    """
    grant = ws.cookies.get(security.CANDIDATE_COOKIE, "")
    if grant and state.session_grant and grant == state.session_grant:
        return True
    supplied = (ws.query_params.get("token") or "").strip()
    return bool(supplied and state.invite_token and supplied == state.invite_token)


def _owning_organization(interview_id: str) -> str | None:
    """Which organization an invitation's interview belongs to.

    Used only to attribute a session to the right organization's pilot run. The
    candidate never names it — it is read from the interview the invitation
    already points at.
    """
    cfg = interviews.get(interview_id) if interview_id else None
    return cfg.organization_id if cfg is not None else None


def _grant(state: SessionState, response: Response) -> None:
    """Bind this browser to this session, and to no other.

    A session id is a uuid4 and hard to guess, but "hard to guess" is not
    authorization: the candidate routes used to accept any session id from
    anyone. The grant is a separate secret, minted here, stored on the session,
    and returned as an HttpOnly cookie the candidate app never has to read.
    """
    if not state.session_grant:
        state.session_grant = secrets.token_urlsafe(32)
        store.save(state)
    response.set_cookie(
        security.CANDIDATE_COOKIE, state.session_grant,
        max_age=7 * 24 * 3600, **security.candidate_cookie_kwargs(),
    )


def _session_payload(state: SessionState) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "candidate_name": state.candidate_name,
        "role_title": orch._context(state)[0].role_title,  # noqa: SLF001 — same package
        "phase": state.phase,
        "channel": state.channel,
        "progress": orch._progress(state),  # noqa: SLF001 — same package, single source of truth
        "transcript": [
            {"speaker": u.speaker, "text": u.text, "at": u.at, "kind": u.kind}
            for u in state.transcript
        ],
    }


@router.get("/api/session/{session_id}")
async def read_session(
    session_id: str,
    scope: security.CandidateScope = Depends(security.candidate_scope),
) -> dict[str, Any]:
    """The resume payload — for the candidate whose session it is.

    `candidate_scope` has already proved that, from the grant cookie or the
    invitation token, and answers 404 otherwise: a candidate who guesses another
    session id must not learn that they guessed right.
    """
    state = store.try_load(scope.session_id)
    if state is None:
        raise HTTPException(404, "No such session.")
    return _session_payload(state)


#: How many recent turn ids a session remembers. A duplicate arrives seconds
#: after the original — from an HTTP retry or a re-send after a socket flap —
#: so a short memory catches every realistic replay, and an unbounded one would
#: grow the session file for the rest of the interview.
TURN_MEMORY = 24


def _remember_turn(state: SessionState, turn_id: str | None, reply: dict[str, Any]) -> None:
    if not turn_id:
        return
    state.applied_turns[turn_id] = reply
    excess = len(state.applied_turns) - TURN_MEMORY
    for stale in list(state.applied_turns)[:excess] if excess > 0 else []:
        del state.applied_turns[stale]
    store.save(state)


def _replayed_turn(
    state: SessionState, turn_id: str | None, session_id: str
) -> dict[str, Any] | None:
    """The reply this turn already produced, if it has been delivered before."""
    if not turn_id:
        return None
    seen = state.applied_turns.get(turn_id)
    if seen is None:
        return None
    store.audit(session_id, "turn_replayed", turn_id=turn_id)
    return seen


def _apply_turn(state: SessionState, body: TurnRequest):
    if body.action == "repeat":
        return orch.on_repeat(state)
    if body.action == "silence":
        return orch.on_silence(state)
    if body.action == "end":
        return orch.on_end(state)
    return orch.on_answer(state, body.said or "")


@router.post("/api/session/{session_id}/turn")
async def take_turn(
    session_id: str, body: TurnRequest,
    scope: security.CandidateScope = Depends(security.candidate_scope),
    _: None = Depends(ratelimit.limiter("turn", by_path="session_id")),
) -> dict[str, Any]:
    state = store.try_load(scope.session_id)
    if state is None:
        raise HTTPException(404, "No such session.")
    replayed = _replayed_turn(state, body.turn_id, session_id)
    if replayed is not None:
        # The same answer again, not a second answer. Hand back what it produced
        # the first time and leave the transcript alone.
        return {"reply": replayed, "replayed": True}
    reply = await asyncio.to_thread(_apply_turn, state, body)
    payload = reply.as_dict()
    if reply.ends:
        _mark_complete(state)
    _remember_turn(state, body.turn_id, payload)
    return {"reply": payload}


def _mark_complete(state: SessionState) -> None:
    """Called only when the ORCHESTRATOR ends the interview.

    There is deliberately no endpoint a candidate can call to mark themselves
    complete — completion is reached through the turn loop or not at all.
    """
    import time as _t

    invite = invites.get(state.invite_token)
    if invite and invite.status != "complete":
        invite.status = "complete"
        invite.completed_at = _t.time()
        invites.update(invite)

    # Queue the recruiter-side evaluation. This writes a pending record and a
    # frozen snapshot; it calls no model and blocks on nothing, so a candidate's
    # last turn is not sitting behind an extraction pipeline. Anything that goes
    # wrong is swallowed inside `on_interview_completed` — the interview ending
    # correctly matters more than the evaluation being queued, and the console
    # can always request it explicitly.
    from services.evaluation import jobs as _jobs

    _jobs.on_interview_completed(state)


# --------------------------------------------------------------------------- #
#  WebSocket — what the live interview screen uses
# --------------------------------------------------------------------------- #
@router.websocket("/ws/interview/{session_id}")
async def interview_socket(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    state = store.try_load(session_id)
    if state is None or not _socket_is_authorised(ws, state):
        # One message for both cases. A socket that says "wrong credential" tells
        # whoever opened it that the session id was right.
        await ws.send_text(json.dumps({"type": "error", "message": "No such session."}))
        await ws.close()
        return

    await ws.send_text(json.dumps({"type": "hello", **_session_payload(state)}))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = msg.get("type")
            if kind == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
                continue

            # Reload each turn: the same session may have been advanced over
            # HTTP, and state on disk is the single source of truth.
            state = store.try_load(session_id) or state

            turn_id = msg.get("turn_id") or None
            if kind == "answer":
                body = TurnRequest(said=msg.get("text", ""), turn_id=turn_id)
            elif kind in ("silence", "repeat", "end"):
                body = TurnRequest(action=kind, turn_id=turn_id)
            else:
                continue

            replayed = _replayed_turn(state, turn_id, session_id)
            if replayed is not None:
                await ws.send_text(json.dumps({"type": "say", "reply": replayed}))
                continue

            # Tell the UI we're thinking, so the orb can show it rather than
            # sitting dead through an LLM round-trip.
            await ws.send_text(json.dumps({"type": "thinking"}))
            try:
                reply = await asyncio.to_thread(_apply_turn, state, body)
            except Exception as exc:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                store.audit(session_id, "turn_failed", error=str(exc)[:300])
                await ws.send_text(json.dumps({
                    "type": "say",
                    "reply": {"text": "Sorry — could you say that once more?", "kind": "repeat",
                              "item_id": None, "ends": False, "progress": {},
                              "awaiting_same_answer": True},
                }))
                continue

            payload = reply.as_dict()
            if reply.ends:
                _mark_complete(state)
            _remember_turn(state, turn_id, payload)
            await ws.send_text(json.dumps({"type": "say", "reply": payload}))

    except WebSocketDisconnect:
        # Do not finalise. State is on disk after every turn, so reopening the
        # link resumes; holding the session open would strand the candidate.
        store.audit(session_id, "socket_disconnected")
        return
