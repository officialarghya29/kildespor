"""Explanations and validation.

Explanations are templated sentences assembled ONLY from already-verified
fields — never free-form generation, so they cannot introduce a fact that
is not evidenced in the profile.

The validator encodes the hard-fail rules:
  - no fact without provenance
  - financial values only from the filed-accounts source
  - website facts must carry an admissible identity gate
"""
from __future__ import annotations

from typing import Any

from .models import CompanyProfile

FINANCIAL_FIELD_NAMES = {
    "operating_revenue", "operating_result", "profit_before_tax",
    "annual_result", "total_equity", "total_liabilities", "total_assets",
}

GATE_LABELS = {
    "G1_ORGNUMMER_ON_PAGE": "the organisation number was found on the company's own page",
    "G2_NAME_ADDRESS": "the page shows the exact legal name and the registered address",
    "G3_REGISTRY_LISTED": "the domain is the one the registry itself lists for this orgnr",
}


def explain_profile(p: CompanyProfile) -> list[str]:
    """Return human-readable sentences, each backed by published facts."""
    out: list[str] = []
    f = p.facts

    def val(name: str) -> Any:
        fact = f.get(name)
        return fact.value if fact and fact.status == "ok" else None

    name = val("company_name") or p.organisasjonsnummer
    form = val("organisation_form")
    industry = val("industry_description")
    muni = val("municipality")
    employees = val("employee_count")
    reg_date = val("registered_date")

    head = f"{name}"
    if form:
        head += f" is a {form}"
    if muni:
        head += f" based in {muni}"
    head += f" (orgnr {p.organisasjonsnummer})"
    if reg_date:
        head += f", registered on {reg_date}"
    out.append(head + ".")

    if industry:
        out.append(f"It is classified in industry {industry}"
                   + (f" (code {val('industry_code')})." if val("industry_code") else "."))
    if employees is not None:
        out.append(f"The registry reports {employees} employees.")

    rev = val("operating_revenue")
    year = val("fiscal_year_end")
    if rev is not None:
        msg = f"Filed accounts show operating revenue of {rev} {val('currency') or 'NOK'}"
        msg += f" for the fiscal year ending {year}" if year else ""
        out.append(msg + ". These figures come verbatim from the filed annual accounts.")
    elif f.get("annual_accounts") and f["annual_accounts"].status == "not_available":
        out.append("No filed annual accounts were available from the accounts registry.")

    site = f.get("website")
    if site and site.status == "ok" and site.source and site.source.gate:
        out.append(
            f"The website {site.value} was verified: {GATE_LABELS[site.source.gate]}."
        )
    elif site and site.status == "not_available":
        out.append("No website could be verified to belong to this company; none is published.")

    jobs = val("active_job_postings")
    if jobs:
        out.append(f"The company currently has {jobs} active job posting(s) on the NAV feed.")
    return out


def validate_profile(p: CompanyProfile) -> list[str]:
    """Return a list of violations (empty == valid). Encodes hard-fail rules."""
    problems: list[str] = []
    for field, fact in p.facts.items():
        if fact.status != "ok":
            continue
        if fact.source is None:
            problems.append(f"{field}: published without source")
        elif fact.source.snapshot_sha256 is None:
            problems.append(f"{field}: published without snapshot hash")
        if field in FINANCIAL_FIELD_NAMES:
            url = fact.source.source_url if fact.source else ""
            if "/regnskapsregisteret/regnskap/" not in url:
                problems.append(f"{field}: financial value not from filed accounts ({url})")
        if field == "website":
            gate = fact.source.gate if fact.source else None
            if gate not in GATE_LABELS:
                problems.append(f"{field}: website published without admissible gate ({gate})")
    return problems
