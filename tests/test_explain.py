"""Tests for the validator (hard-fail rules) and explanations."""
from __future__ import annotations

from datetime import date

from kildespor.explain import explain_profile, validate_profile
from kildespor.models import CompanyProfile, Fact, Source


def _src(url="https://data.brreg.no/regnskapsregisteret/regnskap/925820148", gate=None):
    return Source(source_url=url, retrieved_date=date(2026, 9, 14), evidence="x", gate=gate)


def _profile() -> CompanyProfile:
    return CompanyProfile(organisasjonsnummer="925820148")


def test_valid_profile_passes():
    p = _profile()
    p.add(Fact(field="operating_revenue", value=1000.0, source=_src()))
    p.add(Fact(
        field="website", value="https://beta.no",
        source=_src(url="https://beta.no", gate="G3_REGISTRY_LISTED"),
    ))
    assert validate_profile(p) == []


def test_fact_without_source_fails():
    p = _profile()
    # model_construct bypasses validation: simulates bad/legacy data on disk
    p.add(Fact.model_construct(field="company_name", value="Beta AS", status="ok", source=None))
    problems = validate_profile(p)
    assert any("without source" in x for x in problems)


def test_financial_from_wrong_source_fails():
    p = _profile()
    p.add(Fact(field="operating_revenue", value=1000.0, source=_src(url="https://example.com/x")))
    problems = validate_profile(p)
    assert any("not from filed accounts" in x for x in problems)


def test_website_without_gate_fails():
    p = _profile()
    p.add(Fact(field="website", value="https://beta.no", source=_src(url="https://beta.no")))
    problems = validate_profile(p)
    assert any("without admissible gate" in x for x in problems)


def test_explanations_only_use_verified_fields():
    p = _profile()
    p.add(Fact(field="company_name", value="Beta AS", source=_src()))
    lines = explain_profile(p)
    assert any("Beta AS" in line for line in lines)
    # No financials published -> no financial sentence
    assert not any("revenue" in line for line in lines)


def test_website_ambiguity_is_explained():
    p = _profile()
    p.add(Fact.unavailable("website", "no admissible identity gate passed"))
    lines = explain_profile(p)
    assert any("No website could be verified" in line for line in lines)
