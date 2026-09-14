"""Budgeted, polite HTTP client with evidence snapshots.

Every outbound response is snapshotted (raw bytes + sha256) so that any
published fact can point to the literal response that supported it.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from .config import CONFIG
from .models import new_snapshot_id

log = logging.getLogger("kildespor")


@dataclass
class HttpResponse:
    url: str
    status: int
    content: bytes
    content_type: str = ""
    snapshot_id: str | None = None
    snapshot_path: str | None = None
    elapsed_ms: int = 0

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


@dataclass
class Budget:
    """Hard budget meter: refuses to exceed the configured request cap."""

    max_requests: int
    used: int = 0
    by_host: dict[str, int] = dc_field(default_factory=dict)

    def try_spend(self, host: str) -> bool:
        if self.used >= self.max_requests:
            return False
        self.used += 1
        self.by_host[host] = self.by_host.get(host, 0) + 1
        return True


class PoliteClient:
    """Single session, rate-limited, evidence-snapshotting HTTP client.

    Transient failures (network errors, 429, 5xx) are retried with backoff;
    every attempt — including retries — is charged to the request budget.
    Definitive 4xx responses (404 etc.) are never retried.
    """

    RETRYABLE_STATUSES: ClassVar[set[int]] = {429, 500, 502, 503, 504}
    MAX_ATTEMPTS: ClassVar[int] = 3

    def __init__(self, snapshot_dir: str | None = None):
        self._client = httpx.Client(
            headers={
                "User-Agent": CONFIG.user_agent,
                "Accept": "application/json",
            },
            timeout=CONFIG.timeout_seconds,
            follow_redirects=True,
        )
        self.budget = Budget(max_requests=CONFIG.max_requests)
        self.min_interval = CONFIG.min_interval_seconds
        self._last_request_ts = 0.0
        self.snapshot_dir = snapshot_dir or "data/snapshots"

    # ------------------------------------------------------------------
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        snap: bool = True,
    ) -> HttpResponse | None:
        """GET with budget enforcement, throttling, retries, and snapshots."""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            host = urlparse(url).netloc
            if not self.budget.try_spend(host):
                log.warning("Request budget exhausted; refusing to call %s", url)
                return None
            out = self._get_once(url, params=params, headers=headers)
            if out is not None and out.status not in self.RETRYABLE_STATUSES:
                if out.status >= 400:
                    log.info("HTTP %d for %s (no retry)", out.status, url)
                return out
            if attempt < self.MAX_ATTEMPTS:
                backoff = 2.0 ** (attempt - 1)  # 1s, 2s
                log.info(
                    "transient failure for %s (attempt %d/%d); retrying in %.0fs",
                    url, attempt, self.MAX_ATTEMPTS, backoff,
                )
                time.sleep(backoff)
        log.warning("giving up on %s after %d attempts", url, self.MAX_ATTEMPTS)
        return None

    def _get_once(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse | None:
        wait = self.min_interval - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)

        started = time.monotonic()
        try:
            resp = self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("HTTP error for %s: %s", url, exc)
            return None
        finally:
            self._last_request_ts = time.monotonic()
        elapsed_ms = int((time.monotonic() - started) * 1000)

        out = HttpResponse(
            url=str(resp.request.url),
            status=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type", ""),
            elapsed_ms=elapsed_ms,
        )
        if resp.status_code == 200:
            self._snapshot(out)
        return out

    # ------------------------------------------------------------------
    def _snapshot(self, resp: HttpResponse) -> None:
        raw = resp.content
        if not raw:
            return
        payload = raw
        ext = "bin"
        if "json" in resp.content_type:
            payload, ext = _normalise_json(raw), "json"
        sid = new_snapshot_id(payload)
        out_path = f"{self.snapshot_dir}/{sid}.{ext}"
        try:
            os.makedirs(self.snapshot_dir, exist_ok=True)
            with open(out_path, "wb") as fh:
                fh.write(payload)
            resp.snapshot_id = sid
            resp.snapshot_path = out_path
        except OSError as exc:
            log.warning("Could not write snapshot %s: %s", out_path, exc)

    def close(self) -> None:
        self._client.close()


def _normalise_json(raw: bytes) -> bytes:
    """Canonical JSON bytes for stable hashing (gzip transparent)."""
    try:
        text = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        obj = json.loads(text)
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    except (ValueError, OSError):
        return raw


__all__ = ["Budget", "HttpResponse", "PoliteClient"]
