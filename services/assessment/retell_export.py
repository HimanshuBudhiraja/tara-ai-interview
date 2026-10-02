"""A configured scenario, as the variables one generic Retell agent is called with.

There is one Retell agent for every role-play. Its prompt is written once, in
Retell, with `{{placeholders}}`; each call fills them from a published scenario
through `retell_llm_dynamic_variables` on `create-web-call`. A new scenario is
therefore a new configuration, never a new agent — the same rule the library
applies to `AgentSpec`, carried across the vendor boundary.

    ScenarioDefinition ──▶ dynamic_variables() ──▶ create-web-call body
                                                    └─ {{character_name}} …

What crosses and what does not
------------------------------
Everything the counterparty needs to PLAY the scene crosses: who they are, what
they want, what they know, how the scene should unfold, what they must never
do. Nothing the evaluator needs to JUDGE it crosses — no `looking_for`, no
`red_flags`, no `expected_behaviours`, no competency behaviours or weights. A
vendor-hosted prompt holding the scoring key would steer the subject into it,
and it would sit in a third party's logs where anyone with dashboard access can
read what every future candidate is scored on. `leaked_cues` is the check, and
the test suite runs it over every shipped scenario.

Retell's dynamic variables are strings only, so every list is rendered here as
bullet text. An empty field is sent as an empty string rather than dropped: a
`{{placeholder}}` with no value is left in the prompt literally, which reads to
the model as an instruction.
"""
from __future__ import annotations

from typing import Any

from packages.types.agent import AgentSpec, get_agent
from packages.types.knowledge import KnowledgeBase
from packages.types.scenario import ScenarioDefinition

#: The variable contract. The Retell prompt may use any of these and no others;
#: `content/retell/generic_roleplay_prompt.md` uses all of them. Adding one here
#: without adding it there is harmless; the reverse leaves a literal
#: `{{name}}` in a live call.
VARIABLES: tuple[str, ...] = (
    "scenario_title", "language", "difficulty", "max_minutes", "max_turns",
    "agent_identity", "agent_purpose", "domain_knowledge",
    "max_words_per_turn", "on_silence",
    "character_name", "character_role", "character_disposition", "character_goal",
    "character_knows", "character_hidden_concerns", "character_never",
    "character_escalation", "character_dials",
    "situation", "learner_role", "learner_objective", "learner_was_told",
    "scene_plan", "knowledge", "out_of_scope_line",
    "topic_rules", "exit_conditions", "guardrails",
    "opening_line", "transition_line", "closing_line",
)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i.strip()}" for i in items if i and i.strip())


def _situation(defn: ScenarioDefinition) -> str:
    s = defn.situation
    parts = [
        ("Background", s.background),
        ("What is happening right now", s.current_situation),
        ("Business context", s.business_context),
        ("Your side of it", s.counterparty_context),
    ]
    out = [f"{label}: {text.strip()}" for label, text in parts if text and text.strip()]
    if s.known_challenges:
        out.append("Complications:\n" + _bullets(s.known_challenges))
    return "\n".join(out)


def _scene_plan(defn: ScenarioDefinition) -> str:
    """The beats as stage directions — what the character does, in order.

    Only `intent` and the exchange cap cross. The engine behind the websocket
    path moves beats itself; a vendor-hosted prompt cannot be driven turn by
    turn, so it is given the plan and told to pace it. That is the trade this
    export makes, and why the evaluation still reads the transcript afterwards
    against the authored key rather than trusting the pacing.
    """
    lines = []
    for i, b in enumerate(defn.beats, 1):
        cap = f" (about {b.max_turns} exchange{'s' if b.max_turns != 1 else ''})"
        tag = " [optional — skip if time is short]" if b.optional else ""
        lines.append(f"{i}. {b.intent.strip()}{cap}{tag}")
    return "\n".join(lines)


def _knowledge(defn: ScenarioDefinition, kb: KnowledgeBase | None) -> str:
    """Every passage the scenario is allowed to draw on.

    The websocket path retrieves per turn; a single prompt cannot, so the whole
    permitted set is inlined. Bases are small by design (tens of passages) and
    `allowed_sources` is what keeps a discovery scenario from quoting the price
    list — the filter matters more here than it does per turn.
    """
    if kb is None:
        return ""
    allowed = set(defn.guardrails.allowed_sources)
    out: list[str] = []
    for src in kb.sources:
        if allowed and src.id not in allowed:
            continue
        out.append(f"{src.title}:")
        out.extend(f"- {p.text}" for p in src.passages)
    return "\n".join(out)


def _overlaps_key(text: str, defn: ScenarioDefinition) -> bool:
    low = text.strip().lower()
    return any(c.lower() in low or low in c.lower() for c in assessment_content(defn))


def withheld_exit_conditions(defn: ScenarioDefinition) -> list[str]:
    """Exit conditions that restate a scoring cue, and so cannot cross.

    "End when the subject states a concrete plan" is a success criterion
    wearing an exit condition's clothes. The websocket engine keeps exits
    server-side; a vendor-hosted prompt has to be told them, and telling it
    this one tells the character what a good answer is. The scene still ends
    on the turn and time caps.
    """
    return [e for e in defn.guardrails.exit_conditions if _overlaps_key(e, defn)]


def _dials(defn: ScenarioDefinition) -> str:
    return _bullets(defn.persona.dial_rules())


def dynamic_variables(
    defn: ScenarioDefinition,
    agent: AgentSpec | None = None,
    kb: KnowledgeBase | None = None,
) -> dict[str, str]:
    """The string map passed as `retell_llm_dynamic_variables`."""
    agent = agent or get_agent(defn.agent_type)
    p = defn.persona
    guard = list(agent.permanent_guardrails) + defn.guardrails.rules()
    if defn.guardrails.no_coaching or defn.guardrails.no_hints:
        guard.append("Never coach, hint, lead, or help them toward a better answer.")
    out = {
        "scenario_title": defn.title,
        "language": defn.language,
        "difficulty": defn.difficulty,
        "max_minutes": str(defn.max_duration_min),
        "max_turns": str(defn.interaction.max_turns),
        "agent_identity": agent.identity,
        "agent_purpose": agent.purpose,
        "domain_knowledge": _bullets(agent.domain_knowledge),
        "max_words_per_turn": str(
            defn.interaction.max_response_words or agent.interaction.max_words_per_turn
        ),
        "on_silence": agent.interaction.on_subject_stalls,
        "character_name": p.name,
        "character_role": p.role,
        "character_disposition": p.disposition,
        "character_goal": p.wants,
        "character_knows": _bullets(p.knows),
        "character_hidden_concerns": _bullets(p.hidden_concerns),
        "character_never": _bullets(p.never),
        "character_escalation": p.escalation_behaviour,
        "character_dials": _dials(defn),
        "situation": _situation(defn),
        "learner_role": defn.candidate.role,
        "learner_objective": defn.candidate.objective,
        "learner_was_told": defn.briefing,
        "scene_plan": _scene_plan(defn),
        "knowledge": _knowledge(defn, kb),
        "out_of_scope_line": (kb.out_of_scope_line if kb else "") or "I don't know that, to be honest.",
        "topic_rules": _bullets(defn.guardrails.rules()),
        "exit_conditions": _bullets([
            e for e in defn.guardrails.exit_conditions if not _overlaps_key(e, defn)
        ]),
        "guardrails": _bullets(guard),
        "opening_line": p.opening_line,
        "transition_line": defn.script.transition,
        "closing_line": defn.script.closing or defn.closing,
    }
    assert set(out) == set(VARIABLES), "variable contract drifted"
    return out


def web_call_body(
    defn: ScenarioDefinition,
    agent_id: str,
    agent: AgentSpec | None = None,
    kb: KnowledgeBase | None = None,
) -> dict[str, Any]:
    """The body for Retell's `POST /v2/create-web-call`.

    `metadata` carries the scenario id and version so the call's transcript can
    be scored against exactly the key it was played under — Retell echoes it on
    every webhook.
    """
    return {
        "agent_id": agent_id,
        "retell_llm_dynamic_variables": dynamic_variables(defn, agent, kb),
        "metadata": {
            "scenario_id": defn.scenario_id,
            "scenario_version": defn.version,
            "agent_type": defn.agent_type,
            "surface": defn.policy.surface,
        },
        "withheld_exit_conditions": withheld_exit_conditions(defn),
    }


def assessment_content(defn: ScenarioDefinition) -> list[str]:
    """Every authored string that belongs to the evaluator and nobody else."""
    out: list[str] = []
    for b in defn.beats:
        out.extend(b.looking_for)
        out.extend(b.red_flags)
    out.extend(defn.candidate.expected_behaviours)
    for c in defn.evaluation.competencies:
        out.extend(c.observable_behaviours)
    return [s for s in out if s and len(s.strip()) > 12]


def leaked_cues(payload: dict[str, Any], defn: ScenarioDefinition) -> list[str]:
    """Scoring-key strings that appear anywhere in an outgoing payload.

    Short strings are skipped: "asks why" is a cue and also an ordinary phrase a
    persona might contain honestly, and a check that fires on coincidence gets
    switched off.
    """
    import json

    sent = {k: v for k, v in payload.items() if k != "withheld_exit_conditions"}
    blob = json.dumps(sent).lower()
    return [cue for cue in assessment_content(defn) if cue.strip().lower() in blob]
