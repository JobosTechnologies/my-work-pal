#!/usr/bin/env python3
"""Tests for workspace write actions — Gmail draft, Calendar create/update, Drive upload/download.

Provider action tests live alongside this file.
This file covers:
- Provider capability reporting (google_api, m365)
- Google provider write methods (mocked subprocess)
- Cross-provider capability checks
"""

import sys
import os
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SHARED_SCRIPTS = PLUGIN_ROOT / "shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))


class TestProviderCapabilities:
    def test_google_supports(self):
        from providers.google_workspace import GoogleWorkspaceClient
        with patch("providers.google_workspace._find_google_api_script", return_value=Path("/fake")):
            client = GoogleWorkspaceClient({"google": {}})
        assert client.provider_name == "google_api"
        assert client.supports("gmail.send") is True
        assert client.supports("calendar.create") is True

    def test_capabilities_matrix_unknown_spelling_empty(self):
        """An unregistered provider spelling has no capabilities."""
        from workspace_capabilities import get_capabilities
        assert get_capabilities("google_api:sdk") == {}


class TestGoogleProviderWriteActions:
    """Google provider write methods — mocked subprocess, ActionResult shape."""

    @pytest.fixture
    def google_client(self):
        os.environ["CHIEF_OF_STAFF_AUTO_APPROVE"] = "1"
        from providers.google_workspace import GoogleWorkspaceClient
        with patch("providers.google_workspace._find_google_api_script", return_value=Path("/fake")):
            return GoogleWorkspaceClient({"google": {"delegate_email": "test@test.com", "account_alias": "test"},
                                          "paths": {"project_root": "/tmp/test-workspace-actions"}})

    def test_gmail_create_draft_via_sa_rest(self, google_client):
        """gmail_create_draft uses SA REST when service_account_path is set."""
        google_client.config = {
            "google": {
                "delegate_email": "test@test.com",
                "account_alias": "test",
                "service_account_path": "/fake/sa.json",
            }
        }
        google_client.delegate_email = "test@test.com"
        with patch(
            "providers.google_workspace._gmail_draft_via_service_account",
            return_value={"id": "msg-1", "draft_id": "r-1", "message_id": "msg-1"},
        ) as mock_draft:
            result = google_client.gmail_create_draft("a@test.com", "Subject", "Body")
        assert result["success"] is True
        assert result["data"]["id"] == "msg-1"
        mock_draft.assert_called_once()

    def test_calendar_create(self, google_client):
        """calendar.create uses SA REST; patch the REST helper (old _run CLI path removed)."""
        google_client.config = {
            "google": {
                "delegate_email": "test@test.com",
                "account_alias": "test",
                "service_account_path": "/fake/sa.json",
            }
        }
        with patch(
            "providers.google_workspace._calendar_create_via_service_account",
            return_value={"id": "evt_1", "hangoutLink": None},
        ):
            result = google_client.calendar_create("Meeting", "2026-07-10", "2026-07-10")
        assert result["success"] is True
        assert result["data"]["id"] == "evt_1"

    def test_calendar_update(self, google_client):
        with patch.object(google_client, "_run", return_value=(0, '{"id": "evt_1"}', "")):
            result = google_client.calendar_update("evt_1", title="Updated")
        assert result["success"] is True
        assert result["data"]["id"] == "evt_1"

    def test_drive_download(self, google_client):
        with patch.object(google_client, "_run", return_value=(0, "", "")):
            result = google_client.drive_download("file_123", "/tmp/test-workspace-actions/downloads/downloaded.pdf")
        assert result["success"] is True
        assert result["data"]["path"] == "/tmp/test-workspace-actions/downloads/downloaded.pdf"