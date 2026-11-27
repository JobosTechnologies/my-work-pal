#!/usr/bin/env python3
"""Regression tests for the final v0.3.8 release-hardening review."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SHARED_SCRIPTS = PLUGIN_ROOT / "shared" / "scripts"
PROVIDERS = SHARED_SCRIPTS / "providers"
for path in (SHARED_SCRIPTS, PROVIDERS, PLUGIN_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _ok(data):
    return {"data": {"results": [{"response": {"successful": True, "data": data}}]}}


def test_rebootstrap_preserves_custom_wiki_and_staging_paths(tmp_path):
    import bootstrap

    args = SimpleNamespace(
        project_root=None,
        company=None,
        operator=None,
        operator_name=None,
    )
    current = {
        "company": {"website": "https://real.example"},
        "user": {"name": "Rina", "email": "rina@real.example"},
        "google": {
            "domain": "real.example",
            "delegate_email": "rina@real.example",
            "account_alias": "real",
            "service_account_path": "/secure/google.json",
            "drive_root_folder_id": "root-123",
        },
        "paths": {
            "project_root": str(tmp_path / "project"),
            "wiki_path": str(tmp_path / "knowledge-base"),
            "staging": str(tmp_path / "incoming"),
        },
    }

    overlay, _notices = bootstrap._identity_overlay(args, current)
    assert overlay["paths"]["project_root"] == current["paths"]["project_root"]
    assert overlay["paths"]["wiki_path"] == current["paths"]["wiki_path"]
    assert overlay["paths"]["staging"] == current["paths"]["staging"]


def test_legacy_manifestless_routing_overlay_is_migrated(monkeypatch, tmp_path):
    import bootstrap

    skills = tmp_path / "skills"
    overlay = tmp_path / "skills.local"
    slug = "daily-briefing"
    shipped = skills / slug / "SKILL.md"
    shipped.parent.mkdir(parents=True)
    body = "# Daily Briefing\n\nBody retained from the shipped skill.\n"
    shipped.write_text(
        "---\nname: daily-briefing\ndescription: Shipped description\n---\n" + body,
        encoding="utf-8",
    )

    generated_description = (
        bootstrap.ROUTING_DESCRIPTION_TEMPLATES[slug]
        .replace("{assistant_name}", "Ada")
        .replace("{company_name}", "Real Ops Pte Ltd")
    )
    generated = overlay / slug / "SKILL.md"
    generated.parent.mkdir(parents=True)
    generated.write_text(
        "---\nname: daily-briefing\ndescription: "
        + json.dumps(generated_description)
        + "\n---\n"
        + body,
        encoding="utf-8",
    )

    manual = overlay / "manual-skill" / "SKILL.md"
    manual.parent.mkdir(parents=True)
    manual.write_text(
        "---\nname: manual-skill\ndescription: Manual override\n---\n# Manual\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(bootstrap, "SKILLS_DIR", skills)
    assert not (overlay / bootstrap.ROUTING_OVERLAY_MANIFEST).exists()

    bootstrap._cleanup_routing_overlays(overlay)

    assert not generated.exists()
    assert manual.exists()


def test_docuseal_opener_disables_environment_proxies(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")

    import doctor

    opener = doctor._docuseal_opener()
    proxy_handlers = [
        handler for handler in opener.handlers
        if isinstance(handler, urllib.request.ProxyHandler)
    ]
    # build_opener intentionally omits an empty ProxyHandler. Its absence, plus
    # the lack of proxy_open methods, proves environment proxies are disabled.
    assert proxy_handlers == []
    assert not any(hasattr(handler, "proxy_open") for handler in opener.handlers)
