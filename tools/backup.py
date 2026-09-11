#!/usr/bin/env python3
"""Snapshot, verify and restore the data directory.

    python -m tools.backup --create /backups            # take one
    python -m tools.backup --list /backups              # what exists
    python -m tools.backup --verify /backups/tara-…tar.gz
    python -m tools.backup --restore /backups/tara-…tar.gz --into /srv/tara/data

Core state is a directory of JSON files (DEPLOYMENT.md §"Durable persistence
status"), which means the directory IS the database and a backup of it is the
*only* durability this deployment has. There is no replica, no write-ahead log
and no point-in-time recovery: the recovery-point objective is exactly the
interval between snapshots.

## Consistency, honestly

Writes are atomic per file — temp file plus rename — and guarded by a per-file
lock. They are **not** atomic across files. A snapshot taken while a request is
in flight can therefore catch a moment where, say, an invitation is marked
`complete` and its session file is a fraction of a second behind.

Two ways to deal with that, and this tool supports being told which:

* `--quiesced` asserts the API is stopped. The snapshot is then a clean
  point-in-time copy and is recorded as such in the manifest.
* Without it, the snapshot is recorded as `live` and the manifest says so. A
  live snapshot is worth taking — it is much better than nothing — but it is
  not a guaranteed-consistent one, and a restore from it should be followed by
  reading the manifest's warning.

Nothing here pretends a live copy is transactional.

## What it does not do

* **No scheduling.** Cron, a timer or the platform runs it.
* **No offsite storage.** It writes a file; getting that file somewhere safe is
  the deployment's job.
* **No encryption.** The archive contains verbatim candidate transcripts,
  evaluation evidence and scrypt password hashes. Encrypting it at rest is
  required and is the storage layer's responsibility — see DEPLOYMENT.md.

Those three are INFRASTRUCTURE DEPENDENCIES, named rather than implied.

## Backups and erasure

A snapshot taken before an erasure contains the erased data. Restoring it
resurrects a candidate whose record was deleted. After any restore, re-run
`python -m tools.retention_cleanup`, and keep snapshot retention shorter than
the erasure SLA. The restore path prints this rather than leaving it to be
remembered.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Written into every archive so a restore can check what it is holding before
#: it overwrites anything.
MANIFEST = "tara-backup.json"

#: Transient files that should never be in a snapshot: a half-written atomic
#: write, and the probe files the config and readiness checks leave behind.
_SKIP_SUFFIXES = (".tmp",)
_SKIP_NAMES = (".config-check", ".readiness", MANIFEST)


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _members(data_dir: Path) -> list[Path]:
    return sorted(
        p for p in data_dir.rglob("*")
        if p.is_file()
        and p.suffix not in _SKIP_SUFFIXES
        and p.name not in _SKIP_NAMES
    )


def create(data_dir: Path, into: Path, *, quiesced: bool = False) -> Path:
    """Write one snapshot. Returns its path."""
    from services import config

    into.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    archive = into / f"tara-{stamp}.tar.gz"

    files = _members(data_dir)
    manifest = {
        "created_at": time.time(),
        "created_at_iso": stamp,
        "service_version": config.SERVICE_VERSION,
        "environment": config.ENVIRONMENT,
        # The honest label. A restore reads this before it trusts the archive.
        "consistency": "quiesced" if quiesced else "live",
        "warning": (
            "" if quiesced else
            "Taken while the API was running. Writes are atomic per file but "
            "not across files, so this snapshot may catch two related records "
            "a moment apart. Prefer --quiesced, or a volume snapshot."
        ),
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        # Per-file digests, so `--verify` checks the CONTENT rather than just
        # that the archive opens.
        "files": {
            str(p.relative_to(data_dir)): {"sha256": _digest(p), "bytes": p.stat().st_size}
            for p in files
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / MANIFEST
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(manifest_path, arcname=MANIFEST)
            for path in files:
                tar.add(path, arcname=str(path.relative_to(data_dir)))
    return archive


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile(MANIFEST)
        if member is None:
            raise ValueError("not a Tara backup: no manifest")
        return json.loads(member.read().decode("utf-8"))


def verify(archive: Path) -> list[str]:
    """Check every file's digest against the manifest. Empty means good.

    Reads the whole archive rather than listing it: a truncated gzip, a
    corrupted member or a missing file are all things that only show up when
    the bytes are actually read, and finding out during a restore is finding
    out too late.
    """
    problems: list[str] = []
    try:
        manifest = read_manifest(archive)
    except (tarfile.TarError, ValueError, OSError) as exc:
        return [f"cannot read the archive: {type(exc).__name__}"]

    expected = manifest.get("files", {})
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                if not member.isfile() or member.name == MANIFEST:
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    problems.append(f"{member.name}: unreadable")
                    continue
                digest = hashlib.sha256(handle.read()).hexdigest()
                seen.add(member.name)
                want = expected.get(member.name)
                if want is None:
                    problems.append(f"{member.name}: not in the manifest")
                elif want["sha256"] != digest:
                    problems.append(f"{member.name}: digest mismatch")
    except tarfile.TarError as exc:
        problems.append(f"archive is damaged: {type(exc).__name__}")

    for missing in sorted(set(expected) - seen):
        problems.append(f"{missing}: in the manifest, absent from the archive")
    return problems


def restore(archive: Path, into: Path, *, force: bool = False) -> dict:
    """Replace `into` with the archive's contents. Verifies first.

    Refuses a non-empty target without `--force`, because the overwhelmingly
    common way to lose data with a restore tool is to run it at the wrong
    directory. The existing directory is moved aside rather than deleted — a
    restore that turns out to be the wrong archive should be undoable.
    """
    problems = verify(archive)
    if problems:
        raise ValueError("refusing to restore a damaged archive: " + "; ".join(problems[:3]))

    manifest = read_manifest(archive)
    into = into.resolve()
    displaced = None
    if into.exists() and any(into.iterdir()):
        if not force:
            raise ValueError(
                f"{into} is not empty. Re-run with --force; the existing "
                "directory will be moved aside, not deleted."
            )
        displaced = into.with_name(into.name + f".displaced-{int(time.time())}")
        shutil.move(str(into), str(displaced))

    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name == MANIFEST:
                continue
            # Path traversal defence: an archive is untrusted input, and
            # `../../etc/passwd` as a member name is the classic way in.
            target = (into / member.name).resolve()
            if not str(target).startswith(str(into) + "/"):
                raise ValueError(f"refusing a member outside the target: {member.name}")
            tar.extract(member, path=into, filter="data")

    return {"manifest": manifest, "displaced": str(displaced) if displaced else ""}


def _list(into: Path) -> int:
    archives = sorted(into.glob("tara-*.tar.gz"))
    if not archives:
        print(f"no backups in {into}")
        return 0
    print(f"{len(archives)} backup(s) in {into}")
    for path in archives:
        try:
            m = read_manifest(path)
            print(f"  {path.name}  {m['file_count']:>5} files  "
                  f"{m['total_bytes'] / 1_048_576:>7.1f} MB  {m['consistency']}  "
                  f"v{m.get('service_version', '?')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {path.name}  UNREADABLE ({type(exc).__name__})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", metavar="DIR", help="write a snapshot into DIR")
    group.add_argument("--list", metavar="DIR", help="list snapshots in DIR")
    group.add_argument("--verify", metavar="ARCHIVE", help="check an archive's digests")
    group.add_argument("--restore", metavar="ARCHIVE", help="restore an archive")
    ap.add_argument("--into", metavar="DIR", default="",
                    help="restore target (default: the configured data directory)")
    ap.add_argument("--quiesced", action="store_true",
                    help="assert the API is stopped, so the snapshot is point-in-time")
    ap.add_argument("--force", action="store_true",
                    help="restore over a non-empty directory (it is moved aside)")
    args = ap.parse_args()

    from services import config

    if args.list:
        return _list(Path(args.list))

    if args.create:
        archive = create(config.DATA_DIR, Path(args.create), quiesced=args.quiesced)
        manifest = read_manifest(archive)
        print(f"{archive}")
        print(f"  {manifest['file_count']} files · "
              f"{manifest['total_bytes'] / 1_048_576:.1f} MB · {manifest['consistency']}")
        if manifest["warning"]:
            print(f"  ⚠ {manifest['warning']}")
        # Verify what was just written. A backup nobody read is a backup nobody
        # knows they have.
        problems = verify(archive)
        if problems:
            print(f"  ✗ verification failed: {problems[0]}")
            return 1
        print("  ✓ verified")
        return 0

    if args.verify:
        problems = verify(Path(args.verify))
        if problems:
            print(f"{len(problems)} problem(s):")
            for p in problems[:20]:
                print(f"  ✗ {p}")
            return 1
        m = read_manifest(Path(args.verify))
        print(f"✓ {m['file_count']} files verified · {m['consistency']} · "
              f"taken {m['created_at_iso']}")
        return 0

    target = Path(args.into) if args.into else config.DATA_DIR
    try:
        outcome = restore(Path(args.restore), target, force=args.force)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1
    manifest = outcome["manifest"]
    print(f"✓ restored {manifest['file_count']} files into {target}")
    if outcome["displaced"]:
        print(f"  previous contents moved to {outcome['displaced']}")
    if manifest["warning"]:
        print(f"  ⚠ {manifest['warning']}")
    print("  ⚠ This snapshot may predate an erasure. Re-run "
          "`python -m tools.retention_cleanup` so deleted candidate data "
          "does not come back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
