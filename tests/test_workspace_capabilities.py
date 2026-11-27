#!/usr/bin/env python3
"""Tests for workspace_capabilities.py."""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SHARED_SCRIPTS = PLUGIN_ROOT / "shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))


class TestCapabilities:
    def test_google_api_capabilities(self):
        from workspace_capabilities import get_capabilities
        caps = get_capabilities("google_api")
        assert caps["gmail.search"] is True
        assert caps["gmail.draft"] is True         # SA REST drafts.create (v0.3.15)
        assert caps["gmail.send"] is True          # supported but destructive
        assert caps["calendar.create"] is True
        assert caps["drive.upload"] is True
        assert caps["files.untrash"] is True       # SA REST files.update trashed=False (v0.3.17)
        assert caps["drive.untrash"] is True

    def test_supports(self):
        from workspace_capabilities import supports
        assert supports("google_api", "gmail.send") is True
        assert supports("google_api", "drive.upload") is True
        assert supports("google_api", "files.untrash") is True
        assert supports("m365", "files.untrash") is False
        assert supports("google_api", "calendar.cancel") is True
        assert supports("m365", "mail.move") is True

    def test_unsupported_actions(self):
        from workspace_capabilities import unsupported_actions
        google_unsup = unsupported_actions("google_api")
        assert "gmail.draft" not in google_unsup
        assert "gmail.send" not in google_unsup
        assert "calendar.cancel" not in google_unsup
        assert "files.trash" not in google_unsup
        assert "files.upload" not in google_unsup

    def test_unknown_provider_returns_empty(self):
        from workspace_capabilities import get_capabilities, supports
        assert get_capabilities("unknown") == {}
        assert supports("unknown", "anything") is False

    def test_all_actions(self):
        from workspace_capabilities import all_actions
        actions = all_actions()
        assert "gmail.search" in actions

    def test_workflow_requirements_exist(self):
        from workspace_capabilities import WORKFLOW_REQUIREMENTS
        assert "document.handoff" in WORKFLOW_REQUIREMENTS
        assert "meeting.gather" in WORKFLOW_REQUIREMENTS
        assert "weekly.collect" in WORKFLOW_REQUIREMENTS

    def test_unsupported_reasons_exist(self):
        from workspace_capabilities import UNSUPPORTED_REASONS
        assert ("google_api", "gmail.draft") not in UNSUPPORTED_REASONS
        assert ("google_api", "calendar.cancel") not in UNSUPPORTED_REASONS
        assert ("google_api", "files.upload") not in UNSUPPORTED_REASONS
        assert ("google_api", "files.trash") not in UNSUPPORTED_REASONS

    def test_provider_recommendations_exist(self):
        from workspace_capabilities import PROVIDER_RECOMMENDATIONS
        assert "google_api" in PROVIDER_RECOMMENDATIONS["gmail.draft"]
        assert "google_api" in PROVIDER_RECOMMENDATIONS["document.handoff"]
