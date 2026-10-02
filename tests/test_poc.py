"""The POC's seven success criteria, one section each.

Each test names the criterion it proves. They run offline, against the shipped
agent library and the shipped configurations, so "the POC works" is a command
anyone can run rather than a claim in a document.
"""
from __future__ import annotations

import pytest

from packages.types.agent import LIBRARY, AgentSpec, get_agent
from packages.types.knowledge import KnowledgeBase, KnowledgePassage, KnowledgeSource
from packages.types.scenario import ScenarioDefinition
from packages.types.simulation import ScenarioGuardrails
from services.ai import prompt_assembly
from services.assessment import knowledge, scenarios
from services.orchestrator.roleplay import RoleplayEngine, RoleplayState

SECTION_TITLES = [
    "1. AGENT IDENTITY",
    "2. AGENT PURPOSE",
    "3. GENERAL DOMAIN KNOWLEDGE",
    "4. INTERACTION RULES",
    "5. SCENARIO",
    "6. PERSONA",
    "7. KNOWLEDGE BASE",
    "9. WHO YOU ARE TALKING TO",
    "10. GUARDRAILS",
    "11. SESSION",
    "12. EVALUATION REQUIREMENTS",
]


@pytest.fixture()
def library():
    return scenarios.load_all()


@pytest.fixture()
def bases():
    return knowledge.load_all()


def _assemble(defn: ScenarioDefinition, bases: dict, said: str = "what does it cost?") -> tuple[str, list[str]]:
    return prompt_assembly.assemble(
        agent=get_agent(defn.agent_type),
        defn=defn,
        kb=bases.get(defn.knowledge_base_id),
        beat=defn.beats[0] if defn.beats else None,
        said=said,
    )


# --------------------------------------------------------------------------- #
#  1. Agent reusability — one agent supports multiple scenarios
# --------------------------------------------------------------------------- #
def test_one_agent_runs_three_different_simulations(library):
    sales = [d for d in library.values() if d.agent_type == "sales"]
    assert len(sales) >= 3
    # Same agent, genuinely different configurations on every axis the brief names.
    assert len({d.persona.name for d in sales}) == len(sales)
    assert len({d.difficulty for d in sales}) >= 3
    assert len({d.max_duration_min for d in sales}) >= 3
    assert len({tuple(sorted(d.guardrails.allowed_sources)) for d in sales}) == len(sales)
    assert len({tuple(c.id for c in d.evaluation.competencies) for d in sales}) == len(sales)


def test_the_agent_half_of_the_prompt_is_identical_across_its_scenarios(library, bases):
    """Sections 1-3 come from the agent and must not vary by scenario. If they
    drifted, "one reusable agent" would be three agents wearing one name."""
    sales = [d for d in library.values() if d.agent_type == "sales"]
    heads = set()
    for defn in sales:
        system, _ = _assemble(defn, bases)
        heads.add(system.split("## 4.")[0])
    assert len(heads) == 1


# --------------------------------------------------------------------------- #
#  2. Scenario flexibility — new simulations need no engine or agent change
# --------------------------------------------------------------------------- #
def test_a_new_simulation_is_data_not_code(bases):
    """Built entirely from a dict, run through the real engine, no agent edited."""
    defn = ScenarioDefinition.from_dict({
        "scenario_id": "adhoc", "title": "Procurement squeeze", "agent_type": "sales",
        "briefing": "You are in a procurement call.",
        "difficulty": "hard",
        "policy": {"surface": "sales", "feedback_visibility": "full", "attempts": 0,
                   "selection_grade": False},
        "situation": {"current_situation": "Procurement has entered late.",
                      "objective": "Hold the commercial line."},
        "candidate": {"objective": "Protect margin while keeping the deal alive."},
        "persona": {"name": "Ines", "wants": "A bigger discount",
                    "opening_line": "Your terms are not competitive."},
        "evaluation": {"competencies": [
            {"id": "negotiation", "label": "Negotiation", "weight": 1.0,
             "observable_behaviours": ["Trades rather than concedes"]}]},
        "beats": [{"id": "b1", "intent": "Demand a deeper discount",
                   "skill_id": "negotiation",
                   "looking_for": ["asks what they get in return"],
                   "fallback_lines": ["Well?"], "max_turns": 2}],
        "turn_budget": 6,
        "interaction": {"max_turns": 6},
    })
    assert defn.validate() == []
    engine = RoleplayEngine(knowledge=bases)
    state = RoleplayState.new("Sam", "s1", defn.scenario_id, 1, surface="sales")
    opened = engine.open(state, defn)
    assert opened.text == "Your terms are not competitive."
    assert engine.on_turn(state, defn, "What would I get in return for that?")


# --------------------------------------------------------------------------- #
#  3. Knowledge grounding — and its boundaries
# --------------------------------------------------------------------------- #
def test_the_knowledge_base_reaches_the_prompt(library, bases):
    defn = library["sales_price_objection"]
    system, used = _assemble(defn, bases, said="what does a Business seat cost?")
    assert used, "a pricing question retrieved nothing"
    assert "320" in system


def test_scenario_guardrails_withhold_sources_the_agent_must_not_use(library, bases):
    """The discovery scenario may not reach pricing — a buyer who quotes list
    price on a first call has answered a question the seller should have had
    to earn."""
    defn = library["sales_enterprise_discovery"]
    assert defn.guardrails.allowed_sources == ["product"]
    system, used = _assemble(defn, bases, said="what does a Business seat cost?")
    assert "320" not in system
    assert not any(p.startswith("pr_") for p in used)


def test_an_ungrounded_question_gets_an_authored_refusal_not_an_invention(bases):
    kb = bases["northwind_sales"]
    system, used = prompt_assembly.assemble(
        agent=get_agent("sales"),
        defn=ScenarioDefinition(scenario_id="x", title="x", briefing="x", agent_type="sales"),
        kb=kb, beat=None, said="zzzz qqqq",
    )
    assert used == []
    assert kb.out_of_scope_line in system


def test_retrieval_is_deterministic(bases):
    """A replay must ground on the same facts it was assessed against."""
    kb = bases["northwind_sales"]
    runs = [[p.id for _, p in kb.select("discount approval authority")] for _ in range(5)]
    assert len(set(map(tuple, runs))) == 1


# --------------------------------------------------------------------------- #
#  4. Configuration — every configured control reaches the runtime
# --------------------------------------------------------------------------- #
def test_configured_controls_are_visible_in_the_assembled_prompt(library, bases):
    defn = library["sales_price_objection"]
    system, _ = _assemble(defn, bases)
    assert defn.persona.name in system                       # persona
    assert defn.situation.current_situation in system        # scenario
    assert defn.script.scenario_instructions in system       # script
    assert f"Difficulty: {defn.difficulty}" in system        # difficulty
    assert str(defn.interaction.max_turns) in system         # session
    assert defn.guardrails.persona_boundaries[0] in system   # guardrails
    assert str(defn.interaction.max_response_words) in system  # response length


def test_persona_dials_become_behaviour_not_numbers(library, bases):
    """"aggressiveness: 4" reads as flavour to a model. The band it falls in,
    written as an instruction, does not."""
    defn = library["cs_double_charge"]
    system, _ = _assemble(defn, bases)
    assert "aggressiveness" not in system.lower()
    assert "confrontational" in system.lower()


def test_a_custom_introduction_is_spoken_verbatim_and_nothing_is_added(library):
    defn = library["cs_double_charge"]
    assert defn.script.intro_mode == "custom"
    state = RoleplayState.new("Sam", "s1", defn.scenario_id, 1)
    RoleplayEngine().open(state, defn)
    spoken = state.transcript[0]["text"]
    assert spoken.startswith(defn.script.welcome)
    assert defn.script.scenario_instructions in spoken
    assert defn.briefing not in spoken


def test_a_standard_introduction_uses_the_platform_script(library):
    defn = library["sales_enterprise_discovery"]
    assert defn.script.intro_mode == "standard"
    state = RoleplayState.new("Sam", "s1", defn.scenario_id, 1)
    RoleplayEngine().open(state, defn)
    assert defn.briefing in state.transcript[0]["text"]


def test_competency_weights_normalise(library):
    defn = library["sales_price_objection"]
    weights = defn.evaluation.weights()
    assert round(sum(weights.values()), 6) == 1.0
    assert weights["value_articulation"] > weights["closing"]


# --------------------------------------------------------------------------- #
#  5. Prompt standardisation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario_id", [
    "sales_price_objection", "sales_competitor_objection",
    "sales_enterprise_discovery", "cs_double_charge", "hiring_sjt_missed_handoff",
])
def test_every_simulation_uses_the_same_twelve_section_frame(scenario_id, library, bases):
    system, _ = _assemble(library[scenario_id], bases)
    positions = []
    for title in SECTION_TITLES:
        marker = f"## {title}"
        assert marker in system, f"{scenario_id} is missing {title}"
        positions.append(system.index(marker))
    assert positions == sorted(positions), f"{scenario_id} has sections out of order"


# --------------------------------------------------------------------------- #
#  6. Assessment consistency — the floor cannot be configured away
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario_id", [
    "sales_price_objection", "sales_competitor_objection",
    "sales_enterprise_discovery", "cs_double_charge", "hiring_sjt_missed_handoff",
])
def test_every_permanent_guardrail_appears_in_every_prompt(scenario_id, library, bases):
    defn = library[scenario_id]
    system, _ = _assemble(defn, bases)
    for rule in get_agent(defn.agent_type).permanent_guardrails:
        assert rule in system, f"{scenario_id} dropped: {rule[:50]}"


def test_permanent_guardrails_have_no_setter():
    """A configurer composes what a simulation does. They do not get a control
    that switches off prompt-injection defence."""
    agent = get_agent("sales")
    with pytest.raises(AttributeError):
        agent.permanent_guardrails = ()


def test_coaching_cannot_be_switched_on():
    problems = ScenarioGuardrails(no_coaching=False).validate()
    assert any("told the subject the answer" in p for p in problems)


@pytest.mark.parametrize("scenario_id", [
    "sales_price_objection", "sales_competitor_objection",
    "sales_enterprise_discovery", "cs_double_charge", "hiring_sjt_missed_handoff",
])
def test_the_scoring_key_never_reaches_the_counterparty(scenario_id, library, bases):
    """The integrity property the whole split rests on. A persona holding the
    key steers the subject into it, and the simulation measures itself."""
    defn = library[scenario_id]
    system, _ = _assemble(defn, bases)
    for beat in defn.beats:
        for cue in beat.looking_for:
            assert cue not in system, f"{scenario_id}: leaked '{cue}'"
    for behaviour in defn.candidate.expected_behaviours:
        assert behaviour not in system, f"{scenario_id}: leaked '{behaviour}'"
    for competency in defn.evaluation.competencies:
        for observable in competency.observable_behaviours:
            assert observable not in system, f"{scenario_id}: leaked '{observable}'"


def test_the_agent_is_told_it_is_not_the_evaluator(library, bases):
    system, _ = _assemble(library["sales_price_objection"], bases)
    assert "You are NOT the evaluator" in system


# --------------------------------------------------------------------------- #
#  7. Scalability — a new use case is a spec, not a rebuild
# --------------------------------------------------------------------------- #
def test_a_new_agent_type_needs_no_engine_change(bases):
    agent = AgentSpec(
        agent_type="sales",           # reuses a legal type; the point is the content
        name="Partner Enablement Agent",
        identity="You are playing a channel partner's implementation lead.",
        purpose="To simulate a partner enablement conversation.",
        domain_knowledge=["Partners resell and implement; they are not the end customer."],
    )
    agent.evaluation.dimensions = ["enablement", "clarity"]
    assert agent.validate() == []
    defn = ScenarioDefinition(
        scenario_id="partner", title="Partner onboarding", briefing="b", agent_type="sales"
    )
    system = prompt_assembly.build_system_prompt(agent, defn)
    assert "channel partner's implementation lead" in system
    # The floor comes along for free — a new agent cannot forget the guardrails.
    assert agent.permanent_guardrails[0] in system


def test_the_library_covers_the_three_named_use_cases():
    assert set(LIBRARY) == {"role_readiness", "sales", "customer_service", "roleplay"}
    for agent in LIBRARY.values():
        assert agent.validate() == []


def test_every_shipped_scenario_names_an_agent_in_the_library(library):
    for defn in library.values():
        assert defn.agent_type in LIBRARY, defn.scenario_id


# --------------------------------------------------------------------------- #
#  Knowledge base hygiene
# --------------------------------------------------------------------------- #
def test_shipped_knowledge_bases_validate(bases):
    assert bases
    for kb in bases.values():
        assert kb.validate() == [], kb.kb_id


def test_duplicate_passage_ids_are_refused():
    kb = KnowledgeBase(kb_id="x", sources=[
        KnowledgeSource(id="s", title="S", passages=[
            KnowledgePassage(id="dup", text="one"),
            KnowledgePassage(id="dup", text="two"),
        ])
    ])
    assert any("Duplicate passage" in p for p in kb.validate())


def test_the_session_records_which_passages_grounded_each_turn(library, bases):
    """A transcript where the buyer quotes a price is unauditable without this."""
    defn = library["sales_price_objection"]
    engine = RoleplayEngine(knowledge=bases)
    state = RoleplayState.new("Sam", "s1", defn.scenario_id, 1, surface="sales")
    engine.open(state, defn)
    engine.on_turn(state, defn, "What does a Business seat cost these days?")
    assert state.knowledge_used, "no grounding recorded"
