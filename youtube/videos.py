from __future__ import annotations

from typing import Any, Iterable

from youtube.client import YouTubeClient


class YouTubeVideoService:
    def __init__(self, client: YouTubeClient) -> None:
        self.client = client

    def get_videos(self, video_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(video_ids))
        if not ids:
            return []
        if len(ids) > 50:
            raise ValueError("videos.list accepts at most 50 IDs per request")

        response = self.client.get(
            "videos",
            {
                "part": "snippet,statistics",
                "id": ",".join(ids),
            },
        )
        return response.get("items", [])
