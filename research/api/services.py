"""Services for interacting with YouTube Search, Videos, and Channels endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from research.api.client import YouTubeClient


class YouTubeSearchService:
    """Service for querying the YouTube Data API search endpoint."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def page(
        self,
        query: str,
        max_results: int = 50,
        order: str = "date",
        published_after: str | None = None,
        published_before: str | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a single page of video search results.

        Args:
            query: Search term or topic name.
            max_results: Max items per page (1..50).
            order: Sorting order ('date', 'relevance', etc.).
            published_after: ISO-8601 publishedAfter timestamp.
            published_before: ISO-8601 publishedBefore timestamp.
            page_token: Pagination token.

        Returns:
            Raw JSON dictionary response from search/list.
        """
        if not 1 <= max_results <= 50:
            raise ValueError("max_results must be between 1 and 50")
        params: dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": order,
            "maxResults": max_results,
        }
        for name, value in (
            ("publishedAfter", published_after),
            ("publishedBefore", published_before),
            ("pageToken", page_token),
        ):
            if value:
                params[name] = value
        return self.client.get("search", params)

    def search_videos(
        self,
        query: str,
        max_results: int = 50,
        order: str = "date",
        published_after: str | None = None,
    ) -> list[dict[str, Any]]:
        """Convenience method returning the items list from the first search page."""
        return self.page(query, max_results, order, published_after).get("items", [])


class YouTubeVideoService:
    """Service for reading video details (snippet, statistics) in batched requests."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def get_videos(self, video_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Retrieve video details for an iterable of video IDs, batched in chunks of 50."""
        return self.get_videos_batched(video_ids)

    def get_videos_batched(self, video_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Deduplicate IDs and fetch video snippet and statistics in batches of 50."""
        ids = list(dict.fromkeys(video_ids))
        result = []
        for offset in range(0, len(ids), 50):
            result.extend(
                self.client.get(
                    "videos", {"part": "snippet,statistics", "id": ",".join(ids[offset : offset + 50])}
                ).get("items", [])
            )
        return result


class YouTubeChannelService:
    """Service for resolving channel uploads playlists and retrieving recent channel videos."""

    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def get_uploads_playlist_id(self, channel_id: str) -> str | None:
        """Fetch the uploads playlist ID for a given channel."""
        response = self.client.get(
            "channels",
            {
                "part": "contentDetails",
                "id": channel_id,
            },
        )
        items = response.get("items", [])
        if not items:
            return None
        return items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")

    def get_recent_video_ids(
        self,
        uploads_playlist_id: str,
        excluded_video_ids: set[str],
        required_count: int,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve recent playlist items, excluding specific IDs, up to required_count."""
        if required_count <= 0:
            return []

        selected: list[dict[str, Any]] = []
        page_token: str | None = None

        while len(selected) < required_count:
            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": min(page_size, 50),
            }
            if page_token:
                params["pageToken"] = page_token

            response = self.client.get("playlistItems", params)
            for item in response.get("items", []):
                video_id = item.get("contentDetails", {}).get("videoId")
                if not video_id or video_id in excluded_video_ids:
                    continue
                selected.append(item)
                if len(selected) >= required_count:
                    break

            page_token = response.get("nextPageToken")
            if not page_token or len(selected) >= required_count:
                break

        return selected
