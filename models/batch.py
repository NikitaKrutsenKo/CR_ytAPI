from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from models.raw_video import RawTopicVideo


@dataclass(frozen=True)
class Topic:
    id: str
    name: str


@dataclass(frozen=True)
class CollectionInfo:
    current_time: str
    batch_id: str
    batch_size: int


@dataclass(frozen=True)
class YouTubeBatch:
    schema_version: str
    topic: Topic
    collection: CollectionInfo
    videos: list[RawTopicVideo]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "topic": asdict(self.topic),
            "collection": asdict(self.collection),
            "videos": [video.to_dict() for video in self.videos],
        }
