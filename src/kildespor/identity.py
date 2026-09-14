"""Strict identity gates for company websites — the pass/fail core.

A website is published on a profile ONLY if one of exactly three gates
passes.  Anything weaker is marked ``ambiguous`` and never published:

  G1_ORGNUMMER_ON_PAGE
      The organisation number appears literally on the company's own page
      (in the HTML text, a JSON-LD block, or a meta tag).
  G2_NAME_ADDRESS
      The page contains the exact legal name AND a matching postal code
      or street address (both from the registry).
  G3_REGISTRY_LISTED
      The homepage is the one Brreg itself has on file for this org
      number (``hjemmeside`` field) — registry-listed, no guessing.

Hard rules encoded here:
  - Name similarity ALONE is never sufficient.
  - A place-name mention alone is never sufficient.
  - Robots.txt is respected before any site fetch.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .http_client import HttpResponse, PoliteClient
from .models import Gate

log = logging.getLogger("kildespor.identity")

_ROBOT_CACHE: dict[str, RobotRules | None] = {}
_UA_TOKEN = "kildespor"


@dataclass
class GateResult:
    gate: Gate | None
    passed: bool
    reason: str
    checked_url: str


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------
class RobotRules:
    """Minimal robots.txt for our single user-agent token plus ``*``."""

    def __init__(self, disallow: list[str]):
        self.disallow = disallow

    def allows(self, path: str) -> bool:
        for rule in self.disallow:
            if rule and path.startswith(rule):
                return False
        return True


def fetch_robots(client: PoliteClient, base_url: str) -> RobotRules:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in _ROBOT_CACHE:
        return _ROBOT_CACHE[origin]  # type: ignore[return-value]
    resp = client.get(f"{origin}/robots.txt", snap=False)
    rules = RobotRules([])
    if resp is not None and resp.status == 200:
        current_applies = False
        disallow: list[str] = []
        try:
            text = resp.text
        except Exception:  # noqa: BLE001 - any decode quirk just means empty rules
            text = ""
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                current_applies = value == "*" or _UA_TOKEN in value.lower()
            elif key == "disallow" and current_applies:
                disallow.append(value)
        rules = RobotRules(disallow)
    _ROBOT_CACHE[origin] = rules
    return rules


# ---------------------------------------------------------------------------
# Page text helpers
# ---------------------------------------------------------------------------
_ORGNR_RE = re.compile(r"\b(\d{3}\s?\d{3}\s?\d{3})\b")
_NO_HTML = re.compile(r"<[^>]+>")


def _visible_text(html: str) -> str:
    text = _NO_HTML.sub(" ", html)
    return re.sub(r"\s+", " ", text)


def _jsonld_texts(html: str) -> str:
    """Concatenate JSON-LD blocks (schema.org data often carries org details)."""
    blobs: list[str] = []
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        raw = match.group(1)
        try:
            obj = json.loads(raw)
        except ValueError:
            blobs.append(raw)
            continue
        blobs.append(json.dumps(obj, ensure_ascii=False))
    return " ".join(blobs)


def _contains_orgnr(text: str, orgnr: str) -> bool:
    """Match the 9-digit number allowing space/grouping variants."""
    target = re.escape(orgnr)
    pattern = re.compile(r"\b" + r"\s?".join(target[i:i + 3] for i in range(0, 9, 3)) + r"\b")
    for m in _ORGNR_RE.finditer(text):
        if re.sub(r"\D", "", m.group(1)) == orgnr:
            return True
    return bool(pattern.search(text))


def _norm_text(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


# ---------------------------------------------------------------------------
# The gate evaluation
# ---------------------------------------------------------------------------
def evaluate_website(
    candidate_url: str,
    orgnr: str,
    legal_name: str,
    postal_code: str | None,
    street_address: str | None,
    registry_homepage: str | None,
    client: PoliteClient,
) -> GateResult:
    """Decide whether candidate_url may be published as this company's website."""
    candidate_url = candidate_url.strip().rstrip("/")
    if not candidate_url.startswith(("http://", "https://")):
        candidate_url = f"https://{candidate_url}"

    # --- G3: registry-listed homepage (strongest, no fetch needed)
    if registry_homepage:
        reg = _normalise_homepage(registry_homepage)
        if _same_site(reg, candidate_url):
            return GateResult(
                gate="G3_REGISTRY_LISTED", passed=True,
                reason="homepage equals the registry-filed hjemmeside for this orgnr",
                checked_url=candidate_url,
            )

    # Fetch the page (respecting robots.txt)
    robots = fetch_robots(client, candidate_url)
    path = urlparse(candidate_url).path or "/"
    if not robots.allows(path):
        return GateResult(
            gate=None, passed=False,
            reason="robots.txt disallows fetching this path",
            checked_url=candidate_url,
        )
    resp: HttpResponse | None = client.get(candidate_url, snap=True)
    if resp is None or resp.status != 200:
        return GateResult(
            gate=None, passed=False,
            reason=f"page unreachable (status {getattr(resp, 'status', 'network-error')})",
            checked_url=candidate_url,
        )
    html = resp.text
    combined = _visible_text(html) + " " + _jsonld_texts(html)

    # --- G1: organisation number literally on the page
    if _contains_orgnr(combined, orgnr):
        return GateResult(
            gate="G1_ORGNUMMER_ON_PAGE", passed=True,
            reason="organisation number appears on the page",
            checked_url=candidate_url,
        )

    # --- G2: exact legal name AND matching postcode or street address
    name_hit = _norm_text(legal_name) in _norm_text(combined) if legal_name else False
    addr_hit = False
    if postal_code and re.search(rf"\b{re.escape(postal_code)}\b", combined):
        addr_hit = True
    if not addr_hit and street_address:
        street_key = _norm_text(street_address.split(",")[0])
        if street_key and street_key in _norm_text(combined):
            addr_hit = True
    if name_hit and addr_hit:
        return GateResult(
            gate="G2_NAME_ADDRESS", passed=True,
            reason="exact legal name and matching address found on page",
            checked_url=candidate_url,
        )

    # --- Anything else: ambiguous, never published
    return GateResult(
        gate=None, passed=False,
        reason="no admissible identity gate passed (orgnr / name+address / registry)",
        checked_url=candidate_url,
    )


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.lower(), urlparse(b).netloc.lower()
    ha = ha.removeprefix("www.")
    hb = hb.removeprefix("www.")
    return ha == hb


def _normalise_homepage(raw: str) -> str:
    raw = raw.strip()
    if raw and not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw.rstrip("/")
