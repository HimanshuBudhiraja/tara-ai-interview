"""The standard prompt framework — twelve sections, assembled per turn.

One master prompt structure powers every simulation the library can run. Four
sections come from the agent and never change between simulations; six are
injected from configuration; two are computed per turn. Build the agent once,
configure the simulation many times.

    1  AGENT IDENTITY            agent        fixed
    2  AGENT PURPOSE             agent        fixed
    3  GENERAL DOMAIN KNOWLEDGE  agent        fixed
    4  INTERACTION RULES         agent + config
    5  SCENARIO                  config
    6  PERSONA                   config
    7  KNOWLEDGE BASE            config       per turn (retrieved)
    8  CUSTOM SCRIPT             config
    9  CANDIDATE OBJECTIVE       config       partial — see below
    10 GUARDRAILS                floor + config
    11 SESSION CONFIGURATION     config       per turn (turn counter)
    12 EVALUATION REQUIREMENTS   agent        inverted — see below

Two sections are deliberately not what their names suggest
----------------------------------------------------------
**Section 9 carries the subject's role and objective, and never their expected
behaviours.** The agent has to know who it is talking to — a buyer speaks
differently to an AE than to a support rep. It must not know what a good
response looks like, because a counterparty holding the answer key steers the
conversation toward it, and a simulation that leads the witness measures itself.

**Section 12 tells the agent it is not the evaluator.** The natural reading of
"evaluation requirements" is to hand the model the rubric and ask it to assess.
That would collapse the two halves of the system into one call and make the
score a function of the same model that is improvising the dialogue. So the
requirement this section places on the agent is the inverse: create the
conditions under which the behaviour can be observed, stay in character, and
judge nothing. The judging is a separate call against the authored key.

Everything else about grounding follows from section 7. An agent told "do not
make things up" ignores it; an agent given a bounded set of facts and told that
anything outside it is something its character does not know has a rule it can
actually follow.
"""
from __future__ import annotations

from packages.types.agent import AgentSpec
from packages.types.knowledge import KnowledgeBase, KnowledgePassage, KnowledgeSource
from packages.types.scenario import BeatSpec, ScenarioDefinition


def _block(number: int, title: str, lines: list[str]) -> str:
    """One numbered section. Dropped entirely when it has no content.

    An empty section is worse than a missing one: "## 7. KNOWLEDGE BASE" with
    nothing under it reads to a model as "you have no knowledge", which is a
    different instruction from "this simulation is not grounded".
    """
    body = [ln for ln in lines if ln and ln.strip()]
    if not body:
        return ""
    return f"## {number}. {title}\n" + "\n".join(body)


def _bullets(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items if i and i.strip()]


def knowledge_query(beat: BeatSpec | None, said: str, defn: ScenarioDefinition) -> str:
    """What to retrieve against.

    The subject's turn plus the beat's intent plus the situation. The subject's
    words alone would miss the passages the character needs to have ready before
    being asked; the beat alone would ignore what was actually just said.
    """
    parts = [said or "", beat.intent if beat else "", defn.situation.current_situation]
    return " ".join(p for p in parts if p)


def render_knowledge(
    hits: list[tuple[KnowledgeSource, KnowledgePassage]], out_of_scope_line: str
) -> list[str]:
    if not hits:
        return [
            "You have no reference material for this turn. If you are asked "
            "anything factual about the organisation, product, pricing, policy "
            f"or account, your character does not know it: say \"{out_of_scope_line}\"",
        ]
    lines = [
        "These are the ONLY facts you have. Anything not here, your character "
        f"does not know — say \"{out_of_scope_line}\" rather than inventing it.",
    ]
    for source, passage in hits:
        lines.append(f"- [{source.title}] {passage.text}")
    return lines


def build_system_prompt(
    agent: AgentSpec,
    defn: ScenarioDefinition,
    knowledge: list[tuple[KnowledgeSource, KnowledgePassage]] | None = None,
    beat: BeatSpec | None = None,
    turns_used: int = 0,
    out_of_scope_line: str = "I don't know that, to be honest.",
) -> str:
    """The assembled system prompt for one turn of one simulation."""
    interaction = defn.interaction
    max_words = interaction.max_response_words or agent.interaction.max_words_per_turn
    persona = defn.persona
    situation = defn.situation

    sections = [
        _block(1, "AGENT IDENTITY", [agent.identity]),
        _block(2, "AGENT PURPOSE", [agent.purpose]),
        _block(3, "GENERAL DOMAIN KNOWLEDGE", _bullets(agent.domain_knowledge)),
        _block(4, "INTERACTION RULES", [
            f"- ONE turn. Under {max_words} words.",
            f"- {agent.interaction.style}",
            f"- If they stall or go quiet: {agent.interaction.on_subject_stalls}",
            f"- Channel: {interaction.channel}.",
        ]),
        _block(5, "SCENARIO", [
            f"Background: {situation.background}" if situation.background else "",
            f"Right now: {situation.current_situation}" if situation.current_situation else "",
            f"Business context: {situation.business_context}" if situation.business_context else "",
            f"Your side of it: {situation.counterparty_context}" if situation.counterparty_context else "",
            "Known complications:" if situation.known_challenges else "",
            *_bullets(situation.known_challenges),
            f"Difficulty: {defn.difficulty}.",
        ]),
        _block(6, "PERSONA — this is who you are", [
            f"Name: {persona.name}",
            f"Role: {persona.role}" if persona.role else "",
            f"How you come across: {persona.disposition}" if persona.disposition else "",
            f"What you want: {persona.wants}",
            *([
                "What you are actually worried about — NEVER volunteer this. It comes "
                "out only if they ask the right question:",
                *_bullets(persona.hidden_concerns),
            ] if persona.hidden_concerns else []),
            f"How you escalate: {persona.escalation_behaviour}" if persona.escalation_behaviour else "",
            *_bullets(persona.dial_rules()),
            *([
                "Your character never:",
                *_bullets(persona.never),
            ] if persona.never else []),
            *([
                "What your character knows:",
                *_bullets(persona.knows),
            ] if persona.knows else []),
            *([
                "",
                "WHAT YOU DO THIS TURN (say it in your own words — never recite it):",
                beat.intent,
            ] if beat else []),
        ]),
        _block(7, "KNOWLEDGE BASE", render_knowledge(knowledge or [], out_of_scope_line)),
        _block(8, "SCRIPT", [
            # The agent is told what the subject was told, so it does not
            # re-explain the exercise or contradict the briefing mid-scene.
            f"The person has already been told: {defn.script.scenario_instructions}"
            if defn.script.scenario_instructions else "",
            "Do not repeat or re-explain any of that. You are in the scene.",
        ]),
        _block(9, "WHO YOU ARE TALKING TO", [
            f"Their role: {defn.candidate.role}" if defn.candidate.role else "",
            f"What they are trying to do: {defn.candidate.objective}"
            if defn.candidate.objective else "",
            "You do not know how they are being assessed and you have no view on "
            "whether they are doing it well.",
        ]),
        _block(10, "GUARDRAILS — these override everything above", [
            *_bullets(list(agent.permanent_guardrails)),
            *_bullets(defn.guardrails.rules()),
            *([
                "- Never coach, hint, lead, or help them toward a better answer."
            ] if defn.guardrails.no_coaching or defn.guardrails.no_hints else []),
        ]),
        _block(11, "SESSION", [
            f"- Turn {turns_used + 1} of at most {interaction.max_turns}.",
            f"- Language: {defn.language}.",
        ]),
        _block(12, "EVALUATION REQUIREMENTS", [
            "You are NOT the evaluator. Someone else assesses this conversation "
            "afterwards, against criteria you have never seen.",
            "Your requirement is to make the assessment possible: behave like a "
            "real person in this situation, consistently, so that what they do "
            "about you is worth reading. Do not make it easier. Do not make it "
            "impossible. Do not comment on it.",
            f"The assessment will be about: {', '.join(agent.evaluation.dimensions)}. "
            "Knowing that must not change how you behave — if you start steering "
            "them toward those things, the conversation stops measuring anything.",
        ]),
    ]

    return "\n\n".join(s for s in sections if s) + (
        "\n\nReturn STRICT JSON: {\"say\": \"<what your character says>\"}"
    )


def assemble(
    agent: AgentSpec,
    defn: ScenarioDefinition,
    kb: KnowledgeBase | None,
    beat: BeatSpec | None,
    said: str,
    turns_used: int = 0,
) -> tuple[str, list[str]]:
    """The prompt for this turn, plus the ids of the passages that grounded it.

    The ids are returned rather than discarded so the session can record which
    facts the character had in front of it. Without that, a transcript where the
    buyer quotes a price is unauditable: nobody can tell whether the number came
    from the price list or from the model.
    """
    hits: list[tuple[KnowledgeSource, KnowledgePassage]] = []
    if kb is not None:
        hits = kb.select(
            knowledge_query(beat, said, defn),
            allowed_sources=defn.guardrails.allowed_sources or None,
        )
    prompt = build_system_prompt(
        agent, defn, hits, beat, turns_used,
        out_of_scope_line=kb.out_of_scope_line if kb else "I don't know that, to be honest.",
    )
    return prompt, [p.id for _, p in hits]
