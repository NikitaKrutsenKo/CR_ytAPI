from __future__ import annotations

from typing import Any

from youtube.client import YouTubeClient


class YouTubeSearchService:
    """Fetch one bounded page; the discovery collector owns pagination and dedupe."""

    def __init__(self, client: YouTubeClient):
        self.client = client

    def page(
        self,
        query: str,
        max_results=50,
        order="date",
        published_after=None,
        published_before=None,
        page_token=None,
    ) -> dict[str, Any]:
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be between 1 and 50")
        params = {"part": "snippet", "q": query, "type": "video", "order": order, "maxResults": max_results}
        for name, value in (
            ("publishedAfter", published_after),
            ("publishedBefore", published_before),
            ("pageToken", page_token),
        ):
            if value:
                params[name] = value
        return self.client.get("search", params)

    def search_videos(self, query, max_results=50, order="date", published_after=None):
        return self.page(query, max_results, order, published_after).get("items", [])
