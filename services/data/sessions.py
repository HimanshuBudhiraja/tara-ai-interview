"""Runtime session state — persisted after every turn.

Deliberately separate from the durable product record (`interviews`,
`invitations`, `results`): this is hot, per-turn state whose only job is to let
a dropped connection resume exactly where it left off. It is written on every
turn and read on every turn, which is a different access pattern from anything
else in the product.

`SessionStore` is the seam. `FileSessionStore` is what runs today — atomic JSON
writes to disk, no server, no setup. A `RedisSessionStore` implementing the same
four methods is the drop-in for multi-process deployment, and nothing that calls
this module would change.

The audit log moved to `services.data.audit`; the `audit`/`read_audit` names
here are kept because the orchestrator has always called them and there is no
reason to churn the turn loop.
"""
from __future__ import annotations

import json
import time
from typing import Any, Protocol

from services import config
from services.data import audit as _audit
from services.orchestrator.state import SessionState


class SessionStore(Protocol):
    """What the runtime needs from a session store, and nothing more."""

    def exists(self, session_id: str) -> bool: ...
    def save(self, state: SessionState) -> None: ...
    def load(self, session_id: str) -> SessionState: ...
    def try_load(self, session_id: str) -> SessionState | None: ...
    def delete(self, session_id: str) -> None: ...
    def list_ids(self) -> list[str]: ...


class FileSessionStore:
    """One JSON file per session, written atomically.

    The temp-file-then-rename is not ceremony: a crash partway through a write
    would otherwise leave a torn session, and a candidate mid-interview would
    reopen their link to find nothing there.
    """

    def __init__(self, directory=None) -> None:
        self.dir = directory or config.SESSION_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str):
        return self.dir / f"{session_id}.json"

    def exists(self, session_id: str) -> bool:
        return self._path(session_id).exists()

    def save(self, state: SessionState) -> None:
        state.updated_at = time.time()
        path = self._path(state.session_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    def load(self, session_id: str) -> SessionState:
        return SessionState.from_dict(
            json.loads(self._path(session_id).read_text(encoding="utf-8"))
        )

    def try_load(self, session_id: str) -> SessionState | None:
        try:
            return self.load(session_id)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def list_ids(self) -> list[str]:
        """Every session on disk, oldest first.

        Reporting only. The runtime never enumerates sessions — it is handed one
        id and works on that — so this exists for the pilot's own counting and
        is deliberately not part of the turn loop's path.
        """
        return [
            path.stem
            for path in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        ]


_STORE: SessionStore = FileSessionStore()


def get_store() -> SessionStore:
    return _STORE


def set_store(store: SessionStore) -> None:
    """Swap the implementation — used by tests, and by Redis when it lands."""
    global _STORE
    _STORE = store


# --------------------------------------------------------------------------- #
#  Module-level functions the orchestrator calls (unchanged signatures)
# --------------------------------------------------------------------------- #
def exists(session_id: str) -> bool:
    return _STORE.exists(session_id)


def save(state: SessionState) -> None:
    _STORE.save(state)


def load(session_id: str) -> SessionState:
    return _STORE.load(session_id)


def try_load(session_id: str) -> SessionState | None:
    return _STORE.try_load(session_id)


def delete(session_id: str) -> None:
    _STORE.delete(session_id)


def list_ids() -> list[str]:
    return _STORE.list_ids()


def audit(session_id: str, event: str, **fields: Any) -> None:
    _audit.session(session_id, event, **fields)


def read_audit(session_id: str) -> list[dict[str, Any]]:
    return _audit.read(session_id)
