"""Role-play API.

    GET  /api/roleplay/agents               the library
    GET  /api/roleplay/scenarios            what is configured, per agent
    GET  /api/roleplay/scenario/{id}        one configuration, as a reviewer sees it
    POST /api/roleplay/session/start        briefing + the counterparty's first line
    POST /api/roleplay/session/{id}/turn    one exchange
    GET  /api/roleplay/session/{id}         resume
    GET  /api/roleplay/session/{id}/result  what the POLICY allows this person to see

Two rules this file exists to enforce, both about what must never leave the
server.

**The result endpoint answers to the policy, not to the caller.** A subject on
a hiring scenario gets "complete" and nothing else; on a certification they get
pass/fail and the lines they missed; on practice they get everything. The
decision lives in one place because a second place would eventually disagree
with the first, and the failure mode is a candidate reading their own scoring
key.

**Nothing here returns the scoring key, the beat list, or a red flag to the
person who sat the scenario.** `looking_for`, `red_flags` and
`expected_behaviours` are authored assessment content: a subject who can see
them can rehearse them, and everyone who sits the scenario afterwards is
measured against a key that has leaked.

Session routes are grant-scoped, the same way the candidate side is. A session
id is a uuid4 and hard to guess, but hard to guess is not authorization — and
these routes return a transcript. `start` mints a secret, stores it on the
session and returns it as an HttpOnly cookie; every later call on that session
proves it. The three content routes are genuinely public: they return a
scenario's briefing and situation, which the subject is told anyway, and never
the scoring key.
"""
from __future__ import annotations

from typing import Any

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from packages.types.agent import LIBRARY, get_agent
from packages.types.scenario import ScenarioDefinition
from services.assessment import knowledge, scenarios
from services.data import roleplay_sessions as store
from services.orchestrator.roleplay import RoleplayEngine, RoleplayState, evidence
from services.security import principal as security

router = APIRouter(prefix="/api/roleplay", tags=["roleplay"])

#: Loaded once. Authored content is read-only at runtime and re-reading it per
#: request would mean two people in the same scenario could be running
#: different versions of it if a file changed mid-session.
_SCENARIOS: dict[str, ScenarioDefinition] = {}
_KNOWLEDGE: dict[str, Any] = {}


def _content() -> tuple[dict[str, ScenarioDefinition], dict[str, Any]]:
    global _SCENARIOS, _KNOWLEDGE
    if not _SCENARIOS:
        _SCENARIOS = scenarios.load_all()
        _KNOWLEDGE = knowledge.load_all()
    return _SCENARIOS, _KNOWLEDGE


def _engine() -> RoleplayEngine:
    _, kb = _content()
    return RoleplayEngine(knowledge=kb)


def roleplay_scope(request: Request, session_id: str) -> RoleplayState:
    """Prove this browser owns this session, then hand back its state.

    Named as the dependency so `services.security.matrix.derive` can see it in
    the route's wiring — the access matrix reads the dependency graph rather
    than trusting a declaration, and a guard it cannot see is a guard the audit
    cannot confirm.

    A session with no grant on it is refused rather than allowed. The opposite
    default would mean any row written before this existed stayed readable by
    anyone who learned its id.
    """
    state = store.try_load(session_id)
    if state is None:
        raise HTTPException(404, "no such session")
    grant = request.cookies.get(security.CANDIDATE_COOKIE, "")
    if not state.session_grant or grant != state.session_grant:
        # 404, not 403: confirming that an id is real tells an attacker which
        # ids are worth attacking. Same rule as the tenant boundary.
        raise HTTPException(404, "no such session")
    return state


def _scenario(scenario_id: str) -> ScenarioDefinition:
    defn = _content()[0].get(scenario_id)
    if defn is None:
        raise HTTPException(404, f"no scenario '{scenario_id}'")
    return defn


# --------------------------------------------------------------------------- #
#  The library
# --------------------------------------------------------------------------- #
@router.get("/agents")
def agents() -> dict[str, Any]:
    defns, _ = _content()
    out = []
    for agent_type, agent in LIBRARY.items():
        configs = [d for d in defns.values() if d.agent_type == agent_type]
        out.append({
            "agent_type": agent_type,
            "name": agent.name,
            "identity": agent.identity,
            "purpose": agent.purpose,
            "dimensions": agent.evaluation.dimensions,
            "permanent_guardrails": len(agent.permanent_guardrails),
            "scenarios": [_card(d) for d in configs],
        })
    return {"poc": True, "agents": out}


def _card(defn: ScenarioDefinition) -> dict[str, Any]:
    return {
        "scenario_id": defn.scenario_id,
        "title": defn.title,
        "agent_type": defn.agent_type,
        "surface": defn.policy.surface,
        "difficulty": defn.difficulty,
        "duration_min": defn.max_duration_min,
        "persona": defn.persona.name,
        "persona_role": defn.persona.role,
        "knowledge_base_id": defn.knowledge_base_id,
        "allowed_sources": defn.guardrails.allowed_sources,
        "competencies": [c.label for c in defn.evaluation.competencies],
        "intro_mode": defn.script.intro_mode,
        "attempts": defn.policy.attempts,
        "feedback_visibility": defn.policy.feedback_visibility,
        "selection_grade": defn.policy.selection_grade,
    }


@router.get("/scenarios")
def list_scenarios() -> dict[str, Any]:
    defns, _ = _content()
    return {"scenarios": [_card(d) for d in defns.values()]}


@router.get("/scenario/{scenario_id}")
def scenario_detail(scenario_id: str) -> dict[str, Any]:
    """The configuration, as a REVIEWER sees it.

    Includes the situation and the candidate brief, which the subject is
    entitled to. Excludes `looking_for`, `red_flags` and `expected_behaviours`,
    which they are not — a subject who can read the key can rehearse it, and
    the scenario stops measuring anyone after that.
    """
    defn = _scenario(scenario_id)
    return {
        **_card(defn),
        "briefing": defn.briefing,
        "situation": {
            "background": defn.situation.background,
            "current_situation": defn.situation.current_situation,
            "business_context": defn.situation.business_context,
            "objective": defn.situation.objective,
            "known_challenges": defn.situation.known_challenges,
        },
        "candidate": {
            "role": defn.candidate.role,
            "objective": defn.candidate.objective,
            "information_available": defn.candidate.information_available,
        },
        "beats": len(defn.beats),
        "turn_budget": defn.turn_budget,
    }


# --------------------------------------------------------------------------- #
#  Running one
# --------------------------------------------------------------------------- #
class StartBody(BaseModel):
    scenario_id: str
    subject_name: str = ""
    subject_id: str = Field(default="demo")


class TurnBody(BaseModel):
    said: str = ""


@router.post("/session/start")
def start(body: StartBody, response: Response) -> dict[str, Any]:
    defn = _scenario(body.scenario_id)
    taken = store.attempts_for(body.subject_id, defn.scenario_id)
    if not defn.policy.attempt_allowed(taken):
        raise HTTPException(
            409,
            f"No attempts left on this scenario ({taken} of {defn.policy.attempts} used).",
        )

    state = RoleplayState.new(
        subject_name=body.subject_name,
        subject_id=body.subject_id,
        scenario_id=defn.scenario_id,
        scenario_version=defn.version,
        surface=defn.policy.surface,
        attempt_no=taken + 1,
    )
    reply = _engine().open(state, defn)
    state.session_grant = secrets.token_urlsafe(32)
    store.save(state)
    response.set_cookie(
        security.CANDIDATE_COOKIE, state.session_grant,
        max_age=7 * 24 * 3600, **security.candidate_cookie_kwargs(),
    )
    return {
        "session_id": state.session_id,
        "attempt_no": state.attempt_no,
        "briefing": state.transcript[0]["text"],
        "persona": {"name": defn.persona.name, "role": defn.persona.role},
        "speech_rate": defn.interaction.speech_rate,
        "channel": defn.interaction.channel,
        "reply": reply.as_dict(),
    }


@router.post("/session/{session_id}/turn")
def turn(
    session_id: str,
    body: TurnBody,
    state: RoleplayState = Depends(roleplay_scope),
) -> dict[str, Any]:
    defn = _scenario(state.scenario_id)
    reply = _engine().on_turn(state, defn, body.said)
    store.save(state)
    return {"reply": reply.as_dict(), "phase": state.phase}


@router.get("/session/{session_id}")
def resume(
    session_id: str, state: RoleplayState = Depends(roleplay_scope)
) -> dict[str, Any]:
    defn = _scenario(state.scenario_id)
    return {
        "session_id": state.session_id,
        "phase": state.phase,
        "attempt_no": state.attempt_no,
        "persona": {"name": defn.persona.name, "role": defn.persona.role},
        # The speaker labels only. A transcript entry's `beat_id` would tell the
        # subject which authored moment they are in, which is a map of the
        # assessment they are sitting.
        "transcript": [
            {"speaker": t.get("speaker"), "text": t.get("text")} for t in state.transcript
        ],
        "turns_used": state.turns_used,
        "turn_budget": defn.turn_budget,
    }


# --------------------------------------------------------------------------- #
#  What they are allowed to see afterwards
# --------------------------------------------------------------------------- #
@router.get("/session/{session_id}/result")
def result(
    session_id: str,
    reviewer: bool = False,
    state: RoleplayState = Depends(roleplay_scope),
) -> dict[str, Any]:
    """Answers to the policy, not to the caller.

    `reviewer=true` is a POC affordance so the console view can be demonstrated
    on one machine. It is NOT a security boundary and must become a real
    principal check before this is deployed — it is flagged in the response so
    nobody mistakes the demo for the product.
    """
    defn = _scenario(state.scenario_id)
    ev = evidence(state, defn)

    if reviewer:
        return {
            "audience": "reviewer",
            "warning": "reviewer=true is a POC affordance, not an access control",
            "scenario": _card(defn),
            "evidence": ev,
            "competency_weights": defn.evaluation.weights(),
            "transcript": state.transcript,
            "knowledge_used": state.knowledge_used,
            "injection_flags": state.injection_flags,
        }

    visibility = defn.policy.feedback_visibility
    base = {"audience": "subject", "visibility": visibility, "complete": ev["complete"]}

    if visibility == "hidden":
        # A selection decision. They are told it is over and nothing else:
        # showing a candidate their score turns a rejection into an argument
        # and teaches the next candidate what to say.
        return {**base, "message": "That's the end of the exercise. Thank you."}

    if visibility == "gated":
        # A certification. Enough to fix it before the retake, not enough to
        # game it — so which competencies fell short, never the cues behind them.
        return {
            **base,
            "attempts_used": state.attempt_no,
            "attempts_allowed": defn.policy.attempts,
            "competencies": [
                {
                    "label": _label(defn, row["skill_id"]),
                    "met": row["beats_handled"] >= max(1, row["beats_reached"]),
                    "reached": row["beats_reached"] > 0,
                }
                for row in ev["per_skill"]
            ],
        }

    # Practice. Everything, immediately — the feedback loop IS the value, and a
    # practice tool that withholds feedback is a test.
    return {
        **base,
        "competencies": [
            {
                "label": _label(defn, row["skill_id"]),
                "handled": row["beats_handled"],
                "reached": row["beats_reached"],
                "total": row["beats_total"],
                "covered": row["covered"],
                "missed": row["missing"],
                "red_flags": row["red_flags"],
                "not_reached": len(row["not_reached"]),
            }
            for row in ev["per_skill"]
        ],
        "turns_used": ev["turns_used"],
    }


def _label(defn: ScenarioDefinition, skill_id: str) -> str:
    found = next((c for c in defn.evaluation.competencies if c.id == skill_id), None)
    return found.label if found else skill_id
