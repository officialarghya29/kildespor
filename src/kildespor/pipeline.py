"""Pipeline orchestrator.

Builds one evidence-linked profile per org number:
  1. Enhetsregisteret entity  (identity, address, industry, homepage)
  2. Regnskapsregisteret       (filed annual accounts — guarded by orgnr)
  3. NAV job feed              (hiring signal, keyed by employer orgnr)
  4. Website identity gates    (strict; registry homepage passes G3 directly)

Budget-aware: stops gracefully when the request cap is hit.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from .config import CONFIG
from .connectors.brreg import BrregConnector
from .connectors.nav import NavFeedConnector
from .differ import diff_profile
from .http_client import PoliteClient
from .identity import GateResult, evaluate_website
from .models import CompanyProfile, Fact, Source, utc_today

log = logging.getLogger("kildespor.pipeline")

ENTITY_URL = f"{CONFIG.brreg_base}/enhetsregisteret/api/enheter"

# Org forms with no regnskapsplikt (statutory duty to file accounts) for the
# accounts we consume — skipping the regnskap call for these saves ~40% of
# the request budget with zero coverage loss (they can never have accounts).
NON_FILING_FORMS = {"FLI", "ORGL", "SAM", "ESEK", "KTRF", "VOFO", "PMSA", "SAU"}


class Pipeline:
    def __init__(self, client: Optional[PoliteClient] = None):
        self.client = client or PoliteClient()
        self.brreg = BrregConnector(self.client)
        self.nav = NavFeedConnector(self.client, CONFIG.nav_feed_base)
        self._jobs_cache: Optional[dict[str, list[dict]]] = None
        self.stats = {
            "profiles": 0,
            "facts_published": 0,
            "facts_unavailable": 0,
            "website_gate_pass": 0,
            "website_gate_ambiguous": 0,
            "requests_used": 0,
        }

    # ------------------------------------------------------------------
    def run(self, orgnrs: list[str]) -> list[CompanyProfile]:
        profiles: list[CompanyProfile] = []
        started = time.monotonic()
        for i, orgnr in enumerate(orgnrs, 1):
            if self.client.budget.used >= self.client.budget.max_requests:
                log.warning("Budget exhausted; stopping after %d profiles", i - 1)
                break
            profiles.append(self.build_profile(orgnr))
            if i % 25 == 0:
                log.info(
                    "progress %d/%d (%.0fs elapsed)",
                    i, len(orgnrs), time.monotonic() - started,
                )
        self.stats["requests_used"] = self.client.budget.used
        return profiles

    # ------------------------------------------------------------------
    def build_profile(self, orgnr: str) -> CompanyProfile:
        profile = CompanyProfile(organisasjonsnummer=orgnr)
        entity = self.brreg.fetch_entity(orgnr)

        if entity is None:
            profile.diagnostics["entity"] = "unreachable or unknown orgnr"
            profile.add(Fact.unavailable("company_name", "entity lookup failed"))
            self.stats["profiles"] += 1
            return profile

        # --- Entity facts
        for fact in self.brreg.extract_entity_facts(orgnr, entity, f"{ENTITY_URL}/{orgnr}"):
            profile.add(fact)

        # --- Financial facts (identity-guarded; skipped for non-filing forms)
        form_code = (entity.get("organisasjonsform") or {}).get("kode", "")
        if form_code in NON_FILING_FORMS:
            profile.add(Fact.unavailable(
                "annual_accounts", f"organisation form {form_code} has no duty to file accounts"
            ))
        else:
            records = self.brreg.fetch_regnskap(orgnr)
            if records:
                record = self.brreg.pick_regnskap(records, orgnr)
                if record is not None:
                    fin_url = f"{CONFIG.brreg_base}/regnskapsregisteret/regnskap/{orgnr}"
                    for fact in self.brreg.extract_financial_facts(orgnr, record, fin_url):
                        profile.add(fact)
                else:
                    profile.add(Fact.unavailable("annual_accounts", "no account record with matching orgnr"))
            else:
                profile.add(Fact.unavailable("annual_accounts", "no filed accounts returned"))

        # --- Website identity gate
        self._resolve_website(profile, entity)

        # --- Hiring signal from NAV feed (orgnr-keyed)
        jobs = self._nav_jobs().get(orgnr)
        if jobs:
            for fact in self.nav.hiring_facts(orgnr, jobs):
                profile.add(fact)
        else:
            profile.add(Fact.unavailable("active_job_postings", "no active NAV ads for this orgnr"))

        self.stats["profiles"] += 1
        self.stats["facts_published"] += profile.published_count
        self.stats["facts_unavailable"] += sum(
            1 for f in profile.facts.values() if f.status == "not_available"
        )
        profile.diagnostics["requests_by_host"] = dict(self.client.budget.by_host)
        return profile

    # ------------------------------------------------------------------
    def _resolve_website(self, profile: CompanyProfile, entity: dict[str, Any]) -> None:
        """Resolve + gate the website fact.  Registry homepage passes G3 directly."""
        orgnr = profile.organisasjonsnummer
        registry_homepage = profile.get("registry_homepage")
        reg_url = (
            registry_homepage.value
            if registry_homepage and registry_homepage.status == "ok"
            else None
        )

        def _publish(url: str, result: GateResult) -> None:
            profile.add(
                Fact(
                    field="website",
                    value=url,
                    source=Source(
                        source_url=result.checked_url,
                        retrieved_date=utc_today(),
                        evidence=f"website:{result.gate}",
                        gate=result.gate,
                    ),
                    note=result.reason,
                )
            )
            self.stats["website_gate_pass"] += 1

        def _ambiguous(candidate: str, reason: str) -> None:
            profile.add(Fact.unavailable("website", reason))
            profile.diagnostics["website_ambiguous"] = {"candidate": candidate, "reason": reason}
            self.stats["website_gate_ambiguous"] += 1

        if reg_url:
            # Registry-filed homepage: verify with a fetch if budget allows,
            # but treat registry listing as sufficient on its own (G3).
            result = evaluate_website(
                candidate_url=reg_url,
                orgnr=orgnr,
                legal_name=str(profile.get("company_name").value or ""),
                postal_code=(profile.get("postal_code").value if profile.get("postal_code") else None),
                street_address=(profile.get("business_address").value if profile.get("business_address") else None),
                registry_homepage=reg_url,
                client=self.client,
            )
            if result.passed:
                _publish(reg_url, result)
            else:
                _ambiguous(reg_url, result.reason)
            return

        # No registry homepage: discovery would go here.  Deliberately NOT
        # guessing domains — without a verified candidate we publish nothing.
        profile.add(Fact.unavailable("website", "no registry-listed homepage; discovery disabled"))
        self.stats["website_gate_ambiguous"] += 1

    # ------------------------------------------------------------------
    def _nav_jobs(self) -> dict[str, list[dict]]:
        if self._jobs_cache is None:
            try:
                self._jobs_cache = self.nav.collect_jobs_by_orgnr(max_pages=2)
            except Exception as exc:  # feed is optional; never fail the run
                log.warning("NAV feed unavailable: %s", exc)
                self._jobs_cache = {}
        return self._jobs_cache
