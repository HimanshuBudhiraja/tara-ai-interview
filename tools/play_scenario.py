"""Play a role-play scenario in the terminal.

The role-play sibling of `tests/personas.py`. Two modes:

    play_scenario.py --scenario cs_double_charge --subject strong
    play_scenario.py --scenario cs_double_charge --interactive

With no provider configured the whole thing still runs: the classifier falls
back to its heuristic and the counterparty falls back to the beat's authored
lines. That is not a degraded demo, it is the property the engine is built for —
a scene must never stop because a model did.

Usage:
    ../.venv/bin/python tools/play_scenario.py --list
    ../.venv/bin/python tools/play_scenario.py -s cs_double_charge -u strong
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.types.agent import LIBRARY, get_agent  # noqa: E402
from services.ai import prompt_assembly  # noqa: E402
from services.assessment import knowledge, scenarios  # noqa: E402
from services.orchestrator.roleplay import (  # noqa: E402
    RoleplayEngine,
    RoleplayState,
    evidence,
)

DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
OFF = "\033[0m"


# --------------------------------------------------------------------------- #
#  Scripted subjects
#
#  Written per scenario rather than generically, because a subject who says
#  "I would empathise with the customer" passes a cue-overlap read without
#  doing anything. These say the words a real person would say.
# --------------------------------------------------------------------------- #
SUBJECTS: dict[str, dict[str, list[str]]] = {
    "cs_double_charge": {
        "strong": [
            "I'm really sorry — I can see both charges here and that's our mistake, not yours. "
            "I'm going to get the duplicate eighty-nine dollars back to you today. "
            "Before I do, can I ask what this actually caused on your end?",
            "That's awful, and the overdraft fee is on us to sort out too. Let me confirm — "
            "two charges of eighty-nine on the fourth, and a thirty-five dollar bank fee on top. "
            "Is anything else on the account affected?",
            "Here's exactly where I stand. The eighty-nine dollar duplicate I'm refunding right now, "
            "it'll clear in three to five days. The thirty-five dollar bank fee I can't authorise on "
            "my own — I need my supervisor, and I'm raising it the moment we hang up. "
            "I'll call you back before five today either way.",
            "I understand, and I wouldn't blame you. What happened is a billing retry that "
            "double-fired — engineering has a fix going out this month, and I'm putting a flag on "
            "your account so any duplicate is caught before it hits your card. "
            "You'll hear from me by five with the fee decision, and I'll stay on this until it's closed.",
        ],
        "weak": [
            "Okay, let me just take a look at your account. Can you check your bank statement and "
            "confirm the charges for me?",
            "Right, I see it. I'll get that refunded.",
            "Yeah, I'll definitely get the thirty-five back for you too, no problem at all.",
            "I'm sorry you feel that way. I can offer you a discount on next month if that helps?",
        ],
    },
    "sales_price_objection": {
        "strong": [
            "That's a real gap and I'm not going to pretend it isn't. Before I talk about numbers — "
            "what would actually have to change for you to stay, beyond price?",
            "That's useful. Two things I want to understand: if you did move, what does that look "
            "like for your team in practice? And have you had the other vendor in for a proper demo yet?",
            "So three weeks of migration across a team of six — at a loaded rate that's most of the "
            "first year's saving gone before anyone logs in. And the reporting your team relies on is "
            "the piece they'd be rebuilding. That's the line I'd take to your CFO: the saving is "
            "roughly twelve months out, not day one.",
            "Here's what I can do. A two-year term at ten percent off the renewal number — that holds "
            "your price flat in year two, which is the thing your CFO actually asked for. "
            "I can't go past ten on my own. If you need more than that I'll take it to my VP, but "
            "I'd need the two years to make the case. Can we get thirty minutes with your CFO on Thursday?",
        ],
        "weak": [
            "I hear you. Let me see what I can do on price — I might be able to get you fifteen percent off.",
            "Our platform has a lot of features they don't have. Better reporting, better support, "
            "better integrations.",
            "We're just a much better product overall. Most customers see a lot of value.",
            "Okay, let me speak to my manager. I'm pretty sure I can get you the full twenty-two percent.",
        ],
    },
    "sales_competitor_objection": {
        "strong": [
            "Nothing, maybe — they might genuinely be the right call. Before I try to "
            "convince you of anything: what does your team actually need this to do?",
            "That's helpful. Two more — are there any constraints from IT or security "
            "on new tools? And has anyone on your team migrated a platform before?",
            "Then that's the difference that matters. We support SAML SSO and SCIM "
            "provisioning; the other platform doesn't do SCIM. You can verify that on "
            "their own docs page rather than taking my word for it. Their reporting is "
            "genuinely fine — it's fixed-template, which is fine until you need to change one.",
            "Let's test it rather than argue it. Can we get thirty minutes with you, "
            "your colleague who recommended them, and whoever owns the SSO mandate — "
            "and I'll walk through provisioning live?",
        ],
        "weak": [
            "We've got much better reporting, better integrations and better support.",
            "Honestly, they're a lot less mature than us. A lot of customers switch back.",
            "Our reporting is really flexible. Customers love it.",
            "Let me send you a comparison document and you can take a look.",
        ],
    },
    "sales_enterprise_discovery": {
        "strong": [
            "That's fair enough. Rather than pitch at you — what does your team actually "
            "do today when a report is needed?",
            "You said quarter end is a scramble. What does that scramble actually cost "
            "you — how many people, how many days?",
            "And how many people would eventually touch something like this? Is there "
            "budget sitting anywhere for it this year?",
            "Then I don't think you need a demo yet. What I'd suggest is thirty minutes "
            "with you and the director who asked you to take this, next week, to size "
            "whether the quarter-end problem is worth a business case at all. "
            "Does Tuesday work?",
        ],
        "weak": [
            "Great, thanks for the time. So Northwind is a reporting and analytics "
            "platform used by over two thousand companies to automate their reporting.",
            "We've got scheduled exports, custom dashboards and a metrics API.",
            "It's really easy to get started, onboarding is only four weeks.",
            "I'll send over some information and follow up next week.",
        ],
    },
    "hiring_sjt_missed_handoff": {
        "strong": [
            "No blame — but the customer's expecting a call in the hour, so let me get what I need "
            "first. What was the escalation about?",
            "Okay. Wrong address twice — what was the order number, and what exactly did we promise "
            "them and by when?",
            "Got it, that's enough to work with. One thing before you go — the notes not being there "
            "is what nearly cost us the callback. Has that been happening a lot when you're short-staffed?",
            "Then it's a staffing problem, not a you problem, and I'll raise it that way. "
            "I'm calling the customer now with what you've given me, writing the notes up properly, "
            "and flagging to the shift lead that two sick days left the handoff uncovered.",
        ],
        "weak": [
            "This is the second time this has happened. It's really not okay.",
            "Well, can you just tell me what it was about?",
            "Okay. I guess I'll deal with it.",
            "I'll probably call them at some point.",
        ],
    },
}


def play(scenario_id: str, subject: str, interactive: bool, as_json: bool) -> int:
    defn = scenarios.load(scenario_id)
    engine = RoleplayEngine(knowledge=knowledge.load_all())
    state = RoleplayState.new(
        subject_name="Sam Taylor",
        subject_id="demo",
        scenario_id=defn.scenario_id,
        scenario_version=defn.version,
        surface=defn.policy.surface,
    )

    if not as_json:
        print(f"\n{BOLD}{defn.title}{OFF}")
        print(f"{DIM}agent={defn.agent_type}  difficulty={defn.difficulty}  "
              f"kb={defn.knowledge_base_id or '-'} "
              f"(sources: {', '.join(defn.guardrails.allowed_sources) or 'all'}){OFF}")
        print(f"{DIM}surface={defn.policy.surface}  feedback={defn.policy.feedback_visibility}  "
              f"attempts={'unlimited' if defn.policy.unlimited_attempts else defn.policy.attempts}  "
              f"selection_grade={defn.policy.selection_grade}{OFF}\n")

    reply = engine.open(state, defn)
    if not as_json:
        print(f"{DIM}{state.transcript[0]['text']}{OFF}\n")
        print(f"{CYAN}{defn.persona.name}:{OFF} {reply.text}\n")

    lines = SUBJECTS.get(scenario_id, {}).get(subject, [])
    turn = 0
    while not reply.ends:
        if interactive:
            try:
                said = input(f"{GREEN}you >{OFF} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if said.lower() in ("quit", "exit"):
                break
        else:
            if turn >= len(lines):
                break
            said = lines[turn]
            if not as_json:
                print(f"{GREEN}you:{OFF} {said}\n")
        turn += 1

        before = state.current_beat_id
        reply = engine.on_turn(state, defn, said)
        if not as_json:
            record = state.records.get(before or "")
            if record is not None:
                mark = f"{GREEN}handled{OFF}" if record.satisfied else (
                    f"{YELLOW}open{OFF}" if record.closed_at is None else f"{RED}{record.closed_reason}{OFF}"
                )
                flags = f"  {RED}flags={len(record.red_flags)}{OFF}" if record.red_flags else ""
                print(f"{DIM}  [{before}] covered {len(record.covered)}/{len(record.missing)+len(record.covered)}"
                      f"  → {mark}{flags}{DIM}  turn {state.turns_used}/{defn.turn_budget}{OFF}")
            speaker = "" if reply.kind == "closing" else f"{CYAN}{defn.persona.name}:{OFF} "
            tag = f" {DIM}(authored){OFF}" if reply.authored and reply.kind == "in_character" else ""
            print(f"{speaker}{reply.text}{tag}\n")

    ev = evidence(state, defn)
    if as_json:
        print(json.dumps(ev, indent=2))
        return 0

    print(f"{BOLD}Evidence{OFF} {DIM}(not a score — this is what the scoring engine consumes){OFF}")
    for row in ev["per_skill"]:
        handled = row["beats_handled"]
        reached = row["beats_reached"]
        total = row["beats_total"]
        colour = GREEN if handled == total else (YELLOW if handled else RED)
        print(f"  {colour}{row['skill_id']:34}{OFF} handled {handled}/{reached} reached, {total} authored")
        if row["missing"]:
            print(f"{DIM}      missed: {', '.join(row['missing'][:3])}{OFF}")
        if row["red_flags"]:
            print(f"{RED}      flags:  {', '.join(row['red_flags'])}{OFF}")
        if row["not_reached"]:
            print(f"{DIM}      not reached: {', '.join(row['not_reached'])}{OFF}")
    print(f"\n{DIM}complete={ev['complete']}  turns={ev['turns_used']}  "
          f"injection_flags={len(ev['injection_flags'])}{OFF}\n")
    return 0


def show_prompt(scenario_id: str) -> int:
    """Print the assembled standard prompt — the POC's prompt-standardisation proof."""
    defn = scenarios.load(scenario_id)
    kb = knowledge.load_all().get(defn.knowledge_base_id)
    system, used = prompt_assembly.assemble(
        agent=get_agent(defn.agent_type), defn=defn, kb=kb,
        beat=defn.beats[0] if defn.beats else None,
        said="what does this actually cost per seat?",
    )
    print(system)
    print(f"\n{DIM}--- grounded on passages: {', '.join(used) or 'none'} ---{OFF}")
    return 0


def poc() -> int:
    """One agent, three configurations. Section 12 of the POC brief."""
    by_agent: dict[str, list] = {}
    for defn in scenarios.load_all().values():
        by_agent.setdefault(defn.agent_type, []).append(defn)

    print(f"\n{BOLD}AGENT LIBRARY{OFF}  {DIM}— build the agent once, configure many times{OFF}\n")
    for agent_type, agent in LIBRARY.items():
        configs = by_agent.get(agent_type, [])
        print(f"{BOLD}{agent.name}{OFF} {DIM}({agent_type}){OFF}")
        print(f"{DIM}  dimensions: {', '.join(agent.evaluation.dimensions)}{OFF}")
        print(f"{DIM}  permanent guardrails: {len(agent.permanent_guardrails)}{OFF}")
        for d in configs:
            srcs = ", ".join(d.guardrails.allowed_sources) or "all"
            print(f"    {CYAN}{d.scenario_id:30}{OFF} {d.difficulty:6} "
                  f"{d.max_duration_min:2}min  persona={d.persona.name:18} "
                  f"kb[{srcs}]  {len(d.evaluation.competencies)} competencies  "
                  f"intro={d.script.intro_mode}")
        if not configs:
            print(f"{DIM}    (no scenarios configured yet){OFF}")
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Play a Tara role-play scenario.")
    ap.add_argument("-s", "--scenario", default="cs_double_charge")
    ap.add_argument("-u", "--subject", default="strong", choices=("strong", "weak"))
    ap.add_argument("-i", "--interactive", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the evidence payload only")
    ap.add_argument("--list", action="store_true", help="list shipped scenarios")
    ap.add_argument("--prompt", action="store_true", help="print the assembled standard prompt")
    ap.add_argument("--poc", action="store_true", help="show the agent library and its configurations")
    args = ap.parse_args()

    if args.poc:
        return poc()
    if args.prompt:
        return show_prompt(args.scenario)
    if args.list:
        for sid, defn in scenarios.load_all().items():
            print(f"{sid:32} {defn.policy.surface:7} {defn.title}")
        return 0
    return play(args.scenario, args.subject, args.interactive, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
