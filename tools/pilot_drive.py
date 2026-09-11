#!/usr/bin/env python3
"""Drive scripted pilot candidates against a running server.

    python -m tools.pilot_drive --persona strong
    python -m tools.pilot_drive --concurrent strong thin messy

One rehearsal candidate per persona, using the same answer scripts the
end-to-end persona tests use, over the same HTTP endpoints a browser would.
Nothing here bypasses the orchestrator, the evaluator or the API.

Two things it adds that the plain persona driver does not:

  * the invitation carries `persona:<name>`, so a pilot dataset row can say what
    kind of candidate produced it without storing anything about a person;
  * `--concurrent` runs several interviews at once, which is the only way to
    exercise the concurrency the pilot is restricted to (three at a time).

It does not create the pilot run. Open one from the console (or set
`TARA_PILOT_RUN`) first, so which batch these belong to is a decision somebody
made rather than a side effect of running a script.

It signs in like a recruiter, because it uses recruiter endpoints. The password
comes from `TARA_DRIVER_PASSWORD` or a prompt — never from the command line,
where it would sit in the shell history and in `ps` output for every user on the
machine. The candidate half of the run holds no recruiter session at all: it
carries the invitation token, exactly as a candidate's browser does.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.personas import PERSONAS, PROBE_REPLIES, RECOVERY  # noqa: E402


def sign_in(base: str, email: str, password: str) -> httpx.Cookies:
    """Authenticate once, and hand the session cookie to every worker.

    One sign-in rather than one per persona: `--concurrent` would otherwise trip
    the login rate limit and look like a driver bug.
    """
    with httpx.Client(base_url=base, timeout=30) as client:
        response = client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
        if response.status_code == 401:
            raise SystemExit(
                "Sign-in refused. Check the address and password — or create an "
                "account with `python -m tools.make_user --email you@example.com`."
            )
        if response.status_code == 429:
            raise SystemExit("Sign-in is rate limited right now. Wait a minute.")
        response.raise_for_status()
        who = response.json()["user"]
        print(f"[driver] signed in as {who['email']} ({who['role']}) "
              f"in {response.json()['organization']['name']}", flush=True)
        return client.cookies


def run_one(
    base: str, interview_id: str, persona: str, label: str = "",
    cookies: httpx.Cookies | None = None,
) -> dict:
    """One whole interview, start to evaluation. Returns what it produced."""
    answers = PERSONAS[persona]
    probes = list(PROBE_REPLIES[persona])
    client = httpx.Client(base_url=base, timeout=300, cookies=cookies)
    started_at = time.time()

    invite = client.post(
        f"/api/recruiter/interviews/{interview_id}/invitations",
        json={
            "candidates": [label or f"Pilot {persona.title()}"],
            "note": f"persona:{persona}",
        },
    )
    invite.raise_for_status()
    token = invite.json()["created"][0]["token"]

    # The device check, reported the way the candidate app reports it.
    client.post(f"/api/invite/{token}/precheck",
                json={"token": token, "outcome": "text", "reason": "scripted_driver"})

    reply = client.post("/api/session/start", json={
        "token": token, "consent_recording": True, "channel": "text",
    })
    reply.raise_for_status()
    session_id = reply.json()["session_id"]
    turn = reply.json()["reply"]

    probe_i = 0
    stalls: dict[str, int] = {}
    turns = 0
    while not turn["ends"] and turns < 40:
        if turn["kind"] == "probe":
            said = probes[probe_i % len(probes)]
            probe_i += 1
        elif turn["kind"] in ("clarify", "repeat", "hold"):
            stalls[turn["item_id"]] = stalls.get(turn["item_id"], 0) + 1
            scripted = answers.get(turn["item_id"], "")
            said = scripted if stalls[turn["item_id"]] <= 2 else RECOVERY
        else:
            said = answers.get(turn["item_id"], "I think I've covered what I'd do there.")
        response = client.post(
            f"/api/session/{session_id}/turn",
            params={"token": token},
            json={"said": said, "turn_id": f"{session_id}-{turns}"},
        )
        response.raise_for_status()
        turn = response.json()["reply"]
        turns += 1

    interview_sec = time.time() - started_at
    evaluation_started = time.time()
    evaluation = client.post(
        f"/api/recruiter/sessions/{session_id}/evaluation", json={}
    )
    evaluation.raise_for_status()
    body = evaluation.json()

    return {
        "persona": persona,
        "session_id": session_id,
        "turns": turns,
        "interview_sec": round(interview_sec, 1),
        "evaluation_wall_sec": round(time.time() - evaluation_started, 1),
        "evaluation_id": body.get("evaluation_id"),
        "status": body.get("status"),
        "error": body.get("error", ""),
        "score": (body.get("candidate_details") or {}).get("total_score"),
        "max_score": body.get("maximum_possible_score"),
        "percentage": body.get("percentage"),
        "rating": (body.get("candidate_details") or {}).get("overall_rating"),
        "recommendation": body.get("recommendation"),
        "engine": body.get("engine", {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--interview", default="iv_default")
    ap.add_argument("--persona", default="strong", choices=sorted(PERSONAS))
    ap.add_argument(
        "--concurrent", nargs="*", default=None,
        help="personas to run at the same time, e.g. --concurrent strong thin messy",
    )
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--email", default=os.environ.get("TARA_DRIVER_EMAIL", ""),
        help="recruiter account to sign in as (or TARA_DRIVER_EMAIL)",
    )
    args = ap.parse_args()

    email = args.email or input("Recruiter email: ").strip()
    # Environment or prompt. Deliberately not a `--password` flag: that would
    # put the credential in shell history and in every `ps` on the box.
    password = os.environ.get("TARA_DRIVER_PASSWORD") or getpass.getpass(
        f"Password for {email}: ")
    cookies = sign_in(args.base, email, password)
    del password

    if args.concurrent:
        with ThreadPoolExecutor(max_workers=len(args.concurrent)) as pool:
            futures = [
                pool.submit(run_one, args.base, args.interview, persona, "", cookies)
                for persona in args.concurrent
            ]
            rows = [f.result() for f in futures]
    else:
        rows = [run_one(args.base, args.interview, args.persona, "", cookies)]

    for row in rows:
        print(
            f"{row['persona']:7} {row['status']:9} {row['score']}/{row['max_score']} "
            f"({row['percentage']}%) {row['rating'] or '':9} | {row['recommendation'] or row['error']} "
            f"| interview {row['interview_sec']}s · evaluation {row['evaluation_wall_sec']}s "
            f"| {row['session_id'][:8]}"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
