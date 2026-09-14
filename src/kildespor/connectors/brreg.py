"""Connectors for the Brønnøysundregistrene (Brreg) open-data APIs.

Endpoints verified against the live services on 2026-09-14:

  Entity lookup
    GET {base}/enhetsregisteret/api/enheter/{orgnr}
  Financial key figures (filed annual accounts — the ONLY allowed source
  for any financial number)
    GET {base}/regnskapsregisteret/regnskap/{orgnr}
  Bulk universe (for deterministic sampling)
    GET {base}/enhetsregisteret/api/enheter/lastned/csv

All responses are snapshotted by the HTTP layer; every extracted fact
carries a JSON-path evidence pointer into that snapshot.

Identity safety: financial records are keyed by ``virksomhet.organisasjonsnummer``
inside the payload.  We only accept a record whose org number EXACTLY equals
the one queried — this makes wrong-company financial publication structurally
impossible.
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Any, ClassVar

from ..http_client import PoliteClient
from ..models import Fact, Source, utc_today

log = logging.getLogger("kildespor.brreg")

# ---------------------------------------------------------------------------
# Field extraction maps: model field -> (JSON path, cast)
# Entity (Enhetsregisteret) fields we publish
ENTITY_FIELDS: dict[str, str] = {
    "company_name": "navn",
    "organisation_form": "organisasjonsform.beskrivelse",
    "organisation_form_code": "organisasjonsform.kode",
    "industry_code": "naeringskode1.kode",
    "industry_description": "naeringskode1.beskrivelse",
    "registered_date": "registreringsdatoEnhetsregisteret",
    "employee_count": "antallAnsatte",
    "registered_in_vat_registry": "registrertIMvaregisteret",
    "municipality": "forretningsadresse.kommune",
    "business_address": "forretningsadresse.adresse",
    "postal_code": "forretningsadresse.postnummer",
    "postal_city": "forretningsadresse.poststed",
    "registry_homepage": "hjemmeside",
}


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path; return None if any hop is missing."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


class BrregConnector:
    def __init__(self, client: PoliteClient):
        self.client = client
        self.base = client.base_url if hasattr(client, "base_url") else "https://data.brreg.no"

    # ------------------------------------------------------------------
    # Entity lookup
    # ------------------------------------------------------------------
    def fetch_entity(self, orgnr: str) -> dict[str, Any] | None:
        url = f"{self.base}/enhetsregisteret/api/enheter/{orgnr}"
        resp = self.client.get(url)
        if resp is None or resp.status != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            log.warning("Non-JSON entity response for %s", orgnr)
            return None

    # ------------------------------------------------------------------
    # Filed financial key figures (regnskapsregisteret)
    # ------------------------------------------------------------------
    def fetch_regnskap(self, orgnr: str) -> list[dict[str, Any]] | None:
        url = f"{self.base}/regnskapsregisteret/regnskap/{orgnr}"
        resp = self.client.get(url)
        if resp is None or resp.status != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, list) else None

    def pick_regnskap(self, records: list[dict[str, Any]], orgnr: str) -> dict[str, Any] | None:
        """Choose the newest record whose OWN orgnr matches exactly.

        This is the identity guard for financial facts: a record filed by a
        different organisation (or a malformed payload) is never used.
        """
        candidates = [
            r for r in records
            if str(_dig(r, "virksomhet.organisasjonsnummer") or "") == orgnr
        ]
        if not candidates:
            return None

        def period_key(r: dict[str, Any]) -> str:
            return str(_dig(r, "regnskapsperiode.tilDato") or "")

        return max(candidates, key=period_key)

    # ------------------------------------------------------------------
    # Deterministic extraction into facts
    # ------------------------------------------------------------------
    def extract_entity_facts(
        self, orgnr: str, payload: dict[str, Any], source_url: str
    ) -> list[Fact]:
        today = utc_today()
        facts: list[Fact] = []
        for field, path in ENTITY_FIELDS.items():
            value = _dig(payload, path)
            if value in (None, "", []):
                facts.append(Fact.unavailable(field, f"not present in entity payload ({path})"))
                continue
            if field == "registry_homepage":
                value = _normalise_homepage(str(value))
            if field == "business_address" and isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            facts.append(
                Fact(
                    field=field,
                    value=value,
                    source=Source(
                        source_url=source_url,
                        retrieved_date=today,
                        evidence=path,
                        snapshot_sha256=None,  # filled by the profile builder
                    ),
                )
            )
        return facts

    FINANCIAL_FIELDS: ClassVar[dict[str, str]] = {
        "fiscal_year_end": "regnskapsperiode.tilDato",
        "operating_revenue": "resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter",
        "operating_result": "resultatregnskapResultat.driftsresultat.driftsresultat",
        "profit_before_tax": "resultatregnskapResultat.ordinaertResultatFoerSkattekostnad",
        "annual_result": "resultatregnskapResultat.aarsresultat",
        "total_equity": "egenkapitalGjeld.egenkapital.sumEgenkapital",
        "total_liabilities": "egenkapitalGjeld.gjeldOversikt.sumGjeld",
        "total_assets": "eiendeler.sumEiendeler",
        "currency": "valuta",
    }

    def extract_financial_facts(
        self, orgnr: str, record: dict[str, Any], source_url: str
    ) -> list[Fact]:
        today = utc_today()
        facts: list[Fact] = []
        for field, path in BrregConnector.FINANCIAL_FIELDS.items():
            value = _dig(record, path)
            if value is None:
                facts.append(Fact.unavailable(field, f"not disclosed in filed accounts ({path})"))
                continue
            if field == "fiscal_year_end" or field == "currency":
                facts.append(
                    Fact(
                        field=field,
                        value=value,
                        source=Source(source_url=source_url, retrieved_date=today, evidence=path),
                    )
                )
                continue
            # Numeric financial value: keep it verbatim (never rounded, never
            # derived) — only exact filed numbers are publishable.
            facts.append(
                Fact(
                    field=field,
                    value=value,
                    source=Source(source_url=source_url, retrieved_date=today, evidence=path),
                )
            )
        return facts


def _normalise_homepage(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def normalise_orgnr(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 9:
        raise ValueError(f"orgnr must be 9 digits, got {raw!r}")
    return digits


def orgnr_checksum_valid(orgnr: str) -> bool:
    """Mod-11 check digit validation for Norwegian organisation numbers.

    Weights 3,2,7,6,5,4,3,2 over the first 8 digits; K = 11 - (S mod 11);
    K == 11 maps to 0; K == 10 means the number is invalid.  Checking this
    BEFORE any request avoids spending budget on typo'd/fabricated orgnrs.
    """
    orgnr = normalise_orgnr(orgnr)
    digits = [int(c) for c in orgnr]
    weights = [3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(d * w for d, w in zip(digits[:8], weights))
    r = s % 11
    k = 11 - r
    if k == 11:
        k = 0
    if k == 10:
        return False
    return k == digits[8]


# ---------------------------------------------------------------------------
# Bulk universe (deterministic sampling)
# ------------------------------------------------------------------
def parse_bulk_csv(content: str, limit: int | None = None) -> list[dict[str, str]]:
    """Parse the enhetsregisteret bulk CSV into plain dicts."""
    reader = csv.DictReader(io.StringIO(content))
    rows = []
    for i, row in enumerate(reader):
        if limit is not None and i >= limit:
            break
        rows.append(row)
    return rows
