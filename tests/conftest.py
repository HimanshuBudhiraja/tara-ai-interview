"""Test setup.

Every test that touches storage gets its own temp data directory. The
repositories read `services.config.DATA_DIR` at call time, so pointing it
somewhere else is enough — no test writes into the real `data/`, and a failing
test cannot corrupt a session someone is mid-interview on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Redirect all file-backed storage at a throwaway directory."""
    from services import config
    from services.data import interviews, invites, jobs, versions

    d = tmp_path / "data"
    (d / "sessions").mkdir(parents=True)
    (d / "audit").mkdir(parents=True)

    monkeypatch.setattr(config, "DATA_DIR", d)
    monkeypatch.setattr(config, "SESSION_DIR", d / "sessions")
    monkeypatch.setattr(config, "AUDIT_DIR", d / "audit")

    # These four bind their path at import time, so redirecting DATA_DIR alone
    # is not enough — a missed one means the suite reads and WRITES the real
    # `data/`, which is both a corruption risk and a source of tests that pass
    # on yesterday's rows. Kept in one place so a new store is caught here.
    monkeypatch.setattr(interviews, "_PATH", d / "interviews.json")
    monkeypatch.setattr(versions, "_PATH", d / "interview_versions.json")
    monkeypatch.setattr(invites, "_PATH", d / "invites.json")
    monkeypatch.setattr(jobs, "_PATH", d / "jobs.json")

    from services.data import sessions as session_store

    monkeypatch.setattr(
        session_store, "_STORE", session_store.FileSessionStore(d / "sessions")
    )
    return d


@pytest.fixture()
def pool():
    from services.orchestrator.pool import get_pool

    return get_pool()


# --------------------------------------------------------------------------- #
#  Identity for the tests that talk to the API
#
#  Recruiter routes now require a signed-in principal and an organization that
#  owns the resource. Tests get a real organization and a real user and sign in
#  for real — the alternative, a bypass switch in production code, would mean the
#  suite proved something the deployment does not do.
# --------------------------------------------------------------------------- #
#: Long enough for `accounts.hash_password`, and obviously not a real secret.
TEST_PASSWORD = "test-password-1234"


@pytest.fixture()
def tenant(data_dir, monkeypatch):
    """An organization, an admin in it, and a second organization to be refused by.

    Also defaults `organization_id` on interviews saved without one. Tests
    construct `InterviewConfig` objects directly all over the suite, and an
    interview with no owner is unreachable by design; stamping the fixture's
    organization keeps those tests testing what they were written to test. The
    production paths always set it from the principal, and
    `tests/test_security.py` asserts that separately.
    """
    from types import SimpleNamespace

    from services.data import accounts, interviews

    org = accounts.create_organization("Acme Hiring", organization_id="org_acme")
    other = accounts.create_organization("Rival Corp", organization_id="org_rival")
    user = accounts.create_user(
        org.organization_id, "recruiter@acme.test", TEST_PASSWORD, role=accounts.ADMIN
    )

    real_save = interviews.save

    def save_with_owner(cfg):
        if not cfg.organization_id:
            cfg.organization_id = org.organization_id
        return real_save(cfg)

    monkeypatch.setattr(interviews, "save", save_with_owner)

    return SimpleNamespace(
        org=org, organization_id=org.organization_id,
        other=other, other_organization_id=other.organization_id,
        user=user, email=user.email, password=TEST_PASSWORD,
    )


def sign_in(client, tenant, email: str = "", password: str = "") -> None:
    """Log a TestClient in. The cookie it gets back is what every later call uses."""
    response = client.post("/api/auth/login", json={
        "email": email or tenant.email,
        "password": password or tenant.password,
    })
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def _offline_by_default(request, monkeypatch):
    """No non-live test may reach a provider, whatever ran before it.

    Individual fixtures used to do this themselves with
    `monkeypatch.setattr(get_gateway(), "live", False)`, which works only while
    the gateway singleton they patch is the one still in use. It is not always:
    `tests/test_evaluation_api.py` sets `ai_gateway._GATEWAY = None` to force a
    rebuild, and after that test the object other fixtures had patched is no
    longer the object the runtime holds. The symptom was a three-word answer
    being classified `substantive` instead of `thin` — but only when a
    particular file ran earlier in the session, which is the worst kind of test
    failure to debug.

    So the switch is made here, once, for every test: `TARA_LLM=mock` makes
    `config.llm_is_live()` False, and clearing the singleton means the next
    `get_gateway()` builds an offline one from that. Tests marked `live` opt out
    — they exist precisely to call the provider.
    """
    if request.node.get_closest_marker("live"):
        yield
        return

    from services import config
    from services.ai import gateway as ai_gateway

    monkeypatch.setattr(config, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(ai_gateway, "_GATEWAY", None)
    yield
    # Leave no gateway behind either, so the next test builds its own rather
    # than inheriting one built under this test's configuration.
    monkeypatch.setattr(ai_gateway, "_GATEWAY", None)


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Counters must not leak between tests.

    The login limit is ten a minute per client address, and every test in a file
    shares one address — without this, the eleventh test in a file would fail on
    a limiter rather than on its subject.
    """
    from services.security import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()
