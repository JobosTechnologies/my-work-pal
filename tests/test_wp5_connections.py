"""WP5 connections: sample Google provider, health report, config template.

The sample provider serves seeded Gmail/Calendar data through the real
WorkspaceClient surface; the health report must be honest about what a live
google_api connection still needs; the owner-facing template must declare
the least-access scopes and contain no secret values.
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shared" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import connection_health  # noqa: E402
from providers.sample_google_workspace import NAMED_FIGURE, NAMED_SENDER  # noqa: E402
from workspace_client import get_workspace_client, registered_providers  # noqa: E402

TZ = ZoneInfo("America/New_York")

SAMPLE_CONFIG = {
    "integrations": {"workspace": {"provider": "sample_google", "mode": "direct"}},
    "delivery": {"timezone": "America/New_York"},
    "google": {"account_email": "owner@moejdiamonds.example",
               "delegate_email": "owner@moejdiamonds.example"},
}


@pytest.fixture()
def client():
    return get_workspace_client(SAMPLE_CONFIG)


def _day_window(client, days_ahead=1):
    today = datetime.now(TZ).date()
    start = datetime.combine(today, time(0, 0), TZ).isoformat()
    end = datetime.combine(today + timedelta(days=days_ahead), time(23, 59), TZ).isoformat()
    return client.calendar_list(start, end)


# ── Sample provider ─────────────────────────────────────────────────────


def test_sample_provider_is_registered_and_healthy(client):
    assert "sample_google" in registered_providers()
    assert client.provider_name == "sample_google"
    assert client.health_check() is True
    assert client.acting_identity("mail") == "owner@moejdiamonds.example"
    assert client.supports("gmail.send") and client.supports("calendar.update")


def test_seeded_calendar_matches_the_pilot_seed_shape(client):
    events = _day_window(client)
    titles = [e["summary"] for e in events]
    assert any("10:00" in e["start"] or "T10:00" in e["start"] for e in events)
    assert any("Sarah Linden" in t for t in titles)          # today's viewing
    assert any("Marcus Bell" in t for t in titles)           # today's 3 PM appointment
    assert any("Aisha Rahman" in t for t in titles)          # tomorrow afternoon
    assert any("Patel Gems" in t for t in titles)            # tomorrow afternoon
    three_pm = next(e for e in events if "Marcus Bell" in e["summary"])
    assert "T15:00" in three_pm["start"]


def test_unread_inbox_has_the_three_seeded_kinds(client):
    unread = client.mail_search("is:unread")
    subjects = [m["subject"] for m in unread]
    assert any(s.startswith("URGENT") for s in subjects)
    assert any("2ct lab-grown parcel" in s for s in subjects)
    assert any("gem market" in s for s in subjects)


def test_named_email_body_carries_the_figure(client):
    hits = client.mail_search(f"from:{NAMED_SENDER}")
    assert len(hits) == 1
    assert NAMED_FIGURE in hits[0]["body"]
    assert "Thursday" in hits[0]["body"]


def test_briefing_dialect_queries_run(client):
    urgent = client.mail_search(
        'in:inbox is:unread newer_than:14d ("urgent" OR "approval" OR "deadline")')
    assert any(m["subject"].startswith("URGENT") for m in urgent)
    recent = client.mail_search("is:unread newer_than:3d label:INBOX")
    assert len(recent) == 3
    assert client.mail_search("in:sent") == []


def test_draft_is_recorded_as_draft_not_sent(client):
    # Direct provider call without the approval path: the guarded wrapper
    # requires the break-glass env the approval path sets, so use it the way
    # run_approved does.
    import os
    saved = {k: os.environ.get(k) for k in ("CHIEF_OF_STAFF_AUTO_APPROVE", "CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE")}
    os.environ["CHIEF_OF_STAFF_AUTO_APPROVE"] = "1"
    os.environ["CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE"] = "1"
    try:
        result = client.mail_create_draft("a@example.com", "Hi", "Body")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert result.get("success", True) or result.get("draft_id")
    assert client.sent == []
    assert len(client.drafts) == 1


# ── Health report ───────────────────────────────────────────────────────


def test_health_report_sample_mode_is_green():
    report = connection_health.connection_report(SAMPLE_CONFIG)
    assert report["provider"] == "sample_google"
    assert report["mode"] == "sample"
    assert report["healthy"] is True


def test_health_report_google_api_empty_config_is_honest():
    report = connection_health.connection_report(
        {"integrations": {"workspace": {"provider": "google_api"}}})
    assert report["healthy"] is False
    names = {c["name"]: c["ok"] for c in report["checks"]}
    assert names["account_email"] is False
    assert names["oauth_client"] is False       # default auth mode is oauth_user
    assert names["owner_consent"] is False
    assert report["next_steps"], "an unconfigured connection must say what is missing"


def test_health_report_service_account_mode_checks_files(tmp_path):
    cfg = {
        "integrations": {"workspace": {"provider": "google_api"}},
        "google": {
            "account_email": "owner@acme.example",
            "auth": {"mode": "service_account"},
            "service_account_path": str(tmp_path / "missing.json"),
            "domain": "acme.example",
        },
    }
    report = connection_health.connection_report(cfg)
    names = {c["name"]: c["ok"] for c in report["checks"]}
    assert names["credential_file"] is False
    assert report["healthy"] is False
    # Now place a file (contents are never read — existence only).
    (tmp_path / "missing.json").write_text("{}")
    report2 = connection_health.connection_report(cfg)
    names2 = {c["name"]: c["ok"] for c in report2["checks"]}
    assert names2["credential_file"] is True


def test_health_report_oauth_env_presence_without_values(monkeypatch):
    monkeypatch.setenv("MWP_GOOGLE_OAUTH_CLIENT_ID", "client-id-marker-123")
    monkeypatch.setenv("MWP_GOOGLE_OAUTH_CLIENT_SECRET", "SECRET-VALUE-456")
    cfg = {
        "integrations": {"workspace": {"provider": "google_api"}},
        "google": {"account_email": "owner@acme.example",
                   "auth": {"mode": "oauth_user",
                            "oauth": {"client_id_env": "MWP_GOOGLE_OAUTH_CLIENT_ID",
                                      "client_secret_env": "MWP_GOOGLE_OAUTH_CLIENT_SECRET",
                                      "token_path": "~/.myworkpal/acme/google-token.json",
                                      "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}}},
    }
    report = connection_health.connection_report(cfg)
    names = {c["name"]: c["ok"] for c in report["checks"]}
    assert names["oauth_client"] is True
    assert names["owner_consent"] is False  # no token yet — the owner has not signed in
    assert "SECRET-VALUE-456" not in str(report)  # secret values never leak into the report
    assert "client-id-marker-123" not in str(report)


def test_disconnect_removes_token_and_reports_revocation_step(tmp_path):
    token = tmp_path / "google-token.json"
    token.write_text("{}")
    cfg = {
        "integrations": {"workspace": {"provider": "google_api"}},
        "paths": {"project_root": str(tmp_path)},
        "google": {"auth": {"mode": "oauth_user", "oauth": {"token_path": str(token)}}},
    }
    result = connection_health.disconnect(cfg)
    assert result["token_file_removed"] is True
    assert not token.exists()
    assert result["audited"] is True
    assert any("Revoke" in s for s in result["remaining_steps"])


# ── Config template ─────────────────────────────────────────────────────


def test_connection_template_is_valid_and_secret_free():
    path = ROOT / "shared" / "config" / "connections.google.yaml.example"
    data = yaml.safe_load(path.read_text())
    assert data["integrations"]["workspace"]["provider"] == "google_api"
    oauth = data["google"]["auth"]["oauth"]
    scopes = set(oauth["scopes"])
    assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
    assert "https://www.googleapis.com/auth/gmail.send" in scopes
    assert "https://www.googleapis.com/auth/calendar.readonly" in scopes
    assert "https://www.googleapis.com/auth/calendar.events" in scopes
    # No secret-looking values anywhere in the template.
    text = path.read_text()
    for marker in ("private_key", "BEGIN RSA", "ya29.", "GOCSPX-"):
        assert marker not in text
    assert data["connections"]["allow_live_probe"] is False
    assert data["connections"]["activepieces"]["project_per_owner"] is True
