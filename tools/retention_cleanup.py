#!/usr/bin/env python3
"""Find candidate data that is past its retention deadline, and erase it.

    python -m tools.retention_cleanup --dry-run     # what would go
    python -m tools.retention_cleanup               # do it
    python -m tools.retention_cleanup --status      # counts, delete nothing
    python -m tools.retention_cleanup --token <tok> # one specific candidate

This is the mechanism the retention policy depends on. A policy with no
mechanism is a sentence in a document; the invariant the phase is built around —
"data eligible for deletion must not remain indefinitely because no service
knows it is eligible" — is only true because something actually runs.

**Safe to run twice, and safe to run while another copy is running.** Every
erasure is idempotent, verified after the fact, and the stores serialise their
own writes. Two concurrent sweeps racing on one record produce one deletion and
one no-op, not a corrupted row.

**It does not schedule itself.** Cron, a systemd timer or the platform's
scheduler runs it; `TARA_RETENTION_SWEEP_INTERVAL_HOURS` documents how often
that is expected to be (default 24). Building a scheduler into a process that
may run as several replicas would mean several sweeps a day per replica, which
is not better.

Exit codes, so a scheduler can alert on them:

    0   nothing eligible, or everything erased and verified
    1   at least one erasure failed — the records are in `deletion_failed`
        and re-running is the retry
    2   bad usage
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _print_status() -> int:
    from services import config
    from services.data import retention

    summary = retention.summary()
    print("Retention policy")
    print(f"  candidate data      : {config.CANDIDATE_DATA_RETENTION_DAYS} days")
    print(f"  transcript          : {config.TRANSCRIPT_RETENTION_DAYS} days")
    print(f"  evaluation          : {config.EVALUATION_RETENTION_DAYS} days")
    print(f"  audit               : {config.AUDIT_RETENTION_DAYS} days")
    print(f"  expected sweep every: {config.RETENTION_SWEEP_INTERVAL_HOURS}h")
    print()
    print(f"Candidate records: {summary['candidates']}")
    for state, count in summary["by_lifecycle"].items():
        if count:
            print(f"  {state:20} {count}")
    print()
    print(f"Awaiting cleanup   : {summary['awaiting_cleanup']}")
    print(f"Failed deletions   : {summary['failed']}")
    if summary["awaiting_cleanup"]:
        print(f"Oldest overdue by  : {summary['oldest_overdue_days']} days")
    # A failed deletion is the thing an operator must not miss.
    return 1 if summary["failed"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be erased and change nothing")
    ap.add_argument("--status", action="store_true",
                    help="counts by lifecycle state; erases nothing")
    ap.add_argument("--token", default="",
                    help="erase one specific candidate, ignoring the deadline")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many records (0 = no limit)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args()

    from services.data import erasure, retention

    if args.status:
        return _print_status()

    if args.token:
        # A named erasure is an operator acting on a specific request, so it
        # does not wait for a deadline. It is still audited, still verified,
        # and still refuses to claim success it cannot prove.
        retention.request_deletion(args.token, actor="operator")
        outcome = erasure.erase(args.token, actor="operator")
        report = {"eligible": 1, "erased": int(outcome.ok),
                  "failed": int(not outcome.ok), "results": [outcome.to_dict()]}
    else:
        report = erasure.sweep(limit=args.limit, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        verb = "would erase" if args.dry_run else "erased"
        print(f"eligible {report['eligible']} · {verb} {report.get('erased', 0)} "
              f"· failed {report.get('failed', 0)}")
        for row in report["results"]:
            if row.get("would_erase"):
                print(f"  · {row['token']}  ({row['lifecycle']})")
            elif row.get("ok"):
                counts = ", ".join(f"{k}={v}" for k, v in row["removed"].items() if v)
                note = " (already erased)" if row.get("already_deleted") else ""
                print(f"  ✓ {row['token']}  {counts or 'nothing to remove'}{note}")
            else:
                print(f"  ✗ {row['token']}  {row['error']} "
                      f"— still present: {', '.join(row['remaining']) or 'unknown'}")

    return 1 if report.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
