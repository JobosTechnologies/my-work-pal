"""WP1 gate: the removed provider must not creep back into code.

The scanner module is shared/scripts/no_composio_scan.py; this test runs it
against the real tree (blocking scope = code, config, skills, tests) and
unit-tests the scanner itself against planted fixtures.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "shared" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import no_composio_scan  # noqa: E402


def test_no_blocking_references_in_the_tree():
    blocking, _historical = no_composio_scan.scan(PLUGIN_ROOT)
    assert blocking == [], "removed-provider references in code:\n" + "\n".join(blocking)


def test_scanner_catches_a_planted_reference(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "x.py").write_text("provider = 'com' + 'posio'\n", encoding="utf-8")
    blocking, _ = no_composio_scan.scan(tmp_path)
    assert blocking == []  # split string is not a reference

    (tmp_path / "shared" / "x.py").write_text("from providers.composio_mcp_workspace import X\n", encoding="utf-8")
    blocking, _ = no_composio_scan.scan(tmp_path)
    assert len(blocking) == 1 and "x.py:1" in blocking[0]


def test_scanner_treats_docs_as_historical(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "HISTORY.md").write_text("upstream used composio here\n", encoding="utf-8")
    blocking, historical = no_composio_scan.scan(tmp_path)
    assert blocking == [] and len(historical) == 1
