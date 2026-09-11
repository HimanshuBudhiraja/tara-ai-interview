#!/usr/bin/env python3
"""Drive a whole interview against the running server, as a scripted candidate.

    python backend/drive_interview.py                 # a strong candidate
    python backend/drive_interview.py --persona thin
    python backend/drive_interview.py --persona messy

Personas exercise the flow rather than imitating real transcripts:
  strong  — substantive answers; Tara should mostly advance, probe rarely
  thin    — platitudes; Tara should probe up to the ceiling on every item
  messy   — silence, "say that again", "what do you mean", and a skip

Answers are keyed by item id, and follow-ups get a persona-shaped reply, so the
transcript stays coherent however the orchestrator sequences the pool. It uses
the same HTTP endpoints the browser does: a green run here means the
orchestration is sound independent of any UI.
"""
from __future__ import annotations

import argparse
import sys
import textwrap

import httpx

STRONG: dict[str, str] = {
    "csr-emp-01": (
        "Last winter a customer called about a failed renewal and she was near tears — her whole team "
        "was locked out mid-shift. I said, 'I can hear this has landed badly, so before anything else "
        "let me get your team back in.' I issued a seven-day grace extension while we sorted the card, "
        "confirmed access with her on the line, and only then walked through why the payment failed. "
        "She emailed the next day to say it was the calmest support call she'd had."
    ),
    "csr-emp-02": (
        "Something like: 'Three times is three times too many — I've got your history open here so you "
        "don't need to start again.' Naming it matters because by that point the frustration is about "
        "not being heard, not the original fault. And I read the thread before I speak, so I'm not the "
        "fourth person asking the same opening question."
    ),
    "csr-esc-01": (
        "I let them finish completely, without interrupting — usually about ninety seconds. I take "
        "notes so I'm not just waiting for my turn. When there's a gap I come in low and slow: 'Okay. "
        "Here's what I've got, tell me if I've missed anything,' and I play their problem back in my "
        "own words. If they talk over that, I say it once more, shorter. If it turns personal I set one "
        "clear boundary and offer a callback."
    ),
    "csr-esc-02": (
        "I'd say 'Absolutely, I'll get you to my lead — give me sixty seconds to try one thing first so "
        "they're not starting from zero.' That's an offer, not a wall. If they say no and repeat it, I "
        "transfer, because refusing turns one problem into two. If there's a safety issue or they've "
        "already been bounced around, I skip the attempt and escalate immediately."
    ),
    "csr-esc-03": (
        "I don't let it change the offer — whatever I'd give any customer in that position is what they "
        "get, or it's unfair to everyone who didn't threaten. I'd say 'That's your call, and I'd still "
        "rather fix this for you,' then flag it to my lead and whoever owns social the same hour so "
        "nobody's caught out. The threat is usually a signal we left them stuck too long."
    ),
    "csr-tro-01": (
        "I'd start with what changed — 'Was it working yesterday?' — because that splits the space "
        "fastest. Then browser and device, then the exact wording on screen, then whether it's just "
        "them or the whole team. I narrate as I go: 'I'm asking because it tells me whether this is "
        "your account or all of them.' If I can't reproduce it in ten minutes and there's an error id, "
        "it goes to engineering with the steps written out."
    ),
    "csr-tro-02": (
        "Plainly and early: 'This one's on us, and I'm not going to have it fixed today.' No timeline I "
        "can't keep. I'd give the workaround if there is one, tell them exactly when I'll next update "
        "them even if there's nothing new by then, and I'd own that update myself rather than leave it "
        "to the queue."
    ),
    "csr-tro-03": (
        "I say so out loud — 'I don't want to guess at this, give me a few minutes.' Then I check our "
        "internal docs and past tickets, because someone's usually hit it before. I give it about ten "
        "minutes, then I ask in the team channel with what I've already ruled out. Once I have the "
        "answer I write it up in the knowledge base so the next person doesn't spend the ten minutes."
    ),
    "csr-pol-01": (
        "I'd look at the reason first. Two days outside, good explanation, long history — I'd approve it "
        "if it's within my authority. If it isn't, I'd tell them 'I'm putting this to my lead today with "
        "my recommendation to approve, and I'll come back to you by tomorrow,' so they're not left "
        "guessing. The note would say why: tenure, the reason, and that the gap is two days."
    ),
    "csr-pol-02": (
        "I'd flag it the same morning. My lead first so nobody's blindsided, then the customer within "
        "the hour: 'I gave you the wrong steer yesterday, here's what's actually true, and here's what "
        "I'm doing about the position it's put you in.' Then it goes in our internal notes so the next "
        "person doesn't repeat it."
    ),
    "csr-cla-01": (
        "I'd say the browser keeps a copy of the site so it loads faster next time — like keeping a "
        "photocopy on your desk instead of walking to the filing cabinet. Sometimes the real file gets "
        "updated and you're still reading the old photocopy. Clearing the cache throws the photocopy "
        "away so it fetches a fresh one. Then I'd ask them to tell me what they'd click, so I know it "
        "actually landed."
    ),
    "csr-cla-02": (
        "The no goes first, in the first line, because burying it wastes their time and reads as "
        "evasive. Then one sentence of why — the real reason, not a hedge. Then the nearest thing we "
        "can do, if there is one. I'd keep it under six lines and re-read it once for tone, because "
        "short and cold are not the same thing."
    ),
    "csr-own-01": (
        "The blocked small account first — they can't work at all, and impact beats account size. The "
        "two angry ones get a short holding note within ten minutes so they're not sitting in silence. "
        "The large account's routine question I'd answer before lunch. If the blocked one turns out to "
        "need engineering, I'd re-order on the spot."
    ),
    "csr-own-02": (
        "I moved a billing dispute to our finance lead. I wrote the handover as what the customer wants, "
        "what I've already checked, and what I've promised them — so she wasn't reconstructing it. I "
        "told the customer her name and when to expect her. Then I put a reminder on for two days out, "
        "and when it hadn't moved I chased it rather than assuming."
    ),
}

THIN: dict[str, str] = {
    "csr-emp-01": "I always try to be really empathetic and put myself in the customer's shoes.",
    "csr-emp-02": "I'd apologise for the trouble and try to help them as best I can.",
    "csr-esc-01": "I stay calm and I don't take it personally.",
    "csr-esc-02": "I'd follow the process and get my manager if that's what they want.",
    "csr-esc-03": "I'd take it seriously and try to resolve it for them.",
    "csr-tro-01": "I'd ask them some questions to work out what's wrong.",
    "csr-tro-02": "I'd be honest with them about it.",
    "csr-tro-03": "I'd look it up or ask someone on the team.",
    "csr-pol-01": "I'd follow the policy but also use my judgement.",
    "csr-pol-02": "I'd tell my manager straight away.",
    "csr-cla-01": "I'd explain it in simple terms so they understand.",
    "csr-cla-02": "I'd be polite but clear that we can't do it.",
    "csr-own-01": "I'd prioritise the most urgent ones first.",
    "csr-own-02": "I'd make sure I passed on all the details.",
}

MESSY: dict[str, str] = {
    "csr-emp-01": "",  # silence — Tara should wait, not advance
    "csr-emp-02": "What do you mean exactly?",
    "csr-esc-01": "Sorry, could you say that again?",
    "csr-esc-02": "I honestly haven't been in that situation, I'd have to skip that one.",
    "csr-esc-03": "Um, so, yeah, I'd probably just tell my manager and see what they say.",
    "csr-tro-01": "I'd ask what browser they're on and if it worked before, then go from there.",
    "csr-tro-02": "",  # silence again
    "csr-tro-03": "How many more questions are there?",
    "csr-pol-01": "I'd check the reason and if it's fair I'd push for it with my lead.",
    "csr-pol-02": "I'd own up to it, tell the customer, and fix it.",
    "csr-cla-01": "It's like keeping an old photocopy — clearing it gets you the fresh one.",
    "csr-cla-02": "I'd say no clearly but offer whatever else we can do.",
    "csr-own-01": "Whatever's blocking someone completely, then the loud ones, then the rest.",
    "csr-own-02": "I'd write up what I'd done and follow up to check it landed.",
}

# What each persona says when Tara asks a follow-up.
PROBE_REPLIES = {
    "strong": [
        "The exact words were 'let me get your team back in first' — I wanted the fix in front of the "
        "explanation, because the explanation is no use to someone who's still locked out.",
        "Because it tells me whether I'm dealing with one person's setup or something broader, and that "
        "changes who I need to involve and how urgently.",
        "I'd say it once more in a shorter form, and if we're still talking over each other I'd offer to "
        "put it in writing so they can read it without me in their ear.",
    ],
    "thin": [
        "Yeah, just by being understanding really.",
        "I'd handle it professionally.",
        "I'd do whatever was needed to sort it out.",
    ],
    "messy": [
        "Sorry — I'd probably say something like, I've got your details here so you don't have to repeat "
        "it, and then just get on with fixing it.",
        "I'd tell them straight and not leave them hanging.",
        "Yeah, I'd check back in with them afterwards to make sure it stuck.",
    ],
}

PERSONAS = {"strong": STRONG, "thin": THIN, "messy": MESSY}

# What a persona says once it stops stonewalling, so the silence ladder is
# exercised without the run turning into an infinite standoff.
RECOVERY = "Sorry about that, my microphone dropped. I'd take it as it comes and try to sort it out for them."


def wrap(prefix: str, text: str, width: int = 100) -> str:
    return textwrap.fill(prefix + text, width=width, subsequent_indent=" " * len(prefix))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--persona", default="strong", choices=sorted(PERSONAS))
    ap.add_argument("--token", default="demo")
    ap.add_argument("--max-turns", type=int, default=40)
    args = ap.parse_args()

    answers = PERSONAS[args.persona]
    probe_replies = list(PROBE_REPLIES[args.persona])
    client = httpx.Client(base_url=args.base, timeout=90)

    health = client.get("/api/health").json()
    print(f"brain={health['llm']}   budget={health['question_budget']} items   "
          f"follow-ups<={health['max_probes_per_item']} per item   persona={args.persona}\n")

    invite = client.get(f"/api/invite/{args.token}").json()
    print(f"invite  : {invite['candidate_name']} — {invite['role_title']}\n")

    r = client.post("/api/session/start",
                    json={"token": args.token, "consent_recording": True})
    r.raise_for_status()
    started = r.json()
    session_id = started["session_id"]
    reply = started["reply"]
    print(wrap("TARA      | ", reply["text"]), "\n")

    turns = probes = 0
    probe_i = 0
    stalls: dict[str, int] = {}
    while not reply["ends"] and turns < args.max_turns:
        if reply["kind"] == "probe":
            said = probe_replies[probe_i % len(probe_replies)]
            probe_i += 1
        elif reply["kind"] in ("clarify", "repeat", "hold"):
            # Tara is waiting on the SAME item. Stall a couple of times to
            # exercise the ladder, then recover — a persona that never gives in
            # proves nothing past the first escalation and hides the rest.
            stalls[reply["item_id"]] = stalls.get(reply["item_id"], 0) + 1
            scripted = answers.get(reply["item_id"], "")
            said = scripted if stalls[reply["item_id"]] <= 2 else RECOVERY
        else:
            said = answers.get(reply["item_id"], "I think I've covered what I'd do there.")

        print(wrap("CANDIDATE | ", said or "(silence)"), "\n")
        r = client.post(f"/api/session/{session_id}/turn",
                        json={"said": said} if said else {"action": "silence"})
        r.raise_for_status()
        reply = r.json()["reply"]
        if reply["kind"] == "probe":
            probes += 1
        p = reply.get("progress") or {}
        tag = f"{reply['kind']}"
        if p.get("total"):
            tag += f" {p.get('asked')}/{p.get('total')}"
        print(wrap(f"TARA [{tag}] | ", reply["text"]), "\n")
        turns += 1

    summary = client.get(f"/api/session/{session_id}").json()
    print("─" * 100)
    print(f"phase={summary['phase']}   turns={turns}   follow-ups={probes}   "
          f"items={summary['progress']['asked']}/{summary['progress']['total']}")
    print("coverage : " + ",  ".join(
        f"{c['label']} {c['asked']}/{c['target']}" for c in summary["progress"]["coverage"]))
    print(f"session  : {session_id}")
    return 0 if reply["ends"] else 1


if __name__ == "__main__":
    sys.exit(main())
