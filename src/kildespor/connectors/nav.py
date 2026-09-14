"""NAV job vacancy feed connector (Arbeidsplassen.no public feed).

Verified live on 2026-09-14:
  GET {base}/api/publicToken   -> plain-text rotating public JWT
  GET {base}/api/v1/feed       -> feed page (jsonfeed), bearer auth required
  items[].url                  -> vacancy detail URL (absolute)

We use the feed to add *hiring signal* facts keyed by the employer's org
number (``json.employer.orgnr``), which NAV publishes directly — so the
match is by the official number, never by name similarity.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..http_client import PoliteClient
from ..models import Fact, Source, utc_today

log = logging.getLogger("kildespor.nav")

TOKEN_TTL_SECONDS = 3600


class NavFeedConnector:
    def __init__(self, client: PoliteClient, base: str):
        self.client = client
        self.base = base.rstrip("/")
        self._token: Optional[str] = None
        self._token_ts: float = 0.0

    # ------------------------------------------------------------------
    def _get_token(self) -> Optional[str]:
        if self._token and (time.monotonic() - self._token_ts) < TOKEN_TTL_SECONDS:
            return self._token
        resp = self.client.get(f"{self.base}/api/publicToken", snap=False)
        if resp is None or resp.status != 200:
            log.warning("Could not fetch NAV public token")
            return None
        token = resp.text.strip()
        # The endpoint returns a sentence + token; take the first JWT-ish blob
        for chunk in token.split():
            if chunk.count(".") == 2 and len(chunk) > 50:
                self._token = chunk
                self._token_ts = time.monotonic()
                return self._token
        log.warning("NAV public token not recognised in response")
        return None

    def _authed_get(self, url: str, params: Optional[dict] = None) -> Optional[Any]:
        token = self._get_token()
        if token is None:
            return None
        resp = self.client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp is None or resp.status != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    # ------------------------------------------------------------------
    def collect_jobs_by_orgnr(
        self, max_pages: int = 2, detail_budget: int = 60
    ) -> dict[str, list[dict]]:
        """Scan a bounded number of feed pages, group ACTIVE ads by employer orgnr.

        The orgnr lives in the per-ad detail payload (``json.employer.orgnr``),
        so we fetch details for at most ``detail_budget`` ads (strict request
        budget).  We ONLY accept the official orgnr from that payload —
        employer *names* are never matched, so a hit is always orgnr-exact.
        """
        by_orgnr: dict[str, list[dict]] = {}
        active_ads: list[dict] = []
        url: Optional[str] = f"{self.base}/api/v1/feed"
        pages = 0
        while url and pages < max_pages:
            data = self._authed_get(url)
            if data is None:
                break
            pages += 1
            items = data.get("items", []) if isinstance(data, dict) else []
            for item in items:
                entry = item.get("_feed_entry", {}) or {}
                if entry.get("status") != "ACTIVE":
                    continue
                active_ads.append(item)
                # Some deployments expose orgnr directly on the entry
                orgnr = str(entry.get("orgnr") or "").strip()
                if orgnr:
                    by_orgnr.setdefault(orgnr, []).append(
                        {
                            "title": entry.get("title") or item.get("title"),
                            "date_modified": item.get("date_modified"),
                        }
                    )
            nxt = data.get("next_url")
            url = f"{self.base}{nxt}" if isinstance(nxt, str) and nxt else None

        # Bounded detail pass: resolve orgnr from each ad's detail payload
        details_fetched = 0
        for item in active_ads:
            if details_fetched >= detail_budget:
                break
            detail_url = item.get("url")
            if not isinstance(detail_url, str) or not detail_url:
                continue
            detail = self._authed_get(self._abs(detail_url))
            details_fetched += 1
            if not isinstance(detail, dict):
                continue
            payload = detail.get("json") or detail.get("ad_content") or detail
            employer = payload.get("employer") or {}
            orgnr = str(employer.get("orgnr") or "").strip()
            if not orgnr:
                continue
            by_orgnr.setdefault(orgnr, []).append(
                {
                    "title": payload.get("title") or item.get("title"),
                    "date_modified": item.get("date_modified"),
                }
            )

        log.info(
            "NAV feed: %d pages, %d active ads, %d details fetched, %d orgnr matched",
            pages, len(active_ads), details_fetched, len(by_orgnr),
        )
        return by_orgnr

    def _abs(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"{self.base}{url}"

    # ------------------------------------------------------------------
    def hiring_facts(self, orgnr: str, jobs: list[dict]) -> list[Fact]:
        """Build hiring-signal facts for one company from its ads."""
        if not jobs:
            return []
        today = utc_today()
        feed_url = f"{self.base}/api/v1/feed"
        titles = [str(j.get("title") or "").strip() for j in jobs if j.get("title")]
        latest = max((str(j.get("date_modified") or "") for j in jobs), default="")
        return [
            Fact(
                field="active_job_postings",
                value=len(jobs),
                source=Source(
                    source_url=feed_url,
                    retrieved_date=today,
                    evidence="items[].url -> json.employer.orgnr",
                ),
            ),
            Fact(
                field="job_posting_titles",
                value=titles[:10],
                source=Source(
                    source_url=feed_url,
                    retrieved_date=today,
                    evidence="json.title",
                ),
            ),
            Fact(
                field="latest_posting_date",
                value=latest or None,
                source=Source(
                    source_url=feed_url,
                    retrieved_date=today,
                    evidence="items[].date_modified",
                ),
            ),
        ]
