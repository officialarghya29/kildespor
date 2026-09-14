"""Tests for financial extraction + the orgnr identity guard."""
from __future__ import annotations

import pytest

from kildespor.connectors.brreg import (
    BrregConnector,
    _dig,
    _normalise_homepage,
    normalise_orgnr,
)


RECORD_A = {
    "virksomhet": {"organisasjonsnummer": "925820148"},
    "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
    "resultatregnskapResultat": {
        "driftsresultat": {
            "driftsinntekter": {"sumDriftsinntekter": 9626777.0},
            "driftsresultat": 2921986.0,
        },
        "aarsresultat": 2295210.0,
    },
    "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": 67845.0}},
}

RECORD_B_OLDER = {
    "virksomhet": {"organisasjonsnummer": "925820148"},
    "regnskapsperiode": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"},
    "resultatregnskapResultat": {"aarsresultat": 1000.0},
}

RECORD_OTHER_COMPANY = {
    "virksomhet": {"organisasjonsnummer": "999999999"},
    "regnskapsperiode": {"fraDato": "2027-01-01", "tilDato": "2027-12-31"},
    "resultatregnskapResultat": {"aarsresultat": 999999.0},
}


def test_pick_prefers_newest_matching():
    picked = BrregConnector.pick_regnskap(
        None, [RECORD_B_OLDER, RECORD_A, RECORD_OTHER_COMPANY], "925820148"
    )
    assert picked is RECORD_A


def test_pick_never_crosses_companies():
    """A newer record filed by a DIFFERENT orgnr must never be selected."""
    picked = BrregConnector.pick_regnskap(None, [RECORD_OTHER_COMPANY], "925820148")
    assert picked is None


def test_financial_fact_extraction():
    facts = BrregConnector.extract_financial_facts(
        None, "925820148", RECORD_A, "https://data.brreg.no/regnskapsregisteret/regnskap/925820148"
    )
    by_name = {f.field: f for f in facts}
    assert by_name["operating_revenue"].value == 9626777.0
    assert by_name["annual_result"].value == 2295210.0
    assert by_name["total_equity"].value == 67845.0
    assert by_name["fiscal_year_end"].value == "2025-12-31"
    # Every published fact carries provenance
    for fact in facts:
        if fact.status == "ok":
            assert fact.source is not None
            assert "regnskapsregisteret" in fact.source.source_url


def test_dig_paths():
    assert _dig(RECORD_A, "virksomhet.organisasjonsnummer") == "925820148"
    assert _dig(RECORD_A, "missing.path") is None


def test_orgnr_normalisation():
    assert normalise_orgnr("925 820-148") == "925820148"
    with pytest.raises(ValueError):
        normalise_orgnr("12345")


def test_homepage_normalisation():
    assert _normalise_homepage("brreg.no") == "https://brreg.no"
    assert _normalise_homepage("https://brreg.no/") == "https://brreg.no"
