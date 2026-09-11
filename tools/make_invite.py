#!/usr/bin/env python3
"""Mint a candidate invite link.

    python backend/make_invite.py "Priya Sharma"
    python backend/make_invite.py "Alex Chen" --base http://localhost:5173
    python backend/make_invite.py --list
    python backend/make_invite.py --reset-demo

Stands in for the recruiter console. The candidate never signs up — identity and
role ride on the link, which is why there is no login screen anywhere in the app.
"""
from __future__ import annotations

import argparse
import sys

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import config  # noqa: E402
from services.data import interviews, invites, versions  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="Candidate's full name")
    ap.add_argument("--role", default=config.ROLE)
    ap.add_argument(
        "--interview",
        default="",
        help="Interview id to attach. Defaults to the first published one — an invite "
             "with no interview would silently fall back to the pool's defaults and "
             "ignore whatever the recruiter configured.",
    )
    ap.add_argument("--base", default="http://localhost:5173", help="Where the candidate app is served")
    ap.add_argument("--list", action="store_true", help="Show every invite and its status")
    ap.add_argument("--reset-demo", action="store_true", help="Return the demo link to a fresh state")
    args = ap.parse_args()

    if args.list:
        rows = invites.list_all()
        if not rows:
            print("No invites yet.")
            return 0
        titles = {c.id: c.title for c in interviews.list_all()}
        for row in rows:
            title = titles.get(row.get("interview_id", ""), "— no interview attached —")
            print(f"{row['status']:<12} {row['candidate_name']:<20} "
                  f"{args.base}/?invite={row['token']}")
            print(f"{'':<12} {title}")
        return 0

    if args.reset_demo:
        published = [c for c in interviews.list_all() if c.status == "published"]
        default_id = published[0].id if published else "iv_default"
        latest = versions.latest_published(default_id)
        demo = invites.get("demo")
        if demo is None:
            demo = invites.ensure_demo_invite(default_id, latest.version if latest else 0)
        demo.interview_id = demo.interview_id or default_id
        # Resetting the demo link re-pins it to whatever is published NOW —
        # that is what makes it a demo link rather than a candidate's link.
        demo.interview_version = latest.version if latest else 0
        demo.status = "pending"
        demo.session_id = None
        invites.update(demo)
        print(f"demo reset → {args.base}/?invite=demo")
        return 0

    if not args.name:
        ap.error("give a candidate name, or use --list / --reset-demo")

    interview_id = args.interview
    if not interview_id:
        published = [c for c in interviews.list_all() if c.status == "published"]
        if not published:
            print(
                "No published interview to attach this candidate to. Create and publish one "
                "in the console (/recruiter/interviews) first, or pass --interview.",
                file=sys.stderr,
            )
            return 1
        interview_id = published[0].id

    cfg = interviews.get(interview_id)
    if cfg is None:
        print(f"No interview with id {interview_id!r}.", file=sys.stderr)
        return 1

    latest = versions.latest_published(cfg.id)
    if latest is None:
        print(
            f"{cfg.title!r} has no published version behind it. Publish it once, so this "
            f"candidate sits a fixed set of questions rather than a moving one.",
            file=sys.stderr,
        )
        return 1
    invite = invites.create(args.name, cfg.role, cfg.id, interview_version=latest.version)
    print(f"{invite.candidate_name} → {args.base}/?invite={invite.token}")
    print(f"  interview: {cfg.title} @ v{latest.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
