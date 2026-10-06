"""The opt-in boot-time password reset (TARA_RESET_PASSWORD).

TARA_BOOTSTRAP_PASSWORD alone is read once, for a service with no users. The
reset is the explicit way to give an existing account a new password.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services import config
from services.data import accounts
from services.security import ratelimit

NEW_PASSWORD = "a-brand-new-password-42"


def _boot(monkeypatch, tenant, *, reset: bool, password: str = NEW_PASSWORD):
    from services.api.app import app

    monkeypatch.setattr(config, "BOOTSTRAP_EMAIL", tenant.email)
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", password)
    monkeypatch.setattr(config, "RESET_PASSWORD", reset)
    ratelimit.reset()
    client = TestClient(app)
    client.__enter__()  # runs the startup hook
    return client


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_the_password_variable_alone_never_changes_an_existing_account(
        data_dir, tenant, monkeypatch):
    client = _boot(monkeypatch, tenant, reset=False)
    assert _login(client, tenant.email, NEW_PASSWORD).status_code == 401
    assert _login(client, tenant.email, tenant.password).status_code == 200


def test_reset_gives_an_existing_account_the_new_password(data_dir, tenant, monkeypatch):
    client = _boot(monkeypatch, tenant, reset=True)
    assert _login(client, tenant.email, tenant.password).status_code == 401
    assert _login(client, tenant.email, NEW_PASSWORD).status_code == 200


def test_reset_revokes_every_login_the_account_held(data_dir, tenant, monkeypatch):
    old = accounts.start_session(tenant.user)
    _boot(monkeypatch, tenant, reset=True)
    assert accounts.touch_session(old.token) is None


def test_reset_for_an_unknown_email_changes_nothing(data_dir, tenant, monkeypatch, capsys):
    from services.api.app import app

    monkeypatch.setattr(config, "BOOTSTRAP_EMAIL", "nobody@example.test")
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", NEW_PASSWORD)
    monkeypatch.setattr(config, "RESET_PASSWORD", True)
    ratelimit.reset()
    client = TestClient(app)
    client.__enter__()
    assert "no account has that email" in capsys.readouterr().out
    assert _login(client, tenant.email, tenant.password).status_code == 200


def test_a_too_short_reset_password_is_refused_and_the_old_one_keeps_working(
        data_dir, tenant, monkeypatch):
    client = _boot(monkeypatch, tenant, reset=True, password="short")
    assert _login(client, tenant.email, tenant.password).status_code == 200


def test_set_password_validates_and_returns_none_for_a_missing_user(data_dir, tenant):
    assert accounts.set_password("usr_missing", NEW_PASSWORD) is None
    with pytest.raises(accounts.AccountError):
        accounts.set_password(tenant.user.user_id, "short")


def test_the_reset_flag_is_reported_as_a_production_problem(monkeypatch):
    monkeypatch.setattr(config, "RESET_PASSWORD", True)
    assert any("TARA_RESET_PASSWORD" in p for p in config._debug_problems())
