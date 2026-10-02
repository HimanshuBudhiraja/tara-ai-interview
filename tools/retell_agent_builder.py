"""Check (or update) the one Retell agent behind the Agent Builder.

    python -m tools.retell_agent_builder            # check: live agent vs the prompt file
    python -m tools.retell_agent_builder --push     # update the live LLM's prompt to the file

The global prompt lives in git, in section C of
`content/retell/tara_agent_builder_prompt.md`. The agent lives in Retell. This
keeps them the same. `check` reads both and lists every difference: the prompt
text, begin message, start speaker, end_call tool, default variables, and the
turn-taking settings. `--push` writes only the LLM's general prompt and begin
message, and asks first. It never creates an agent; there is exactly one.

Needs RETELL_API_KEY, RETELL_AGENT_BUILDER_AGENT_ID and RETELL_AGENT_BUILDER_LLM_ID.
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

from services import config
from services.assessment import agent_builder_retell as rx

API = "https://api.retellai.com"

#: Section A of the prompt file, as Retell field → expected value.
AGENT_SETTINGS = {
    "responsiveness": 0.4,
    "interruption_sensitivity": 0.4,
    "enable_backchannel": True,
    "backchannel_frequency": 0.7,
    "backchannel_words": ["mm-hmm", "I see", "right"],
    "reminder_trigger_ms": 15000,
    "reminder_max_count": 1,
}


def _get(http: httpx.Client, path: str) -> dict:
    r = http.get(API + path)
    r.raise_for_status()
    return r.json()


def check_flow(http: httpx.Client) -> list[str]:
    """The same checks for a conversation-flow agent."""
    agent = _get(http, f"/get-agent/{config.RETELL_AGENT_BUILDER_AGENT_ID}")
    flow = _get(http, f"/get-conversation-flow/{config.RETELL_AGENT_BUILDER_FLOW_ID}")
    problems: list[str] = []
    if (agent.get("response_engine") or {}).get("conversation_flow_id") != config.RETELL_AGENT_BUILDER_FLOW_ID:
        problems.append("the agent is not connected to RETELL_AGENT_BUILDER_FLOW_ID")
    if (flow.get("global_prompt") or "").strip() != rx.general_prompt():
        problems.append("the live global prompt differs from section C of the prompt file")
    if flow.get("start_speaker") != "agent":
        problems.append(f"start_speaker is {flow.get('start_speaker')!r}, not 'agent'")
    defaults = flow.get("default_dynamic_variables") or {}
    missing = [v for v in rx.VARIABLES if not defaults.get(v)]
    if missing:
        problems.append("default dynamic variables missing: " + ", ".join(missing))
    used = set(rx.prompt_variables(flow.get("global_prompt") or ""))
    for n in flow.get("nodes") or []:
        used |= rx.prompt_variables(json.dumps(n))
    unknown = used - set(rx.VARIABLES) - rx.SYSTEM_VARIABLES
    if unknown:
        problems.append("the flow uses variables nobody sends: " + ", ".join(sorted(unknown)))
    nodes = {n.get("type") for n in flow.get("nodes") or []}
    if "end" not in nodes:
        problems.append("the flow has no End Call node")
    return problems


def check(http: httpx.Client) -> list[str]:
    if config.RETELL_AGENT_BUILDER_FLOW_ID:
        return check_flow(http)
    agent = _get(http, f"/get-agent/{config.RETELL_AGENT_BUILDER_AGENT_ID}")
    llm = _get(http, f"/get-retell-llm/{config.RETELL_AGENT_BUILDER_LLM_ID}")
    problems: list[str] = []
    if (agent.get("response_engine") or {}).get("llm_id") != config.RETELL_AGENT_BUILDER_LLM_ID:
        problems.append("the agent is not connected to RETELL_AGENT_BUILDER_LLM_ID")
    if (llm.get("general_prompt") or "").strip() != rx.general_prompt():
        problems.append("the live general prompt differs from section C of the prompt file")
    if llm.get("begin_message") != "{{opening_line}}":
        problems.append(f"begin_message is {llm.get('begin_message')!r}, not '{{{{opening_line}}}}'")
    if llm.get("start_speaker") != "agent":
        problems.append(f"start_speaker is {llm.get('start_speaker')!r}, not 'agent'")
    if not any(t.get("type") == "end_call" for t in llm.get("general_tools") or []):
        problems.append("the end_call tool is missing")
    defaults = llm.get("default_dynamic_variables") or {}
    missing = [v for v in rx.VARIABLES if not defaults.get(v)]
    if missing:
        problems.append("default dynamic variables missing: " + ", ".join(missing))
    unknown = rx.prompt_variables(llm.get("general_prompt") or "") - set(rx.VARIABLES)
    if unknown:
        problems.append("the live prompt uses variables outside the contract: " + ", ".join(sorted(unknown)))
    for k, want in AGENT_SETTINGS.items():
        if agent.get(k) != want:
            problems.append(f"agent {k} is {agent.get(k)!r}, expected {want!r}")
    return problems


def push(http: httpx.Client) -> None:
    r = http.patch(f"{API}/update-retell-llm/{config.RETELL_AGENT_BUILDER_LLM_ID}",
                   json={"general_prompt": rx.general_prompt(), "begin_message": "{{opening_line}}"})
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--push", action="store_true", help="update the live prompt to the file")
    args = ap.parse_args()
    if not (config.RETELL_API_KEY and config.RETELL_AGENT_BUILDER_AGENT_ID
            and (config.RETELL_AGENT_BUILDER_LLM_ID or config.RETELL_AGENT_BUILDER_FLOW_ID)):
        print("Set RETELL_API_KEY, RETELL_AGENT_BUILDER_AGENT_ID and RETELL_AGENT_BUILDER_LLM_ID (or _FLOW_ID).")
        return 2
    with httpx.Client(timeout=20, headers={"Authorization": f"Bearer {config.RETELL_API_KEY}"}) as http:
        if args.push and config.RETELL_AGENT_BUILDER_FLOW_ID:
            print("--push updates single-prompt agents only. Edit the flow in Retell, then re-run the check.")
            return 2
        if args.push:
            if input(f"Update the live prompt on {config.RETELL_AGENT_BUILDER_LLM_ID}? [y/N] ").strip().lower() != "y":
                print("Nothing changed.")
                return 1
            push(http)
            print("Pushed.")
        problems = check(http)
    if problems:
        print("The live agent differs from the repo:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"OK: {config.RETELL_AGENT_BUILDER_AGENT_ID} matches the repo "
          f"({len(rx.VARIABLES)} variables, prompt, begin message, end_call, turn-taking).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
