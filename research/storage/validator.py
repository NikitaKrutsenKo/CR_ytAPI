"""Validation utilities for batches, snapshots, and metrics CSV exports."""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable
from pathlib import Path

from research.core.models import MetricSnapshot, YouTubeBatch


class ValidationError(ValueError):
    """Validation constraint violation error."""


def validate_batch(batch: YouTubeBatch, min_recent_videos: int = 5, max_recent_videos: int = 10) -> None:
    """Validate structural and numerical integrity of a YouTubeBatch."""
    if batch.collection.batch_size != len(batch.videos):
        raise ValidationError("batch_size does not match the number of videos")

    seen_video_ids: set[str] = set()
    for video in batch.videos:
        if video.video_id in seen_video_ids:
            raise ValidationError(f"Duplicate thematic video_id: {video.video_id}")
        seen_video_ids.add(video.video_id)

        if not (min_recent_videos <= video.recent_video_count <= max_recent_videos):
            raise ValidationError(
                f"Video {video.video_id}: recent_video_count must be in "
                f"[{min_recent_videos}, {max_recent_videos}]"
            )
        if len(video.recent_video_stats) != video.recent_video_count:
            raise ValidationError(f"Video {video.video_id}: recent_video_count mismatch")

        recent_ids = {item.video_id for item in video.recent_video_stats}
        if video.video_id in recent_ids:
            raise ValidationError(f"Thematic video {video.video_id} appears in recent_video_stats")
        if any(item.views < 0 or item.video_age_hours < 0 for item in video.recent_video_stats):
            raise ValidationError(f"Video {video.video_id}: invalid recent video values")


def validate_snapshot(snapshot: MetricSnapshot) -> None:
    """Validate finiteness and [0, 1] range bounds of a MetricSnapshot."""
    numeric = snapshot.to_dict()
    for key, value in numeric.items():
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            raise ValidationError(f"Non-finite metric: {key}")

    bounded = {
        "er_norm": snapshot.er_norm,
        "pr_norm": snapshot.pr_norm,
        "supply_norm": snapshot.supply_norm,
        "demand_norm": snapshot.demand_norm,
        "gap_score": snapshot.gap_score,
    }
    for key, value in bounded.items():
        if not 0.0 <= value <= 1.0:
            raise ValidationError(f"{key} is outside [0, 1]: {value}")


def validate_metrics_csv(path: str | Path, required_columns: Iterable[str]) -> tuple[int, list[str]]:
    """Verify that a metrics CSV file exists, has required columns, and strictly increasing timestamps."""
    target = Path(path)
    if not target.exists():
        raise ValidationError(f"Metrics CSV does not exist: {target}")

    with target.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        missing = [column for column in required_columns if column not in columns]
        if missing:
            raise ValidationError(f"Missing metrics CSV columns: {missing}")

        count = 0
        previous_timestamp = None
        for row_number, row in enumerate(reader, start=2):
            timestamp = row["timestamp"]
            if previous_timestamp is not None and timestamp <= previous_timestamp:
                raise ValidationError(
                    f"Timestamps must be strictly increasing; row {row_number} is not"
                )
            previous_timestamp = timestamp
            for column in columns:
                if column in {"timestamp", "topic"}:
                    continue
                try:
                    value = float(row[column])
                except (TypeError, ValueError) as exc:
                    raise ValidationError(
                        f"Non-numeric value in column {column}, row {row_number}"
                    ) from exc
                if not math.isfinite(value):
                    raise ValidationError(f"Non-finite value in {column}, row {row_number}")
            count += 1

    return count, columns
