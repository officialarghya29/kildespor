"""Deterministic sampling of the company universe.

`bootstrap` downloads the official Enhetsregisteret bulk CSV once;
`sample_orgnrs` then picks a reproducible, seed-stable sample so the
exact same 1,000+ profiles can be regenerated from the exact commit.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
import random
from pathlib import Path

import httpx

log = logging.getLogger("kildespor.sampling")

BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def download_bulk_csv(out_path: str, timeout: float = 300.0) -> str:
    """Stream the bulk entity CSV to disk (one big request, no snapshot)."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream(
        "GET",
        BULK_URL,
        headers={"User-Agent": "kildespor/1.0 (open-data research agent)"},
        timeout=timeout,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        tmp = out_path + ".part"
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    os.replace(tmp, out_path)
    log.info("bulk CSV written to %s", out_path)
    return out_path


def _find_orgnr_column(fieldnames: list[str]) -> str:
    for name in fieldnames:
        if name and "organisasjonsnummer" in name.lower():
            return name
    raise ValueError(f"no organisasjonsnummer column in {fieldnames!r}")


def iter_universe(csv_path: str):
    """Yield 9-digit org numbers from the bulk CSV."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError("empty bulk CSV")
        col = _find_orgnr_column(reader.fieldnames)
        for row in reader:
            orgnr = (row.get(col) or "").strip()
            if len(orgnr) == 9 and orgnr.isdigit():
                yield orgnr


def sample_orgnrs(csv_path: str, n: int, seed: str = "kildespor-v1") -> tuple[list[str], dict]:
    """Reproducible sample: stable ordering + seeded RNG.

    Returns (sorted orgnr list, manifest dict recording how it was drawn).
    """
    universe = sorted(set(iter_universe(csv_path)))
    rng = random.Random(hashlib.sha256(seed.encode()).hexdigest())
    if n >= len(universe):
        picked = universe
    else:
        picked = rng.sample(universe, n)
    picked.sort()
    manifest = {
        "source": BULK_URL,
        "seed": seed,
        "universe_size": len(universe),
        "sample_size": len(picked),
        "csv_sha256": _sha256_file(csv_path),
    }
    return picked, manifest


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
