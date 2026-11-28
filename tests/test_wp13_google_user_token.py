"""WP13: the google_api provider acts with the owner's stored OAuth token.

When a connections config declares ``google.auth.mode: oauth_user``, the
provider's REST calls (Gmail draft, Calendar create + invite, Drive
untrash, People token) build credentials from the stored user token via
google_oauth — refreshing an expired access token first — instead of the
service-account / domain-wide-delegation route. Configs that declare (or
imply) ``service_account`` keep the old route exactly.

Everything runs offline: the token endpoint is an injected fake
transport, the Google REST endpoints are fake ``requests`` functions,
and the client id/secret are marker env values that must never appear
in an error message.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shared" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import google_oauth  # noqa: E402
from google_oauth import GoogleOAuthConfigError, REQUIRED_SCOPES  # noqa: E402
from providers import google_workspace as gw  # noqa: E402

ENV = {"MWP_GOOGLE_OAUTH_CLIENT_ID": "client-id-marker",
       "MWP_GOOGLE_OAUTH_CLIENT_SECRET": "client-secret-marker"}


@pytest.fixture(autouse=True)
def clean_env():
    import os

    for key in ("CHIEF_OF_STAFF_AUTO_APPROVE", "CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE"):
        os.environ.pop(key, None)
    yield
    for key in ("CHIEF_OF_STAFF_AUTO_APPROVE", "CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE"):
        os.environ.pop(key, None)


def _oauth_config(tmp_path, **google_overrides):
    google = {
        "account_email": "owner@acme.example",
        "auth": {"mode": "oauth_user", "oauth": {
            "client_id_env": "MWP_GOOGLE_OAUTH_CLIENT_ID",
            "client_secret_env": "MWP_GOOGLE_OAUTH_CLIENT_SECRET",
            "redirect_uri": google_oauth.DEFAULT_REDIRECT_URI,
            "token_path": str(tmp_path / "owner" / "google-token.json"),
            "scopes": list(REQUIRED_SCOPES),
        }},
    }
    google.update(google_overrides)
    return {
        "integrations": {"workspace": {"provider": "google_api", "mode": "direct"}},
        "google": google,
    }


def _sa_config(tmp_path):
    return {
        "integrations": {"workspace": {"provider": "google_api", "mode": "direct"}},
        "google": {
            "service_account_path": str(tmp_path / "sa.json"),
            "domain": "acme.example",
            "delegate_email": "owner@acme.example",
            "account_alias": "",
        },
    }


def _store_token(config, **record_overrides):
    settings = google_oauth.load_oauth_settings(config, environ=ENV)
    record = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "token_type": "Bearer",
        "expires_at": int(time.time()) + 3600,
        "obtained_at": int(time.time()),
    }
    record.update(record_overrides)
    google_oauth.save_token(settings.token_path, record)
    return settings.token_path


def _ok_transport(payload=None):
    calls = []

    def post(url, data):
        calls.append((url, dict(data)))
        return 200, payload or {"access_token": "at-2", "expires_in": 3600,
                                "token_type": "Bearer"}

    post.calls = calls
    return post


def _no_transport(url, data):  # pragma: no cover - must never be called
    raise AssertionError("token endpoint called for an unexpired token")


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


# ── Auth-mode resolution ──────────────────────────────────────────────


def test_auth_mode_explicit_oauth_user(tmp_path):
    assert gw._config_auth_mode(_oauth_config(tmp_path)) == "oauth_user"


def test_auth_mode_explicit_service_account_wins_over_sa_path(tmp_path):
    config = _oauth_config(tmp_path)
    config["google"]["auth"]["mode"] = "service_account"
    config["google"]["service_account_path"] = str(tmp_path / "sa.json")
    assert gw._config_auth_mode(config) == "service_account"


def test_auth_mode_sa_path_implies_service_account(tmp_path):
    assert gw._config_auth_mode(_sa_config(tmp_path)) == "service_account"


def test_auth_mode_defaults_to_oauth_user(tmp_path):
    assert gw._config_auth_mode({"google": {}}) == "oauth_user"
    assert gw._config_auth_mode({}) == "oauth_user"


# ── Credential building from the stored token ─────────────────────────


def test_user_credentials_load_the_stored_token_without_http(tmp_path):
    config = _oauth_config(tmp_path)
    _store_token(config)
    creds = gw._user_credentials(config, environ=ENV, http_post=_no_transport)
    assert creds.token == "at-1"
    assert creds.refresh_token == "rt-1"


def test_user_credentials_refresh_an_expired_token_and_save_it(tmp_path):
    config = _oauth_config(tmp_path)
    token_path = _store_token(config, expires_at=1)  # long past
    transport = _ok_transport()
    creds = gw._user_credentials(config, environ=ENV, http_post=transport)
    assert creds.token == "at-2"
    url, data = transport.calls[0]
    assert url == google_oauth.TOKEN_URL
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "rt-1"
    # The refreshed record is persisted, refresh token preserved.
    stored = google_oauth.load_token(token_path)
    assert stored["access_token"] == "at-2"
    assert stored["refresh_token"] == "rt-1"
    assert stored["expires_at"] > int(time.time())


def test_user_credentials_missing_token_file_is_a_clear_error(tmp_path):
    config = _oauth_config(tmp_path)
    with pytest.raises(GoogleOAuthConfigError) as excinfo:
        gw._user_credentials(config, environ=ENV, http_post=_no_transport)
    message = str(excinfo.value)
    assert "one-time sign-in" in message
    assert "client-secret-marker" not in message


def test_user_credentials_expired_without_refresh_token_errors(tmp_path):
    config = _oauth_config(tmp_path)
    _store_token(config, expires_at=1, refresh_token="")
    with pytest.raises(GoogleOAuthConfigError) as excinfo:
        gw._user_credentials(config, environ=ENV, http_post=_ok_transport())
    assert "refresh_token" in str(excinfo.value)
    assert "client-secret-marker" not in str(excinfo.value)


def test_user_credentials_missing_env_vars_are_named(tmp_path):
    config = _oauth_config(tmp_path)
    _store_token(config)
    with pytest.raises(GoogleOAuthConfigError) as excinfo:
        gw._user_credentials(config, environ={}, http_post=_no_transport)
    assert "MWP_GOOGLE_OAUTH_CLIENT_ID" in str(excinfo.value)


# ── REST helpers act with the stored token ────────────────────────────


def test_draft_via_user_token_sends_the_stored_bearer(tmp_path):
    config = _oauth_config(tmp_path)
    _store_token(config)
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        return _Resp({"id": "draft-1", "message": {"id": "msg-1"}})

    with patch.dict("os.environ", ENV), patch("requests.post", side_effect=fake_post):
        out = gw._gmail_draft_via_user_token(
            config=config, to="a@b.example", subject="Hi", body="Body")
    assert seen["url"] == gw._GMAIL_DRAFTS_URL
    assert seen["headers"]["Authorization"] == "Bearer at-1"
    assert out["id"] == "msg-1"
    assert out["draft_id"] == "draft-1"


def test_calendar_create_via_user_token_inserts_and_invites(tmp_path):
    config = _oauth_config(tmp_path)
    _store_token(config)
    calls = []

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        if url.startswith(gw._CALENDAR_EVENTS_URL):
            return _Resp({"id": "evt-1", "htmlLink": "https://cal.example/evt-1",
                          "hangoutLink": "https://meet.google.com/abc-defg-hij"})
        if url == gw._GMAIL_SEND_URL:
            return _Resp({"id": "invite-1"})
        raise AssertionError(f"unexpected URL: {url}")

    with patch.dict("os.environ", ENV), patch(
        "providers.google_workspace._sa_credentials",
        side_effect=AssertionError("service-account route must not run"),
    ), patch("requests.post", side_effect=fake_post):
        out = gw._calendar_create_via_user_token(
            config=config,
            title="Viewing",
            start="2026-11-28T10:00:00-05:00",
            end="2026-11-28T11:00:00-05:00",
            attendees=["client@example.com"],
        )
    assert out["id"] == "evt-1"
    assert out["invite_message_id"] == "invite-1"
    assert out["invite_sent_to"] == ["client@example.com"]
    assert len(calls) == 2
    assert all(
        c["headers"]["Authorization"] == "Bearer at-1" for c in calls
    ), "both the insert and the invite must act with the owner's token"


def test_untrash_via_user_token_sends_the_stored_bearer(tmp_path):
    config = _oauth_config(tmp_path)
    _store_token(config)
    seen = {}

    def fake_patch(url, headers=None, params=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["json"] = json
        return _Resp({"id": "file-1", "trashed": False})

    with patch.dict("os.environ", ENV), patch("requests.patch", side_effect=fake_patch):
        out = gw._drive_untrash_via_user_token(config=config, file_id="file-1")
    assert seen["url"] == f"{gw._DRIVE_FILES_URL}/file-1"
    assert seen["headers"]["Authorization"] == "Bearer at-1"
    assert seen["json"] == {"trashed": False}
    assert out["trashed"] is False
    assert out["reversible"] is True


def test_people_token_comes_from_the_stored_token(tmp_path):
    config = _oauth_config(tmp_path)
    _store_token(config)
    with patch.dict("os.environ", ENV), patch(
        "providers.google_workspace._find_google_api_script",
        return_value=Path("/fake/google_api.py"),
    ):
        client = gw.GoogleWorkspaceClient(config)
        assert client._people_token() == "at-1"


# ── Client dispatch: mode chooses the route ───────────────────────────


@pytest.fixture()
def mock_script():
    with patch(
        "providers.google_workspace._find_google_api_script",
        return_value=Path("/fake/google_api.py"),
    ):
        yield


def test_client_draft_dispatches_to_user_token_in_oauth_mode(tmp_path, mock_script, monkeypatch):
    monkeypatch.setenv("CHIEF_OF_STAFF_AUTO_APPROVE", "1")
    client = gw.GoogleWorkspaceClient(_oauth_config(tmp_path))
    with patch.object(
        gw, "_gmail_draft_via_user_token", return_value={"id": "u1"}
    ) as user_draft, patch.object(
        gw, "_gmail_draft_via_service_account",
        side_effect=AssertionError("service-account route must not run"),
    ):
        result = client.mail_create_draft("a@b.example", "Hi", "Body")
    assert result["success"] is True
    assert result["data"]["id"] == "u1"
    kwargs = user_draft.call_args.kwargs
    assert kwargs["to"] == "a@b.example"
    assert kwargs["config"]["google"]["auth"]["mode"] == "oauth_user"


def test_client_draft_dispatches_to_service_account_when_configured(tmp_path, mock_script, monkeypatch):
    monkeypatch.setenv("CHIEF_OF_STAFF_AUTO_APPROVE", "1")
    client = gw.GoogleWorkspaceClient(_sa_config(tmp_path))
    with patch.object(
        gw, "_gmail_draft_via_service_account", return_value={"id": "s1"}
    ) as sa_draft, patch.object(
        gw, "_gmail_draft_via_user_token",
        side_effect=AssertionError("oauth route must not run"),
    ):
        result = client.mail_create_draft("a@b.example", "Hi", "Body")
    assert result["success"] is True
    assert result["data"]["id"] == "s1"
    kwargs = sa_draft.call_args.kwargs
    assert kwargs["to"] == "a@b.example"
    assert kwargs["service_account_path"].endswith("sa.json")


def test_client_untrash_dispatches_to_user_token_in_oauth_mode(tmp_path, mock_script, monkeypatch):
    monkeypatch.setenv("CHIEF_OF_STAFF_AUTO_APPROVE", "1")
    client = gw.GoogleWorkspaceClient(_oauth_config(tmp_path))
    with patch.object(
        gw, "_drive_untrash_via_user_token",
        return_value={"id": "file-1", "trashed": False},
    ) as user_untrash, patch.object(
        gw, "_drive_untrash_via_service_account",
        side_effect=AssertionError("service-account route must not run"),
    ):
        result = client.files_untrash("file-1")
    assert result["success"] is True
    assert user_untrash.call_args.kwargs["file_id"] == "file-1"


def test_acting_identity_is_the_owner_in_oauth_mode(tmp_path, mock_script):
    client = gw.GoogleWorkspaceClient(_oauth_config(tmp_path))
    assert client.acting_identity("mail") == "owner@acme.example"


def test_acting_identity_is_the_delegate_in_service_account_mode(tmp_path, mock_script):
    client = gw.GoogleWorkspaceClient(_sa_config(tmp_path))
    assert client.acting_identity("mail") == "owner@acme.example"
