"""Tests for the NAV feed connector (tier policy + hiring facts)."""
from __future__ import annotations

from kildespor.connectors.nav import NavFeedConnector


def _conn(private: str | None = None) -> NavFeedConnector:
    conn = NavFeedConnector.__new__(NavFeedConnector)
    conn.base = "https://pam-stilling-feed.nav.no"
    conn.client = None  # must never be touched in the paths these tests hit
    conn.private_token = private
    return conn


def test_public_tier_spends_no_requests():
    """Without a private token the connector must return {} WITHOUT any HTTP."""
    conn = _conn(private=None)
    assert conn.collect_jobs_by_orgnr() == {}


def test_private_tier_extracts_orgnr_from_detail_payload():
    """With a private token, orgnr comes from json.employer.orgnr (stubbed)."""
    conn = _conn(private="stub-token")

    calls: list[str] = []

    def fake_authed_get(url, params=None):
        calls.append(url)
        if url.endswith("/api/v1/feed"):
            return {
                "items": [
                    {
                        "url": "/api/v1/feedentry/abc",
                        "title": "Utvikler",
                        "date_modified": "2026-09-01T10:00:00Z",
                        "_feed_entry": {"status": "ACTIVE"},
                    },
                    {
                        "url": "/api/v1/feedentry/def",
                        "title": "Stoppet",
                        "_feed_entry": {"status": "INACTIVE"},
                    },
                ],
                "next_url": None,
            }
        if url.endswith("/feedentry/abc"):
            return {"json": {"title": "Utvikler", "employer": {"orgnr": "925820148"}}}
        raise AssertionError(f"unexpected url {url}")

    conn._authed_get = fake_authed_get  # type: ignore[method-assign]
    result = conn.collect_jobs_by_orgnr()

    assert set(result.keys()) == {"925820148"}
    assert result["925820148"][0]["title"] == "Utvikler"
    # exactly 2 calls: 1 feed page + 1 detail (inactive ad's detail skipped)
    assert len(calls) == 2


def test_ads_without_orgnr_contribute_to_no_company():
    """An ad with no employer.orgnr must never be name-matched to a company."""
    conn = _conn(private="stub-token")

    def fake_authed_get(url, params=None):
        if url.endswith("/api/v1/feed"):
            return {
                "items": [
                    {
                        "url": "/api/v1/feedentry/x",
                        "_feed_entry": {"status": "ACTIVE", "businessName": "SOME AS"},
                    }
                ],
                "next_url": None,
            }
        return {"json": {"title": "X", "employer": {}}}  # no orgnr

    conn._authed_get = fake_authed_get  # type: ignore[method-assign]
    assert conn.collect_jobs_by_orgnr() == {}


def test_hiring_facts_keyed_by_orgnr():
    conn = _conn(private="stub-token")
    jobs = [
        {"title": "Utvikler", "date_modified": "2026-09-01T10:00:00Z"},
        {"title": "Designer", "date_modified": "2026-09-05T09:00:00Z"},
    ]
    facts = conn.hiring_facts("925820148", jobs)
    by_name = {f.field: f for f in facts}
    assert by_name["active_job_postings"].value == 2
    assert by_name["latest_posting_date"].value == "2026-09-05T09:00:00Z"
    assert all(f.source is not None for f in facts)


def test_hiring_facts_empty():
    conn = _conn(private="stub-token")
    assert conn.hiring_facts("925820148", []) == []
