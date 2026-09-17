from __future__ import annotations

from typing import Any

from youtube.client import YouTubeClient


class YouTubeSearchService:
    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def search_videos(
        self,
        query: str,
        max_results: int = 50,
        order: str = "date",
        published_after: str | None = "2026-09-16T19:13:21Z",
    ) -> list[dict[str, Any]]:
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be between 1 and 50")

        params: dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": order,
            "maxResults": max_results,
        }
        if published_after:
            params["publishedAfter"] = published_after

        response = self.client.get("search", params)
        return response.get("items", [])
