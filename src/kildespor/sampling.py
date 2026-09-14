"""Deterministic sampling of the company universe.

`bootstrap` downloads the official Enhetsregisteret bulk CSV once;
`sample_orgnrs` then picks a reproducible, seed-stable sample so the
exact same 1,000+ profiles can be regenerated from the exact commit.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import logging
import os
import random
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("kildespor.sampling")

BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"

GZIP_MAGIC = b"\x1f\x8b"


def _is_gzip(path: str) -> bool:
    with open(path, "rb") as fh:
        return fh.read(2) == GZIP_MAGIC


def download_bulk_csv(out_path: str, timeout: float = 600.0) -> str:
    """Stream the bulk entity CSV to disk (one big request, no snapshot).

    The endpoint serves a gzip stream regardless of Accept-Encoding, so the
    payload is transparently decompressed before the final write.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path + ".part"
    with httpx.stream(
        "GET",
        BULK_URL,
        headers={"User-Agent": "kildespor/1.0 (open-data research agent)"},
        timeout=timeout,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            fh.writelines(resp.iter_bytes(1 << 20))

    if _is_gzip(tmp):
        raw_size = 0
        with gzip.open(tmp, "rb") as src, open(out_path, "wb") as dst:
            for chunk in iter(lambda: src.read(1 << 22), b""):
                raw_size += len(chunk)
                dst.write(chunk)
        os.remove(tmp)
        log.info("bulk CSV decompressed: %.0f MB raw", raw_size / 1e6)
    else:
        os.replace(tmp, out_path)
    log.info("bulk CSV written to %s", out_path)
    return out_path


def _find_orgnr_column(fieldnames: list[str]) -> str:
    for name in fieldnames:
        if name and "organisasjonsnummer" in name.lower():
            return name
    raise ValueError(f"no organisasjonsnummer column in {fieldnames!r}")


def _find_form_column(fieldnames: list[str]) -> Optional[str]:
    for name in fieldnames:
        if name and "organisasjonsform" in name.lower():
            return name
    return None


# Organisation forms that are not trading companies in the challenge's sense
# (sole proprietors, associations, public org units...).  They never have
# filed accounts and almost never a website — sampling them wastes ~45%% of
# a run's coverage budget.  They remain fully supported at profile time.
LOW_VALUE_FORMS = {
    "ENK",   # enkeltpersonforetak (sole proprietor)
    "ESEK",  # enkeltselskap
    "FLI",   # forening/lag/innretning
    "ORGL",  # organisasjonsledd
    "SAM",   # sameie
    "KTRF",  # kontorfellesskap
    "VOFO",  # veldedig/allmennyttig organisasjon
    "SAU",   # sau
    "PMSA",  # partrederi med selskapsansvar
}


def _sniff_delimiter(sample: str) -> str:
    """Detect the CSV dialect (the bulk file has historically used ';' or ',')."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ";"  # historical Brreg default


def iter_universe(csv_path: str, with_forms: bool = False):
    """Yield 9-digit org numbers (optionally ``(orgnr, form_code)`` pairs).

    Gzip-transparent; works with both the headerful bulk CSV variants.
    """
    opener = gzip.open if _is_gzip(csv_path) else open
    with opener(csv_path, "rt", encoding="utf-8-sig", newline="") as fh:  # type: ignore[operator]
        first = fh.readline()
        if not first:
            raise ValueError("empty bulk CSV")
        delimiter = _sniff_delimiter(first)
        fh.seek(0)
        reader = csv.DictReader(fh, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("empty bulk CSV header")
        col = _find_orgnr_column(reader.fieldnames)
        form_col = _find_form_column(reader.fieldnames) if with_forms else None
        for row in reader:
            orgnr = (row.get(col) or "").strip()
            if len(orgnr) != 9 or not orgnr.isdigit():
                continue
            if with_forms:
                form = (row.get(form_col) or "").strip() if form_col else ""
                yield orgnr, form
            else:
                yield orgnr


def sample_orgnrs(
    csv_path: str,
    n: int,
    seed: str = "kildespor-v1",
    exclude_forms: Optional[set[str]] = None,
) -> tuple[list[str], dict]:
    """Reproducible sample: stable ordering + seeded RNG.

    ``exclude_forms`` drops organisation forms that cannot yield filed
    accounts or websites (sole proprietors, associations, ...), matching the
    challenge's own company-universe definition.  Returns
    (sorted orgnr list, manifest dict recording how it was drawn).
    """
    universe: set[str] = set()
    forms_seen: dict[str, int] = {}
    for orgnr, form in iter_universe(csv_path, with_forms=True):
        forms_seen[form or "?"] = forms_seen.get(form or "?", 0) + 1
        if exclude_forms and form in exclude_forms:
            continue
        universe.add(orgnr)
    universe = sorted(universe)
    rng = random.Random(hashlib.sha256(seed.encode()).hexdigest())
    if n >= len(universe):
        picked = universe
    else:
        picked = rng.sample(universe, n)
    picked.sort()
    manifest = {
        "source": BULK_URL,
        "seed": seed,
        "universe_size_total": sum(forms_seen.values()),
        "universe_size_after_filter": len(universe),
        "excluded_forms": sorted(exclude_forms) if exclude_forms else [],
        "forms_in_universe": dict(sorted(forms_seen.items(), key=lambda kv: -kv[1])[:12]),
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
