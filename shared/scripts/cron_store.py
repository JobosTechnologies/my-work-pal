#!/usr/bin/env python3
"""Standalone cron jobs store: ``<data home>/cron/jobs.json``.

The core runs without any host runtime, so scheduled jobs live in a plain
JSON file the operator's scheduler (or a future service layer) reads. The
file holds either a bare list of job dicts or ``{"jobs": [...]}``; each job
carries at least ``id``/``name``, ``schedule`` (cron expression), ``prompt``,
``skills`` and ``deliver``. Writes replace a job with the same id/name, so
re-installing is idempotent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def jobs_file() -> Path:
    from config_loader import get_hermes_home

    return get_hermes_home() / "cron" / "jobs.json"


def load_jobs(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the jobs store. Missing file -> empty list."""
    target = path or jobs_file()
    if not target.is_file():
        return []
    data = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("jobs", [])
    if not isinstance(data, list):
        raise ValueError("expected a list of jobs")
    return [job for job in data if isinstance(job, dict)]


def _save_jobs(jobs: list[dict[str, Any]], path: Path | None = None) -> Path:
    target = path or jobs_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"jobs": jobs}
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = {**existing, "jobs": jobs}
        except Exception:
            pass
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def _job_key(job: dict[str, Any]) -> str:
    for key in ("id", "name"):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def upsert_job(job: dict[str, Any], path: Path | None = None) -> Path:
    """Insert or replace a job (matched by id, else name). Returns the store path."""
    key = _job_key(job)
    if not key:
        raise ValueError("cron job needs an id or name")
    jobs = [j for j in load_jobs(path) if _job_key(j) != key]
    jobs.append(dict(job))
    return _save_jobs(jobs, path)


def remove_job(job_id: str, path: Path | None = None) -> bool:
    """Remove a job by id/name. Returns True when one was removed."""
    jobs = load_jobs(path)
    kept = [j for j in jobs if _job_key(j) != job_id]
    if len(kept) == len(jobs):
        return False
    _save_jobs(kept, path)
    return True
