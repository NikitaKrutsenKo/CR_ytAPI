from __future__ import annotations
import logging
import random
import time
from typing import Any
import requests
from research.domain import QuotaTelemetry, iso, now_utc

log = logging.getLogger(__name__)


class YouTubeApiError(RuntimeError):
    """Sanitized API error: never includes URL, key, response body or request params."""


class RequestCancelled(RuntimeError):
    pass


class YouTubeClient:
    """The only HTTP boundary. Guard and account for every attempt, then bounded retry."""
    def __init__(self, api_key: str, base_url: str = "https://www.googleapis.com/youtube/v3",
                 quota=None, profile="DEFAULT", max_retries=2, cancelled=lambda: False,
                 sleeper=time.sleep, session=None):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.quota, self.profile, self.max_retries = quota, profile, max_retries
        self.cancelled, self.sleeper = cancelled, sleeper
        self.telemetry: list[QuotaTelemetry] = []

    def get(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            if self.cancelled():
                raise RequestCancelled("Collection cancelled")
            if self.quota:
                self.quota.consume(resource)
            started = time.monotonic()
            status, items, retry = "network_error", 0, False
            log.info("api_request_started endpoint=%s attempt=%s profile=%s", resource, attempt, self.profile)
            try:
                response = self.session.get(f"{self.base_url}/{resource}", params={**params, "key": self._api_key}, timeout=30)
                status = str(response.status_code)
                retry = response.status_code in (429, 500, 502, 503, 504)
                if response.ok:
                    try:
                        payload = response.json()
                        if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
                            raise ValueError()
                        items = len(payload.get("items", []))
                        return payload
                    except (ValueError, TypeError):
                        raise YouTubeApiError(f"Invalid JSON response: {resource}") from None
            except (requests.Timeout, requests.ConnectionError):
                retry = True
            except requests.RequestException:
                raise YouTubeApiError(f"YouTube transport failed: {resource}") from None
            finally:
                elapsed = (time.monotonic() - started) * 1000
                self.telemetry.append(QuotaTelemetry(resource, iso(now_utc()), self.profile, 1, status, items, elapsed, pages=int(resource == "search")))
                log.info("api_request_finished endpoint=%s status=%s items=%s duration_ms=%.1f", resource, status, items, elapsed)
            if retry and attempt < self.max_retries:
                log.warning("api_retry endpoint=%s attempt=%s status=%s", resource, attempt + 1, status)
                self.sleeper(min(8, 2 ** attempt) + random.uniform(0, .25))
                continue
            raise YouTubeApiError(f"YouTube request failed: {resource} HTTP {status}; check profile/quota") from None
        raise AssertionError("Unreachable retry state")
