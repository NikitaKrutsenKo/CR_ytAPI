from __future__ import annotations

from typing import Any

from youtube.client import YouTubeClient


class YouTubeChannelService:
    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def get_uploads_playlist_id(self, channel_id: str) -> str | None:
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

        return (
            items[0]
            .get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )

    def get_recent_video_ids(
        self,
        uploads_playlist_id: str,
        excluded_video_ids: set[str],
        required_count: int,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
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
