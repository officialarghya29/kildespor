"""Pydantic models for Kildespor profiles and evidence-linked facts.

Design rule enforced here: a fact *cannot* be published without source
metadata.  Every published value must carry:
  - source_url     : the exact endpoint the value came from
  - retrieved_date : ISO date the value was fetched
  - evidence       : pointer to the snapshotted raw response + JSON path
  - gate           : identity gate used (required for website facts)
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

FactValue = Any

# The three admissible identity gates for a company website.
Gate = Literal["G1_ORGNUMMER_ON_PAGE", "G2_NAME_ADDRESS", "G3_REGISTRY_LISTED"]


class Source(BaseModel):
    """Provenance for one published fact."""

    source_url: str
    retrieved_date: date
    # JSON path inside the snapshotted response that supports the value,
    # e.g. "resultatregnskapResultat.aarsresultat"
    evidence: str
    # sha256 of the raw response bytes at retrieval time
    snapshot_sha256: Optional[str] = None
    # Identity gate (website facts only)
    gate: Optional[Gate] = None

    @model_validator(mode="after")
    def _website_requires_gate(self) -> "Source":
        if self.evidence.startswith("website") and self.gate is None:
            raise ValueError("website facts must carry an identity gate")
        return self


class Fact(BaseModel):
    """One field on a company profile, always with its provenance."""

    field: str
    value: Optional[FactValue] = None
    status: Literal["ok", "not_available"] = "ok"
    source: Optional[Source] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def _status_consistency(self) -> "Fact":
        if self.status == "ok" and self.source is None:
            raise ValueError(f"fact {self.field!r}: published facts need a source")
        return self

    @classmethod
    def unavailable(cls, field: str, note: str) -> "Fact":
        return cls(field=field, status="not_available", note=note)


class CompanyProfile(BaseModel):
    """Full profile for one organisation number."""

    organisasjonsnummer: str
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    facts: dict[str, Fact] = Field(default_factory=dict)
    # Facts from the previous run that differ now (filled by the differ)
    changes: list[dict[str, Any]] = Field(default_factory=list)
    # Non-published diagnostics: request budget spent, warnings
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    def add(self, fact: Fact) -> None:
        self.facts[fact.field] = fact

    def get(self, field: str) -> Optional[Fact]:
        return self.facts.get(field)

    @property
    def published_count(self) -> int:
        return sum(1 for f in self.facts.values() if f.status == "ok")


def new_snapshot_id(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def utc_today() -> date:
    return datetime.now(timezone.utc).date()
