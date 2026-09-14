"""Profile persistence: JSON files per run, so the next run can diff."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .models import CompanyProfile


def _serialize(obj):  # json default hook
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"not serializable: {type(obj)}")


def save_profiles(profiles: list[CompanyProfile], out_dir: str) -> str:
    """Write one JSON file per profile; returns the run directory."""
    run_dir = Path(out_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    for p in profiles:
        path = run_dir / f"{p.organisasjonsnummer}.json"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(p.model_dump_json(indent=2))
    return str(run_dir)


def load_profiles(profiles_dir: str) -> dict[str, CompanyProfile]:
    """Load a directory of profiles keyed by orgnr (missing dir -> empty)."""
    out: dict[str, CompanyProfile] = {}
    d = Path(profiles_dir)
    if not d.is_dir():
        return out
    for path in d.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out[data["organisasjonsnummer"]] = CompanyProfile.model_validate(data)
        except (ValueError, KeyError, OSError):
            continue
    return out


def latest_run_dir(base_dir: str, exclude: Optional[str] = None) -> Optional[str]:
    """Find the most recent run_* directory under base_dir (for diffing)."""
    base = Path(base_dir)
    if not base.is_dir():
        return None
    runs = sorted(
        (p for p in base.iterdir() if p.is_dir() and p.name.startswith("run_")),
        key=lambda p: p.name,
    )
    if exclude:
        runs = [r for r in runs if str(r) != exclude]
    return str(runs[-1]) if runs else None
