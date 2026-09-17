from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RecentVideoStat:
    video_id: str
    video_age_hours: float
    views: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RawTopicVideo:
    video_id: str
    channel_id: str
    published_at: str
    views: int
    likes: int
    comments: int
    recent_video_count: int
    recent_video_stats: list[RecentVideoStat]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["recent_video_stats"] = [x.to_dict() for x in self.recent_video_stats]
        return result
