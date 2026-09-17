from __future__ import annotations

from typing import Any

import requests


class YouTubeApiError(RuntimeError):
    pass


class YouTubeClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def get(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        request_params = dict(params)
        request_params["key"] = self.api_key

        response = self.session.get(
            f"{self.base_url}/{resource}",
            params=request_params,
            timeout=30,
        )

        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            raise YouTubeApiError(
                f"YouTube API request failed: {response.status_code}: {payload}"
            )

        return response.json()
