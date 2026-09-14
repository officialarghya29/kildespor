"""Kildespor configuration.

All knobs come from environment variables so the pipeline is reproducible
from one command with no hidden state.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    # Source endpoints (verified 2026-09-14 against live services)
    brreg_base: str = os.environ.get("KILDESPIR_BRREG_BASE", "https://data.brreg.no")
    nav_feed_base: str = os.environ.get(
        "KILDESPIR_NAV_FEED_BASE", "https://pam-stilling-feed.nav.no"
    )

    # Budget (challenge limits: 2,000 outbound requests / 45 min / $10 per daily run)
    max_requests: int = _int_env("KILDESPIR_MAX_REQUESTS", 2000)
    timeout_seconds: float = float(
        os.environ.get("KILDESPIR_TIMEOUT_SECONDS", "20") or 20
    )

    # Refresh policy
    min_refresh_days: int = _int_env("KILDESPIR_MIN_REFRESH_DAYS", 3)

    # Politeness
    min_interval_seconds: float = 0.3  # >= 2 req/s ceiling against Brreg
    user_agent: str = (
        "kildespor/1.0 (github.com/officialarghya29/kildespor; "
        "open-data research agent)"
    )

    # Snapshot retention for evidence (raw bytes are hashed for provenance)
    keep_snapshot_bytes: bool = True

    data_dir: str = os.environ.get("KILDESPIR_DATA_DIR", "data")

    @classmethod
    def from_env(cls) -> "Config":
        return cls()


CONFIG = Config.from_env()
