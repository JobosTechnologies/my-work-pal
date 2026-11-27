#!/usr/bin/env python3
"""Onboarding script for workspace provider setup.

Usage:
    python connect_workspace.py --status
    python connect_workspace.py --provider google_api
    python connect_workspace.py --provider m365
    python connect_workspace.py --capabilities
    python connect_workspace.py --verify
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

def _read_yaml_config(path: str) -> dict[str, Any]:
    """Load a YAML config file into a plain dict (no validation)."""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return {}


def _debug_env_enabled() -> bool:
    """Return true when connect_workspace debug logging is explicitly enabled."""
    return os.getenv("CHIEF_OF_STAFF_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def _load_config(config_path: str | None, verbose: bool = False) -> dict[str, Any]:
    """Resolve the workspace config through the standard discovery chain.

    Priority: explicit ``--config`` > ``CHIEF_OF_STAFF_CONFIG`` > plugin-root
    ``shared/config/company.yaml`` > ``{}``. This mirrors ``config_loader``'s
    discovery so that a fully-configured ``company.yaml`` is auto-loaded even
    when neither ``--config`` nor ``CHIEF_OF_STAFF_CONFIG`` is supplied (the
    documented commands omit both). Also auto-loads the plugin-root ``.env`` so
    documented secrets are visible. When falling back to a discovered config
    (i.e. no explicit ``--config``), the resolved path is logged only when
    ``verbose`` or ``CHIEF_OF_STAFF_DEBUG`` is enabled.
    """
    # Auto-load plugin-root .env (shell env always wins; values never logged).
    _default_config_path = None
    try:
        from config_loader import load_dotenv_file, _default_config_path as _dcp
        load_dotenv_file()
        _default_config_path = _dcp
    except Exception:
        pass

    # 1. Explicit --config always wins (behaviour unchanged; no discovery log).
    if config_path:
        return _read_yaml_config(config_path)

    # 2/3. Discover via CHIEF_OF_STAFF_CONFIG, else plugin-root company.yaml.
    discovered: Path | None = None
    if _default_config_path is not None:
        try:
            candidate = Path(_default_config_path())
            if candidate.exists():
                discovered = candidate
        except Exception:
            discovered = None
    else:  # Fallback if config_loader is unavailable for any reason.
        env_path = os.getenv("CHIEF_OF_STAFF_CONFIG")
        if env_path and Path(env_path).exists():
            discovered = Path(env_path)

    if discovered is not None:
        if verbose or _debug_env_enabled():
            print(f"connect_workspace: loaded config from {discovered}", file=sys.stderr)
        return _read_yaml_config(str(discovered))
    return {}


def cmd_status(config: dict[str, Any]) -> int:
    """Print current workspace provider status."""
    integrations = config.get("integrations", {}) if isinstance(config, Mapping) else {}
    workspace = integrations.get("workspace", {}) if isinstance(integrations, Mapping) else {}
    provider = workspace.get("provider", "google_api")
    mode = workspace.get("mode", "direct")

    result: dict[str, Any] = {"provider": provider, "mode": mode}

    if provider == "google_api":
        try:
            from workspace_client import get_workspace_client
            client = get_workspace_client(config)
            healthy = client.health_check()
            result["healthy"] = healthy
            result["class"] = client.__class__.__name__
            google = config.get("google", {})
            result["delegate_email"] = google.get("delegate_email", "")
            result["account_alias"] = google.get("account_alias", "")
        except Exception as exc:
            result["healthy"] = False
            result["error"] = str(exc)
    elif provider == "m365":
        m365 = config.get("m365", {}) if isinstance(config, Mapping) else {}
        auth_mode = str(m365.get("auth", "client_credentials") or "client_credentials")
        secret_env = str(m365.get("client_secret_env", "M365_CLIENT_SECRET") or "M365_CLIENT_SECRET")
        result["auth"] = auth_mode
        result["tenant_id_set"] = bool(m365.get("tenant_id"))
        result["client_id_set"] = bool(m365.get("client_id"))
        result["user_principal"] = m365.get("user_principal", "")
        result["secret_env"] = secret_env
        result["secret_set"] = bool(os.getenv(secret_env))
        try:
            import importlib.util as _ilu
            result["msal_installed"] = _ilu.find_spec("msal") is not None
        except Exception:
            result["msal_installed"] = False
        tcp = m365.get("token_cache_path")
        result["token_cache_path"] = str(tcp) if tcp else ""
        result["token_cache_present"] = bool(tcp) and Path(str(tcp)).expanduser().exists()
        try:
            from workspace_client import get_workspace_client
            client = get_workspace_client(config)
            result["class"] = client.__class__.__name__
            result["healthy"] = client.health_check()
        except Exception as exc:
            result["healthy"] = False
            result["error"] = str(exc)
    else:
        result["healthy"] = False
        result["error"] = f"Unknown provider: {provider}"

    print(json.dumps(result, indent=2))
    return 0 if result.get("healthy") else 1


def cmd_provider_google_api(config: dict[str, Any]) -> int:
    """Verify google_api provider setup."""
    google = config.get("google", {}) if isinstance(config, Mapping) else {}
    sa_path = google.get("service_account_path", "")

    print("=== Google API Provider Setup ===\n")

    try:
        from providers.google_workspace import _find_google_api_script
        script = _find_google_api_script()
        print(f"✅ google_api.py found: {script}")
    except FileNotFoundError as exc:
        print(f"❌ {exc}")
        print("\nNext steps:")
        print("  1. Install the google-workspace skill for Hermes")
        return 1

    if sa_path:
        sa = Path(str(sa_path)).expanduser()
        if sa.exists():
            print(f"✅ Service account found: {sa}")
        else:
            print(f"❌ Service account not found at: {sa}")
            return 1
    else:
        print("⚠️  No service_account_path in config — using OAuth mode")

    delegate = google.get("delegate_email", "")
    if delegate:
        print(f"✅ Delegate email: {delegate}")

    try:
        from workspace_client import get_workspace_client
        client = get_workspace_client(config)
        healthy = client.health_check()
        if healthy:
            print("\n✅ Auth test passed — calendar list succeeded")
            return 0
        else:
            print("\n❌ Auth test failed")
            return 1
    except Exception as exc:
        print(f"\n❌ Auth test error: {exc}")
        return 1


def cmd_provider_m365(config: dict[str, Any], connect: bool) -> int:
    """Verify or guide Microsoft 365 (Graph) provider setup."""
    m365 = config.get("m365", {}) if isinstance(config, Mapping) else {}
    auth_mode = str(m365.get("auth", "client_credentials") or "client_credentials")
    secret_env = str(m365.get("client_secret_env", "M365_CLIENT_SECRET") or "M365_CLIENT_SECRET")

    print("=== Microsoft 365 (Graph) Provider Setup ===\n")
    print(f"Auth mode: {auth_mode}")

    ok = True
    for field in ("tenant_id", "client_id"):
        if m365.get(field):
            print(f"✅ {field}: set")
        else:
            print(f"❌ {field}: NOT set")
            ok = False

    if auth_mode == "client_credentials":
        if m365.get("user_principal"):
            print(f"✅ user_principal: {m365.get('user_principal')}")
        else:
            print("❌ user_principal: NOT set (required for client_credentials)")
            ok = False
        if os.getenv(secret_env):
            print(f"✅ {secret_env}: set")
        else:
            print(f"❌ {secret_env}: NOT set")
            ok = False
    else:
        print("ℹ️  device_code mode: interactive sign-in, no client secret required")

    try:
        import importlib.util as _ilu
        if _ilu.find_spec("msal") is not None:
            print("✅ msal: installed")
        else:
            print("❌ msal: NOT installed — run: pip install msal")
            ok = False
    except Exception:
        print("❌ msal: NOT installed — run: pip install msal")
        ok = False

    if connect:
        print("\nConnect guidance:")
        if auth_mode == "client_credentials":
            print("  client_credentials needs no interactive connect step.")
            print("  Ensure the Entra ID app has application permissions granted")
            print("  (Mail.ReadWrite, Calendars.ReadWrite, Files.ReadWrite.All,")
            print("   User.Read.All) with admin consent, then verify:")
            print("     python connect_workspace.py --provider m365 --status")
        else:
            print("  device_code mode signs in interactively on first token request.")
            print("  A one-time device code + URL are printed to stderr; open the URL,")
            print("  enter the code, and sign in. Then verify:")
            print("     python connect_workspace.py --provider m365 --status")
        return 0

    if not ok:
        print("\nNext steps:")
        print("  1. Register an app in Entra ID (Azure AD) — see docs/SETUP.md Option 3")
        print("  2. Set tenant_id/client_id/user_principal under m365: in company.yaml")
        print(f"  3. Set {secret_env} in .env (client_credentials)")
        print("  4. pip install msal")
        print("  5. python connect_workspace.py --provider m365 --status")
        return 1

    # Live health check
    try:
        from workspace_client import get_workspace_client
        client = get_workspace_client(config)
        if client.health_check():
            print("\n✅ Health check passed — Graph token + user lookup succeeded")
            return 0
        print("\n❌ Health check failed")
        return 1
    except Exception as exc:
        print(f"\n❌ Health check error: {exc}")
        return 1


def cmd_capabilities(config: dict[str, Any], provider_override: str | None = None) -> int:
    """Print provider capabilities — supported and unsupported actions."""
    from workspace_capabilities import (
        get_capabilities, unsupported_actions, recommend_provider_for,
        get_unsupported_reason, WORKFLOW_REQUIREMENTS,
    )

    # Determine which provider to show
    if provider_override:
        provider = provider_override
    else:
        integrations = config.get("integrations", {}) if isinstance(config, dict) else {}
        workspace = integrations.get("workspace", {}) if isinstance(integrations, dict) else {}
        provider = workspace.get("provider", "google_api")

    caps = get_capabilities(provider)
    if not caps:
        print(f"Unknown provider: {provider}")
        return 1

    print(f"Workspace provider: {provider}")
    print()

    # Supported actions
    supported = [a for a, v in caps.items() if v]
    print("Supported:")
    for action in sorted(supported):
        note = ""
        if action in ("gmail.send", "mail.send"):
            note = (
                "  destructive — approve via send_email.py "
                "(or CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE=1)"
            )
        print(f"  ✅ {action}{note}")
    print()

    # Unsupported actions
    unsupported = unsupported_actions(provider)
    if unsupported:
        print("Unsupported:")
        for action in sorted(unsupported):
            reason = get_unsupported_reason(provider, action)
            rec = recommend_provider_for(action)
            print(f"  ❌ {action}  {reason}; use provider={rec}")
    print()

    # Derived workflow capabilities
    print("Workflows:")
    for wf_name, requirements in sorted(WORKFLOW_REQUIREMENTS.items()):
        missing = [a for a in requirements if not caps.get(a, False)]
        if not missing:
            print(f"  ✅ {wf_name}")
        else:
            rec = recommend_provider_for(wf_name)
            print(f"  ❌ {wf_name}  missing: {', '.join(missing)}; use provider={rec}")

    return 0


def _plugin_version() -> str:
    """The plugin version from plugin.yaml (evidence files record it)."""
    import re as _re

    try:
        text = (Path(__file__).resolve().parents[2] / "plugin.yaml").read_text(encoding="utf-8")
        match = _re.search(r"^version:\s*['\"]?([^'\"\s]+)", text, _re.M)
        return match.group(1) if match else "unknown"
    except OSError:
        return "unknown"


def cmd_verify(config: dict[str, Any], include_writes: bool = False,
               include_calendar_writes: bool = False,
               json_output: bool = False,
               record_evidence: bool = False) -> int:
    """Run per-capability verification of the configured workspace provider.

    Read-only by default; ``include_writes`` adds the opt-in, non-destructive
    write smoke checks (draft/tag/upload, each cleaned up). Never sends mail.
    ``include_calendar_writes`` opts into create→update→delete of a marked
    verify event (destructive delete gated inside the verifier). Exit code: 0
    if read_ready (and, when any writes were requested, no tested write
    failed), else 1.
    """
    try:
        from workspace_verify import run_verification, format_report
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Could not load workspace_verify: {exc}", file=sys.stderr)
        return 1

    try:
        report = run_verification(
            config,
            include_writes=include_writes,
            include_calendar_writes=include_calendar_writes,
        )
    except Exception as exc:  # noqa: BLE001
        if json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"❌ Verification could not start: {exc}")
        return 1

    print(format_report(report, fmt="json" if json_output else "human"))
    if record_evidence:
        try:
            import verification_matrix

            path = verification_matrix.record_evidence(config, report, version=_plugin_version())
            print(f"Evidence recorded: {path}")
            print("  Promotion candidates (a maintainer adds them to verification_matrix.EVIDENCE):")
            print(f"  verification_matrix.py --candidates {path}")
        except Exception as exc:  # noqa: BLE001 — never fail the verify run on bookkeeping
            print(f"⚠️  Could not record evidence: {exc}", file=sys.stderr)

    if not report.get("read_ready"):
        return 1
    if (include_writes or include_calendar_writes) and report.get("write_ready") == "no":
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser (exposed so tooling/tests can introspect the
    real, supported flag set without invoking the CLI)."""
    parser = argparse.ArgumentParser(
        description="Workspace provider onboarding and status"
    )
    parser.add_argument("--config", help="Path to company.yaml")
    parser.add_argument("--verbose", action="store_true",
                        help="Print diagnostic details such as discovered config path")
    parser.add_argument("--status", action="store_true", help="Print current provider status")
    parser.add_argument("--verify", action="store_true",
                        help="Run per-capability read verification of the workspace provider")
    parser.add_argument("--verify-writes", action="store_true",
                        help="Run read verification plus opt-in non-destructive write smoke checks")
    parser.add_argument("--verify-calendar-writes", action="store_true",
                        help="Also create/update/delete a marked [CoS verify] calendar event "
                             "(destructive delete of the artefact only; restored after the probe)")
    parser.add_argument("--json", action="store_true",
                        help="Emit the machine-readable JSON report (with --verify/--verify-writes)")
    parser.add_argument("--record-evidence", action="store_true",
                        help="With --verify*: save the check statuses to "
                             "<project_root>/verify/evidence/ for the verification matrix")
    parser.add_argument("--provider", choices=["google_api", "m365"],
                        help="Verify or set up a specific provider")
    parser.add_argument("--connect-m365", action="store_true",
                        help="Print Microsoft 365 connect guidance")
    parser.add_argument("--print-next-steps", action="store_true",
                        help="Print next steps for the selected provider")
    parser.add_argument("--capabilities", action="store_true",
                        help="Print provider capabilities (supported/unsupported actions and workflows)")
    return parser


def _main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = _load_config(args.config, verbose=args.verbose)

    if args.verify or args.verify_writes or args.verify_calendar_writes:
        # --provider overrides the configured workspace provider for this run.
        if args.provider:
            config.setdefault("integrations", {}).setdefault("workspace", {})["provider"] = args.provider
        return cmd_verify(
            config,
            include_writes=args.verify_writes,
            include_calendar_writes=args.verify_calendar_writes,
            json_output=args.json,
            record_evidence=args.record_evidence,
        )

    if args.status:
        return cmd_status(config)
    elif args.capabilities:
        return cmd_capabilities(config, provider_override=args.provider)
    elif args.provider == "google_api":
        return cmd_provider_google_api(config)
    elif args.provider == "m365":
        connect = bool(args.connect_m365)
        return cmd_provider_m365(config, connect)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(_main())
