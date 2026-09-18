"""Retell as transport — the orchestrator stays the brain.

Two kinds of test here. The turn-taking ones pin behaviour that came from real
failed calls and is invisible in the vendor's documentation. The authorisation
ones pin the two exemptions claimed in `services/security/matrix.py`, so the
exemption is a promise with a test behind it rather than a place to hide.
"""
from __future__ import annotations

import pytest

from services.api import retell


@pytest.fixture(autouse=True)
def _clean():
    retell._calls.clear()
    retell._last_seen.clear()
    retell._last_processed.clear()
    yield
    retell._calls.clear()
    retell._last_seen.clear()
    retell._last_processed.clear()


def _t(*rows: tuple[str, str]) -> list[dict]:
    return [{"role": role, "content": text} for role, text in rows]


# --------------------------------------------------------------------------- #
#  Turn-taking
# --------------------------------------------------------------------------- #
def test_it_stays_silent_while_one_answer_is_still_growing():
    """The RACING call: Retell grows a continuous answer in one row and fires
    every few seconds. Replying on each fire marched through the whole
    interview in about a minute."""
    call = "call_racing"
    first = _t(("agent", "Tell me about a hard ticket."), ("user", "So there was"))
    assert retell.turn_decision(call, first, False)[0] == "process"

    grown = _t(("agent", "Tell me about a hard ticket."),
               ("user", "So there was a customer who"))
    assert retell.turn_decision(call, grown, False)[0] == "listen"

    more = _t(("agent", "Tell me about a hard ticket."),
              ("user", "So there was a customer who had been waiting three days"))
    assert retell.turn_decision(call, more, False)[0] == "listen"


def test_a_new_row_means_the_previous_utterance_is_finished():
    """The SILENT call: short bursts, each a new row. Waiting for the block to
    stabilise meant Tara never replied at all."""
    call = "call_bursts"
    retell.turn_decision(call, _t(("agent", "Ready?"), ("user", "Yes.")), False)

    action, answer = retell.turn_decision(
        call, _t(("agent", "Ready?"), ("user", "Yes."), ("user", "Shall we start?")), False
    )
    assert action == "process"
    assert "Shall we start?" in answer


def test_a_reminder_always_breaks_the_silence():
    """Retell's 'gone quiet' signal must never be answered with more silence,
    or a candidate who pauses mid-sentence is left listening to nothing."""
    call = "call_reminder"
    transcript = _t(("agent", "Go on."), ("user", "I think"))
    retell.turn_decision(call, transcript, False)
    grown = _t(("agent", "Go on."), ("user", "I think the main thing"))
    assert retell.turn_decision(call, grown, False)[0] == "listen"
    assert retell.turn_decision(call, grown, True)[0] == "process"


def test_nothing_said_yet_is_a_nudge_only_on_a_reminder():
    call = "call_quiet"
    empty = _t(("agent", "Tell me about a hard ticket."))
    assert retell.turn_decision(call, empty, False)[0] == "listen"
    assert retell.turn_decision(call, empty, True)[0] == "nudge"


def test_the_same_finished_answer_is_never_processed_twice():
    """Retell resends events. Answering twice asks the next question twice."""
    call = "call_resend"
    transcript = _t(("agent", "Q?"), ("user", "A complete answer."))
    assert retell.turn_decision(call, transcript, False)[0] == "process"
    assert retell.turn_decision(call, transcript, False)[0] == "listen"


# --------------------------------------------------------------------------- #
#  Authorisation — the two matrix exemptions
# --------------------------------------------------------------------------- #
def test_an_unminted_call_id_reaches_no_interview():
    """The socket is unauthenticated, so the binding must be one the SERVER
    made. A call id nobody minted resolves to nothing, and a forged
    `call_details` naming someone else's session cannot change that."""
    retell.bind("call_real", "sess_real")
    assert retell.session_for("call_real") == "sess_real"
    assert retell.session_for("call_forged") == ""
    assert retell.session_for("") == ""


def test_forgetting_a_call_unbinds_it():
    retell.bind("call_done", "sess_done")
    retell.forget("call_done")
    assert retell.session_for("call_done") == ""


def test_voice_is_disabled_until_both_halves_are_configured(monkeypatch):
    """A key with no agent would mint a call against a configuration that does
    not exist; an agent with no key cannot mint one at all."""
    from services import config

    monkeypatch.setattr(config, "RETELL_API_KEY", "")
    monkeypatch.setattr(config, "RETELL_AGENT_ID", "")
    assert retell.enabled() is False

    monkeypatch.setattr(config, "RETELL_API_KEY", "key")
    assert retell.enabled() is False

    monkeypatch.setattr(config, "RETELL_AGENT_ID", "agent_x")
    assert retell.enabled() is True


def test_a_voice_call_cannot_be_minted_for_someone_elses_session(data_dir):
    """The mint endpoint is candidate-scoped: a call token is permission to
    speak into somebody's interview, so a caller holding neither the grant
    cookie nor the invitation token gets the same answer as for a session that
    does not exist."""
    from fastapi.testclient import TestClient

    from services.api.app import app

    with TestClient(app) as c:
        response = c.post("/api/session/sess_not_mine/voice")
    # 503 when Retell is not configured at all — checked before the lookup so a
    # deployment without voice does not leak which session ids are real.
    assert response.status_code in (404, 503)
    assert "not_mine" not in response.text


def test_an_unknown_session_is_a_404_and_never_a_500(data_dir, monkeypatch):
    """Caught on the live deployment, not in the suite.

    `sessions.load` RAISES for a session that does not exist — `try_load` is
    the one that returns None — so the `is None` guard never fired and an
    unknown id came back as a 500. Wrong twice over: it is the wrong status,
    and a 500-versus-404 split tells an attacker which session ids are real.
    """
    from fastapi.testclient import TestClient

    from services import config
    from services.api.app import app

    monkeypatch.setattr(config, "RETELL_API_KEY", "key_test")
    monkeypatch.setattr(config, "RETELL_AGENT_ID", "agent_test")

    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.post("/api/session/definitely-not-a-session/voice")
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
#  The opening
# --------------------------------------------------------------------------- #
def test_a_retell_call_opens_with_the_greeting_not_a_rejoin(data_dir, monkeypatch):
    """The bug every candidate heard and no test caught.

    `/api/session/start` runs the orchestrator before the browser asks for a
    call, so by the time Retell connects the interview is already under way.
    Calling `start()` again therefore took the RESUME path, and Tara's opening
    words on a first-ever call were "We're back — sorry about that" — no
    greeting, no name, no instructions, apologising for a disconnection that
    had not happened.
    """
    from services.api.retell import _opening
    from services.orchestrator.state import SessionState

    state = SessionState.new(
        candidate_name="Priya Sharma", candidate_id="c1", role="csr",
        invite_token="t", interview_id="iv", interview_version=1,
    )
    # What /api/session/start leaves behind: Tara has spoken, nobody answered.
    state.say("Hi Priya! I'm Tara. …Let's start with something straightforward.",
              "greeting", "q1")

    text, ends = _opening(state)
    assert text.startswith("Hi Priya!"), text
    assert "sorry about that" not in text.lower()
    assert ends is False


def test_a_genuine_rejoin_still_resumes(data_dir):
    """The resume path is right — it was only being reached at the wrong time."""
    from services.api.retell import _opening
    from services.orchestrator.state import SessionState

    state = SessionState.new(
        candidate_name="Priya Sharma", candidate_id="c1", role="csr",
        invite_token="t", interview_id="iv", interview_version=1,
    )
    state.say("Hi Priya! …", "greeting", "q1")
    state.heard("I handled a refund dispute last month.", item_id="q1")
    # What a real mid-interview session carries: the orchestrator has asked
    # something. Without this, `start()` correctly reads the session as never
    # begun and greets — which is right, and is why the test has to set it.
    state.asked_item_ids.append("q1")

    text, _ = _opening(state)
    assert not text.startswith("Hi Priya!"), "replayed the greeting mid-interview"
