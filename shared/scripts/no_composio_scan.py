#!/usr/bin/env python3
"""Fail if the removed workspace provider is referenced anywhere in code.

WP1 removed the Composio provider (paid hosted connector — breaks the
open-source rule). This scan is the regression gate: it searches the whole
repository for the provider's name, package, or API host and fails when any
hit appears in executable code, configuration, skills, or tests.

Historical documents are reported separately and do not fail the scan:
``docs/``, ``CHANGELOG.md``, ``README.md``, ``BRIEF.md`` and ``.hermes/``
record what the upstream project was; they are not executable surface.

Usage:
    python shared/scripts/no_composio_scan.py [--root PATH]
Exit code 0 = clean, 1 = in-scope references found.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_PATTERN = re.compile(r"composio", re.IGNORECASE)

_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
# The scanner and its test necessarily name the thing they hunt.
_SKIP_FILES = {"no_composio_scan.py", "test_no_composio_scan.py"}
# Historical / documentation trees: reported, never failing.
_DOC_PREFIXES = ("docs/", ".hermes/")
_DOC_FILES = {"CHANGELOG.md", "README.md", "BRIEF.md"}
_TEXT_SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".toml", ".md", ".json", ".txt", ".cfg", ".ini"}


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if path.name in _SKIP_FILES:
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in {"plugin.yaml"}:
            continue
        yield rel, path


def scan(root: Path) -> tuple[list[str], list[str]]:
    """Return (blocking_hits, historical_hits) as 'path:line: text' strings."""
    blocking: list[str] = []
    historical: list[str] = []
    for rel, path in _iter_files(root):
        rel_posix = rel.as_posix()
        is_doc = rel_posix.startswith(_DOC_PREFIXES) or rel.name in _DOC_FILES
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, 1):
            if _PATTERN.search(line):
                entry = f"{rel_posix}:{lineno}: {line.strip()[:120]}"
                (historical if is_doc else blocking).append(entry)
    return blocking, historical


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on any reference to the removed Composio provider in code")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]),
                        help="Repository root to scan (default: this repo)")
    args = parser.parse_args(argv)
    blocking, historical = scan(Path(args.root))
    for hit in blocking:
        print(f"FAIL {hit}")
    for hit in historical:
        print(f"note (historical doc) {hit}")
    if blocking:
        print(f"\n{len(blocking)} blocking reference(s) found.")
        return 1
    print(f"OK: no references in code/config/skills/tests ({len(historical)} historical doc mention(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
