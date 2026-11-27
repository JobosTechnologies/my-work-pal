#!/usr/bin/env python3
"""Tests for workspace guardrails — write action confirmation and ActionResult."""

import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SHARED_SCRIPTS = PLUGIN_ROOT / "shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))


@pytest.fixture(autouse=True)
def clean_env():
    """Remove guardrail env vars before each test."""
    for key in ("CHIEF_OF_STAFF_AUTO_APPROVE", "CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE"):
        os.environ.pop(key, None)
    yield
    for key in ("CHIEF_OF_STAFF_AUTO_APPROVE", "CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE"):
        os.environ.pop(key, None)


class TestIsWriteAction:
    def test_gmail_draft_is_write(self):
        from workspace_guardrails import is_write_action
        assert is_write_action("gmail.draft") is True

    def test_calendar_create_is_write(self):
        from workspace_guardrails import is_write_action
        assert is_write_action("calendar.create") is True

    def test_drive_upload_is_write(self):
        from workspace_guardrails import is_write_action
        assert is_write_action("drive.upload") is True

    def test_gmail_search_is_not_write(self):
        from workspace_guardrails import is_write_action
        assert is_write_action("gmail.search") is False

    def test_calendar_list_is_not_write(self):
        from workspace_guardrails import is_write_action
        assert is_write_action("calendar.list") is False


class TestRequiresConfirmation:
    def test_destructive_always_requires(self):
        from workspace_guardrails import requires_confirmation
        os.environ["CHIEF_OF_STAFF_AUTO_APPROVE"] = "1"
        assert requires_confirmation("gmail.send") is True
        assert requires_confirmation("calendar.delete") is True
        assert requires_confirmation("drive.delete") is True

    def test_safe_write_no_confirmation_with_auto_approve(self):
        from workspace_guardrails import requires_confirmation
        os.environ["CHIEF_OF_STAFF_AUTO_APPROVE"] = "1"
        assert requires_confirmation("gmail.draft") is False
        assert requires_confirmation("calendar.create") is False
        assert requires_confirmation("drive.upload") is False

    def test_safe_write_requires_confirmation_without_auto_approve(self):
        from workspace_guardrails import requires_confirmation
        os.environ.pop("CHIEF_OF_STAFF_AUTO_APPROVE", None)
        assert requires_confirmation("gmail.draft") is True

    def test_read_actions_never_require_confirmation(self):
        from workspace_guardrails import requires_confirmation
        assert requires_confirmation("gmail.search") is False
        assert requires_confirmation("calendar.list") is False
        assert requires_confirmation("drive.search") is False


class TestConfirmAction:
    def test_read_action_always_proceeds(self):
        from workspace_guardrails import confirm_action
        assert confirm_action("gmail.search") is True

    def test_destructive_blocked_without_allow_env(self):
        from workspace_guardrails import confirm_action
        os.environ["CHIEF_OF_STAFF_AUTO_APPROVE"] = "1"
        assert confirm_action("gmail.send") is False

    def test_destructive_allowed_with_allow_env(self):
        from workspace_guardrails import confirm_action
        os.environ["CHIEF_OF_STAFF_AUTO_APPROVE"] = "1"
        os.environ["CHIEF_OF_STAFF_ALLOW_DESTRUCTIVE"] = "1"
        assert confirm_action("gmail.send") is True

    def test_safe_write_proceeds_with_auto_approve(self):
        from workspace_guardrails import confirm_action
        os.environ["CHIEF_OF_STAFF_AUTO_APPROVE"] = "1"
        assert confirm_action("gmail.draft", to="test@test.com") is True

    def test_safe_write_blocked_without_auto_approve_non_tty(self):
        from workspace_guardrails import confirm_action
        # In test env, stdin is not a TTY
        assert confirm_action("gmail.draft", to="test@test.com") is False


class TestActionResult:
    def test_success_result(self):
        from workspace_guardrails import ActionResult
        result = ActionResult(
            success=True,
            action="gmail.draft",
            provider="m365",
            tool_slug="GMAIL_CREATE_EMAIL_DRAFT",
            target="test@test.com",
            data={"id": "draft_1"},
            audited=True,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["action"] == "gmail.draft"
        assert d["provider"] == "m365"
        assert d["tool_slug"] == "GMAIL_CREATE_EMAIL_DRAFT"
        assert d["target"] == "test@test.com"
        assert d["data"]["id"] == "draft_1"
        assert d["audited"] is True
        assert d["error"] is None

    def test_error_result(self):
        from workspace_guardrails import ActionResult
        result = ActionResult(
            success=False,
            action="calendar.create",
            provider="m365",
            error="Connection failed",
            audited=True,
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["error"] == "Connection failed"

    def test_default_values(self):
        from workspace_guardrails import ActionResult
        result = ActionResult(success=True)
        d = result.to_dict()
        assert d["action"] == ""
        assert d["data"] == {}
        assert d["audited"] is False


