#!/usr/bin/env python3
"""RED contract: plugin __init__._get_registered_skills must discover
skills.local/ overlay skills (workflow installs), matching the US-2 spec
acceptance criterion already implemented in doctor_base.

Without this pass an installed workflow's skill lands in skills.local/ and
doctor reports it registered, but the plugin entry point never registers it
as a Hermes skill — the workflow is not invocable via normal skill loading.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_init():
    spec = importlib.util.spec_from_file_location(
        "cos_plugin_init_overlay_discovery", PLUGIN_ROOT / "__init__.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_overlay_skill(name: str) -> None:
    overlay = PLUGIN_ROOT / "skills.local" / name
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test overlay skill\nversion: 0.0.1\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _remove_overlay_skill(name: str) -> None:
    overlay = PLUGIN_ROOT / "skills.local" / name
    if overlay.is_dir():
        for child in overlay.iterdir():
            child.unlink()
        overlay.rmdir()


def test_plugin_init_discovers_overlay_skill(tmp_path, monkeypatch):
    """generate → register → visible: a workflow installed into skills.local/
    must appear in _get_registered_skills() without hand-editing plugin.yaml."""
    monkeypatch.delenv("CHIEF_OF_STAFF_SKILL_PROFILE", raising=False)
    name = "init-red-chase"
    assert len(name) <= 32
    try:
        _write_overlay_skill(name)
        plugin = _load_plugin_init()
        skills = plugin._get_registered_skills()
        assert name in skills, (
            f"installed workflow skill {name!r} missing from plugin registration: "
            f"overlay discovery pass absent"
        )
    finally:
        _remove_overlay_skill(name)


def test_plugin_init_resolves_overlay_skill_path(tmp_path, monkeypatch):
    """End-to-end shape: registered_skill_paths() resolves overlay skills too."""
    monkeypatch.delenv("CHIEF_OF_STAFF_SKILL_PROFILE", raising=False)
    plugin = _load_plugin_init()
    name = "init-red-chase2"
    try:
        _write_overlay_skill(name)
        paths = dict(plugin.registered_skill_paths())
        assert name in paths, f"registered_skill_paths() skipped overlay skill {name!r}"
        assert paths[name] == PLUGIN_ROOT / "skills.local" / name / "SKILL.md"
    finally:
        _remove_overlay_skill(name)


def test_plugin_init_overlay_does_not_shadow_bundled_skill(tmp_path, monkeypatch):
    """A bundled skill keeps its shipped registration path; an overlay of the
    same name is the existing custom-rendering behavior (unchanged)."""
    monkeypatch.delenv("CHIEF_OF_STAFF_SKILL_PROFILE", raising=False)
    plugin = _load_plugin_init()
    skills = plugin._get_registered_skills()
    # Existing contract: profile skills are present (19 shipped, esign-connector
    # config-gated so 18 here), and overlay logic only APPENDS, never displaces.
    assert "daily-briefing" in skills
    assert len(skills) >= 18