"""The candidate runtime's behaviour, pinned.

`tara-candidate` is the source of truth for how an interview runs, and this file
is what stops a refactor changing it quietly. Everything here was true of the
runtime before it moved into this repository and must stay true after.

No provider is called: the classifier falls back to heuristics with no key, so
these run offline and deterministically.
"""
from __future__ import annotations

import pytest

from services import config
from services.data import interviews
from services.data import sessions as store
from services.data.interviews import InterviewConfig
from services.ai.workloads.interview_designer import Skill
from services.orchestrator.engine import Orchestrator
from services.orchestrator.state import SessionState


@pytest.fixture()
def interview(data_dir, pool):
    cfg = interviews.save(
        InterviewConfig(
            id="iv_rt", title="Runtime", role=pool.role, role_title=pool.role_title,
            skills=[
                Skill(name=c.label, competency_id=c.id, evaluated=True, pool_competency=c.id,
                      priority="high" if c.weight >= 0.18 else "medium")
                for c in pool.competencies
            ],
        )
    )
    interviews.publish(cfg, pool)
    return cfg


@pytest.fixture()
def session(interview, pool):
    return SessionState.new("Priya", "cand_1", pool.role, invite_token="t",
                            interview_id=interview.id, interview_version=1)


@pytest.fixture()
def orch(monkeypatch):
    o = Orchestrator()
    # No provider: the heuristic classifier runs, and no probe is generated, so
    # the authored banks are exercised — which is the path that must never break.
    monkeypatch.setattr(o.llm, "read_answer",
                        lambda q, a, lf: _heuristic(q, a, lf))
    monkeypatch.setattr(o.llm, "write_probe", lambda *a, **k: {"probe": ""})
    return o


def _heuristic(question, answer, looking_for):
    from services.ai.workloads.answer_classifier import classify_heuristic

    return classify_heuristic(question, answer, looking_for)


SUBSTANTIVE = (
    "Last winter a customer called about a failed renewal and her whole team was locked "
    "out mid-shift. I said I could hear it had landed badly, issued a seven-day grace "
    "extension while we sorted the card, confirmed access with her on the line, and only "
    "then walked through why the payment failed. She emailed the next day to say it was "
    "the calmest support call she had had, which told me the order mattered more than the "
    "explanation did."
)
THIN = "I'd just try to help them out as best I can."


# --------------------------------------------------------------------------- #
#  Question selection
# --------------------------------------------------------------------------- #
def test_selection_is_deterministic(interview, pool):
    """Same inputs, same interview — the property the recruiter preview rests on."""
    from services.data import versions
    from services.orchestrator.pool import Pool

    defn = versions.definition_for("iv_rt", 1)
    p = Pool.from_definition(defn)
    plan = p.plan_from_definition(defn)
    runs = [[i.id for i in p.running_order(plan)] for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_the_first_question_is_always_a_warm_up(interview, pool, orch, session):
    """Nobody should meet a hard scenario as the first thing they hear."""
    orch.start(session)
    first = orch._context(session)[0].item(session.asked_item_ids[0])
    assert first.difficulty == "easy"


def test_selection_respects_the_budget(interview, pool, orch, session):
    orch.start(session)
    reply = None
    for _ in range(40):
        reply = orch.on_answer(session, SUBSTANTIVE)
        if reply.ends:
            break
    assert reply.ends
    assert len(session.asked_item_ids) == 8


# --------------------------------------------------------------------------- #
#  Probing
# --------------------------------------------------------------------------- #
def test_a_thin_answer_is_probed(interview, orch, session):
    orch.start(session)
    assert orch.on_answer(session, THIN).kind == "probe"


def test_an_answer_that_covers_everything_is_not_probed(interview, orch, session, monkeypatch):
    """Pushing further on a complete answer just wastes the candidate's time.

    Note what the rule actually is: depth alone does not stop a probe. A long,
    fluent answer that still leaves an expected signal unevidenced gets followed
    up — which is the behaviour that makes the interview adaptive rather than
    merely polite.
    """
    orch.start(session)
    item = orch._context(session)[0].item(session.current_item_id)
    monkeypatch.setattr(
        orch.llm, "read_answer",
        lambda q, a, lf, session_id="": {"intent": "answer", "depth": "substantive",
                                         "covered": list(item.looking_for), "missing": [],
                                         "affect": "neutral", "quote": ""},
    )
    assert orch.on_answer(session, SUBSTANTIVE).kind == "question"


def test_a_long_answer_with_a_missing_signal_is_still_probed(interview, orch, session,
                                                             monkeypatch):
    orch.start(session)
    item = orch._context(session)[0].item(session.current_item_id)
    monkeypatch.setattr(
        orch.llm, "read_answer",
        lambda q, a, lf: {"intent": "answer", "depth": "substantive",
                          "covered": list(item.looking_for[:1]),
                          "missing": list(item.looking_for[1:]),
                          "affect": "neutral", "quote": ""},
    )
    assert orch.on_answer(session, SUBSTANTIVE).kind == "probe"


def test_probes_are_capped_per_question(interview, orch, session):
    """No candidate gets stuck in an interrogation."""
    orch.start(session)
    first_item = session.asked_item_ids[0]
    for _ in range(6):
        reply = orch.on_answer(session, THIN)
        if reply.item_id != first_item:
            break
    record = session.records[first_item]
    assert record.probe_count <= 2


def test_a_rejected_probe_falls_back_to_the_authored_bank_never_to_silence(
    interview, orch, session, monkeypatch
):
    monkeypatch.setattr(orch, "_probes_may_be_generated", lambda s: True)
    monkeypatch.setattr(orch.llm, "write_probe",
                        lambda *a, **k: {"probe": "How old are you?"})  # blocked by legality

    orch.start(session)
    item_id = session.asked_item_ids[0]
    reply = orch.on_answer(session, THIN)

    assert reply.kind == "probe"
    assert "How old" not in reply.text
    assert any(p in reply.text for p in orch._context(session)[0].item(item_id).probe_bank)


# --------------------------------------------------------------------------- #
#  Non-answers: nobody gets stuck, and silence is never an answer
# --------------------------------------------------------------------------- #
def test_silence_does_not_advance_the_question(interview, orch, session):
    """Advancing on the first silence costs a candidate the item for thinking."""
    orch.start(session)
    item = session.current_item_id
    reply = orch.on_silence(session)
    assert session.current_item_id == item
    assert reply.awaiting_same_answer


def test_repeated_silence_offers_the_typed_channel_then_moves_on(interview, orch, session):
    """From the server, a broken microphone and a thoughtful pause look identical."""
    orch.start(session)
    item = session.current_item_id
    offered = False
    for _ in range(6):
        reply = orch.on_silence(session)
        offered = offered or reply.device_help_offered
        if session.current_item_id != item:
            break
    assert offered, "never offered the typed fallback"
    assert session.current_item_id != item, "the candidate was left stuck on a dead mic"


def test_an_unanswered_question_is_recorded_unanswered_not_scored_zero(
    interview, orch, session
):
    orch.start(session)
    item = session.asked_item_ids[0]
    for _ in range(6):
        orch.on_silence(session)
        if session.current_item_id != item:
            break
    record = session.records[item]
    assert record.answers == []
    assert record.closed_at is not None      # closed, so the interview moves on
    trail = [e["event"] for e in store.read_audit(session.session_id)]
    assert "item_unanswered" in trail


def test_asking_to_repeat_is_bounded(interview, orch, session):
    orch.start(session)
    item = session.current_item_id
    for _ in range(6):
        orch.on_repeat(session)
        if session.current_item_id != item:
            break
    assert session.current_item_id != item


def test_clarify_is_bounded_and_releases_the_candidate(interview, orch, session):
    orch.start(session)
    item = session.current_item_id
    for _ in range(6):
        reply = orch.on_answer(session, "What do you mean by that?")
        if reply.item_id != item:
            break
    assert session.current_item_id != item


# --------------------------------------------------------------------------- #
#  Rejoin
# --------------------------------------------------------------------------- #
def test_state_is_on_disk_after_every_turn(interview, orch, session):
    orch.start(session)
    orch.on_answer(session, SUBSTANTIVE)
    assert store.exists(session.session_id)

    reloaded = store.load(session.session_id)
    assert reloaded.asked_item_ids == session.asked_item_ids
    assert len(reloaded.transcript) == len(session.transcript)


def test_rejoining_reasks_the_question_that_was_on_the_table(interview, orch, session):
    """Repeating a question they already answered costs a few seconds; skipping
    one they never heard costs them the item."""
    orch.start(session)
    orch.on_answer(session, THIN)          # now mid-probe on question one
    reloaded = store.load(session.session_id)

    reply = orch.resume(reloaded)
    record = reloaded.current
    assert reply.item_id == record.item_id
    expected = record.probes_asked[-1] if record.probes_asked else record.prompt
    assert expected in reply.text


def test_rejoining_preserves_the_pinned_version(interview, orch, session, pool):
    orch.start(session)
    orch.on_answer(session, SUBSTANTIVE)

    # A new version is published while the candidate is disconnected.
    cfg = interviews.get("iv_rt")
    cfg.question_budget = 3
    interviews.save(cfg)
    interviews.publish(cfg, pool)

    reloaded = store.load(session.session_id)
    assert reloaded.interview_version == 1
    assert orch._plan(reloaded).budget == 8


def test_a_completed_session_cannot_be_reopened(interview, orch, session):
    orch.start(session)
    orch.on_end(session)
    assert session.phase == "complete"
    assert orch.on_answer(session, SUBSTANTIVE).ends


# --------------------------------------------------------------------------- #
#  Candidate safety
# --------------------------------------------------------------------------- #
def test_tara_never_praises_the_candidate(interview, orch, session):
    """"Great answer" tells a candidate how they are doing, changes how they
    behave for the rest of the interview, and is unfair to whoever got the
    flatter phrasing."""
    banned = ("great answer", "well done", "excellent", "perfect", "good job",
              "nice work", "that's a great")
    orch.start(session)
    for answer in (SUBSTANTIVE, THIN, SUBSTANTIVE, THIN):
        reply = orch.on_answer(session, answer)
        if reply.ends:
            break
        assert not any(b in reply.text.lower() for b in banned), reply.text


def test_nothing_the_candidate_sees_carries_a_score(interview, orch, session):
    orch.start(session)
    reply = orch.on_answer(session, SUBSTANTIVE)
    payload = reply.as_dict()
    assert "score" not in payload
    assert "level" not in payload
    assert set(payload["progress"]) <= {"asked", "answered", "total", "coverage", "phase"}


def test_the_progress_rail_never_exposes_the_question_pool(interview, orch, session):
    """The client is told what to say and how far along it is — never what is
    coming next."""
    orch.start(session)
    progress = orch._progress(session)
    for entry in progress["coverage"]:
        assert set(entry) == {"id", "label", "asked", "target"}
