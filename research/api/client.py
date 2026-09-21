"""Guarded HTTP client for YouTube Data API v3 with quota tracking, timeouts, and retries."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

from research.core.domain import QuotaTelemetry
from research.core.time import iso, now_utc

log = logging.getLogger(__name__)


class YouTubeApiError(RuntimeError):
    """Sanitized API error: never includes URL, API key, response body, or sensitive parameters."""


class YouTubeQuotaExceeded(YouTubeApiError):
    """Server reported quota exhaustion (HTTP 403 quotaExceeded or dailyLimitExceeded)."""


class RequestCancelled(RuntimeError):
    """User requested cancellation of the active network operation."""


class YouTubeClient:
    """The sole HTTP communication boundary with YouTube Data API v3.

    Handles quota charging, exponential retry backoff with jitter, error sanitization,
    cancellation checks, and detailed telemetry logging.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://www.googleapis.com/youtube/v3",
        quota=None,
        profile: str = "DEFAULT",
        max_retries: int = 2,
        cancelled=lambda: False,
        sleeper=time.sleep,
        session=None,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.quota = quota
        self.profile = profile
        self.max_retries = max_retries
        self.cancelled = cancelled
        self.sleeper = sleeper
        self.telemetry: list[QuotaTelemetry] = []
        self.context: dict[str, str] = {}

    def close(self) -> None:
        """Close the underlying HTTP session connection pool."""
        self.session.close()

    def __enter__(self) -> YouTubeClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def get(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a guarded GET request against a YouTube API endpoint.

        Args:
            resource: Endpoint path (e.g. 'search', 'videos', 'channels', 'playlistItems').
            params: Query parameters excluding 'key'.

        Returns:
            JSON response payload dictionary.

        Raises:
            RequestCancelled: If cancellation flag is set.
            YouTubeQuotaExceeded: If server indicates quota is exhausted.
            YouTubeApiError: On network failures or non-retryable HTTP errors.
        """
        for attempt in range(self.max_retries + 1):
            if self.cancelled():
                raise RequestCancelled("Collection cancelled")
            if self.quota:
                self.quota.consume(resource)
            started = time.monotonic()
            status, items, retry = "network_error", 0, False
            log.info("api_request_started endpoint=%s attempt=%s profile=%s", resource, attempt, self.profile)
            try:
                response = self.session.get(
                    f"{self.base_url}/{resource}", params={**params, "key": self._api_key}, timeout=30
                )
                status = str(response.status_code)
                retry = response.status_code in (429, 500, 502, 503, 504)
                if response.status_code == 403:
                    try:
                        reasons = {
                            item.get("reason") for item in response.json().get("error", {}).get("errors", [])
                        }
                    except (ValueError, TypeError, AttributeError):
                        reasons = set()
                    if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
                        raise YouTubeQuotaExceeded(f"Server quota exhausted: {resource}") from None
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
                self.telemetry.append(
                    QuotaTelemetry(
                        resource,
                        iso(now_utc()),
                        self.profile,
                        1,
                        status,
                        items,
                        elapsed,
                        pages=int(resource == "search"),
                        **self.context,
                    )
                )
                log.info(
                    "api_request_finished endpoint=%s status=%s items=%s duration_ms=%.1f",
                    resource,
                    status,
                    items,
                    elapsed,
                )
            if retry and attempt < self.max_retries:
                log.warning("api_retry endpoint=%s attempt=%s status=%s", resource, attempt + 1, status)
                self.sleeper(min(8, 2**attempt) + random.uniform(0, 0.25))
                continue
            raise YouTubeApiError(
                f"YouTube request failed: {resource} HTTP {status}; check profile/quota"
            ) from None
        raise AssertionError("Unreachable retry state")
