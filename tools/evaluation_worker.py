#!/usr/bin/env python3
"""Run queued evaluations. One process, independently deployable.

    python -m tools.evaluation_worker                 # loop until stopped
    python -m tools.evaluation_worker --once          # drain and exit
    python -m tools.evaluation_worker --status        # what is queued

## Why a worker at all

Evaluation is already asynchronous in the data model: `jobs.request()` persists
a `PENDING` record with a frozen snapshot, and `jobs.run()` executes it. The API
currently does both in the request, which means a slow model makes a recruiter's
click hang and a crash mid-evaluation leaves a `RUNNING` record nobody retries.
Splitting the second half into a process that can be restarted is the smallest
change that makes the invariant hold:

> A completed interview is not presented as a completed evaluation until a
> readable result exists.

This worker does not change that invariant, and it does not change the
evaluator. It executes records that are already queued.

## Guarantees

* **Idempotent.** `jobs.run()` takes a per-session lock (a threading lock plus
  `fcntl.flock`) and re-reads the record inside it, so two workers on one record
  produce one evaluation and one no-op — never two "current" results.
* **Retryable.** A failure lands the record in `FAILED` with a `failed_stage`,
  which is a state an operator can see and re-run. Nothing is silently retried
  forever: `--max-attempts` bounds it.
* **Graceful shutdown.** SIGTERM and SIGINT stop the loop after the record in
  flight finishes rather than in the middle of one. A platform's rolling
  restart therefore costs at most one evaluation's latency, not one corrupted
  record.
* **No duplicates.** Claiming is the lock; the record's `status` transition is
  the receipt.

One instance is enough for the first deployment. Two are safe; they will
contend on the lock and one will do the work.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Set by SIGTERM/SIGINT. Checked between records, never inside one.
_STOPPING = False


def _stop(signum, frame) -> None:  # noqa: ANN001, ARG001
    global _STOPPING
    _STOPPING = True


def _status() -> int:
    from services.data import evaluations

    pending = evaluations.list_pending(limit=1000)
    rows = evaluations.list_all()
    by_status: dict[str, int] = {}
    for record in rows:
        by_status[record.status] = by_status.get(record.status, 0) + 1

    print(f"evaluations: {len(rows)}")
    for status, count in sorted(by_status.items()):
        print(f"  {status:12} {count}")
    print(f"queued now : {len(pending)}")
    if pending:
        oldest = min(r.created_at for r in pending)
        print(f"oldest wait: {round((time.time() - oldest) / 60, 1)} min")
    # A backlog is not an error; a failure is something to look at.
    return 1 if by_status.get("failed") else 0


def drain(limit: int, max_attempts: int) -> dict[str, int]:
    """Execute what is queued, once. Returns counts."""
    from services import observability
    from services.data import evaluations
    from services.evaluation import jobs

    done = failed = skipped = 0
    for record in evaluations.list_pending(limit=limit):
        if _STOPPING:
            break
        if record.attempt > max_attempts:
            # Bounded on purpose: an evaluation that has failed repeatedly is a
            # thing to investigate, not a thing to keep paying a provider for.
            skipped += 1
            continue
        started = time.perf_counter()
        try:
            result = jobs.run(record)
        except Exception as exc:  # noqa: BLE001 — a worker must survive one bad record
            failed += 1
            observability.log(
                "evaluation.worker_error", logging.ERROR,
                evaluation_id=record.evaluation_id, session_id=record.session_id,
                attempt=record.attempt, failure_category=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            continue
        duration = round((time.perf_counter() - started) * 1000, 1)
        ok = result.status == "complete"
        done += int(ok)
        failed += int(not ok)
        observability.log(
            "evaluation.completed" if ok else "evaluation.failed",
            logging.INFO if ok else logging.WARNING,
            evaluation_id=result.evaluation_id, session_id=result.session_id,
            job_state=result.status, attempt=result.attempt,
            provider=(result.model_meta or {}).get("provider", ""),
            model=(result.model_meta or {}).get("model", ""),
            failure_category=result.error_kind or "",
            failed_stage=result.failed_stage or "",
            duration_ms=duration,
        )
    return {"completed": done, "failed": failed, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="drain the queue and exit")
    ap.add_argument("--status", action="store_true", help="report the queue and exit")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="seconds between polls when idle (default 5)")
    ap.add_argument("--limit", type=int, default=10,
                    help="records per pass (default 10)")
    ap.add_argument("--max-attempts", type=int, default=3,
                    help="stop re-running a record after this many attempts")
    args = ap.parse_args()

    if args.status:
        return _status()

    from services import observability

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    observability.log("worker.started", interval_sec=args.interval,
                      limit=args.limit, max_attempts=args.max_attempts)
    passes = 0
    totals = {"completed": 0, "failed": 0, "skipped": 0}
    while not _STOPPING:
        counts = drain(args.limit, args.max_attempts)
        for key, value in counts.items():
            totals[key] += value
        passes += 1
        if args.once:
            break
        # Sleep in short slices so SIGTERM is noticed promptly rather than after
        # a full interval — a platform that waits 30s for a graceful stop should
        # not spend most of it watching a sleeping process.
        waited = 0.0
        while waited < args.interval and not _STOPPING:
            time.sleep(min(0.25, args.interval - waited))
            waited += 0.25

    observability.log("worker.stopped", passes=passes, **totals)
    print(f"passes {passes} · completed {totals['completed']} "
          f"· failed {totals['failed']} · skipped {totals['skipped']}")
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
