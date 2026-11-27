"""WP5 flow: the whole connection path on sample data, in seven steps.

health → brief (four parts through the provider) → named email → draft
queued unsent → signed approval → guarded send (once) → approved calendar
move → audit. Nothing leaves the machine and no credentials exist.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shared" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import wp5_flow  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "real-hermes-home"
    user_home = tmp_path / "user-home"
    for d in (home, user_home, tmp_path / "hermes-env-home"):
        d.mkdir()
    monkeypatch.setenv("CHIEF_OF_STAFF_HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-env-home"))
    monkeypatch.setenv("HOME", str(user_home))
    return home


def _run() -> tuple[int, str]:
    lines: list[str] = []
    rc = wp5_flow.run_wp5_flow(out=lines.append)
    return rc, "\n".join(lines)


def test_wp5_flow_runs_all_seven_steps():
    rc, out = _run()
    assert rc == 0
    headers = re.findall(r"^Step (\d)/7 — (.+)$", out, re.M)
    assert [int(n) for n, _ in headers] == list(range(1, 8))


def test_wp5_flow_proves_the_gate_properties():
    rc, out = _run()
    assert rc == 0
    # The named email's figure was read from the body, not invented.
    assert "USD 4,250" in out
    # The draft sat in the queue unsent before approval.
    assert "Outbox is still empty" in out
    # One send; the duplicate attempt was refused at the claim stage.
    assert "Second attempt refused at 'claim'" in out
    # The appointment exists once, at the new time.
    assert "exactly one copy exists" in out
    # Both actions executed and audited.
    assert "send: state executed" in out
    assert "move: state executed" in out
