#!/usr/bin/env python3
"""Google OAuth connect flow for owner sign-in (WP6 prep).

This is the ``oauth_user`` route the WP5 connections template declares:
the owner signs in once with Google, the host exchanges the returned code
for tokens, and the tokens live in one per-owner file. Until this module
existed the repo had no connect flow at all — the ``google_api`` provider
only knew the service-account / domain-wide-delegation route, the template
had no ``redirect_uri`` field, and nothing could build an authorisation
URL, exchange a code, or store a user token.

What lives here:

* :func:`load_oauth_settings` — reads the OAuth client id/secret from the
  environment variables the config names (defaults
  ``MWP_GOOGLE_OAUTH_CLIENT_ID`` / ``MWP_GOOGLE_OAUTH_CLIENT_SECRET``).
  Values come from the host's secret store via the secure flow; they are
  never read from config files and never appear in errors or reports.
  A missing variable raises :class:`GoogleOAuthConfigError` naming the
  variable — that is the only thing a wiring session needs to fix.
* :func:`build_authorization_url` — the consent URL, using the redirect
  URI from config (default :data:`DEFAULT_REDIRECT_URI`, the URI proposed
  in docs/wp6-pilot-prep.md Part C; it must match the URI registered in
  the Google Cloud console character for character).
* :func:`exchange_code` / :func:`refresh_token_record` — the token
  endpoint calls (``https://oauth2.googleapis.com/token``). The HTTP
  transport is injectable so tests never touch the network.
* :func:`save_token` / :func:`load_token` — the per-owner token file:
  written atomically, file mode ``0o600``, parent directory ``0o700``.
* :func:`load_user_credentials` — builds a ``google.oauth2.credentials``
  object from the stored token, the hand-off point for any REST caller.
* :func:`revoke_stored_token` — the Google-side half of disconnect
  (revoke endpoint + local file removal), which WP5 left as a live-stage
  step.

Provider execution: since WP13 the ``google_api`` provider
(providers/google_workspace.py) acts with these user credentials when a
connections config declares ``auth.mode: oauth_user`` — its REST calls
build credentials via :func:`load_user_credentials`, refreshing an
expired token through :func:`refresh_token_record` first. Configs that
declare ``service_account`` keep the delegation route unchanged.

Usage:
    python google_oauth.py --config connections.yaml url
    python google_oauth.py --config connections.yaml exchange --code CODE
    python google_oauth.py --config connections.yaml revoke
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

DEFAULT_CLIENT_ID_ENV = "MWP_GOOGLE_OAUTH_CLIENT_ID"
DEFAULT_CLIENT_SECRET_ENV = "MWP_GOOGLE_OAUTH_CLIENT_SECRET"

# Proposed in docs/wp6-pilot-prep.md Part C; registered in the Cloud
# console at the owner sitting (docs/wp6-google-sitting.md). The flow and
# the registration must match character for character.
DEFAULT_REDIRECT_URI = "https://labs.jobos.us/oauth/google/callback"

# The least-access set decided in WP5 — one scope per capability.
REQUIRED_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
)

HttpPost = Callable[[str, dict[str, str]], "tuple[int, Mapping[str, Any]]"]


class GoogleOAuthConfigError(RuntimeError):
    """Missing or unusable OAuth configuration. Messages name env vars
    and fields only — never a credential value."""


@dataclass(frozen=True)
class OAuthSettings:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scopes: tuple[str, ...] = REQUIRED_SCOPES
    token_path: Path | None = None
    account_email: str = ""


def _oauth_block(config: Mapping[str, Any]) -> Mapping[str, Any]:
    google = config.get("google", {})
    if not isinstance(google, Mapping):
        return {}
    auth = google.get("auth", {})
    if not isinstance(auth, Mapping):
        return {}
    oauth = auth.get("oauth", {})
    return oauth if isinstance(oauth, Mapping) else {}


def load_oauth_settings(
    config: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
) -> OAuthSettings:
    """Build settings from config + environment.

    The client id/secret are looked up by the env-var *names* the config
    declares. Raises :class:`GoogleOAuthConfigError` naming every missing
    variable, so a wiring session sees exactly what the secure capture
    still has to provide.
    """
    env = os.environ if environ is None else environ
    oauth = _oauth_block(config)
    id_env = str(oauth.get("client_id_env") or DEFAULT_CLIENT_ID_ENV)
    secret_env = str(oauth.get("client_secret_env") or DEFAULT_CLIENT_SECRET_ENV)
    client_id = str(env.get(id_env, "") or "").strip()
    client_secret = str(env.get(secret_env, "") or "").strip()
    missing = [name for name, value in ((id_env, client_id), (secret_env, client_secret)) if not value]
    if missing:
        raise GoogleOAuthConfigError(
            "Google OAuth client is not configured: environment variable(s) "
            + ", ".join(missing)
            + " are not set. Provide the client id and secret from the "
            "'My Work Pal Web' OAuth client through the secure flow "
            "(docs/wp6-google-sitting.md); values never go in config files."
        )
    google = config.get("google", {}) if isinstance(config.get("google", {}), Mapping) else {}
    scopes_raw = oauth.get("scopes") or REQUIRED_SCOPES
    scopes = tuple(str(s) for s in scopes_raw)
    token_raw = str(oauth.get("token_path") or "").strip()
    return OAuthSettings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=str(oauth.get("redirect_uri") or DEFAULT_REDIRECT_URI),
        scopes=scopes,
        token_path=Path(token_raw).expanduser() if token_raw else None,
        account_email=str(google.get("account_email") or "").strip(),
    )


def build_authorization_url(settings: OAuthSettings, *, state: str | None = None) -> str:
    """The Google consent URL the owner opens to connect their account."""
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": " ".join(settings.scopes),
        "access_type": "offline",   # ask for a refresh token
        "prompt": "consent",        # so the refresh token is always issued
        "include_granted_scopes": "true",
    }
    if settings.account_email:
        params["login_hint"] = settings.account_email
    if state:
        params["state"] = state
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _default_http_post(url: str, data: dict[str, str]) -> tuple[int, Mapping[str, Any]]:
    import requests

    resp = requests.post(url, data=data, timeout=30)
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    return resp.status_code, payload if isinstance(payload, Mapping) else {}


def _token_record(payload: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    now = int(time.time())
    record: dict[str, Any] = dict(existing or {})
    record["access_token"] = str(payload.get("access_token") or "")
    # A refresh response may omit the refresh token; keep the stored one.
    if payload.get("refresh_token"):
        record["refresh_token"] = str(payload["refresh_token"])
    record["token_type"] = str(payload.get("token_type") or record.get("token_type") or "Bearer")
    if payload.get("scope"):
        record["scope"] = str(payload["scope"])
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        record["expires_at"] = now + int(expires_in)
    elif isinstance(payload.get("expires_at"), (int, float)):
        # Already a token record (refresh_token_record re-wraps the
        # record _post_token built): carry its expiry instead of
        # resurrecting the expired one from `existing`.
        record["expires_at"] = int(payload["expires_at"])
    record["obtained_at"] = now
    return record


def _post_token(settings: OAuthSettings, data: dict[str, str], http_post: HttpPost | None) -> dict[str, Any]:
    post = http_post or _default_http_post
    status, payload = post(TOKEN_URL, data)
    if status >= 400 or payload.get("error"):
        # Google's error code only — the request (which carries the
        # secret) is never echoed into the exception.
        raise GoogleOAuthConfigError(
            f"Google token request failed ({status}): "
            f"{payload.get('error', 'unknown error')} — check the client id/secret "
            "and that the redirect URI matches the registered one exactly."
        )
    if not payload.get("access_token"):
        raise GoogleOAuthConfigError("Google token response carried no access_token.")
    return _token_record(payload)


def exchange_code(settings: OAuthSettings, code: str, *, http_post: HttpPost | None = None) -> dict[str, Any]:
    """Trade the consent callback's code for the owner's token record."""
    if not str(code or "").strip():
        raise GoogleOAuthConfigError("No authorisation code was provided to exchange.")
    return _post_token(settings, {
        "code": str(code).strip(),
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "redirect_uri": settings.redirect_uri,
        "grant_type": "authorization_code",
    }, http_post)


def refresh_token_record(
    settings: OAuthSettings,
    record: Mapping[str, Any],
    *,
    http_post: HttpPost | None = None,
) -> dict[str, Any]:
    """Refresh an expired access token, preserving the refresh token."""
    refresh = str(record.get("refresh_token") or "").strip()
    if not refresh:
        raise GoogleOAuthConfigError(
            "The stored token has no refresh_token; the owner must complete "
            "the one-time Google sign-in again."
        )
    fresh = _post_token(settings, {
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }, http_post)
    return _token_record(fresh, existing=record)


def save_token(token_path: Path, record: Mapping[str, Any]) -> Path:
    """Write the token record atomically with owner-only permissions."""
    path = Path(token_path).expanduser()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(parent, 0o700)
    tmp = parent / (path.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(dict(record), fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def load_token(token_path: Path) -> dict[str, Any] | None:
    """Read the stored token record, or None when the owner has not
    connected yet. A corrupt file is an error, not a silent miss."""
    path = Path(token_path).expanduser()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise GoogleOAuthConfigError(f"Stored Google token at {path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else None


def complete_connection(
    config: Mapping[str, Any],
    code: str,
    *,
    environ: Mapping[str, str] | None = None,
    http_post: HttpPost | None = None,
) -> Path:
    """Exchange ``code`` and store the token at the configured path."""
    settings = load_oauth_settings(config, environ)
    if settings.token_path is None:
        raise GoogleOAuthConfigError(
            "Config declares no google.auth.oauth.token_path — nowhere to store the owner's token."
        )
    record = exchange_code(settings, code, http_post=http_post)
    return save_token(settings.token_path, record)


def load_user_credentials(config: Mapping[str, Any], *, environ: Mapping[str, str] | None = None):
    """Build ``google.oauth2.credentials.Credentials`` from the stored
    token — the hand-off for any REST caller acting as the owner."""
    settings = load_oauth_settings(config, environ)
    if settings.token_path is None:
        raise GoogleOAuthConfigError("Config declares no google.auth.oauth.token_path.")
    record = load_token(settings.token_path)
    if not record:
        raise GoogleOAuthConfigError(
            "No stored Google token — the owner has not completed the one-time sign-in yet."
        )
    from google.oauth2.credentials import Credentials

    return Credentials(
        token=str(record.get("access_token") or "") or None,
        refresh_token=str(record.get("refresh_token") or "") or None,
        token_uri=TOKEN_URL,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        scopes=list(settings.scopes),
    )


def revoke_stored_token(
    config: Mapping[str, Any],
    *,
    http_post: HttpPost | None = None,
) -> dict[str, Any]:
    """Google-side half of disconnect: revoke the grant, remove the file."""
    oauth = _oauth_block(config)
    token_raw = str(oauth.get("token_path") or "").strip()
    result: dict[str, Any] = {"revoked": False, "token_file_removed": False}
    if not token_raw:
        return result
    path = Path(token_raw).expanduser()
    record = load_token(path) if path.is_file() else None
    if record:
        token_value = str(record.get("refresh_token") or record.get("access_token") or "")
        if token_value:
            post = http_post or _default_http_post
            status, _payload = post(REVOKE_URL, {"token": token_value})
            result["revoked"] = status < 400
    if path.is_file():
        path.unlink()
        result["token_file_removed"] = True
    return result


def _load_config(path: str) -> dict[str, Any]:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Google OAuth connect flow (WP6)")
    parser.add_argument("--config", required=True, help="Path to the owner's connections.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("url", help="Print the consent URL for the owner to open")
    exchange = sub.add_parser("exchange", help="Exchange a callback code and store the token")
    exchange.add_argument("--code", required=True)
    sub.add_parser("revoke", help="Revoke the Google grant and remove the stored token")
    args = parser.parse_args(argv)
    config = _load_config(args.config)
    if args.command == "url":
        settings = load_oauth_settings(config)
        print(build_authorization_url(settings, state=secrets.token_urlsafe(16)))
        return 0
    if args.command == "exchange":
        path = complete_connection(config, args.code)
        print(json.dumps({"token_stored": True, "token_path": str(path)}, indent=2))
        return 0
    print(json.dumps(revoke_stored_token(config), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
