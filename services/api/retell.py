"""Retell as the mouth and ears. The orchestrator stays the brain.

Retell handles microphone, speech-to-text, text-to-speech and turn-taking. It
decides none of the interview: every word Tara says is chosen here, by the same
`Orchestrator` the browser-speech path uses. That is the O2 boundary, and it is
load-bearing — the moment the vendor's own LLM is allowed to answer, the
interview stops being the published, versioned thing the recruiter approved.

The protocol is inverted from the rest of this app. Everywhere else the client
calls the server; here **Retell** connects to us and asks what to say:

    candidate's browser ──audio──▶ Retell ──websocket──▶ /llm-websocket/{id}
                        ◀─audio──         ◀──text─────   (this module)

So `VoiceChannel` on the client is the wrong seam for this, despite what its
docstring claims. That interface models a speech *engine* the client drives —
`speak(text)`, `listen()`. Retell is not that: it drives the conversation and
calls us. The client's job here shrinks to joining a call and rendering state.

Every hard-won detail below is ported from the prototype's `main.py`, which has
taken real calls. The comments name the live failures that produced each rule,
because none of them are guessable from the vendor's documentation.
"""
from __future__ import annotations

import json
import random
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from services import config
from services.data import invites
from services.data import sessions as store
from services.orchestrator.engine import Orchestrator

router = APIRouter(tags=["voice"])

_orch = Orchestrator()


def enabled() -> bool:
    """Whether this deployment can place a Retell call at all.

    Both halves are required and neither has a safe default: without the key we
    cannot mint a call, and without an agent we would be calling a
    configuration that does not exist. When this is False the candidate app
    uses browser speech, which is not a degraded Retell — it is a different,
    working path.
    """
    return bool(config.RETELL_API_KEY and config.RETELL_AGENT_ID)


# --------------------------------------------------------------------------- #
#  Turn-taking
#
#  The single most important thing in this file, and the least obvious.
# --------------------------------------------------------------------------- #
#: call_id → session_id, recorded when WE mint the call.
#:
#: This is the socket's only source of truth for whose interview it is holding,
#: and the reason is a real hole rather than a theoretical one. Taking the
#: session id from the `call_details` message — which is what the prototype
#: does — means trusting a value that arrives over an unauthenticated socket:
#: anyone who can reach `/llm-websocket/anything` could send a `call_details`
#: naming somebody else's session and drive their interview. Retell does not
#: authenticate this direction, so the binding has to be one the server
#: established itself, from a `call_id` that came back from Retell's own API in
#: response to a request an authorised candidate made.
#:
#: In-process, like the progress board, and with the same caveat: one worker.
#: A call minted on worker A and socketed to worker B would not resolve. That
#: holds for the documented single-process deployment; behind two workers this
#: needs a shared store, and failing closed (no session, no interview) is the
#: correct behaviour in the meantime.
_calls: dict[str, str] = {}

#: call_id → (agent_turns, row_count, last_row_text) at the previous fire.
_last_seen: dict[str, tuple[int, int, str]] = {}
#: call_id → (agent_turns, answer) already processed, so a resent event is not
#: answered twice.
_last_processed: dict[str, tuple[int, str]] = {}


def _current_answer_block(transcript: list[dict]) -> tuple[int, str, int, str]:
    """Everything the candidate has said since Tara last spoke.

    Returns (agent turns so far, the joined answer, how many candidate rows it
    spans, the text of the last row). The row count and last row are what the
    debounce below reasons about.
    """
    agent_turns = sum(1 for row in transcript if row.get("role") == "agent")
    rows: list[str] = []
    for row in reversed(transcript):
        if row.get("role") == "agent":
            break
        content = (row.get("content") or "").strip()
        if content:
            rows.append(content)
    rows.reverse()
    return agent_turns, " ".join(rows).strip(), len(rows), (rows[-1] if rows else "")


def turn_decision(
    call_id: str, transcript: list[dict], is_reminder: bool
) -> tuple[str, str]:
    """What to do when Retell asks for a response. Pure, so it is testable.

    Two real calls define the correct behaviour, and getting this wrong is
    exactly the "it races" and "it talks over me" feedback:

    * **Racing.** The candidate spoke continuously and Retell kept the answer
      in ONE row, growing it in place and firing every few seconds. Replying on
      each fire marched through every question in about a minute.
      → While the same row is still being extended, stay silent.

    * **Silence.** The candidate spoke in short bursts, each a NEW row and each
      a finished turn. An earlier fix waited for the text to "stabilise", but
      every burst grew the block, so Tara never replied at all.
      → A new row means the previous utterance is done. Reply.

    THE RULE: listen only while the last row is growing in place. A new row,
    stable text, or a reminder (Retell's "gone quiet" signal) all mean the turn
    is over. This cannot get stuck silent — a row cannot grow forever, and all
    three of the other cases produce a reply.

    Returns ("listen", ""), ("nudge", "") or ("process", answer).
    """
    agent_turns, answer, rows, last_row = _current_answer_block(transcript)

    if not answer:
        _last_seen[call_id] = (agent_turns, 0, "")
        return ("nudge" if is_reminder else "listen", "")

    # Retell resends events. Answering the same finished answer twice asks the
    # next question twice.
    if _last_processed.get(call_id) == (agent_turns, answer):
        return ("nudge" if is_reminder else "listen", "")

    prev_turns, prev_rows, prev_last = _last_seen.get(call_id, (-1, 0, ""))
    _last_seen[call_id] = (agent_turns, rows, last_row)

    growing_in_place = (
        agent_turns == prev_turns and rows == prev_rows and last_row != prev_last
    )
    if growing_in_place and not is_reminder:
        return ("listen", "")

    _last_processed[call_id] = (agent_turns, answer)
    return ("process", answer)


def bind(call_id: str, session_id: str) -> None:
    """Record that this call belongs to this session. Server-side only."""
    if call_id and session_id:
        _calls[call_id] = session_id


def session_for(call_id: str) -> str:
    """Whose interview this call is. Empty for a call we did not mint."""
    return _calls.get(call_id, "")


def forget(call_id: str) -> None:
    """Drop a finished call's state."""
    _calls.pop(call_id, None)
    _last_seen.pop(call_id, None)
    _last_processed.pop(call_id, None)


# --------------------------------------------------------------------------- #
#  Minting a call
# --------------------------------------------------------------------------- #
@router.post("/api/session/{session_id}/voice")
async def create_web_call(session_id: str, request: Request) -> dict[str, Any]:
    """A Retell web call bound to an existing interview session.

    The candidate's own session cookie authorises this — the same check the
    interview websocket uses — because a call token is permission to speak into
    somebody's interview. The session id travels to Retell as metadata and comes
    back on `call_details`, which is how the socket below knows whose interview
    it is holding.
    """
    if not enabled():
        raise HTTPException(503, "Voice calling is not configured for this deployment.")

    # try_load, not load: `load` raises for a session that does not exist, so
    # the `is None` check below never fired and an unknown session id came back
    # as a 500 with "Internal Server Error" — both a worse answer than 404 and
    # a different one, which is itself a way to tell real session ids from
    # invented ones.
    state = store.try_load(session_id)
    if state is None:
        raise HTTPException(404, "No such session.")
    if not _session_belongs_to_caller(request, state):
        # Non-disclosing, like every other candidate-scoped read: a session
        # that is not yours is indistinguishable from one that is not there.
        raise HTTPException(404, "No such session.")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.post(
                "https://api.retellai.com/v2/create-web-call",
                headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"},
                json={
                    "agent_id": config.RETELL_AGENT_ID,
                    # Everything the socket needs to resume the right interview.
                    # Retell echoes this back verbatim on `call_details`.
                    "metadata": {"session_id": session_id},
                    # Available to the agent's prompt as {{candidate_name}}.
                    # Tara's actual words still come from the orchestrator.
                    "retell_llm_dynamic_variables": {
                        "candidate_name": state.spoken_name,
                    },
                },
            )
    except Exception as exc:  # noqa: BLE001 — a transport failure is not a crash
        raise HTTPException(502, "Couldn't reach the voice service.") from exc

    if response.status_code >= 400:
        # Never the vendor's body: it can echo configuration back, and this
        # response goes to a candidate's browser.
        raise HTTPException(502, "The voice service refused the call.")

    body = response.json()
    # The binding, from Retell's own answer. Nothing the socket later receives
    # can change whose interview that call is attached to.
    bind(str(body.get("call_id") or ""), session_id)
    # Only what the browser SDK needs. The call object also carries account
    # detail that has no business leaving the server.
    return {
        "access_token": body.get("access_token", ""),
        "call_id": body.get("call_id", ""),
    }


def _session_belongs_to_caller(request: Request, state: Any) -> bool:
    """The same proof every other candidate route requires.

    The grant cookie minted at `/api/session/start`, or — for a client without
    a cookie jar — the invitation token, which is a secret the candidate
    already holds. Deliberately the same two accepted proofs as
    `candidate._socket_is_authorised`, so the voice path cannot end up with a
    weaker door than the one beside it.
    """
    from services import security

    grant = request.cookies.get(security.CANDIDATE_COOKIE, "")
    if grant and state.session_grant and grant == state.session_grant:
        return True
    supplied = (request.query_params.get("token") or "").strip()
    return bool(supplied and state.invite_token and supplied == state.invite_token)


# --------------------------------------------------------------------------- #
#  The protocol
# --------------------------------------------------------------------------- #
#: Spoken the instant the candidate stops, while the orchestrator is still
#: classifying the answer and choosing the next question. Retell lets one
#: response_id stream several chunks, so this fills the gap with a human sound
#: instead of dead air. It changes nothing about what is actually asked.
_FILLERS = ("Mm-hmm.", "Okay.", "Got it.", "I see.", "Right.")


@router.websocket("/llm-websocket/{call_id}")
async def llm_websocket(ws: WebSocket, call_id: str) -> None:
    """Retell's custom-LLM protocol, answered by our orchestrator."""
    await ws.accept()
    # Resolved from the call id in the URL, which Retell took from the call we
    # minted — never from anything the socket sends us.
    session_id = session_for(call_id)
    opened = False

    async def say(response_id: Any, content: str, *, end: bool = False,
                  complete: bool = True) -> None:
        await ws.send_text(json.dumps({
            "response_type": "response",
            "response_id": response_id,
            "content": content,
            "content_complete": complete,
            "end_call": end,
        }))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = msg.get("interaction_type")

            if kind == "ping_pong":
                await ws.send_text(json.dumps({
                    "response_type": "ping_pong", "timestamp": msg.get("timestamp"),
                }))
                continue

            if kind == "call_details":
                # Do NOT speak here. Retell does not reliably speak a reply sent
                # to `call_details` — that message is informational. Sending the
                # greeting here is the "candidate heard nothing, and their first
                # words were swallowed as the acknowledgement" bug.
                #
                # And note what this does NOT do: read the session id out of
                # the message. See `_calls`.
                continue

            if kind == "update_only":
                continue

            if kind not in ("response_required", "reminder_required"):
                continue

            response_id = msg.get("response_id", 0)
            state = store.try_load(session_id) if session_id else None

            if state is None:
                await say(response_id, "Sorry — I can't find your interview. "
                                       "Please reopen your interview link.", end=True)
                return

            if not opened:
                # The greeting goes on the FIRST response_required, which
                # Retell always speaks.
                opened = True
                text, ends = _opening(state)
                await say(response_id, text, end=ends)
                continue

            action, answer = turn_decision(
                call_id, msg.get("transcript", []) or [],
                kind == "reminder_required",
            )
            if action == "listen":
                # Empty content keeps the mic open and says nothing. This is
                # the line that stops Tara talking over a candidate mid-answer.
                await say(response_id, "")
                continue
            if action == "nudge":
                reply = _orch.on_silence(state)
                store.save(state)
                await say(response_id, reply.text, end=reply.ends)
                continue

            await say(response_id, random.choice(_FILLERS), complete=False)
            reply = _orch.on_answer(state, answer)
            store.save(state)
            await say(response_id, reply.text, end=reply.ends)
            if reply.ends:
                _finish(state)

    except WebSocketDisconnect:
        # State is saved every turn, so reconnecting on the same call resumes
        # mid-interview. A dropped socket is not a finished interview.
        return
    except Exception:  # noqa: BLE001 — never take the socket down on one bad turn
        try:
            await say(msg.get("response_id", 0) if isinstance(msg, dict) else 0,
                      "Sorry, could you say that once more?")
        except Exception:  # noqa: BLE001
            pass
    finally:
        forget(call_id)


def _opening(state: Any) -> tuple[str, bool]:
    """The first thing Tara says on a Retell call.

    This used to be `orch.start(state)`, and that was wrong in a way no test
    caught and every candidate heard.

    `/api/session/start` has ALREADY run the orchestrator: it created the
    session and generated the greeting and the first question before the
    browser ever asked for a call. So by the time Retell connects,
    `asked_item_ids` is non-empty, and `start()` correctly interprets that as
    "this interview is already under way" and takes the RESUME path. The
    candidate's opening words from Tara were therefore:

        "We're back — sorry about that. Where we were: <question>"

    No greeting, no name, no instructions — on a first-ever call, apologising
    for a disconnection that never happened. The orchestrator was behaving
    correctly; the caller was asking the wrong question.

    What is actually wanted is the utterance the orchestrator already produced.
    It is on the transcript, so it is replayed verbatim rather than
    regenerated — regenerating would select a different question and leave the
    session state pointing at one thing while the candidate heard another.
    """
    spoken = [u for u in state.transcript if u.speaker == "tara"]
    heard = [u for u in state.transcript if u.speaker == "candidate"]

    # Started, and the candidate has not said anything yet: replay the opening.
    if spoken and not heard:
        return spoken[0].text, False

    # A genuine rejoin, or a session somehow never started. `start()` routes to
    # resume or to the greeting as appropriate.
    reply = _orch.start(state)
    store.save(state)
    return reply.text, reply.ends


def _finish(state: Any) -> None:
    """Close the invitation out, exactly as the browser-speech path does."""
    invite = invites.get(state.invite_token) if state.invite_token else None
    if invite and invite.status != "complete":
        invite.status = "complete"
        import time

        invite.completed_at = time.time()
        invites.update(invite)
