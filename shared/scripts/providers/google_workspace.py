#!/usr/bin/env python3
"""Google Workspace backend for WorkspaceClient.

Wraps the existing google_api.py subprocess calls behind a clean interface.
Write methods use guardrails (confirm_action) and return ActionResult dicts.
All write actions are audited via workspace_audit.

Two auth routes, chosen by the connections config (see _config_auth_mode):
``service_account`` (domain-wide delegation, the legacy Workspace route)
and ``oauth_user`` (the owner signed in once; the provider's REST calls
act with the owner's stored token from the WP6 connect flow in
google_oauth.py). The google_api.py subprocess calls keep their own auth
inside that script either way.
"""
from __future__ import annotations

import base64
import functools
import json
import os
import subprocess
import sys
import time
import uuid
import warnings
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Mapping

# Ensure parent dir is importable for workspace_client
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

import plugin_secrets  # noqa: E402
from providers import google_contacts
from workspace_client import WorkspaceClient
from workspace_guardrails import guarded

# Draft creation uses gmail.modify (a superset of gmail.compose) because it is
# ALREADY in google_api.py's standard SCOPES / domain-wide delegation set — so
# drafts work with any existing google_api service-account setup, no new admin
# scope authorization required. gmail.compose is narrower but would force every
# operator to add a scope in Workspace Admin. Execution-verified 2026-07-16: the
# identical drafts.create call under gmail.modify returned HTTP 200 with the
# draft in the delegate's Drafts folder.
_GMAIL_DRAFT_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
_GMAIL_DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
_GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
# Calendar create uses events.insert over REST so we can request a Meet link
# (google_api.py's calendar create CLI has no conferenceData support).
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
# Drive untrash uses the same SA + delegate pattern as draft: google_api.py has
# no drive-untrash CLI, so we PATCH files.update with trashed=False over REST.
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


class CalendarInsertError(RuntimeError):
    """Insert-path failure inside calendar_create (REST/credential/validation)."""


def _reject_crlf(value: str, kind: str) -> str:
    """Reject CR/LF so values cannot be injected into MIME headers."""
    text = str(value)
    if "\r" in text or "\n" in text:
        raise RuntimeError(f"invalid {kind}: CR/LF not allowed")
    return text


def _validated_email(value: str, kind: str) -> str:
    """Return a non-empty addr-spec containing '@', or raise RuntimeError."""
    raw = str(value)
    if "\r" in raw or "\n" in raw:
        raise RuntimeError(f"invalid {kind}: {value}")
    _name, addr = parseaddr(raw.strip())
    if not addr or "@" not in addr:
        raise RuntimeError(f"invalid {kind}: {value}")
    return addr


def _validated_attendee_emails(attendees: list[str] | None) -> list[str]:
    """Validate every attendee; never silently drop an invalid address."""
    out: list[str] = []
    for raw in attendees or []:
        text = str(raw)
        if not text.strip():
            continue
        out.append(_validated_email(text, "attendee email"))
    return out


def _raise_if_naive_datetimelike(value: str, arg_name: str) -> None:
    """Reject datetimelike strings that have ``T`` but no UTC offset or Z."""
    text = str(value)
    if "T" not in text:
        return
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return
    if text.endswith("Z"):
        return
    if parsed.tzinfo is None:
        raise CalendarInsertError(
            f"Calendar events.insert failed: naive datetime {arg_name!r} "
            f"{text!r} requires a UTC offset or Z"
        )


def _conference_status_code(event: Mapping[str, Any]) -> str:
    conf = event.get("conferenceData")
    if not isinstance(conf, Mapping):
        return ""
    create_req = conf.get("createRequest")
    if not isinstance(create_req, Mapping):
        return ""
    status = create_req.get("status")
    if not isinstance(status, Mapping):
        return ""
    return str(status.get("statusCode") or "")


def _sa_credentials(*, service_account_path: str, delegate_email: str, scopes: list[str]):
    """Build domain-wide-delegated SA credentials for a REST call."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    if not (delegate_email or "").strip():
        raise RuntimeError(
            "google.delegate_email is required for this Google REST operation"
        )
    if service_account_path == plugin_secrets.SA_SECRET_SOURCE:
        raw = plugin_secrets.get(plugin_secrets.SA_JSON_ENV) or ""
        try:
            info = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(
                "the GOOGLE_SERVICE_ACCOUNT_JSON secret is not valid JSON "
                "(paste the whole key file's contents)"
            ) from exc
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        sa_path = Path(service_account_path).expanduser()
        if not sa_path.is_file():
            raise RuntimeError(f"service account JSON not found: {sa_path}")
        credentials = service_account.Credentials.from_service_account_file(
            str(sa_path),
            scopes=scopes,
        )
    credentials = credentials.with_subject(delegate_email.strip())
    credentials.refresh(Request())
    return credentials


# ── Auth route + owner (oauth_user) credentials ───────────────────────
# The REST helpers below build credentials one of two ways, per the
# connections config: a delegated service account (above), or the owner's
# stored OAuth token via google_oauth (the WP6 connect flow). The token
# endpoint transport is injectable (http_post) so tests never touch the
# network; google_oauth's errors name env vars, fields and the token
# path only — never a credential value.

_TOKEN_REFRESH_SKEW_SECONDS = 60


def _config_auth_mode(config: Any) -> str:
    """Resolve the Google auth route: 'oauth_user' or 'service_account'.

    Mirrors connection_health.resolve_auth_mode so the health report and
    the provider never disagree: an explicit ``google.auth.mode`` wins;
    a configured service-account path means the legacy Workspace route;
    otherwise the owner default is ``oauth_user``.
    """
    google = config.get("google", {}) if isinstance(config, Mapping) else {}
    if not isinstance(google, Mapping):
        google = {}
    auth = google.get("auth", {})
    if not isinstance(auth, Mapping):
        auth = {}
    mode = str(auth.get("mode") or "").strip().lower()
    if mode in {"oauth_user", "service_account"}:
        return mode
    if str(google.get("service_account_path") or "").strip():
        return "service_account"
    return "oauth_user"


def _user_credentials(
    config: Any,
    *,
    environ: Mapping[str, str] | None = None,
    http_post: Any = None,
):
    """Build owner credentials from the stored Google token.

    The token record is the one google_oauth stored at the one-time
    sign-in. An expired access token is refreshed via
    google_oauth.refresh_token_record and the refreshed record is saved
    back before credentials are built, so the stored file never lags the
    live grant. Raises google_oauth.GoogleOAuthConfigError with a clear,
    value-free message when the owner has not connected yet.
    """
    import google_oauth

    settings = google_oauth.load_oauth_settings(config, environ)
    if settings.token_path is None:
        raise google_oauth.GoogleOAuthConfigError(
            "Config declares no google.auth.oauth.token_path — nowhere to "
            "read the owner's stored Google token."
        )
    record = google_oauth.load_token(settings.token_path)
    if not record:
        raise google_oauth.GoogleOAuthConfigError(
            "No stored Google token — the owner has not completed the "
            "one-time sign-in yet (docs/wp6-google-sitting.md)."
        )
    expires_at = record.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at <= (
        time.time() + _TOKEN_REFRESH_SKEW_SECONDS
    ):
        record = google_oauth.refresh_token_record(
            settings, record, http_post=http_post
        )
        google_oauth.save_token(settings.token_path, record)
    return google_oauth.load_user_credentials(config, environ=environ)


def _gmail_draft_with_credentials(
    *,
    credentials: Any,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
) -> dict[str, Any]:
    """Create a Gmail draft via REST with caller-built credentials.

    Surfaces the underlying message id as ``id`` (keeps ``draft_id``),
    matching the shared provider contract.
    """
    import requests

    mime = MIMEText(body or "", _charset="utf-8")
    mime["To"] = to
    mime["Subject"] = subject
    if cc:
        mime["Cc"] = cc
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")

    resp = requests.post(
        _GMAIL_DRAFTS_URL,
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        },
        json={"message": {"raw": raw}},
        timeout=45,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Gmail drafts.create failed ({resp.status_code}): {resp.text[:500]}"
        )
    data = resp.json() if resp.content else {}
    draft_id = str(data.get("id") or "")
    msg = data.get("message") if isinstance(data.get("message"), Mapping) else {}
    msg_id = str(msg.get("id") or "") if isinstance(msg, Mapping) else ""
    out: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    if msg_id:
        out["draft_id"] = draft_id or out.get("id")
        out["id"] = msg_id
        out["message_id"] = msg_id
    elif draft_id:
        out["id"] = draft_id
        out["draft_id"] = draft_id
    return out


def _gmail_draft_via_service_account(
    *,
    service_account_path: str,
    delegate_email: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
) -> dict[str, Any]:
    """Create a Gmail draft via REST using domain-wide delegation.

    ``google_api.py`` has no draft subcommand; this path uses the same SA +
    delegate identity already configured for the google_api provider.
    """
    credentials = _sa_credentials(
        service_account_path=service_account_path,
        delegate_email=delegate_email,
        scopes=[_GMAIL_DRAFT_SCOPE],
    )
    return _gmail_draft_with_credentials(
        credentials=credentials, to=to, subject=subject, body=body, cc=cc
    )


def _gmail_draft_via_user_token(
    *,
    config: Any,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    environ: Mapping[str, str] | None = None,
    http_post: Any = None,
) -> dict[str, Any]:
    """Create a Gmail draft via REST acting as the owner (oauth_user)."""
    credentials = _user_credentials(config, environ=environ, http_post=http_post)
    return _gmail_draft_with_credentials(
        credentials=credentials, to=to, subject=subject, body=body, cc=cc
    )


def _drive_untrash_with_credentials(
    *,
    credentials: Any,
    file_id: str,
) -> dict[str, Any]:
    """Restore a trashed Drive file via REST with caller-built credentials."""
    import requests

    resp = requests.patch(
        f"{_DRIVE_FILES_URL}/{file_id}",
        headers={
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        },
        params={"supportsAllDrives": "true", "fields": "id,name,trashed,webViewLink"},
        json={"trashed": False},
        timeout=45,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Drive files.update untrash failed ({resp.status_code}): {resp.text[:500]}"
        )
    data = resp.json() if resp.content else {}
    out: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    out.setdefault("id", file_id)
    out["reversible"] = True
    out["trashed"] = False
    return out


def _drive_untrash_via_service_account(
    *,
    service_account_path: str,
    delegate_email: str,
    file_id: str,
) -> dict[str, Any]:
    """Restore a trashed Drive file via REST (files.update trashed=False)."""
    credentials = _sa_credentials(
        service_account_path=service_account_path,
        delegate_email=delegate_email,
        scopes=[_DRIVE_SCOPE],
    )
    return _drive_untrash_with_credentials(credentials=credentials, file_id=file_id)


def _drive_untrash_via_user_token(
    *,
    config: Any,
    file_id: str,
    environ: Mapping[str, str] | None = None,
    http_post: Any = None,
) -> dict[str, Any]:
    """Restore a trashed Drive file via REST acting as the owner (oauth_user)."""
    credentials = _user_credentials(config, environ=environ, http_post=http_post)
    return _drive_untrash_with_credentials(credentials=credentials, file_id=file_id)


def _calendar_create_rest(
    *,
    credentials: Any,
    invite_sender: Any,
    title: str,
    start: str,
    end: str,
    attendee_emails: list[str],
    description: str | None = None,
) -> dict[str, Any]:
    """events.insert + invite orchestration with caller-built credentials.

    ``invite_sender`` is called with the Meet link (possibly "") once the
    event exists and attendees are present; it returns a dict with an
    optional "error" / "invite_message_id" and never raises.
    """
    import requests

    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "conferenceData": {
            "createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    if attendee_emails:
        body["attendees"] = [{"email": a} for a in attendee_emails]
    if description:
        body["description"] = description

    try:
        resp = requests.post(
            _CALENDAR_EVENTS_URL,
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            },
            params={"conferenceDataVersion": 1},
            json=body,
            timeout=45,
        )
    except Exception as exc:
        raise CalendarInsertError(f"Calendar events.insert failed: {exc}") from exc
    if resp.status_code >= 400:
        raise CalendarInsertError(
            f"Calendar events.insert failed ({resp.status_code}): {resp.text[:500]}"
        )
    event = resp.json() if resp.content else {}
    if not isinstance(event, dict):
        event = {}

    hangout_link = event.get("hangoutLink") or ""
    out: dict[str, Any] = {
        "id": event.get("id"),
        "htmlLink": event.get("htmlLink"),
        "hangoutLink": event.get("hangoutLink"),
        "invite_message_id": None,
        "invite_sent_to": [],
    }

    if not attendee_emails:
        return out

    status_code = _conference_status_code(event)
    if not hangout_link and status_code == "pending":
        out["error"] = "meet link pending — invite email not sent"
        return out

    invite_result = invite_sender(str(hangout_link) if hangout_link else "")
    if invite_result.get("error"):
        out["error"] = invite_result["error"]
    else:
        out["invite_message_id"] = invite_result.get("invite_message_id")
        out["invite_sent_to"] = list(attendee_emails)
    return out


def _calendar_create_via_service_account(
    *,
    service_account_path: str,
    delegate_email: str,
    title: str,
    start: str,
    end: str,
    attendees: list[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Insert a calendar event with a Meet link, then email attendees.

    ``google_api.py calendar create`` cannot request conferenceData (no Meet
    link) and service-account-created events do not reliably generate Google's
    own invitation email, so this path uses Calendar events.insert + a Gmail
    messages.send follow-up.
    """
    title = _reject_crlf(title, "title")
    attendee_emails = _validated_attendee_emails(attendees)
    delegate = _validated_email(delegate_email, "delegate email")

    try:
        credentials = _sa_credentials(
            service_account_path=service_account_path,
            delegate_email=delegate,
            scopes=[_CALENDAR_SCOPE],
        )
    except Exception as exc:
        raise CalendarInsertError(f"Calendar events.insert failed: {exc}") from exc

    def _invite(hangout_link: str) -> dict[str, Any]:
        return _gmail_send_calendar_invite(
            service_account_path=service_account_path,
            delegate_email=delegate,
            title=title,
            start=start,
            end=end,
            attendees=attendee_emails,
            hangout_link=hangout_link,
        )

    return _calendar_create_rest(
        credentials=credentials,
        invite_sender=_invite,
        title=title,
        start=start,
        end=end,
        attendee_emails=attendee_emails,
        description=description,
    )


def _calendar_create_via_user_token(
    *,
    config: Any,
    title: str,
    start: str,
    end: str,
    attendees: list[str] | None = None,
    description: str | None = None,
    environ: Mapping[str, str] | None = None,
    http_post: Any = None,
) -> dict[str, Any]:
    """Insert a calendar event with a Meet link, acting as the owner.

    Same contract as the service-account path (Meet link + one follow-up
    invite email), but the credentials are the owner's stored OAuth token
    and the invite is sent from the owner's own account.
    """
    title = _reject_crlf(title, "title")
    attendee_emails = _validated_attendee_emails(attendees)
    google_cfg = config.get("google", {}) if isinstance(config, Mapping) else {}
    from_email = (
        str(google_cfg.get("account_email") or "").strip()
        if isinstance(google_cfg, Mapping)
        else ""
    )
    try:
        credentials = _user_credentials(config, environ=environ, http_post=http_post)
    except Exception as exc:
        raise CalendarInsertError(f"Calendar events.insert failed: {exc}") from exc

    def _invite(hangout_link: str) -> dict[str, Any]:
        return _gmail_send_invite_with_credentials(
            credentials=credentials,
            from_email=from_email,
            title=title,
            start=start,
            end=end,
            attendees=attendee_emails,
            hangout_link=hangout_link,
        )

    return _calendar_create_rest(
        credentials=credentials,
        invite_sender=_invite,
        title=title,
        start=start,
        end=end,
        attendee_emails=attendee_emails,
        description=description,
    )


def _gmail_send_invite_with_credentials(
    *,
    credentials: Any,
    from_email: str,
    title: str,
    start: str,
    end: str,
    attendees: list[str],
    hangout_link: str,
) -> dict[str, Any]:
    """Send one follow-up invite email with caller-built credentials.

    Never raises: the calendar insert already succeeded, so invite
    failure is partial success for the caller.
    """
    import requests

    meet_line = hangout_link or "(Meet link will appear on the calendar event)"
    body_text = (
        f"You are invited to: {title}\n"
        f"\n"
        f"When: {start} to {end}\n"
        f"\n"
        f"Join Google Meet: {meet_line}\n"
        f"\n"
        f"This event is also on the organizer's Google Calendar.\n"
    )
    # Keep the MIMEText us-ascii default; UnicodeEncodeError is caught below
    # so a non-ascii title cannot raise after the event already exists.
    try:
        mime = MIMEText(body_text)
        mime["To"] = ", ".join(attendees)
        mime["Subject"] = f"Invitation: {title}"
        mime["From"] = from_email
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        resp = requests.post(
            _GMAIL_SEND_URL,
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            },
            json={"raw": raw},
            timeout=45,
        )
        if resp.status_code >= 400:
            return {
                "error": (
                    f"invite email failed ({resp.status_code}): {resp.text[:500]}"
                )
            }
        data = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001 — invite failure must not drop the event
        return {"error": f"invite email failed: {exc}"}

    msg_id = ""
    if isinstance(data, dict):
        msg_id = str(data.get("id") or "")
    return {"invite_message_id": msg_id}


def _gmail_send_calendar_invite(
    *,
    service_account_path: str,
    delegate_email: str,
    title: str,
    start: str,
    end: str,
    attendees: list[str],
    hangout_link: str,
) -> dict[str, Any]:
    """Send one follow-up invite email covering every attendee.

    Never raises: insert already succeeded, so invite failure is partial
    success for the caller. Uses gmail.modify (same scope as drafts) rather
    than gmail.send so existing domain-wide delegation keeps working.
    """
    try:
        credentials = _sa_credentials(
            service_account_path=service_account_path,
            delegate_email=delegate_email,
            scopes=[_GMAIL_DRAFT_SCOPE],
        )
    except Exception as exc:  # noqa: BLE001 — invite failure must not drop the event
        return {"error": f"invite email failed: {exc}"}
    return _gmail_send_invite_with_credentials(
        credentials=credentials,
        from_email=delegate_email,
        title=title,
        start=start,
        end=end,
        attendees=attendees,
        hangout_link=hangout_link,
    )


def _calendar_create_contract(fn):
    """Adapt @guarded's ActionResult to the calendar.create contract.

    ``@guarded`` swallows body exceptions into ``success=False`` and always
    sets ``error=None`` on success. Contract tests require insert HTTP
    failures to raise ``RuntimeError``, and invite-send failures to keep
    ``success=True`` with a top-level ``error`` note.
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        result = fn(self, *args, **kwargs)
        if not isinstance(result, dict):
            return result
        err = result.get("error") or ""
        # Re-raise insert-path failures only. Guardrail blocks
        # ("cancelled by guardrail ...") are not insert failures.
        if result.get("success") is False and err.startswith(
            "Calendar events.insert failed"
        ):
            raise CalendarInsertError(err)
        data = result.get("data")
        if (
            result.get("success") is True
            and not result.get("error")
            and isinstance(data, dict)
            and data.get("error")
        ):
            result = dict(result)
            result["error"] = data["error"]
        return result

    return wrapper


def _refuse_several_emails_on_cli(addresses: list[str]) -> None:
    if len(addresses) > 1:
        raise ValueError(
            "google_api.py takes one --email per contact; several addresses need the People "
            "API path — set google.service_account_path and google.delegate_email"
        )


def _find_google_api_script() -> Path:
    """Locate google_api.py — check shared/scripts first, then Hermes skill.

    Search order:
    1. shared/scripts/google_api.py (if shipped alongside this plugin)
    2. $CHIEF_OF_STAFF_HERMES_HOME/skills/productivity/google-workspace/scripts/
    3. $HERMES_HOME/skills/productivity/google-workspace/scripts/
    4. ~/.hermes/skills/productivity/google-workspace/scripts/ (default)

    The google-workspace skill is an OPTIONAL external dependency.
    Install it via: hermes skill install google-workspace
    Or set GOOGLE_WORKSPACE_API env var to point to a custom google_api.py.
    """
    # Check env override first
    env_script = os.getenv("GOOGLE_WORKSPACE_API")
    if env_script:
        p = Path(env_script).expanduser()
        if p.exists():
            return p

    # Determine Hermes home (env-configurable for non-Hermes agents)
    hermes_env = os.getenv("CHIEF_OF_STAFF_HERMES_HOME") or os.getenv("HERMES_HOME")
    hermes_home = Path(hermes_env).expanduser() if hermes_env else Path.home() / ".hermes"

    candidates = [
        _PARENT / "google_api.py",
        hermes_home / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
        hermes_home / "skills" / "google-workspace" / "scripts" / "google_api.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "google_api.py not found. The google-workspace skill is an optional "
        "external dependency. Install it via 'hermes skill install google-workspace' "
        "or set GOOGLE_WORKSPACE_API to point to a custom google_api.py."
    )


class GoogleWorkspaceClient(WorkspaceClient):
    """Google Workspace provider using google_api.py subprocess."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self._provider_name = "google_api"
        google_cfg = config.get("google", {}) if isinstance(config, Mapping) else {}
        self.delegate_email = str(google_cfg.get("delegate_email", ""))
        self.account_alias = str(google_cfg.get("account_alias", ""))
        self._script = _find_google_api_script()

    def acting_identity(self, family: str) -> str | None:
        """Who acts: the owner account (oauth_user) or the delegate (SA).

        Domain-wide delegation impersonates delegate_email; an oauth_user
        connection acts as the owner themselves (google.account_email).
        """
        if _config_auth_mode(self.config) == "oauth_user":
            google_cfg = (
                self.config.get("google", {})
                if isinstance(self.config, Mapping)
                else {}
            )
            account = (
                str(google_cfg.get("account_email") or "").strip()
                if isinstance(google_cfg, Mapping)
                else ""
            )
            return account or self.delegate_email.strip() or None
        return self.delegate_email.strip() or None

    def _build_cmd(self, *args: str) -> list[str]:
        """Build a google_api.py command with auth flags."""
        cmd = [sys.executable, str(self._script)]
        if self.account_alias:
            cmd.extend(["--account", self.account_alias])
        if self.delegate_email:
            cmd.extend(["--as", self.delegate_email])
        cmd.extend(args)
        return cmd

    def _run(self, cmd: list[str], timeout: int = 45) -> tuple[int, str, str]:
        """Run a command, return (exit_code, stdout, stderr)."""
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "google_api.py timed out"
        except Exception as exc:
            return 1, "", str(exc)

    def _parse_json(self, stdout: str) -> list[dict[str, Any]] | str:
        """Parse JSON output, falling back to raw text."""
        try:
            result = json.loads(stdout or "[]")
            return result if isinstance(result, list) else [result]
        except (json.JSONDecodeError, TypeError):
            return stdout.strip() if stdout else []

    # ── Read methods (return lists, no guardrails needed) ──────────────

    def mail_search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        cmd = self._build_cmd("gmail", "search", query, "--max", str(max_results))
        rc, out, err = self._run(cmd)
        if rc != 0:
            warnings.warn(f"mail_search failed: {err.strip() or out.strip()}")
            return []
        result = self._parse_json(out)
        return result if isinstance(result, list) else []

    def calendar_list(self, start: str, end: str) -> list[dict[str, Any]]:
        # Ensure RFC3339 format (google_api.py requires datetime, not bare date)
        if "T" not in start:
            start = f"{start}T00:00:00Z"
        if "T" not in end:
            end = f"{end}T23:59:59Z"
        cmd = self._build_cmd("calendar", "list", "--start", start, "--end", end)
        rc, out, err = self._run(cmd)
        if rc != 0:
            warnings.warn(f"calendar_list failed: {err.strip() or out.strip()}")
            return []
        result = self._parse_json(out)
        return result if isinstance(result, list) else []

    def files_search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        cmd = self._build_cmd("drive", "search", query, "--max", str(max_results))
        rc, out, err = self._run(cmd)
        if rc != 0:
            warnings.warn(f"files_search failed: {err.strip() or out.strip()}")
            return []
        result = self._parse_json(out)
        return result if isinstance(result, list) else []

    def mail_list_tags(self) -> list[dict[str, Any]]:
        """List all Gmail labels (tags). Read-only — no mutation."""
        cmd = self._build_cmd("gmail", "labels")
        rc, out, err = self._run(cmd, timeout=30)
        if rc != 0:
            warnings.warn(f"mail_list_tags failed: {err.strip() or out.strip()}")
            return []
        try:
            labels = json.loads(out) if out else []
        except json.JSONDecodeError:
            return []
        return labels if isinstance(labels, list) else []

    # ── Write methods (guarded: confirm + audit + ActionResult) ────────
    # Each body performs the raw work and returns a data dict, or raises on
    # failure; @guarded wraps it. Legacy action ids ("gmail.send", ...) are
    # preserved for back-compat with stored queues and tests.

    @guarded("drive.upload", target_arg="file_path", audit_provider="google_api")
    def files_upload(self, file_path: str, parent_id: str | None = None) -> dict[str, Any]:
        cmd = self._build_cmd("drive", "upload", file_path)
        if parent_id:
            cmd.extend(["--parent", parent_id])
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        try:
            return json.loads(out) if out else {}
        except json.JSONDecodeError:
            return {"raw": out.strip()}

    @guarded("gmail.send", target_arg="to", audit_provider="google_api",
             block_error="cancelled by guardrail (requires CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE=1)")
    def mail_send(self, to: str, subject: str, body: str,
                  cc: str | None = None) -> dict[str, Any]:
        # gmail.send is destructive — requires CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE=1
        cmd = self._build_cmd("gmail", "send", "--to", to, "--subject", subject, "--body", body)
        if cc:
            cmd.extend(["--cc", cc])
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip()}

    @guarded("gmail.draft", target_arg="to", audit_provider="google_api")
    def mail_create_draft(self, to: str, subject: str, body: str,
                          cc: str | None = None) -> dict[str, Any]:
        """Create a mail draft.

        ``google_api.py`` has no draft CLI; drafts are created via the Gmail
        REST API — with the owner's stored token when the config's auth
        mode is oauth_user, else the configured service-account +
        ``delegate_email``. Surfaces the underlying message id as ``id``
        (keeps ``draft_id``), matching the shared provider contract.
        """
        if _config_auth_mode(self.config) == "oauth_user":
            return _gmail_draft_via_user_token(
                config=self.config,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
            )
        google_cfg = (
            self.config.get("google", {})
            if isinstance(self.config, Mapping)
            else {}
        )
        sa_path, _ = google_contacts.service_account_settings(self.config)
        delegate = self.delegate_email or str(
            google_cfg.get("delegate_email") or ""
        ).strip()
        if not sa_path:
            raise RuntimeError(
                "gmail.draft requires google.service_account_path (or "
                "GOOGLE_SERVICE_ACCOUNT_PATH) — google_api.py has no draft CLI"
            )
        return _gmail_draft_via_service_account(
            service_account_path=sa_path,
            delegate_email=delegate,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
        )

    @_calendar_create_contract
    @guarded("calendar.create", target_arg="title", audit_provider="google_api")
    def calendar_create(self, title: str, start: str, end: str,
                        attendees: list[str] | None = None,
                        description: str | None = None) -> dict[str, Any]:
        """Create a calendar event with a Google Meet link (Meet-by-default).

        Inserts via Calendar REST (conferenceDataVersion=1), then sends
        one follow-up Gmail invite to attendees. Credentials follow the
        config's auth mode: the owner's stored token (oauth_user) or the
        configured service account + delegate — service-account-created
        events do not reliably generate Google's own invitation email.
        """
        # Date-only strings still get a default time so callers that passed
        # YYYY-MM-DD to the old CLI keep working; RFC3339 values are used as-is.
        if "T" not in start:
            start = f"{start}T10:00:00Z"
        if "T" not in end:
            end = f"{end}T11:00:00Z"
        _raise_if_naive_datetimelike(start, "start")
        _raise_if_naive_datetimelike(end, "end")
        google_cfg = (
            self.config.get("google", {})
            if isinstance(self.config, Mapping)
            else {}
        )
        if _config_auth_mode(self.config) == "oauth_user":
            try:
                return _calendar_create_via_user_token(
                    config=self.config,
                    title=title,
                    start=start,
                    end=end,
                    attendees=attendees,
                    description=description,
                )
            except CalendarInsertError:
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                raise CalendarInsertError(
                    f"Calendar events.insert failed: {exc}"
                ) from exc
        sa_path, _ = google_contacts.service_account_settings(self.config)
        delegate = self.delegate_email or str(
            google_cfg.get("delegate_email") or ""
        ).strip()
        if not sa_path:
            raise RuntimeError(
                "calendar.create requires google.service_account_path (or "
                "GOOGLE_SERVICE_ACCOUNT_PATH) — events.insert is REST-only"
            )
        try:
            return _calendar_create_via_service_account(
                service_account_path=sa_path,
                delegate_email=delegate,
                title=title,
                start=start,
                end=end,
                attendees=attendees,
                description=description,
            )
        except CalendarInsertError:
            raise
        except RuntimeError:
            raise
        except Exception as exc:
            raise CalendarInsertError(
                f"Calendar events.insert failed: {exc}"
            ) from exc

    @guarded("calendar.update", target_arg="event_id", audit_provider="google_api")
    def calendar_update(self, event_id: str, **fields: Any) -> dict[str, Any]:
        cmd = self._build_cmd("calendar", "update", "--event-id", event_id)
        for key, value in fields.items():
            cmd.extend([f"--{key.replace('_', '-')}", str(value)])
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        try:
            return json.loads(out) if out else {}
        except json.JSONDecodeError:
            return {"raw": out.strip()}

    @guarded("drive.download", target_arg="file_id", audit_provider="google_api")
    def files_download(self, file_id: str, output_path: str) -> dict[str, Any]:
        cmd = self._build_cmd("drive", "download", "--file-id", file_id, "--output", output_path)
        rc, out, err = self._run(cmd, timeout=120)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"path": output_path}

    @guarded("gmail.archive", target_arg="message_id", audit_provider="google_api")
    def mail_archive(self, message_id: str) -> dict[str, Any]:
        """Archive a mail message (remove from INBOX). Reversible."""
        cmd = self._build_cmd("gmail", "modify", message_id, "--remove-labels", "INBOX")
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip()}

    @guarded("gmail.trash", target_arg="message_id", audit_provider="google_api")
    def mail_trash(self, message_id: str) -> dict[str, Any]:
        """Move a mail message to trash. Reversible (30-day auto-delete by Google)."""
        cmd = self._build_cmd("gmail", "modify", message_id, "--add-labels", "TRASH", "--remove-labels", "INBOX")
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip(), "reversible": True}

    @guarded("drive.trash", target_arg="file_id", audit_provider="google_api")
    def files_trash(self, file_id: str) -> dict[str, Any]:
        """Move a Drive file to trash. Reversible (30-day auto-delete by Google)."""
        # drive delete defaults to trash (not --permanent)
        cmd = self._build_cmd("drive", "delete", file_id)
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip(), "reversible": True}

    @guarded("drive.untrash", target_arg="file_id", audit_provider="google_api")
    def files_untrash(self, file_id: str) -> dict[str, Any]:
        """Restore a trashed Drive file (files.update trashed=False via REST).

        ``google_api.py`` has no drive-untrash CLI; this mirrors the
        ``mail_create_draft`` routes: the owner's stored token in
        oauth_user mode, else the service-account REST path.
        """
        if _config_auth_mode(self.config) == "oauth_user":
            return _drive_untrash_via_user_token(
                config=self.config,
                file_id=file_id,
            )
        google_cfg = (
            self.config.get("google", {})
            if isinstance(self.config, Mapping)
            else {}
        )
        sa_path, _ = google_contacts.service_account_settings(self.config)
        delegate = self.delegate_email or str(
            google_cfg.get("delegate_email") or ""
        ).strip()
        if not sa_path:
            raise RuntimeError(
                "files.untrash requires google.service_account_path (or "
                "GOOGLE_SERVICE_ACCOUNT_PATH) — google_api.py has no untrash CLI"
            )
        return _drive_untrash_via_service_account(
            service_account_path=sa_path,
            delegate_email=delegate,
            file_id=file_id,
        )

    @guarded("calendar.cancel", target_arg="event_id", audit_provider="google_api")
    def calendar_cancel(self, event_id: str) -> dict[str, Any]:
        """Cancel a calendar event (set status to cancelled). Reversible via update."""
        cmd = self._build_cmd("calendar", "update", "--event-id", event_id, "--status", "cancelled")
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip(), "reversible": True}

    @guarded("gmail.label", target_arg="message_id", audit_provider="google_api")
    def mail_tag(self, message_id: str, tag_id: str) -> dict[str, Any]:
        """Apply an existing tag (Gmail label) to a message. Guardrailed + audited."""
        cmd = self._build_cmd("gmail", "modify", message_id, "--add-labels", tag_id)
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"label_id": tag_id}

    @guarded("gmail.create_label", target_arg="name", audit_provider="google_api")
    def mail_create_tag(self, name: str) -> dict[str, Any]:
        """Create a new tag (Gmail label). Guardrailed + audited.
        Note: google_api.py may not support label creation yet.
        """
        # google_api.py may not have a labels --create subcommand.
        cmd = self._build_cmd("gmail", "labels", "--create", name)
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip() or "label creation not supported by google_api.py")
        try:
            return json.loads(out) if out else {}
        except json.JSONDecodeError:
            return {"raw": out.strip()}

    @guarded("gmail.unarchive", target_arg="message_id", audit_provider="google_api")
    def mail_unarchive(self, message_id: str) -> dict[str, Any]:
        """Restore an archived mail message (add INBOX label back)."""
        cmd = self._build_cmd("gmail", "modify", message_id, "--add-labels", "INBOX")
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip()}

    @guarded("gmail.untrash", target_arg="message_id", audit_provider="google_api")
    def mail_untrash(self, message_id: str) -> dict[str, Any]:
        """Restore a trashed mail message (remove TRASH label)."""
        cmd = self._build_cmd("gmail", "modify", message_id, "--remove-labels", "TRASH")
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip()}

    @guarded("calendar.uncancel", target_arg="event_id", audit_provider="google_api")
    def calendar_uncancel(self, event_id: str) -> dict[str, Any]:
        """Restore a cancelled calendar event (set status back to confirmed)."""
        cmd = self._build_cmd("calendar", "update", "--event-id", event_id, "--status", "confirmed")
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        return {"output": out.strip()}

    # ── Contacts ────────────────────────────────────────────────────────
    # Reads go through google_api.py ``contacts list``. Writes go through the
    # People API when a service account is configured, else through
    # google_api.py — but only if the installed script has the subcommand
    # (the shipped google-workspace skill offers ``list`` only).

    def supports(self, action: str) -> bool:
        if action in google_contacts.CONTACT_WRITES:
            return self._contacts_route(action)[0] is not None
        return super().supports(action)

    def unsupported_reason(self, action: str) -> str | None:
        if action in google_contacts.CONTACT_WRITES:
            backend, reason = self._contacts_route(action)
            return None if backend else reason
        return super().unsupported_reason(action)

    def _contacts_route(self, action: str) -> tuple[str | None, str]:
        return google_contacts.contacts_write_backend(self.config, self._script, action)

    def _contacts_backend(self, action: str) -> str:
        backend, reason = self._contacts_route(action)
        if backend is None:
            raise RuntimeError(reason)
        return backend

    def _people_token(self) -> str:
        if _config_auth_mode(self.config) == "oauth_user":
            return _user_credentials(self.config).token
        sa_path, delegate = google_contacts.service_account_settings(self.config)
        credentials = _sa_credentials(
            service_account_path=sa_path,
            delegate_email=self.delegate_email or delegate,
            scopes=[google_contacts.PEOPLE_SCOPE],
        )
        return credentials.token

    def contacts_list(self, max_results: int = 50) -> list[dict[str, Any]]:
        """List contacts (name/emails/phones). Read-only."""
        cmd = self._build_cmd("contacts", "list", "--max", str(max_results))
        rc, out, err = self._run(cmd)
        if rc != 0:
            warnings.warn(f"contacts_list failed: {err.strip() or out.strip()}")
            return []
        result = self._parse_json(out)
        return result if isinstance(result, list) else []

    @guarded("contacts.create", target_arg="given_name", audit_provider="google_api")
    def contacts_create(self, given_name: str = "", family_name: str = "",
                        email: str = "", phone: str = "",
                        organization: str = "", note: str = "",
                        emails: list[str] | None = None) -> dict[str, Any]:
        """Create a contact. ``email`` and ``emails`` combine, ``email`` first.

        Requires given_name: @guarded resolves the approval/audit target from
        that argument, so a family-only create (blank target) is rejected
        here rather than audited with an empty target.
        """
        if not given_name:
            raise ValueError(
                "contacts.create requires given_name (the approval/audit "
                "target); family-only creates are CLI-only"
            )
        addresses = google_contacts.merge_emails(email, emails)
        if self._contacts_backend("contacts.create") == "rest":
            return google_contacts.rest_create(
                self._people_token(), given_name=given_name, family_name=family_name,
                emails=addresses, phone=phone, organization=organization, note=note)
        _refuse_several_emails_on_cli(addresses)
        args: list[str] = ["contacts", "create"]
        if given_name:
            args += ["--given-name", given_name]
        if family_name:
            args += ["--family-name", family_name]
        if addresses:
            args += ["--email", addresses[0]]
        if phone:
            args += ["--phone", phone]
        if organization:
            args += ["--organization", organization]
        if note:
            args += ["--note", note]
        cmd = self._build_cmd(*args)
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        try:
            return json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            return {"output": out.strip()}

    @guarded("contacts.update", target_arg="person_id", audit_provider="google_api")
    def contacts_update(self, person_id: str, **fields: Any) -> dict[str, Any]:
        """Update a contact. Merge-safe: omitted field types are preserved.

        ``email``/``phone``/``organization`` replace the first entry of their
        type; ``emails`` replaces the whole address list.
        """
        if not person_id:
            raise ValueError("contacts.update requires person_id")
        supported = ("given_name", "family_name", "email", "emails", "phone", "organization", "note")
        unknown = [k for k in fields if k not in supported]
        if unknown:
            # A typo'd kwarg (emial=...) must fail loudly, not silently drop
            # and masquerade as "requires at least one field".
            raise ValueError(
                f"contacts.update got unknown field(s): {', '.join(sorted(unknown))}. "
                f"Supported: {', '.join(supported)}"
            )
        # Only non-empty values update; google_api.py has no field-clearing
        # surface, so an explicit empty string is rejected rather than dropped.
        empty = [k for k in supported if k in fields and not fields[k]]
        if empty:
            raise ValueError(
                f"contacts.update cannot clear field(s) (google_api.py has no "
                f"clearing surface): {', '.join(sorted(empty))}"
            )
        supplied = {k: v for k, v in fields.items() if k in supported and v}
        if not supplied:
            raise ValueError(
                "contacts.update requires at least one field: "
                + ", ".join(supported)
            )
        if self._contacts_backend("contacts.update") == "rest":
            return google_contacts.rest_update(self._people_token(), person_id, supplied)
        if "emails" in supplied:
            addresses = google_contacts.merge_emails(supplied.get("email", ""), supplied.pop("emails"))
            _refuse_several_emails_on_cli(addresses)
            supplied["email"] = addresses[0]
        args: list[str] = ["contacts", "update", "--person-id", person_id]
        flag_map = {"given_name": "--given-name", "family_name": "--family-name",
                    "email": "--email", "phone": "--phone",
                    "organization": "--organization", "note": "--note"}
        for key, flag in flag_map.items():
            if key in supplied:
                args += [flag, str(supplied[key])]
        cmd = self._build_cmd(*args)
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        try:
            return json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            return {"output": out.strip()}

    @guarded("contacts.delete", target_arg="person_id", audit_provider="google_api")
    def contacts_delete(self, person_id: str) -> dict[str, Any]:
        """Delete a contact PERMANENTLY (no trash step, not reversible)."""
        if not person_id:
            raise ValueError("contacts.delete requires person_id")
        if self._contacts_backend("contacts.delete") == "rest":
            return google_contacts.rest_delete(self._people_token(), person_id)
        cmd = self._build_cmd("contacts", "delete", "--person-id", person_id)
        rc, out, err = self._run(cmd)
        if rc != 0:
            raise RuntimeError(err.strip() or out.strip())
        # Return the CLI's response when it carries metadata, else a synthetic ack.
        try:
            parsed = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict) and parsed:
            return parsed
        return {"status": "deleted", "person_id": person_id}

    def health_check(self) -> bool:
        cmd = self._build_cmd("calendar", "list")
        rc, _, _ = self._run(cmd, timeout=20)
        return rc == 0