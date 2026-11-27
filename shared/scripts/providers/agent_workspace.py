#!/usr/bin/env python3
"""Agent-native workspace backend for WorkspaceClient.

This provider is a deliberate no-op fetcher. Under the "fetch/compute split",
workspace data (mail, calendar, files) is fetched by the AI agent using its
OWN tools — NOT by any Python API client — then passed to compute scripts via
``--input PATH``.

Approved read front-ends (any one is fine):

1. Native Gmail / Google Calendar / Microsoft 365 connectors in the agent host
2. Any other host connector that can produce the same record shapes
   (mail, calendar and file records in the shared schema)

Normalize every record to ``shared/scripts/schemas.py`` and hand the envelope
to daily_briefing / weekly-review / meeting-prep via ``--input``.

**Writes stay on Chief of Staff.** Do not call Gmail/Outlook *write*
tools from the agent for CoS workflows — use ``get_workspace_client(config)``
(provider ``m365`` / ``google_api``)
so ``@guarded``, the review queue, and workspace audit still apply.

Because there is no Python client to call, every mail_*/calendar_*/files_*
method here raises NotImplementedError with actionable guidance.
``health_check`` returns True (the provider is the agent itself).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure parent dir is importable for workspace_client
_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from workspace_client import WorkspaceClient

_GUIDANCE = (
    "provider 'agent': fetch workspace *reads* with the agent's own tools "
    "(native Gmail/M365 connectors in the agent host), write a JSON envelope "
    "matching shared/scripts/schemas.py, and "
    "pass it via --input to the aggregate script. For *writes* (draft/send/"
    "archive/tag/upload), use get_workspace_client(config) so Chief-of-Staff "
    "guardrails and audit still apply — do not call write tools raw from the agent."
)


class AgentWorkspaceClient(WorkspaceClient):
    """No-op workspace provider for the agent-native fetch/compute split.

    All read/write methods raise NotImplementedError with guidance pointing the
    agent at the ``--input`` JSON-envelope workflow for reads, and at
    ``get_workspace_client`` for guarded writes.
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self._provider_name = "agent"

    def _unsupported(self, method: str) -> "NotImplementedError":
        return NotImplementedError(f"{method}: {_GUIDANCE}")

    # ── Mail ───────────────────────────────────────────────────────────
    def mail_search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        raise self._unsupported("mail_search")

    def mail_create_draft(self, to: str, subject: str, body: str,
                          cc: str | None = None) -> dict[str, Any]:
        raise self._unsupported("mail_create_draft")

    def mail_send(self, to: str, subject: str, body: str,
                  cc: str | None = None) -> dict[str, Any]:
        raise self._unsupported("mail_send")

    def mail_archive(self, message_id: str) -> dict[str, Any]:
        raise self._unsupported("mail_archive")

    def mail_unarchive(self, message_id: str) -> dict[str, Any]:
        raise self._unsupported("mail_unarchive")

    def mail_trash(self, message_id: str) -> dict[str, Any]:
        raise self._unsupported("mail_trash")

    def mail_untrash(self, message_id: str) -> dict[str, Any]:
        raise self._unsupported("mail_untrash")

    def mail_list_tags(self) -> list[dict[str, Any]]:
        raise self._unsupported("mail_list_tags")

    def mail_tag(self, message_id: str, tag_id: str) -> dict[str, Any]:
        raise self._unsupported("mail_tag")

    def mail_create_tag(self, name: str) -> dict[str, Any]:
        raise self._unsupported("mail_create_tag")

    # ── Calendar ───────────────────────────────────────────────────────
    def calendar_list(self, start: str, end: str) -> list[dict[str, Any]]:
        raise self._unsupported("calendar_list")

    def calendar_create(self, title: str, start: str, end: str,
                        attendees: list[str] | None = None,
                        description: str | None = None) -> dict[str, Any]:
        raise self._unsupported("calendar_create")

    def calendar_update(self, event_id: str, **fields: Any) -> dict[str, Any]:
        raise self._unsupported("calendar_update")

    def calendar_cancel(self, event_id: str) -> dict[str, Any]:
        raise self._unsupported("calendar_cancel")

    # ── Files ──────────────────────────────────────────────────────────
    def files_search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        raise self._unsupported("files_search")

    def files_upload(self, file_path: str, parent_id: str | None = None) -> dict[str, Any]:
        raise self._unsupported("files_upload")

    def files_download(self, file_id: str, output_path: str) -> dict[str, Any]:
        raise self._unsupported("files_download")

    def files_trash(self, file_id: str) -> dict[str, Any]:
        raise self._unsupported("files_trash")

    def files_untrash(self, file_id: str) -> dict[str, Any]:
        raise self._unsupported("files_untrash")

    # ── Health ─────────────────────────────────────────────────────────
    def health_check(self) -> bool:
        """The agent provider is always available (it is the agent itself)."""
        return True
