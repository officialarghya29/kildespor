"""Adversarial edge-case tests: the pipeline under hostile/malformed conditions.

All HTTP is mocked — no network access in this module.
"""
from __future__ import annotations

from typing import Any

from kildespor.explain import validate_profile
from kildespor.http_client import Budget, HttpResponse, PoliteClient
from kildespor.pipeline import Pipeline


class FakeClient:
    """Stands in for PoliteClient; serves canned responses per URL prefix."""

    def __init__(self, routes: dict[str, Any], fail_after: int | None = None):
        self.routes = routes
        self.fail_after = fail_after  # return None after N requests (budget death)
        self.calls = 0
        self.budget = Budget(max_requests=10_000)

    def get(self, url: str, **_kwargs: Any) -> HttpResponse | None:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            return None
        for prefix, payload in self.routes.items():
            if url.startswith(prefix):
                body = payload(orgnr=url.rstrip("/").split("/")[-1]) if callable(payload) else payload
                if body is None:
                    return HttpResponse(url=url, status=404, content=b"")
                import json as _json

                raw = _json.dumps(body).encode()
                return HttpResponse(
                    url=url,
                    status=200,
                    content=raw,
                    content_type="application/json",
                    snapshot_sha256="e" * 64,
                )
        return HttpResponse(url=url, status=404, content=b"")


ENTITY = {
    "organisasjonsnummer": "925820148",
    "navn": "7 FJELL KJEVEORTOPEDI AS",
    "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
    "naeringskode1": {"kode": "86.230", "beskrivelse": "Tannlegetjenester"},
    "registreringsdatoEnhetsregisteret": "2020-10-22",
    "registrertIMvaregisteret": False,
    "antallAnsatte": 5,
    "forretningsadresse": {
        "adresse": ["Testgata 1"],
        "postnummer": "5003",
        "poststed": "BERGEN",
        "kommune": "BERGEN",
    },
    "hjemmeside": "example.no",
}

REGNSKAP = [
    {
        "virksomhet": {"organisasjonsnummer": "925820148"},
        "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
        "valuta": "NOK",
        "resultatregnskapResultat": {"aarsresultat": 123.0},
        "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": 456.0}},
    }
]


def _routes(**overrides: Any) -> dict[str, Any]:
    routes = {
        "https://data.brreg.no/enhetsregisteret/api/enheter/": ENTITY,
        "https://data.brreg.no/regnskapsregisteret/regnskap/": REGNSKAP,
    }
    routes.update(overrides)
    return routes


def test_happy_path_publishes_facts():
    pipe = Pipeline(client=FakeClient(_routes()))  # type: ignore[arg-type]
    p = pipe.build_profile("925820148")
    assert p.published_count >= 15
    assert validate_profile(p) == []
    assert p.get("company_name").value == "7 FJELL KJEVEORTOPEDI AS"  # type: ignore[union-attr]
    assert p.get("operating_revenue") is None or p.get("operating_revenue").status == "not_available" or p.get("operating_revenue").value == 123.0


def test_malformed_entity_payload_does_not_crash():
    broken = {"organisasjonsnummer": "925820148"}  # missing every field
    pipe = Pipeline(client=FakeClient({"https://data.brreg.no/enhetsregisteret/api/enheter/": broken}))  # type: ignore[arg-type]
    p = pipe.build_profile("925820148")
    assert p.get("company_name").status == "not_available"  # type: ignore[union-attr]
    assert validate_profile(p) == []


def test_wrong_company_regnskap_record_is_ignored():
    """A filed-accounts record with a DIFFERENT embedded orgnr must not be used."""
    impostor = [dict(REGNSKAP[0], virksomhet={"organisasjonsnummer": "111111111"})]
    pipe = Pipeline(client=FakeClient(_routes(**{"https://data.brreg.no/regnskapsregisteret/regnskap/": impostor})))  # type: ignore[arg-type]
    p = pipe.build_profile("925820148")
    assert p.get("operating_revenue") is None or p.get("operating_revenue").status == "not_available"


def test_budget_death_midrun_produces_explicit_not_available():
    """When the source dies mid-run, facts degrade to not_available, no crash."""
    pipe = Pipeline(client=FakeClient(_routes(), fail_after=0))  # type: ignore[arg-type]
    p = pipe.build_profile("925820148")
    assert p.get("company_name").status == "not_available"  # type: ignore[union-attr]
    assert pipe.stats["entity_failures"] == 1


def test_invalid_checksum_never_reaches_http():
    client = FakeClient(_routes())  # type: ignore[arg-type]
    pipe = Pipeline(client=client)  # type: ignore[arg-type]
    p = pipe.build_profile("921609699")  # fails mod-11
    assert client.calls == 0
    assert p.diagnostics.get("entity") and "checksum" in p.diagnostics["entity"]


def test_run_stops_cleanly_on_exhausted_budget():
    client = FakeClient(_routes())  # type: ignore[arg-type]
    client.budget = Budget(max_requests=3)
    pipe = Pipeline(client=client)  # type: ignore[arg-type]
    profiles = pipe.run(["925820148"] * 10)
    assert len(profiles) >= 1  # some completed
    assert client.budget.used <= 3  # budget respected


def test_website_gate_uses_registry_hash():
    pipe = Pipeline(client=FakeClient(_routes()))  # type: ignore[arg-type]
    p = pipe.build_profile("925820148")
    site = p.get("website")
    assert site is not None and site.status == "ok"
    assert site.source.gate == "G3_REGISTRY_LISTED"  # type: ignore[union-attr]
    assert site.source.snapshot_sha256 == "e" * 64  # type: ignore[union-attr]


def test_politeclient_budget_refusal():
    pc = PoliteClient.__new__(PoliteClient)
    pc.budget = Budget(max_requests=0)
    assert pc.get("https://data.brreg.no/x") is None
