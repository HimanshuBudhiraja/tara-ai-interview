"""Deployment configuration: the things that are only wrong in production.

Every test here is a mistake that works perfectly on a laptop. A wildcard CORS
origin, a cookie without `Secure`, a `localhost` baked into a bundle, a liveness
probe that calls a paid API — none of them fail locally, and all of them fail
after a deploy, usually in a way that looks like something else.
"""
from __future__ import annotations

import json
import re

import pytest

from services import config, observability
from tests.test_security import _interview, world  # noqa: F401

pytestmark = pytest.mark.usefixtures("data_dir")


@pytest.fixture()
def production(monkeypatch):
    """A correctly configured production deployment, to vary one thing at a time.

    Including revoking the development seed's `demo` invitation, which the test
    app's startup creates and which a real production boot does not — the check
    that flags it is `test_production_refuses_to_boot_carrying_the_demo_invitation`
    over in the security suite.
    """
    from services.data import invites

    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "COOKIES_SECURE", True)
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://hire.example.com"])
    # The suite-wide `_offline_by_default` fixture sets `LLM_PROVIDER=mock` so no
    # test can reach a provider. That is itself one of the things production
    # refuses, so the baseline here has to undo it — otherwise every test below
    # starts from a configuration that already has a problem in it, and each one
    # would pass whether or not its own check works.
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "test-key-not-a-real-one")
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "")
    monkeypatch.setattr(config, "LOG_LEVEL", "INFO")
    if invites.get(config.DEMO_TOKEN) is not None:
        invites.revoke(config.DEMO_TOKEN)
    return config


# =========================================================================== #
#  Liveness and readiness
# =========================================================================== #
def test_liveness_is_cheap_and_says_nothing_about_the_deployment(world):
    body = world.anonymous.get("/api/health")
    assert body.status_code == 200
    payload = body.json()
    assert payload["ok"] is True
    assert set(payload) == {"ok", "service", "version", "uptime_sec"}


def test_liveness_does_not_depend_on_the_provider_or_the_data_directory(world, monkeypatch):
    """The point of the split. A provider outage or a full disk must not make
    the process look dead — that turns an incident into a crash loop."""
    from services.ai import gateway as ai_gateway

    def explode(*a, **k):
        raise RuntimeError("provider is down")

    monkeypatch.setattr(ai_gateway.get_gateway(), "generate_structured", explode)
    monkeypatch.setattr(config, "DATA_DIR", config.DATA_DIR / "does-not-exist" / "nope")

    assert world.anonymous.get("/api/health").status_code == 200


def test_readiness_never_calls_the_provider(world, monkeypatch):
    """A probe every ten seconds that costs a token costs real money forever."""
    from services.ai import gateway as ai_gateway

    calls = {"n": 0}

    def counted(*a, **k):
        calls["n"] += 1
        raise RuntimeError("should not be called")

    monkeypatch.setattr(ai_gateway.get_gateway(), "generate_structured", counted)
    body = world.anonymous.get("/api/ready").json()
    assert calls["n"] == 0
    assert body["checks"]["ai_provider"]["checked"] is False


def test_readiness_fails_when_storage_is_not_writable(world, monkeypatch, tmp_path):
    unwritable = tmp_path / "readonly"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    monkeypatch.setattr(config, "DATA_DIR", unwritable / "data")

    response = world.anonymous.get("/api/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["storage"]["status"] == "unavailable"
    unwritable.chmod(0o700)


def test_readiness_fails_when_production_configuration_is_wrong(world, production, monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["*"])
    response = world.anonymous.get("/api/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["configuration"]["status"] == "unavailable"


def test_neither_probe_leaks_a_path_a_secret_or_a_configuration_value(world, production):
    """Both are reachable without a credential."""
    for path in ("/api/health", "/api/ready"):
        body = world.anonymous.get(path).text
        for leak in ("/Users/", "/app/data", "site-packages", "sk-or-", "scrypt$",
                     "hire.example.com", "password", "traceback"):
            assert leak not in body, (path, leak)


def test_readiness_reports_the_version_so_a_failing_probe_can_be_placed(world, monkeypatch):
    monkeypatch.setattr(config, "SERVICE_VERSION", "abc1234")
    assert world.anonymous.get("/api/ready").json()["version"] == "abc1234"
    assert world.anonymous.get("/api/health").json()["version"] == "abc1234"


# =========================================================================== #
#  Production configuration contract
# =========================================================================== #
@pytest.mark.parametrize("setting,value,expected", [
    ("ALLOWED_ORIGINS", ["*"], "ALLOWED_ORIGINS"),
    ("COOKIES_SECURE", False, "Secure"),
])
def test_production_refuses_a_development_default(production, monkeypatch, setting, value, expected):
    monkeypatch.setattr(config, setting, value)
    problems = config.require_production_configuration()
    assert any(expected in p for p in problems), problems


def test_a_correctly_configured_production_deployment_has_no_problems(world, production):
    assert config.require_production_configuration() == []


def test_development_is_not_held_to_the_production_contract(world):
    """Otherwise a fresh clone cannot boot, and the check gets disabled."""
    assert config.ENVIRONMENT == "development"
    assert config.require_production_configuration() == []


def test_every_required_production_variable_is_documented(production):
    """A setting the deployment must set, that `.env.example` never mentions, is
    a setting somebody will discover from an outage."""
    import pathlib

    example = (pathlib.Path(__file__).resolve().parents[1] / ".env.example").read_text()
    for name in ("TARA_ENV", "TARA_COOKIES_SECURE", "ALLOWED_ORIGINS",
                 "RECRUITER_AUTH_REQUIRED", "OPENROUTER_API_KEY",
                 "TARA_BOOTSTRAP_EMAIL", "CANDIDATE_DATA_RETENTION_DAYS",
                 "TARA_LOG_FORMAT", "TARA_VERSION"):
        assert name in example, name


# =========================================================================== #
#  Structured logging
# =========================================================================== #
def test_every_response_carries_a_request_id(world):
    for path in ("/api/health", "/api/ready", "/api/recruiter/interviews"):
        response = world.anonymous.get(path)
        assert response.headers.get("x-request-id"), path


def test_a_client_supplied_request_id_is_echoed_so_a_trace_spans_the_browser(world):
    response = world.client.get("/api/recruiter/interviews",
                                headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["x-request-id"] == "trace-abc-123"


def test_a_hostile_request_id_cannot_inject_into_a_log_line(world):
    hostile = 'x"\n{"level":"info","message":"forged"}\n' + "y" * 500
    response = world.anonymous.get("/api/health", headers={"X-Request-ID": hostile})
    echoed = response.headers["x-request-id"]
    assert "\n" not in echoed and '"' not in echoed
    assert len(echoed) <= 64


def _request_lines(caplog) -> list[dict]:
    """The structured fields of every `request` line.

    Read from the logger rather than from stdout: the handler binds `sys.stdout`
    when it is first configured, which is before `capsys` replaces it, so
    capturing the stream sees nothing. `test_the_json_formatter_emits_one_object_per_line`
    covers the serialisation separately.
    """
    return [r.fields for r in caplog.records
            if r.name == "tara" and r.getMessage() == "request"]


def test_a_log_line_names_the_route_template_not_the_resource_id(world, caplog):
    with caplog.at_level("INFO", logger="tara"):
        world.client.get("/api/recruiter/interviews/iv_acme")
    lines = _request_lines(caplog)
    assert lines, "no structured request line was emitted"
    row = lines[-1]
    assert row["route"] == "/api/recruiter/interviews/{interview_id}"
    assert "iv_acme" not in row["route"]
    assert row["status"] == 200
    assert isinstance(row["duration_ms"], float)


def test_a_failed_request_is_logged_with_a_stable_error_category(world, caplog):
    with caplog.at_level("INFO", logger="tara"):
        world.anonymous.get("/api/recruiter/interviews")
        world.client.get("/api/recruiter/interviews/iv_rival")
    categories = [row.get("error_category") for row in _request_lines(caplog)]
    assert observability.AUTHENTICATION_ERROR in categories
    assert observability.NOT_FOUND in categories


def test_the_json_formatter_emits_one_object_per_line(world):
    """Whatever a log shipper receives has to parse, and has to not split."""
    import logging

    record = logging.LogRecord(
        "tara", logging.INFO, __file__, 1, "request", (), None)
    record.fields = {"route": "/api/health", "status": 200, "duration_ms": 1.5}
    record.request_id = "req_abc"
    line = observability.JsonFormatter().format(record)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["message"] == "request"
    assert parsed["request_id"] == "req_abc"
    assert parsed["route"] == "/api/health"
    assert parsed["service"] == "tara-api"
    assert parsed["environment"] == config.ENVIRONMENT


def test_the_log_never_carries_a_credential_or_candidate_content(world, caplog):
    from tests.test_security import _invite, _publish, _sit

    with caplog.at_level("INFO", logger="tara"):
        _publish(world.client, "iv_acme")
        token = _invite(world.client, "iv_acme", "Zephyrine Quennell")["token"]
        _sit(world.client, token)
        world.anonymous.post("/api/auth/login", json={
            "email": world.admin.email, "password": "a-wrong-password"})

    # Serialise exactly as the shipper would see it, then search that.
    formatter = observability.JsonFormatter()
    printed = "\n".join(formatter.format(r) for r in caplog.records if r.name == "tara")
    assert printed, "nothing was logged, so this proves nothing"
    for secret in (token, "a-wrong-password", "Zephyrine",
                   "idempotency key on the capture call",
                   world.client.cookies.get("tara_session") or "-impossible-"):
        assert secret not in printed, secret[:24]
    # The query allow-list held: `?token=` is how a candidate authenticates and
    # never reaches a log line.
    assert '"token"' not in printed


def test_the_error_category_vocabulary_is_stable_and_covers_every_status(world):
    for status, expected in (
        (200, ""), (400, observability.VALIDATION_ERROR),
        (401, observability.AUTHENTICATION_ERROR),
        (403, observability.AUTHORIZATION_ERROR),
        (404, observability.NOT_FOUND), (409, observability.CONFLICT),
        (410, observability.NOT_FOUND), (422, observability.VALIDATION_ERROR),
        (429, observability.RATE_LIMITED), (500, observability.INTERNAL_ERROR),
        (503, observability.DEPENDENCY_ERROR), (599, observability.INTERNAL_ERROR),
    ):
        assert observability.error_category(status) == expected, status


# =========================================================================== #
#  The built bundles
# =========================================================================== #
def _bundle_files():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    return [p for directory in ("apps/candidate/dist", "apps/recruiter/dist")
            for p in (root / directory).rglob("*") if p.is_file()]


@pytest.mark.skipif(not _bundle_files(), reason="run `npm run build` first")
def test_no_built_bundle_hardcodes_a_development_host():
    """A `localhost` in a shipped bundle is a deployment that works for whoever
    built it and nobody else."""
    offenders = []
    for path in _bundle_files():
        if path.suffix not in (".js", ".css", ".html", ".map"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in (r"https?://localhost", r"https?://127\.0\.0\.1",
                        r"ws://localhost", r":5173", r":5174"):
            if re.search(pattern, text):
                offenders.append(f"{path.name}: {pattern}")
    assert offenders == [], offenders


@pytest.mark.skipif(not _bundle_files(), reason="run `npm run build` first")
def test_no_built_bundle_carries_a_credential():
    from tools import secrets_audit

    findings = [f for f in secrets_audit.audit() if "bundle" in f or "frontend" in f]
    assert findings == [], findings


# =========================================================================== #
#  Cost guardrails
# =========================================================================== #
def test_an_interview_cannot_make_unbounded_provider_calls(world):
    """Every loop that can call a model is bounded by configuration, not by the
    candidate's behaviour. One malformed request must not be able to spend."""
    assert config.QUESTION_BUDGET > 0
    assert config.MAX_PROBES_PER_ITEM > 0
    assert config.MAX_REASKS_PER_ITEM > 0
    assert config.MAX_CLARIFIES_PER_ITEM > 0
    assert config.LLM_TIMEOUT_SEC > 0

    # The ceiling is the product of the bounds, and it is a small number.
    worst_case = config.QUESTION_BUDGET * (
        1 + config.MAX_PROBES_PER_ITEM + config.MAX_REASKS_PER_ITEM
        + config.MAX_CLARIFIES_PER_ITEM)
    assert worst_case <= 100, worst_case


def test_the_expensive_endpoints_are_rate_limited(world):
    from services.security import ratelimit

    for bucket in ("evaluation", "generation", "turn"):
        limit = ratelimit.LIMITS[bucket]
        assert limit.requests > 0 and limit.window_sec > 0


def test_a_repeated_evaluation_request_does_not_run_a_second_evaluation(world):
    """Duplicate-evaluation prevention is a cost guardrail as much as a
    correctness one: each run is a full pass over the transcript."""
    from services.data import evaluations
    from tests.test_security import _invite, _publish, _sit

    _publish(world.client, "iv_acme")
    session_id = _sit(world.client, _invite(world.client, "iv_acme")["token"])

    first = world.client.post(f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    assert first.status_code == 200
    before = len(evaluations.list_for_session(session_id))

    for _ in range(3):
        world.client.post(f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    current = [r for r in evaluations.list_for_session(session_id) if r.is_current]
    assert len(current) == 1, "more than one current evaluation"
    assert len(evaluations.list_for_session(session_id)) == before


# =========================================================================== #
#  The worker
# =========================================================================== #
def test_the_worker_drains_the_queue_and_reports_counts(world):
    from tests.test_security import _invite, _publish, _sit
    from tools import evaluation_worker

    _publish(world.client, "iv_acme")
    _sit(world.client, _invite(world.client, "iv_acme")["token"])

    counts = evaluation_worker.drain(limit=10, max_attempts=3)
    assert set(counts) == {"completed", "failed", "skipped"}


def test_the_worker_stops_re_running_a_record_that_keeps_failing(world, monkeypatch):
    """Bounded on purpose: an evaluation that has failed three times is
    something to investigate, not something to keep paying a provider for."""
    from services.data import evaluations
    from services.evaluation import jobs
    from tools import evaluation_worker

    record = evaluations.create(
        session_id="sess_x", interview_id="iv_acme", interview_version=1,
        engine_version="deep_evidence_v2", snapshot_checksum="abc", snapshot={},
    )
    record.attempt = 5
    evaluations.save(record)

    ran = {"n": 0}
    monkeypatch.setattr(jobs, "run", lambda r, **k: ran.__setitem__("n", ran["n"] + 1))
    counts = evaluation_worker.drain(limit=10, max_attempts=3)
    assert ran["n"] == 0
    assert counts["skipped"] == 1


def test_the_worker_survives_one_bad_record(world, monkeypatch):
    from services.data import evaluations
    from services.evaluation import jobs
    from tools import evaluation_worker

    for n in range(2):
        evaluations.create(
            session_id=f"sess_{n}", interview_id="iv_acme", interview_version=1,
            engine_version="deep_evidence_v2", snapshot_checksum=str(n), snapshot={},
        )

    seen = []

    def flaky(record, **kwargs):
        seen.append(record.session_id)
        if record.session_id == "sess_0":
            raise RuntimeError("boom")
        record.status = "complete"
        return record

    monkeypatch.setattr(jobs, "run", flaky)
    counts = evaluation_worker.drain(limit=10, max_attempts=3)
    assert len(seen) == 2, "one bad record must not stop the pass"
    assert counts["failed"] == 1 and counts["completed"] == 1


# =========================================================================== #
#  The production configuration contract, check by check
#
#  One test per check, each varying exactly one setting away from a valid
#  production configuration. A check that fires for the wrong reason is a check
#  nobody trusts the second time it fires.
# =========================================================================== #
@pytest.mark.parametrize("setting,bad_value,expected", [
    ("ALLOWED_ORIGINS", ["*"], "ALLOWED_ORIGINS"),
    ("COOKIES_SECURE", False, "Secure"),
    ("OPENROUTER_API_KEY", "", "provider API key"),
    ("SCORING_MODEL", "", "Model slots"),
    ("OPENROUTER_BASE_URL", "http://insecure.example", "not HTTPS"),
    ("CANDIDATE_DATA_RETENTION_DAYS", 0, "positive number of days"),
    ("AUDIT_RETENTION_DAYS", 5, "shorter than the candidate retention period"),
    ("LLM_PROVIDER", "mock", "deterministic stub"),
    ("BOOTSTRAP_PASSWORD", "still-in-the-env", "BOOTSTRAP_PASSWORD"),
    ("LOG_LEVEL", "DEBUG", "DEBUG"),
])
def test_each_production_check_fires_on_its_own_setting(
    world, production, monkeypatch, setting, bad_value, expected
):
    assert config.require_production_configuration() == [], "the baseline must be clean"
    monkeypatch.setattr(config, setting, bad_value)
    problems = config.require_production_configuration()
    assert any(expected in p for p in problems), (setting, problems)


@pytest.mark.parametrize("path", [
    "/tmp/tara", "/private/tmp/tara", "/var/tmp/tara", "/dev/shm/tara", "/run/tara",
])
def test_an_ephemeral_data_directory_is_refused(world, production, monkeypatch, path):
    """Core state is file-backed, so the data directory IS the database. A
    container writing it to `/tmp` loses every interview on restart, and finds
    out at the worst possible moment."""
    import pathlib

    monkeypatch.setattr(config, "DATA_DIR", pathlib.Path(path))
    problems = config.require_production_configuration()
    assert any("ephemeral" in p for p in problems), (path, problems)


def test_a_persistent_data_directory_is_not_refused(world, production, monkeypatch):
    import pathlib

    monkeypatch.setattr(config, "DATA_DIR", pathlib.Path("/srv/tara/data"))
    assert not [p for p in config.require_production_configuration() if "ephemeral" in p]


def test_configuration_validation_never_calls_the_provider(world, production, monkeypatch):
    """It runs at boot and on every readiness probe. A check that costs a
    provider call costs money forever, and fails for reasons that are not
    configuration problems — an outage, or an exhausted budget."""
    from services.ai import gateway as ai_gateway

    calls = {"n": 0}

    def counted(*a, **k):
        calls["n"] += 1
        raise AssertionError("configuration validation must not call the provider")

    monkeypatch.setattr(ai_gateway.get_gateway(), "generate_structured", counted)
    monkeypatch.setattr(ai_gateway.get_gateway(), "complete", counted, raising=False)
    config.require_production_configuration()
    assert calls["n"] == 0


def test_no_production_problem_names_a_secret_value(world, production, monkeypatch):
    """The list is printed at boot and counted in readiness. It must be safe to
    paste into a ticket."""
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-or-v1-super-secret-value")
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "hunter2-the-real-password")
    monkeypatch.setattr(config, "LOG_LEVEL", "DEBUG")
    blob = " ".join(config.require_production_configuration())
    assert "sk-or-v1-super-secret-value" not in blob
    assert "hunter2-the-real-password" not in blob


# =========================================================================== #
#  Backup and restore
#
#  Core state is a directory of JSON files, so a snapshot of that directory is
#  the only durability this deployment has — there is no replica and no
#  write-ahead log. These tests exercise the whole round trip against real
#  records, because a backup tool that has only ever been run on an empty
#  directory is a backup tool nobody should trust.
# =========================================================================== #
def _populate(world) -> tuple[str, str]:
    from tests.test_security import _invite, _publish, _sit

    _publish(world.client, "iv_acme")
    token = _invite(world.client, "iv_acme", "Zephyrine Quennell")["token"]
    session_id = _sit(world.client, token)
    world.client.post(f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    return token, session_id


def test_a_snapshot_round_trips_every_record(world, data_dir, tmp_path):
    from services.data import evaluations, interviews, invites
    from services.data import sessions as store
    from tools import backup

    token, session_id = _populate(world)
    before = {
        "interviews": len(interviews.list_all()),
        "invites": len(invites.list_all()),
        "sessions": len(store.list_ids()),
        "evaluations": len(evaluations.list_for_session(session_id)),
    }

    archive = backup.create(data_dir, tmp_path / "backups", quiesced=True)
    assert backup.verify(archive) == []

    target = tmp_path / "restored"
    backup.restore(archive, target)

    # Read the restored copy through the real stores, pointed at it.
    import json as _json

    assert len(_json.loads((target / "interviews.json").read_text())) == before["interviews"]
    assert len(_json.loads((target / "invites.json").read_text())) == before["invites"]
    assert len(list((target / "sessions").glob("*.json"))) == before["sessions"]
    assert len(list((target / "evaluations").glob("*.json"))) >= before["evaluations"]

    # And the candidate's actual words survived, which is the point of a backup.
    restored_session = (target / "sessions" / f"{session_id}.json").read_text()
    assert "Zephyrine Quennell" in restored_session
    assert "idempotency key on the capture call" in restored_session


def test_the_manifest_says_whether_the_snapshot_was_consistent(world, data_dir, tmp_path):
    """Writes are atomic per file, not across files. A live snapshot is worth
    taking and is not a guaranteed-consistent one, and the archive has to say
    which it is rather than leaving a restorer to assume."""
    from tools import backup

    live = backup.create(data_dir, tmp_path / "a", quiesced=False)
    stopped = backup.create(data_dir, tmp_path / "b", quiesced=True)

    assert backup.read_manifest(live)["consistency"] == "live"
    assert backup.read_manifest(live)["warning"]
    assert backup.read_manifest(stopped)["consistency"] == "quiesced"
    assert backup.read_manifest(stopped)["warning"] == ""


def test_verification_reads_the_bytes_rather_than_listing_the_archive(world, data_dir, tmp_path):
    """A truncated gzip or a flipped bit only shows up when the content is
    actually read — and finding out during a restore is finding out too late."""
    from tools import backup

    archive = backup.create(data_dir, tmp_path / "backups", quiesced=True)
    assert backup.verify(archive) == []

    damaged = tmp_path / "damaged.tar.gz"
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    damaged.write_bytes(bytes(raw))

    problems = backup.verify(damaged)
    assert problems, "a corrupted archive verified clean"


def test_a_damaged_archive_is_never_restored(world, data_dir, tmp_path):
    from tools import backup

    archive = backup.create(data_dir, tmp_path / "backups", quiesced=True)
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    archive.write_bytes(bytes(raw))

    target = tmp_path / "restored"
    with pytest.raises(ValueError, match="damaged"):
        backup.restore(archive, target)
    assert not target.exists() or not any(target.iterdir())


def test_a_restore_will_not_silently_overwrite_a_populated_directory(world, data_dir, tmp_path):
    """The common way to lose data with a restore tool is running it at the
    wrong directory."""
    from tools import backup

    archive = backup.create(data_dir, tmp_path / "backups", quiesced=True)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "important.json").write_text('{"keep": true}')

    with pytest.raises(ValueError, match="not empty"):
        backup.restore(archive, occupied)
    assert (occupied / "important.json").exists()

    # With --force the previous contents are moved aside, not deleted.
    outcome = backup.restore(archive, occupied, force=True)
    assert outcome["displaced"]
    from pathlib import Path

    assert (Path(outcome["displaced"]) / "important.json").exists()


def test_a_snapshot_excludes_transient_files(world, data_dir, tmp_path):
    from tools import backup

    (data_dir / "half-written.tmp").write_text("{")
    (data_dir / ".config-check").write_text("ok")

    archive = backup.create(data_dir, tmp_path / "backups", quiesced=True)
    names = set(backup.read_manifest(archive)["files"])
    assert "half-written.tmp" not in names
    assert ".config-check" not in names


def test_an_archive_cannot_write_outside_the_restore_target(world, data_dir, tmp_path):
    """An archive is untrusted input, and `../../etc/passwd` as a member name is
    the classic way out of the target directory."""
    import tarfile

    from tools import backup

    hostile = tmp_path / "hostile.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("owned")
    manifest = tmp_path / backup.MANIFEST
    manifest.write_text(json.dumps({
        "created_at": 0, "created_at_iso": "x", "consistency": "quiesced",
        "warning": "", "file_count": 1, "total_bytes": 5,
        "files": {"../escaped": {"sha256": backup._digest(payload), "bytes": 5}},
    }))
    with tarfile.open(hostile, "w:gz") as tar:
        tar.add(manifest, arcname=backup.MANIFEST)
        tar.add(payload, arcname="../escaped")

    with pytest.raises(ValueError, match="outside the target"):
        backup.restore(hostile, tmp_path / "target")
    assert not (tmp_path / "escaped").exists()


def test_a_restored_snapshot_can_still_be_swept_for_retention(world, data_dir, tmp_path):
    """The documented follow-up to any restore. A snapshot taken before an
    erasure contains the erased data, so the sweep has to work against a
    restored directory — otherwise the guarantee quietly depends on nobody ever
    restoring anything."""
    from services import config
    from services.data import erasure, retention
    from tools import backup

    token, session_id = _populate(world)
    archive = backup.create(data_dir, tmp_path / "backups", quiesced=True)

    # Erase, then restore the pre-erasure snapshot: the candidate is back.
    assert erasure.erase(token, actor="usr_admin").ok
    assert erasure.verify(token) == []

    backup.restore(archive, data_dir, force=True)
    assert erasure.verify(token) != [], "the restore should have brought the data back"

    # Which is exactly why the sweep has to be re-runnable afterwards.
    from services.data import invites

    invite = invites.get(token)
    invite.created_at -= (config.CANDIDATE_DATA_RETENTION_DAYS + 1) * 86400
    invite.completed_at = invite.created_at
    invites.update(invite)
    assert token in [c.token for c in retention.eligible()]
    assert erasure.sweep()["erased"] >= 1
    assert erasure.verify(token) == []


# =========================================================================== #
#  Provider failure classification
#
#  Found by the staging acceptance harness: every failure inside evidence
#  extraction or skill assessment was recorded as `error_kind: "model"`,
#  including an exhausted provider budget. An operator reading a wall of
#  "model" failures investigates prompts and calibration; the actual cause was
#  that the account was out of credit.
# =========================================================================== #
@pytest.mark.parametrize("exception,expected", [
    ("ProviderUnavailable:403: Key limit exceeded (total limit)", "provider"),
    ("RateLimited:429 after every retry", "provider"),
    ("AIError:the request timed out after 12s", "provider"),
    ("AIError:connection reset by peer", "provider"),
    ("AIError:401 unauthorized", "provider"),
    ("AIError:the model returned an empty object", "model"),
    ("ValueError:schema mismatch on field skill_id", "model"),
    ("KeyError:looking_for", "model"),
])
def test_a_provider_failure_is_not_recorded_as_a_model_failure(exception, expected):
    from services.ai.gateway import AIError, ProviderUnavailable, RateLimited
    from services.evaluation import jobs

    kinds = {"ProviderUnavailable": ProviderUnavailable, "RateLimited": RateLimited,
             "AIError": AIError, "ValueError": ValueError, "KeyError": KeyError}
    name, _, message = exception.partition(":")
    assert jobs._kind_for(kinds[name](message)) == expected  # noqa: SLF001


def test_the_two_failure_kinds_lead_somewhere_different(world):
    """They are not cosmetic labels: one is a calibration question and the
    other is a billing question, and the runbook routes them separately."""
    from services.data import evaluations

    assert evaluations.PROVIDER_FAILURE != evaluations.MODEL_FAILURE
    assert evaluations.PROVIDER_FAILURE == "provider"


def test_an_exhausted_budget_fails_the_evaluation_rather_than_fabricating_one(
    world, monkeypatch
):
    """The behaviour that matters most when a provider is down: a failed
    evaluation, visibly failed, never a result nobody produced."""
    from services.ai.gateway import ProviderUnavailable
    from services.data import evaluations
    from services.evaluation import jobs
    from services.evaluation import stub as eval_stub
    from tests.test_security import _invite, _publish, _sit

    _publish(world.client, "iv_acme")
    session_id = _sit(world.client, _invite(world.client, "iv_acme")["token"])

    # The `world` fixture stubs the evaluator so the suite never calls a
    # provider. This test is about what the REAL path does when the provider
    # refuses, so the stub comes off and the refusal is injected where the
    # gateway would raise it.
    monkeypatch.delenv(eval_stub.STUB_ENV, raising=False)

    def exhausted(*a, **k):
        raise ProviderUnavailable(
            '403: {"error":{"message":"Key limit exceeded (total limit)","code":403}}')

    # Injected at the extractor the job actually builds, not at a module
    # attribute that happens to share the name. Getting this wrong the first
    # time is how "no provider configured" was found being labelled a model
    # failure — the real path ran, and reported the wrong kind.
    monkeypatch.setattr(jobs, "_extractor", lambda: exhausted)

    response = world.client.post(
        f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_kind"] == evaluations.PROVIDER_FAILURE
    assert body["failed_stage"] == "evidence_extraction"

    # No result is readable, and none is invented.
    result = world.client.get(
        f"/api/recruiter/sessions/{session_id}/evaluation/result")
    assert result.status_code == 409
    record = evaluations.list_for_session(session_id)[0]
    assert record.result == {}
    assert record.evidence == []


def test_an_unconfigured_gateway_is_a_provider_failure_not_a_model_failure(world, monkeypatch):
    """The offline case, which is what a deployment with no key actually hits.

    Found while testing the exhausted-budget case: an evaluation run against a
    gateway with no provider configured recorded `error_kind: "model"`, sending
    an operator to look at prompts when the answer was a missing environment
    variable.
    """
    from services.data import evaluations
    from services.evaluation import stub as eval_stub
    from tests.test_security import _invite, _publish, _sit

    _publish(world.client, "iv_acme")
    session_id = _sit(world.client, _invite(world.client, "iv_acme")["token"])
    monkeypatch.delenv(eval_stub.STUB_ENV, raising=False)

    # The suite's autouse fixture already puts the gateway offline, so this is
    # simply the real path with no provider.
    response = world.client.post(
        f"/api/recruiter/sessions/{session_id}/evaluation", json={})
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_kind"] == evaluations.PROVIDER_FAILURE, body.get("error")
    assert "no provider configured" in body["error"]
