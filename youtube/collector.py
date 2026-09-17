from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from config import CollectorConfig
from models.batch import CollectionInfo, Topic, YouTubeBatch
from models.raw_video import RawTopicVideo, RecentVideoStat
from youtube.channels import YouTubeChannelService
from youtube.client import YouTubeClient
from youtube.search import YouTubeSearchService
from youtube.videos import YouTubeVideoService


from models.time_utils import age_hours
def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class YouTubeCollector:
    def __init__(self, config: CollectorConfig) -> None:
        client = YouTubeClient(config.api_key, config.api_base_url)
        self.search = YouTubeSearchService(client)
        self.videos = YouTubeVideoService(client)
        self.channels = YouTubeChannelService(client)
        self.config = config

    def collect(
        self,
        topic_name: str,
        max_videos: int | None = None,
        current_time: datetime | None = None,
    ) -> YouTubeBatch:
        now = current_time or datetime.now(timezone.utc)
        limit = max_videos or self.config.default_search_max_results
        limit = min(limit, 50)

        search_items = self.search.search_videos(
            query=topic_name,
            max_results=limit,
            order="date",
        )
        candidate_ids = [
            item.get("id", {}).get("videoId")
            for item in search_items
            if item.get("id", {}).get("videoId")
        ]

        detailed_items = self.videos.get_videos(candidate_ids)
        detailed_by_id = {item["id"]: item for item in detailed_items}
        excluded_ids = set(candidate_ids)

        creators: dict[str, dict[str, Any]] = {}
        for item in detailed_items:
            snippet = item.get("snippet", {})
            channel_id = snippet.get("channelId")
            if channel_id:
                creators.setdefault(channel_id, {})[item["id"]] = item

        videos: list[RawTopicVideo] = []

        for channel_id, topic_videos_by_id in creators.items():
            # We need up to 10 videos AFTER excluding every video in the current thematic batch.
            recent_items = self._get_recent_other_videos(
                channel_id=channel_id,
                excluded_video_ids=excluded_ids,
            )
            recent_video_ids = [
                item["contentDetails"]["videoId"]
                for item in recent_items
                if item.get("contentDetails", {}).get("videoId")
            ]
            recent_details = self.videos.get_videos(recent_video_ids)
            recent_by_id = {item["id"]: item for item in recent_details}

            if len(recent_by_id) < self.config.recent_min_videos:
                continue

            recent_stats: list[RecentVideoStat] = []
            for recent_item in recent_items:
                video_id = recent_item["contentDetails"]["videoId"]
                detail = recent_by_id.get(video_id)
                if not detail:
                    continue
                snippet = detail.get("snippet", {})
                published_at = snippet.get("publishedAt")
                if not published_at:
                    continue
                recent_stats.append(
                    RecentVideoStat(
                        video_id=video_id,
                        video_age_hours=age_hours(published_at, now),
                        views=as_int(detail.get("statistics", {}).get("viewCount")),
                    )
                )
                if len(recent_stats) >= self.config.recent_max_videos:
                    break

            if len(recent_stats) < self.config.recent_min_videos:
                continue

            for item in topic_videos_by_id.values():
                video_id = item["id"]
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                videos.append(
                    RawTopicVideo(
                        video_id=video_id,
                        channel_id=channel_id,
                        published_at=snippet.get("publishedAt", ""),
                        views=as_int(stats.get("viewCount")),
                        likes=as_int(stats.get("likeCount")),
                        comments=as_int(stats.get("commentCount")),
                        recent_video_count=len(recent_stats),
                        recent_video_stats=recent_stats.copy(),
                    )
                )

        current_time_iso = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        batch_id = f"{_slug(topic_name)}_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        return YouTubeBatch(
            schema_version="1.0",
            topic=Topic(id=_slug(topic_name), name=topic_name),
            collection=CollectionInfo(
                current_time=current_time_iso,
                batch_id=batch_id,
                batch_size=len(videos),
            ),
            videos=videos,
        )

    def _get_recent_other_videos(
        self,
        channel_id: str,
        excluded_video_ids: set[str],
    ) -> list[dict[str, Any]]:
        uploads_id = self.channels.get_uploads_playlist_id(channel_id)
        if not uploads_id:
            return []

        # Fetch up to 30 candidates per page cycle by using the playlist API's max 50.
        # Pagination continues until 10 non-excluded videos are collected or the playlist ends.
        return self.channels.get_recent_video_ids(
            uploads_playlist_id=uploads_id,
            excluded_video_ids=excluded_video_ids,
            required_count=self.config.recent_max_videos,
        )


def _slug(value: str) -> str:
    cleaned = "_".join(value.strip().lower().split())
    return "".join(ch for ch in cleaned if ch.isalnum() or ch in {"_", "-"}) or "topic"
