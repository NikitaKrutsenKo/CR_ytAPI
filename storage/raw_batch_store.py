from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from models.batch import Topic, CollectionInfo, YouTubeBatch
from models.raw_video import RawTopicVideo, RecentVideoStat


class RawBatchStore:
    """Append-only JSONL storage for complete raw YouTube batches."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, batch: YouTubeBatch) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(batch.to_dict(), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    def read_all(self) -> Iterator[YouTubeBatch]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    yield batch_from_dict(payload)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid raw batch at line {line_number}: {exc}") from exc


def batch_from_dict(payload: dict) -> YouTubeBatch:
    topic_raw = payload["topic"]
    collection_raw = payload["collection"]

    videos: list[RawTopicVideo] = []
    for raw_video in payload["videos"]:
        stats = [
            RecentVideoStat(
                video_id=item["video_id"],
                video_age_hours=float(item["video_age_hours"]),
                views=int(item["views"]),
            )
            for item in raw_video.get("recent_video_stats", [])
        ]
        videos.append(
            RawTopicVideo(
                video_id=raw_video["video_id"],
                channel_id=raw_video["channel_id"],
                published_at=raw_video["published_at"],
                views=int(raw_video["views"]),
                likes=int(raw_video["likes"]),
                comments=int(raw_video["comments"]),
                recent_video_count=int(raw_video.get("recent_video_count", len(stats))),
                recent_video_stats=stats,
            )
        )

    return YouTubeBatch(
        schema_version=str(payload.get("schema_version", "1.0")),
        topic=Topic(id=topic_raw["id"], name=topic_raw["name"]),
        collection=CollectionInfo(
            current_time=collection_raw["current_time"],
            batch_id=collection_raw["batch_id"],
            batch_size=int(collection_raw["batch_size"]),
        ),
        videos=videos,
    )
