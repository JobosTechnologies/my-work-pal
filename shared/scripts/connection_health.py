#!/usr/bin/env python3
"""Connection health for workspace providers (WP5).

Answers one owner-facing question — "is my inbox and calendar connected, and
if not, what is missing?" — as a structured report. The checks are local and
honest:

* config fields are present or named as missing;
* credential *files* are reported by existence only — contents are never
  read, hashed, or printed;
* OAuth client id/secret are looked up by the environment-variable *names*
  in config; only "set / not set" is reported, never a value;
* a live probe (the provider's own ``health_check``) runs only when the
  config explicitly allows it (``connections.allow_live_probe: true``) and
  every local check passed. In the WP5 stage nothing is connected, so the
  probe stays off and the report says exactly what a first connection needs.

``disconnect`` performs the local half of disconnecting: it deletes the
stored OAuth token file (if one is configured and present) and records the
disconnect in the audit log. Revoking the grant on Google's side is part of
the live connection checklist (docs/wp5-connections.md) — it needs the real
token, which this stage never holds.

``--config-only`` is the dry-run shape check: it verifies that a
connections file is *well-formed* for a real ``oauth_user`` connection —
provider, account field, exact env-var names, the registered redirect URI,
the exact least-access scope set, a token path — without needing any
credential to exist. Things that only the owner sitting can supply (the
env values, the stored token) are reported as pending steps, not
failures, so a template can be proven correct before wiring day.

Usage:
    python connection_health.py --config company.yaml [--json]
    python connection_health.py --config company.yaml --config-only
    python connection_health.py --config company.yaml --disconnect
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

Check = dict[str, Any]


def _check(name: str, ok: bool, detail: str) -> Check:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _google_block(config: Mapping[str, Any]) -> Mapping[str, Any]:
    google = config.get("google", {})
    return google if isinstance(google, Mapping) else {}


def _auth_block(google: Mapping[str, Any]) -> Mapping[str, Any]:
    auth = google.get("auth", {})
    return auth if isinstance(auth, Mapping) else {}


def resolve_auth_mode(config: Mapping[str, Any]) -> str:
    """'oauth_user' (owner sign-in) or 'service_account' (Workspace DWD).

    An explicit ``google.auth.mode`` wins. Without one, a configured
    service-account path means the upstream Workspace route; otherwise the
    owner default going forward is ``oauth_user``.
    """
    google = _google_block(config)
    mode = str(_auth_block(google).get("mode") or "").strip().lower()
    if mode in {"oauth_user", "service_account"}:
        return mode
    if str(google.get("service_account_path") or "").strip():
        return "service_account"
    return "oauth_user"


def _env_present(name: str) -> bool:
    return bool(str(os.getenv(str(name), "") or "").strip())


def _google_api_checks(config: Mapping[str, Any]) -> tuple[list[Check], list[str]]:
    checks: list[Check] = []
    next_steps: list[str] = []
    google = _google_block(config)
    auth = _auth_block(google)
    mode = resolve_auth_mode(config)

    account = str(google.get("account_email") or google.get("delegate_email") or "").strip()
    checks.append(_check(
        "account_email", bool(account),
        f"account is {account}" if account else "no account email in config (google.account_email)",
    ))
    if not account:
        next_steps.append("Add the mailbox address the assistant should act as (google.account_email).")

    checks.append(_check("auth_mode", True, f"auth mode is {mode}"))

    if mode == "service_account":
        sa_path_raw = str(google.get("service_account_path") or "").strip()
        sa_path = Path(sa_path_raw).expanduser() if sa_path_raw else None
        checks.append(_check(
            "credential_file", bool(sa_path and sa_path.is_file()),
            "service-account key file exists (contents not read)"
            if sa_path and sa_path.is_file()
            else "service-account key file missing (google.service_account_path)",
        ))
        if not (sa_path and sa_path.is_file()):
            next_steps.append(
                "Place the Google service-account key file at google.service_account_path "
                "(Workspace route: the domain admin authorises it for delegation)."
            )
        try:
            from providers.google_workspace import _find_google_api_script
            script = _find_google_api_script()
            script_ok = bool(script and Path(script).is_file())
        except Exception:
            script_ok = False
        checks.append(_check(
            "read_script", script_ok,
            "google_api.py read script is installed" if script_ok
            else "google_api.py read script not found (the google-workspace skill provides it)",
        ))
        if not script_ok:
            next_steps.append("Install the google-workspace skill so google_api.py reads are available.")
        domain = str(google.get("domain") or "").strip()
        checks.append(_check(
            "workspace_domain", bool(domain),
            f"domain is {domain}" if domain else "no Workspace domain in config (google.domain)",
        ))
        if not domain:
            next_steps.append("Add the Google Workspace domain (google.domain).")
    else:  # oauth_user
        oauth = auth.get("oauth", {}) if isinstance(auth.get("oauth", {}), Mapping) else {}
        id_env = str(oauth.get("client_id_env") or "MWP_GOOGLE_OAUTH_CLIENT_ID")
        secret_env = str(oauth.get("client_secret_env") or "MWP_GOOGLE_OAUTH_CLIENT_SECRET")
        id_ok, secret_ok = _env_present(id_env), _env_present(secret_env)
        checks.append(_check(
            "oauth_client", id_ok and secret_ok,
            f"OAuth client configured via {id_env} / {secret_env} (values never shown)"
            if id_ok and secret_ok
            else f"OAuth client not configured: set {id_env} and {secret_env} from the JobOS Labs Google Cloud project",
        ))
        if not (id_ok and secret_ok):
            next_steps.append(
                "Create the OAuth client in the JobOS Labs Google Cloud project and provide "
                "its id and secret through the secure flow (they are referenced by env name, "
                "never written into config)."
            )
        token_raw = str(oauth.get("token_path") or "").strip()
        token_path = Path(token_raw).expanduser() if token_raw else None
        token_ok = bool(token_path and token_path.is_file())
        checks.append(_check(
            "owner_consent", token_ok,
            "owner has connected (token stored; contents not read)" if token_ok
            else "owner has not connected yet — no stored token",
        ))
        if not token_ok:
            next_steps.append(
                "The owner completes the one-time Google sign-in (consent screen shows "
                "'My Work Pal' by JobOS Labs) so a token can be stored."
            )
        scopes = oauth.get("scopes") or []
        checks.append(_check(
            "scopes_declared", bool(scopes),
            f"{len(scopes)} least-access scope(s) declared" if scopes
            else "no OAuth scopes declared in the connection template",
        ))
        if not scopes:
            next_steps.append("Declare the least-access scopes from the connection template.")
    return checks, next_steps


def connection_report(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build the structured connection report for a config dict."""
    config = config if isinstance(config, Mapping) else {}
    integrations = config.get("integrations", {})
    workspace = integrations.get("workspace", {}) if isinstance(integrations, Mapping) else {}
    provider = str(workspace.get("provider", "google_api") or "google_api")

    from workspace_client import registered_providers
    registered = provider in registered_providers()

    checks: list[Check] = [_check(
        "provider_registered", registered,
        f"provider '{provider}' is registered" if registered
        else f"provider '{provider}' is not registered",
    )]
    next_steps: list[str] = []

    if provider == "sample_google":
        checks.append(_check(
            "sample_mode", True,
            "sample provider: seeded data, no credentials, no network — not a real account",
        ))
        mode = "sample"
        healthy = registered
    elif provider == "google_api":
        mode = "live"
        g_checks, g_steps = _google_api_checks(config)
        checks.extend(g_checks)
        next_steps.extend(g_steps)
        healthy = registered and all(c["ok"] for c in checks)
        # Live probe: opt-in only, and only when everything local passed.
        connections = config.get("connections", {})
        allow_probe = bool(connections.get("allow_live_probe")) if isinstance(connections, Mapping) else False
        if allow_probe and healthy:
            try:
                from workspace_client import get_workspace_client
                probe_ok = bool(get_workspace_client(config).health_check())
                checks.append(_check("live_probe", probe_ok,
                                     "provider answered a live calendar read"
                                     if probe_ok else "provider live probe failed"))
                healthy = probe_ok
            except Exception as exc:
                checks.append(_check("live_probe", False, f"live probe refused: {exc}"))
                healthy = False
        else:
            checks.append(_check(
                "live_probe", True,
                "live probe skipped (connections.allow_live_probe is off or local checks incomplete)",
            ))
    else:
        mode = "live"
        healthy = False
        next_steps.append(f"No WP5 health checks are defined for provider '{provider}' yet.")

    return {
        "provider": provider,
        "mode": mode,
        "healthy": healthy,
        "configured": all(c["ok"] for c in checks if c["name"] != "live_probe"),
        "checks": checks,
        "next_steps": next_steps,
    }


def config_shape_report(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Dry-run shape check for an ``oauth_user`` google_api connection.

    Verifies the config file is well-formed for a real connection without
    requiring any credential to exist: env-var *names* must be the decided
    ones, the redirect URI must match the registered URI exactly, and the
    declared scopes must be exactly the least-access set. Owner-supplied
    state (env values set, token stored) is reported under ``pending`` —
    those are sitting steps (docs/wp6-google-sitting.md), not shape
    failures.
    """
    from google_oauth import (
        DEFAULT_CLIENT_ID_ENV,
        DEFAULT_CLIENT_SECRET_ENV,
        DEFAULT_REDIRECT_URI,
        REQUIRED_SCOPES,
    )

    config = config if isinstance(config, Mapping) else {}
    integrations = config.get("integrations", {})
    workspace = integrations.get("workspace", {}) if isinstance(integrations, Mapping) else {}
    provider = str(workspace.get("provider", "") or "")
    google = _google_block(config)
    auth = _auth_block(google)
    oauth = auth.get("oauth", {}) if isinstance(auth.get("oauth", {}), Mapping) else {}

    checks: list[Check] = []
    pending: list[str] = []

    checks.append(_check(
        "provider", provider == "google_api",
        "provider is google_api" if provider == "google_api"
        else f"provider is '{provider or 'unset'}' — the pilot connects with google_api",
    ))
    account = str(google.get("account_email") or "").strip()
    account_ok = bool(account and "@" in account)
    placeholder = account_ok and (account.endswith(".example") or "yourbusiness" in account)
    checks.append(_check(
        "account_email", account_ok,
        (f"account email is a template placeholder ({account}) — replace it at the sitting"
         if placeholder else f"account email present ({account})")
        if account_ok else "no google.account_email — the field must exist",
    ))
    if placeholder:
        pending.append("Replace the template account email with the pilot owner's real Google address.")
    mode = resolve_auth_mode(config)
    checks.append(_check(
        "auth_mode", mode == "oauth_user",
        "auth mode is oauth_user" if mode == "oauth_user" else f"auth mode is {mode}, not oauth_user",
    ))
    id_env = str(oauth.get("client_id_env") or "")
    checks.append(_check(
        "client_id_env_name", id_env == DEFAULT_CLIENT_ID_ENV,
        f"client id env name is {DEFAULT_CLIENT_ID_ENV}" if id_env == DEFAULT_CLIENT_ID_ENV
        else f"client_id_env is '{id_env or 'unset'}' — it must be exactly {DEFAULT_CLIENT_ID_ENV}",
    ))
    secret_env = str(oauth.get("client_secret_env") or "")
    checks.append(_check(
        "client_secret_env_name", secret_env == DEFAULT_CLIENT_SECRET_ENV,
        f"client secret env name is {DEFAULT_CLIENT_SECRET_ENV}" if secret_env == DEFAULT_CLIENT_SECRET_ENV
        else f"client_secret_env is '{secret_env or 'unset'}' — it must be exactly {DEFAULT_CLIENT_SECRET_ENV}",
    ))
    redirect = str(oauth.get("redirect_uri") or "").strip()
    checks.append(_check(
        "redirect_uri", redirect == DEFAULT_REDIRECT_URI,
        "redirect URI matches the registered URI exactly" if redirect == DEFAULT_REDIRECT_URI
        else (f"redirect_uri is '{redirect}' — it must be exactly {DEFAULT_REDIRECT_URI}"
              if redirect else f"no redirect_uri declared — add {DEFAULT_REDIRECT_URI}"),
    ))
    declared = {str(s) for s in (oauth.get("scopes") or [])}
    required = set(REQUIRED_SCOPES)
    missing = sorted(required - declared)
    extra = sorted(declared - required)
    checks.append(_check(
        "scopes_exact", declared == required,
        "scopes are exactly the five least-access scopes" if declared == required
        else "scope set differs: "
             + (f"missing {[s.rsplit('/', 1)[-1] for s in missing]} " if missing else "")
             + (f"extra {[s.rsplit('/', 1)[-1] for s in extra]}" if extra else ""),
    ))
    token_raw = str(oauth.get("token_path") or "").strip()
    checks.append(_check(
        "token_path", bool(token_raw),
        f"token path declared ({token_raw})" if token_raw else "no google.auth.oauth.token_path declared",
    ))
    connections = config.get("connections", {})
    allow_probe = bool(connections.get("allow_live_probe")) if isinstance(connections, Mapping) else False
    checks.append(_check(
        "live_probe_off", not allow_probe,
        "live probe is off (correct until a real account is connected)" if not allow_probe
        else "connections.allow_live_probe is true — it must stay off until the account is live",
    ))

    for env_name in (DEFAULT_CLIENT_ID_ENV, DEFAULT_CLIENT_SECRET_ENV):
        if not _env_present(env_name):
            pending.append(
                f"Set {env_name} on the host from the 'My Work Pal Web' client, "
                "via the secure capture at the sitting."
            )
    if token_raw:
        token_path = Path(token_raw).expanduser()
        if not token_path.is_file():
            pending.append(
                "The owner completes the one-time Google sign-in so a token is "
                f"stored at {token_raw}."
            )

    return {
        "provider": provider or "google_api",
        "mode": "config-only",
        "shape_ok": all(c["ok"] for c in checks),
        "checks": checks,
        "pending": pending,
    }


def disconnect(config: Mapping[str, Any], *, actor: str = "owner") -> dict[str, Any]:
    """Local half of disconnect: remove the stored token, audit the event.

    Returns what happened. The Google-side grant revocation is a live-stage
    step (docs/wp5-connections.md) and is reported as a remaining step, never
    silently assumed.
    """
    google = _google_block(config)
    auth = _auth_block(google)
    oauth = auth.get("oauth", {}) if isinstance(auth.get("oauth", {}), Mapping) else {}
    token_raw = str(oauth.get("token_path") or "").strip()
    removed = False
    if token_raw:
        token_path = Path(token_raw).expanduser()
        if token_path.is_file():
            token_path.unlink()
            removed = True
    result = {
        "token_file_removed": removed,
        "remaining_steps": [
            "Revoke the grant on Google's side (myaccount.google.com → third-party access, "
            "or the token revocation endpoint at connect time) — live-stage step.",
        ],
    }
    try:
        from audit_log import append_audit
        append_audit("workspace", "connection.disconnect",
                     {"token_path_configured": bool(token_raw)},
                     {"token_file_removed": removed}, actor=actor, config=config)
        result["audited"] = True
    except Exception as exc:  # audit must be reported, not hidden
        result["audited"] = False
        result["audit_error"] = str(exc)
    return result


def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    import yaml
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Workspace connection health (WP5)")
    parser.add_argument("--config", help="Path to company.yaml")
    parser.add_argument("--config-only", action="store_true",
                        help="Dry-run shape check only: no credentials needed; "
                             "owner-supplied state is reported as pending")
    parser.add_argument("--disconnect", action="store_true",
                        help="Remove the stored OAuth token and audit the disconnect")
    args = parser.parse_args(argv)
    config = _load_config(args.config)
    if args.disconnect:
        print(json.dumps(disconnect(config), indent=2))
        return 0
    if args.config_only:
        shape = config_shape_report(config)
        print(json.dumps(shape, indent=2))
        return 0 if shape["shape_ok"] else 1
    report = connection_report(config)
    print(json.dumps(report, indent=2))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(_main())
