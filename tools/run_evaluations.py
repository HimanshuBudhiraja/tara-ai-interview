"""Drain pending evaluations.

The repository has no queue and this phase did not add one. This is the worker
that would sit behind one: run it on a timer, after a batch of interviews, or by
hand while developing. It calls exactly what an HTTP request calls, so there is
one execution path rather than two that drift.

    python tools/run_evaluations.py            # run everything pending
    python tools/run_evaluations.py --limit 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.evaluation import jobs, stub  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    if stub.enabled():
        print(f"[warn] {stub.STUB_ENV} is set — evidence is deterministic stub output, "
              f"not model extraction.", flush=True)

    done = jobs.run_pending(args.limit)
    if not done:
        print("Nothing pending.")
        return 0
    for record in done:
        detail = (
            f"{record.result.get('recommendation', '')}"
            if record.status == "completed" else f"{record.error_kind}: {record.error}"
        )
        print(f"{record.evaluation_id}  {record.session_id}  {record.status:<9}  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
