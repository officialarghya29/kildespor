"""Tests for the differ (typed update records)."""
from __future__ import annotations

from datetime import date

from kildespor.differ import diff_profile, summarise_changes
from kildespor.models import CompanyProfile, Fact, Source


def _fact(value, when=date(2026, 9, 1)):
    return Fact(
        field="x",
        value=value,
        source=Source(source_url="https://x", retrieved_date=when, evidence="x"),
    )


def _profile(facts: dict):
    p = CompanyProfile(organisasjonsnummer="925820148")
    for name, f in facts.items():
        f.field = name
        p.add(f)
    return p


def test_no_changes_when_identical():
    prev = _profile({"a": _fact("Alpha AS"), "b": _fact(5)})
    curr = _profile({"a": _fact("Alpha AS"), "b": _fact(5)})
    assert diff_profile(prev, curr) == []


def test_changed_value():
    prev = _profile({"employee_count": _fact(5)})
    curr = _profile({"employee_count": _fact(7)})
    changes = diff_profile(prev, curr)
    assert len(changes) == 1
    assert changes[0]["type"] == "changed_value"
    assert changes[0]["was"] == 5 and changes[0]["now"] == 7


def test_became_available_and_unavailable():
    prev = _profile({
        "website": Fact.unavailable("website", "none"),
        "revenue": _fact(100),
    })
    curr = _profile({
        "website": _fact("https://alpha.no"),
        "revenue": Fact.unavailable("revenue", "not filed"),
    })
    changes = diff_profile(prev, curr)
    types = {c["field"]: c["type"] for c in changes}
    assert types["website"] == "became_available"
    assert types["revenue"] == "became_unavailable"


def test_new_fact_and_retraction():
    prev = _profile({"a": _fact(1)})
    curr = _profile({"b": _fact(2)})
    changes = diff_profile(prev, curr)
    types = {c["field"]: c["type"] for c in changes}
    assert types["a"] == "retracted"
    assert types["b"] == "new_fact"


def test_evidence_refresh_only_notes_date():
    prev = _profile({"a": _fact("same", when=date(2026, 9, 1))})
    curr = _profile({"a": _fact("same", when=date(2026, 9, 10))})
    changes = diff_profile(prev, curr)
    assert len(changes) == 1
    assert changes[0]["type"] == "evidence_refreshed"
    assert changes[0]["field_value_unchanged"] is True


def test_summary_counts():
    prev = _profile({"a": _fact(1), "b": _fact(2)})
    curr = _profile({"a": _fact(3), "b": _fact(4)})
    counts = summarise_changes(diff_profile(prev, curr))
    assert counts == {"changed_value": 2}
