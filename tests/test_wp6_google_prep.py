"""WP6 Google prep: OAuth connect flow, config-only shape check, pilot template.

Everything runs without real credentials: the token endpoint is an
injected fake transport, the client id/secret are marker env values, and
the pilot template is verified as a *shape* — the owner-supplied state
(env values, stored token) must come back as pending steps, not failures.
"""
from __future__ import annotations

import json
import stat
import sys
import urllib.parse
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shared" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import connection_health  # noqa: E402
import google_oauth  # noqa: E402
from google_oauth import (  # noqa: E402
    DEFAULT_REDIRECT_URI,
    REQUIRED_SCOPES,
    GoogleOAuthConfigError,
)

PILOT_TEMPLATE = ROOT / "shared" / "config" / "connections.pilot.yaml.example"

ENV = {"MWP_GOOGLE_OAUTH_CLIENT_ID": "client-id-marker",
       "MWP_GOOGLE_OAUTH_CLIENT_SECRET": "client-secret-marker"}


def _config(tmp_path, **oauth_overrides):
    oauth = {
        "client_id_env": "MWP_GOOGLE_OAUTH_CLIENT_ID",
        "client_secret_env": "MWP_GOOGLE_OAUTH_CLIENT_SECRET",
        "redirect_uri": DEFAULT_REDIRECT_URI,
        "token_path": str(tmp_path / "owner" / "google-token.json"),
        "scopes": list(REQUIRED_SCOPES),
    }
    oauth.update(oauth_overrides)
    return {
        "integrations": {"workspace": {"provider": "google_api", "mode": "direct"}},
        "google": {"account_email": "owner@acme.example",
                   "auth": {"mode": "oauth_user", "oauth": oauth}},
        "connections": {"allow_live_probe": False},
    }


def _ok_transport(payload=None):
    calls = []

    def post(url, data):
        calls.append((url, dict(data)))
        return 200, payload or {"access_token": "at-1", "refresh_token": "rt-1",
                                 "token_type": "Bearer", "expires_in": 3600,
                                 "scope": " ".join(REQUIRED_SCOPES)}

    post.calls = calls
    return post


# ── Settings loading ──────────────────────────────────────────────────


def test_missing_env_vars_raise_a_clear_named_error(tmp_path):
    with pytest.raises(GoogleOAuthConfigError) as excinfo:
        google_oauth.load_oauth_settings(_config(tmp_path), environ={})
    message = str(excinfo.value)
    assert "MWP_GOOGLE_OAUTH_CLIENT_ID" in message
    assert "MWP_GOOGLE_OAUTH_CLIENT_SECRET" in message


def test_settings_read_env_by_configured_names(tmp_path):
    settings = google_oauth.load_oauth_settings(_config(tmp_path), environ=ENV)
    assert settings.client_id == "client-id-marker"
    assert settings.redirect_uri == DEFAULT_REDIRECT_URI
    assert settings.scopes == REQUIRED_SCOPES
    assert "client-secret-marker" not in repr(settings)


# ── Authorisation URL ─────────────────────────────────────────────────


def test_authorization_url_carries_the_registered_redirect_and_scopes(tmp_path):
    settings = google_oauth.load_oauth_settings(_config(tmp_path), environ=ENV)
    url = google_oauth.build_authorization_url(settings, state="state-123")
    parsed = urllib.parse.urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == google_oauth.AUTH_URL
    params = urllib.parse.parse_qs(parsed.query)
    assert params["client_id"] == ["client-id-marker"]
    assert params["redirect_uri"] == [DEFAULT_REDIRECT_URI]
    assert params["response_type"] == ["code"]
    assert params["access_type"] == ["offline"]
    assert params["state"] == ["state-123"]
    assert set(params["scope"][0].split()) == set(REQUIRED_SCOPES)


# ── Code exchange / refresh ───────────────────────────────────────────


def test_exchange_posts_the_code_and_builds_a_token_record(tmp_path):
    settings = google_oauth.load_oauth_settings(_config(tmp_path), environ=ENV)
    transport = _ok_transport()
    record = google_oauth.exchange_code(settings, "code-xyz", http_post=transport)
    url, data = transport.calls[0]
    assert url == google_oauth.TOKEN_URL
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "code-xyz"
    assert data["redirect_uri"] == DEFAULT_REDIRECT_URI
    assert record["access_token"] == "at-1"
    assert record["refresh_token"] == "rt-1"
    assert record["expires_at"] > record["obtained_at"]


def test_exchange_failure_never_echoes_the_secret(tmp_path):
    settings = google_oauth.load_oauth_settings(_config(tmp_path), environ=ENV)

    def failing(url, data):
        return 400, {"error": "invalid_grant"}

    with pytest.raises(GoogleOAuthConfigError) as excinfo:
        google_oauth.exchange_code(settings, "bad-code", http_post=failing)
    assert "invalid_grant" in str(excinfo.value)
    assert "client-secret-marker" not in str(excinfo.value)


def test_refresh_keeps_the_stored_refresh_token(tmp_path):
    settings = google_oauth.load_oauth_settings(_config(tmp_path), environ=ENV)
    transport = _ok_transport({"access_token": "at-2", "expires_in": 3600})
    record = google_oauth.refresh_token_record(
        settings, {"access_token": "at-1", "refresh_token": "rt-keep"}, http_post=transport)
    assert record["access_token"] == "at-2"
    assert record["refresh_token"] == "rt-keep"
    assert transport.calls[0][1]["grant_type"] == "refresh_token"


# ── Token storage ─────────────────────────────────────────────────────


def test_token_storage_round_trip_with_owner_only_permissions(tmp_path):
    settings = google_oauth.load_oauth_settings(_config(tmp_path), environ=ENV)
    path = google_oauth.complete_connection(
        _config(tmp_path), "code-xyz", environ=ENV, http_post=_ok_transport())
    assert path == settings.token_path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    record = google_oauth.load_token(path)
    assert record["refresh_token"] == "rt-1"
    assert google_oauth.load_token(tmp_path / "absent.json") is None


def test_load_user_credentials_uses_the_stored_token(tmp_path):
    google_oauth.complete_connection(
        _config(tmp_path), "code-xyz", environ=ENV, http_post=_ok_transport())
    creds = google_oauth.load_user_credentials(_config(tmp_path), environ=ENV)
    assert creds.token == "at-1"
    assert creds.refresh_token == "rt-1"
    assert set(creds.scopes) == set(REQUIRED_SCOPES)


def test_revoke_posts_to_google_and_removes_the_file(tmp_path):
    path = google_oauth.complete_connection(
        _config(tmp_path), "code-xyz", environ=ENV, http_post=_ok_transport())
    transport = _ok_transport({})
    result = google_oauth.revoke_stored_token(_config(tmp_path), http_post=transport)
    assert transport.calls[0][0] == google_oauth.REVOKE_URL
    assert result == {"revoked": True, "token_file_removed": True}
    assert not path.exists()


# ── Config-only shape check ───────────────────────────────────────────


def test_pilot_template_shape_is_right_and_owner_state_is_pending(monkeypatch):
    monkeypatch.delenv("MWP_GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("MWP_GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    config = yaml.safe_load(PILOT_TEMPLATE.read_text())
    report = connection_health.config_shape_report(config)
    assert report["mode"] == "config-only"
    assert report["shape_ok"] is True, report["checks"]
    pending = " ".join(report["pending"])
    assert "MWP_GOOGLE_OAUTH_CLIENT_ID" in pending
    assert "MWP_GOOGLE_OAUTH_CLIENT_SECRET" in pending
    assert "sign-in" in pending          # the token is a sitting outcome
    # The pilot email was fixed by the Nov 27–28, 2026 decision
    # (jobostech@gmail.com), so it is no longer a pending sitting fix.
    assert "Replace the template account email" not in pending
    checks = {c["name"]: c for c in report["checks"]}
    assert "jobostech@gmail.com" in checks["account_email"]["detail"]
    assert "~/.myworkpal/jobos/google-token.json" in checks["token_path"]["detail"]


def test_shape_check_names_each_defect(tmp_path):
    config = _config(
        tmp_path,
        client_secret_env="SOME_OTHER_NAME",
        redirect_uri="https://labs.jobos.us/wrong/path",
        scopes=[s for s in REQUIRED_SCOPES if "gmail.send" not in s],
    )
    report = connection_health.config_shape_report(config)
    assert report["shape_ok"] is False
    names = {c["name"]: c["ok"] for c in report["checks"]}
    assert names["client_secret_env_name"] is False
    assert names["redirect_uri"] is False
    assert names["scopes_exact"] is False
    scope_check = next(c for c in report["checks"] if c["name"] == "scopes_exact")
    assert "gmail.send" in scope_check["detail"]


def test_config_only_cli_exits_zero_on_the_pilot_template(capsys):
    rc = connection_health._main(["--config", str(PILOT_TEMPLATE), "--config-only"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["shape_ok"] is True


# ── Pilot template hygiene ────────────────────────────────────────────


def test_pilot_template_is_secret_free_and_probe_off():
    text = PILOT_TEMPLATE.read_text()
    data = yaml.safe_load(text)
    assert data["integrations"]["workspace"]["provider"] == "google_api"
    assert data["google"]["auth"]["mode"] == "oauth_user"
    oauth = data["google"]["auth"]["oauth"]
    assert oauth["redirect_uri"] == DEFAULT_REDIRECT_URI
    assert set(oauth["scopes"]) == set(REQUIRED_SCOPES)
    assert data["connections"]["allow_live_probe"] is False
    for marker in ("private_key", "BEGIN RSA", "ya29.", "GOCSPX-"):
        assert marker not in text
