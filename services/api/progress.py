"""What a long generation is doing right now.

Designing an interview from a job description takes upwards of two minutes: one
model call to read the JD and propose the skills and tasks, then one per
blueprint slot to write the questions. For all of that time the recruiter had a
pulsing icon and a sentence, which looks identical at five seconds and at a
hundred and fifty — so the only question they actually have ("is this working,
or has it hung?") was the one thing the screen could not answer.

This is a progress board, not a progress *bar*. Every value in it is something
that has already happened: a stage the server has entered, a slot the generator
has finished. Nothing here interpolates, estimates a percentage, or advances on
a timer. A bar that fills on a timer is a lie that happens to be reassuring, and
the first time generation hangs it keeps filling — which is precisely when the
recruiter most needs to be told the truth.

Scope and lifetime, stated plainly:

* **In-process.** A dict in one worker's memory. `DEPLOYMENT.md` documents this
  deployment as a single process, so that holds today; behind two workers the
  poll can land on the worker that is not doing the work and would read
  `unknown`. The client treats `unknown` as "no news", never as failure, so the
  degradation is a screen that stops updating rather than one that lies.
* **Ephemeral.** Entries are pruned after `_TTL`. Losing one costs a progress
  animation, never work: the generation itself is driven by the POST, and
  finishes whether or not anybody is watching.
* **Not authorisation.** The token says which generation, never who may see it.
  The endpoint checks the caller's organization against `organization_id` below.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

#: The stages a generation moves through, in order. `complete` and `failed` are
#: terminal. Named after what the server is DOING, because the recruiter reads
#: these as a sentence about the work rather than as internal state.
STAGES = (
    "preparing",
    "designing",
    "writing_questions",
    "complete",
    "failed",
)

#: How long a finished entry stays readable. Long enough that a client polling
#: every two seconds always sees the terminal stage before it is pruned, short
#: enough that an abandoned tab cannot pin entries in memory.
_TTL = 600.0

#: Belt and braces against unbounded growth if something stops pruning.
_MAX_ENTRIES = 256


@dataclass
class Progress:
    token: str
    #: Who is allowed to read this. The endpoint compares the caller's
    #: organization against it and answers 404 otherwise — the same
    #: non-disclosing shape every other cross-tenant read uses.
    organization_id: str
    stage: str = "preparing"
    #: One short line in the recruiter's language. Never an exception string:
    #: this is rendered on screen, and internal detail on a recruiter's screen
    #: is both unhelpful and a disclosure.
    detail: str = ""
    #: Slots finished / slots planned, once the question stage begins. Both 0
    #: before that, and the client renders a count only when `total` is set.
    done: int = 0
    total: int = 0
    #: The interview being built, known from the moment the row is created.
    #: Lets the client navigate as soon as the work finishes.
    interview_id: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def payload(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "detail": self.detail,
            "done": self.done,
            "total": self.total,
            "interview_id": self.interview_id,
            # Elapsed is computed here rather than by the client so it survives
            # a clock that disagrees between browser and server.
            "elapsed_sec": round(time.time() - self.started_at, 1),
        }


_ENTRIES: dict[str, Progress] = {}
_LOCK = threading.Lock()


def _prune(now: float) -> None:
    """Drop what nobody can still be watching. Caller holds the lock."""
    stale = [t for t, p in _ENTRIES.items() if now - p.updated_at > _TTL]
    for token in stale:
        _ENTRIES.pop(token, None)
    if len(_ENTRIES) > _MAX_ENTRIES:
        for token, _ in sorted(_ENTRIES.items(), key=lambda kv: kv[1].updated_at)[
            : len(_ENTRIES) - _MAX_ENTRIES
        ]:
            _ENTRIES.pop(token, None)


def start(token: str, organization_id: str, detail: str = "") -> None:
    """Open a board. A blank token disables reporting for this request."""
    if not token:
        return
    now = time.time()
    with _LOCK:
        _prune(now)
        _ENTRIES[token] = Progress(
            token=token, organization_id=organization_id, detail=detail,
        )


def update(
    token: str,
    *,
    stage: str | None = None,
    detail: str | None = None,
    done: int | None = None,
    total: int | None = None,
    interview_id: str | None = None,
) -> None:
    """Record something that has happened. Silent if the token is unknown —
    progress reporting must never be able to fail the work it describes."""
    if not token:
        return
    with _LOCK:
        entry = _ENTRIES.get(token)
        if entry is None:
            return
        if stage is not None and stage in STAGES:
            entry.stage = stage
        if detail is not None:
            entry.detail = detail
        if done is not None:
            entry.done = done
        if total is not None:
            entry.total = total
        if interview_id is not None:
            entry.interview_id = interview_id
        entry.updated_at = time.time()


def get(token: str, organization_id: str) -> Progress | None:
    """One board, if it exists AND belongs to this organization.

    A token from another tenant is indistinguishable from one that was never
    issued. Confirming that a token is real would tell an attacker which of
    their guesses were worth pursuing.
    """
    with _LOCK:
        entry = _ENTRIES.get(token)
        if entry is None or entry.organization_id != organization_id:
            return None
        return entry


def clear() -> None:
    """Tests only."""
    with _LOCK:
        _ENTRIES.clear()
