"""Three scripted candidates, driven through the real HTTP endpoints.

Marked `server` because it needs the API running (`make api`) and, with a key
configured, a live model. Run it with `pytest -m server`.

These are the tests that catch what unit tests cannot: that the whole loop —
select, deliver, capture, read, probe-or-advance — still terminates, still
covers the interview, and still adapts. The turn counts are the adaptivity: a
strong candidate finishes in roughly half the turns a thin one takes, and the
gap between them IS the product.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.server

BASE = "http://localhost:8000"


def _run(persona: str) -> dict:
    from tests.personas import PERSONAS, PROBE_REPLIES, RECOVERY

    client = httpx.Client(base_url=BASE, timeout=120)
    client.get("/api/health").raise_for_status()

    started = client.post(
        "/api/session/start",
        json={"token": "demo", "consent_recording": True, "channel": "text"},
    )
    started.raise_for_status()
    session_id = started.json()["session_id"]
    reply = started.json()["reply"]

    answers = PERSONAS[persona]
    probe_replies = list(PROBE_REPLIES[persona])
    turns = probes = probe_i = 0
    stalls: dict[str, int] = {}

    while not reply["ends"] and turns < 60:
        if reply["kind"] == "probe":
            said = probe_replies[probe_i % len(probe_replies)]
            probe_i += 1
        elif reply["kind"] in ("clarify", "repeat", "hold"):
            stalls[reply["item_id"]] = stalls.get(reply["item_id"], 0) + 1
            said = answers.get(reply["item_id"], "") if stalls[reply["item_id"]] <= 2 else RECOVERY
        else:
            said = answers.get(reply["item_id"], "I think I've covered what I'd do there.")

        body = {"said": said} if said else {"action": "silence"}
        r = client.post(f"/api/session/{session_id}/turn", json=body)
        r.raise_for_status()
        reply = r.json()["reply"]
        if reply["kind"] == "probe":
            probes += 1
        turns += 1

    summary = client.get(f"/api/session/{session_id}").json()
    return {"turns": turns, "probes": probes, "phase": summary["phase"],
            "progress": summary["progress"], "session_id": session_id}


@pytest.fixture(autouse=True)
def _fresh_demo_link():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "tools" / "make_invite.py"), "--reset-demo"],
                   cwd=root, capture_output=True, check=False)


@pytest.mark.parametrize("persona", ["strong", "thin", "messy"])
def test_every_persona_reaches_a_clean_end(persona):
    """However badly a turn goes, the interview terminates and covers its budget.

    Every holding pattern in the runtime is bounded on purpose; this is the test
    that would fail if one of them stopped being."""
    result = _run(persona)
    assert result["phase"] == "complete"
    assert result["progress"]["asked"] == result["progress"]["total"]


def test_a_thin_candidate_draws_follow_ups():
    """A candidate answering in platitudes must be followed up on.

    Deliberately NOT written as `thin_probes > strong_probes`. That comparison
    is true on average and was the headline number during development, but as a
    single-run assertion against a live model it flakes: one run measured here
    put a strong candidate at 16 follow-ups and another at 4, because how a model
    classifies depth varies. A test that fails for that reason teaches people to
    re-run the suite until it passes.

    The deterministic version of this claim — thin answers get probed, answers
    that cover every expected signal do not — is asserted against a stubbed
    classifier in test_runtime_preserved.py, where it belongs. What is worth
    checking against a live model is that the loop actually engages at all.
    """
    thin = _run("thin")
    assert thin["probes"] > 0, "a platitude-only candidate was never followed up on"
    assert thin["phase"] == "complete"
