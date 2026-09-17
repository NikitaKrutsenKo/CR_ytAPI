from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from youtube.client import YouTubeClient


class YouTubeVideoService:
    """Deduplicate IDs within a request and chunk all reads to 50 IDs."""

    def __init__(self, client: YouTubeClient):
        self.client = client

    def get_videos(self, video_ids: Iterable[str]) -> list[dict[str, Any]]:
        return self.get_videos_batched(video_ids)

    def get_videos_batched(self, video_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(video_ids))
        result = []
        for offset in range(0, len(ids), 50):
            result.extend(
                self.client.get(
                    "videos", {"part": "snippet,statistics", "id": ",".join(ids[offset : offset + 50])}
                ).get("items", [])
            )
        return result
