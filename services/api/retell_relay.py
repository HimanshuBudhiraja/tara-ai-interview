"""Retell's new browser client (retell-client-js-sdk v3), pointed at our server.

Retell retires POST /v2/create-web-call and the browser's RetellWebClient on
2026-10-18. The v3 client normally creates calls itself with a public key,
which would put the scenario (persona instructions, questions, call length)
in the participant's browser. Instead, its address is set to this server:

    /call or /test-call   our gatekeepers, unchanged: session, booking, limits,
                          then POST /v3/create-web-call with the secret key.
                          The browser hands that answer to the v3 client.
    .../retell/v2/stop-call/{call_id}       the client's "end before it started".
    .../retell/v2/monitor-call/{call_id}    the live transcript: a WebSocket we
                                            relay to Retell with the secret key.
    .../retell/webrtc-proxy/{call_id}/...   the audio connection's set-up (WebRTC
                                            signalling) on Retell's "gateway"
                                            transport, passed through as-is. It
                                            carries the call's own access token,
                                            not our key; the audio itself flows
                                            browser <-> Retell directly.

Both work only for a call this participant (or this builder's agent) placed.
The secret key never leaves the server.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketException

from services import config
from services.data import agent_sessions as sessions
from services.data import built_agents as agents

RETELL = "https://api.retellai.com"
RETELL_WS = "wss://api.retellai.com"

participant_router = APIRouter(prefix="/api/participant/session/{session_id}/retell", tags=["participant"])
# Outside /api/recruiter on purpose (like the webhook): its own guard, no /api/admin alias.
builder_router = APIRouter(prefix="/api/agent-builder/agents/{agent_id}/retell", tags=["agent-builder"])


async def _stop(call_id: str) -> None:
    if not config.RETELL_API_KEY:
        return
    async with httpx.AsyncClient(timeout=15) as http:
        await http.post(f"{RETELL}/v2/stop-call/{call_id}", headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"})


_PASS_HEADERS = ("authorization", "content-type", "x-retell-client-js-sdk-version")


async def _signal(request: Request, call_id: str, rest: str) -> Response:
    """Pass one WebRTC signalling request through to Retell's gateway for this call."""
    if not rest.startswith("v1/webrtc/"):
        raise HTTPException(404, "Not found.")
    headers = {k: v for k, v in request.headers.items() if k.lower() in _PASS_HEADERS}
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.request(request.method, f"{RETELL}/webrtc-proxy/{call_id}/{rest}",
                               headers=headers, content=await request.body())
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


async def _relay(ws: WebSocket, call_id: str) -> None:
    """Pipe Retell's monitor stream for one call to the browser, and close codes back."""
    import websockets

    await ws.accept(subprotocol="bearer")
    try:
        async with websockets.connect(f"{RETELL_WS}/v2/monitor-call/{call_id}",
                                      subprotocols=["bearer", config.RETELL_API_KEY], open_timeout=15) as up:
            async def down() -> None:
                async for msg in up:
                    await ws.send_text(msg if isinstance(msg, str) else msg.decode())

            async def upstream() -> None:
                while True:
                    m = await ws.receive()
                    if m.get("type") == "websocket.disconnect":
                        return
                    if m.get("text") is not None:
                        await up.send(m["text"])

            done, pending = await asyncio.wait({asyncio.create_task(down()), asyncio.create_task(upstream())},
                                               return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            code = up.close_code or 1000
    except Exception:  # noqa: BLE001 — the transcript is a convenience; the call goes on without it
        code = 1011
    try:
        await ws.close(code=code if 1000 <= code <= 4999 else 1011)
    except RuntimeError:
        pass


# --------------------------------------------------------------------------- #
#  Participant
# --------------------------------------------------------------------------- #
def _participant_call(ws_or_request: Any, session_id: str, call_id: str) -> dict[str, Any] | None:
    row = sessions.load(session_id)
    grant = ws_or_request.cookies.get("tara_participant", "")
    if (row is None or not grant or not row.get("grant_hash")
            or sessions.hash_grant(grant) != row["grant_hash"] or call_id not in (row.get("calls") or [])):
        return None
    return row


def participant_relay_scope(request: Request, session_id: str, call_id: str) -> dict[str, Any]:
    """The participant's own call, proven by their session cookie. Named so the access matrix sees it."""
    row = _participant_call(request, session_id, call_id)
    if row is None:
        raise HTTPException(404, "No such call.")
    return row


@participant_router.post("/v2/stop-call/{call_id}")
async def participant_stop(call_id: str, _: dict[str, Any] = Depends(participant_relay_scope)) -> dict[str, Any]:
    await _stop(call_id)
    return {"ok": True}


def participant_relay_ws_scope(websocket: WebSocket, session_id: str, call_id: str) -> dict[str, Any]:
    """The same check for the live-transcript WebSocket."""
    row = _participant_call(websocket, session_id, call_id)
    if row is None:
        raise WebSocketException(code=4404, reason="No such call.")
    return row


@participant_router.api_route("/webrtc-proxy/{call_id}/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def participant_signal(request: Request, call_id: str, rest: str,
                             _: dict[str, Any] = Depends(participant_relay_scope)) -> Response:
    return await _signal(request, call_id, rest)


@participant_router.websocket("/v2/monitor-call/{call_id}")
async def participant_monitor(ws: WebSocket, call_id: str, _: dict[str, Any] = Depends(participant_relay_ws_scope)) -> None:
    await _relay(ws, call_id)


# --------------------------------------------------------------------------- #
#  Builder (Test agent)
# --------------------------------------------------------------------------- #
def _builder_may(ws_or_request: Any, agent_id: str, call_id: str) -> bool:
    from services.security import principal as security

    row = agents.load(agent_id)
    if row is None or call_id not in {t.get("call_id") for t in row.get("tests") or []}:
        return False
    p = security.optional_principal(ws_or_request)
    if p is not None:
        return p.organization_id == row.get("org_id")
    host = ws_or_request.client.host if ws_or_request.client else ""
    open_demo = config.BUILDER_OPEN
    local = config.LOCAL_NO_LOGIN and not config.is_production() and host in {"127.0.0.1", "::1", "localhost"}
    return open_demo or local


def builder_relay_scope(request: Request, agent_id: str, call_id: str) -> None:
    """A test call this builder's agent placed. Named so the access matrix sees it."""
    if not _builder_may(request, agent_id, call_id):
        raise HTTPException(404, "No such call.")


@builder_router.post("/v2/stop-call/{call_id}")
async def builder_stop(call_id: str, _: None = Depends(builder_relay_scope)) -> dict[str, Any]:
    await _stop(call_id)
    return {"ok": True}


def builder_relay_ws_scope(websocket: WebSocket, agent_id: str, call_id: str) -> None:
    """The same check for the live-transcript WebSocket."""
    if not _builder_may(websocket, agent_id, call_id):
        raise WebSocketException(code=4404, reason="No such call.")


@builder_router.api_route("/webrtc-proxy/{call_id}/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def builder_signal(request: Request, call_id: str, rest: str, _: None = Depends(builder_relay_scope)) -> Response:
    return await _signal(request, call_id, rest)


@builder_router.websocket("/v2/monitor-call/{call_id}")
async def builder_monitor(ws: WebSocket, call_id: str, _: None = Depends(builder_relay_ws_scope)) -> None:
    await _relay(ws, call_id)
