"""Data models for batches, videos, and metrics snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Topic:
    """Topic metadata for YouTube data collection.

    Attributes:
        id: Unique sanitized identifier slug for the topic.
        name: Human-readable display name of the topic.
    """

    id: str
    name: str


@dataclass(frozen=True)
class CollectionInfo:
    """Metadata regarding a collection execution cycle.

    Attributes:
        current_time: UTC timestamp when collection completed.
        batch_id: Unique batch execution identifier.
        batch_size: Number of videos collected in the batch.
    """

    current_time: str
    batch_id: str
    batch_size: int


@dataclass(frozen=True)
class RecentVideoStat:
    """Historical baseline video statistics for creator normalization.

    Attributes:
        video_id: YouTube video identifier.
        video_age_hours: Age of video in hours at observation time.
        views: View count observed for the video.
    """

    video_id: str
    video_age_hours: float
    views: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize baseline video stat to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class RawTopicVideo:
    """Detailed observation of a single video belonging to a topic query.

    Attributes:
        video_id: YouTube video identifier.
        channel_id: Unique creator channel identifier.
        published_at: ISO-8601 publication timestamp.
        views: View count of the video.
        likes: Like count of the video.
        comments: Comment count of the video.
        recent_video_count: Number of baseline videos collected for this creator.
        recent_video_stats: List of baseline video statistics.
    """

    video_id: str
    channel_id: str
    published_at: str
    views: int
    likes: int
    comments: int
    recent_video_count: int
    recent_video_stats: list[RecentVideoStat]

    def to_dict(self) -> dict[str, Any]:
        """Serialize topic video record to dictionary."""
        result = asdict(self)
        result["recent_video_stats"] = [x.to_dict() for x in self.recent_video_stats]
        return result


@dataclass(frozen=True)
class YouTubeBatch:
    """Top-level batch collection bundle containing collected topic videos.

    Attributes:
        schema_version: Schema format version string (e.g. '1.0').
        topic: Topic metadata.
        collection: Execution metadata for this batch.
        videos: List of collected raw topic videos.
    """

    schema_version: str
    topic: Topic
    collection: CollectionInfo
    videos: list[RawTopicVideo]

    def to_dict(self) -> dict[str, Any]:
        """Serialize entire batch to dictionary representation."""
        return {
            "schema_version": self.schema_version,
            "topic": asdict(self.topic),
            "collection": asdict(self.collection),
            "videos": [video.to_dict() for video in self.videos],
        }


@dataclass(frozen=True)
class MetricSnapshot:
    """Computed snapshot containing Gap score, intermediate ratios, and components.

    Attributes:
        schema_version: Schema format version string.
        timestamp: Calculation timestamp in UTC.
        topic: Topic canonical name.
        batch_id: Batch identifier that generated this snapshot.
        batch_size: Number of videos in the batch.
        creator_count: Number of unique creators in the batch.
        qualifying_video_count: Number of videos qualifying for Gap calculation.
        total_views: Total sum of views across qualifying videos.
        total_likes: Total sum of likes across qualifying videos.
        total_comments: Total sum of comments across qualifying videos.
        total_engagement: Total sum of likes and comments.
        mean_view_rate: Mean view rate across batch videos.
        median_view_rate: Median view rate across batch videos.
        mean_creator_views_rate: Mean expected creator view rate.
        median_creator_views_rate: Median expected creator view rate.
        mean_creator_authority: Mean creator authority ratio.
        median_creator_authority: Median creator authority ratio.
        max_creator_authority: Maximum creator authority ratio.
        expected_views_rate_mean: Mean baseline view rate.
        expected_views_rate_median: Median baseline view rate.
        delta_hours: Elapsed hours since previous state update.
        decay_factor: Temporal decay factor applied to prior state.
        topic_rate_batch: Raw topic view rate for this batch.
        topic_rate: Exponentially smoothed topic view rate.
        er_batch: Raw engagement rate for this batch.
        er_norm: Normalized engagement rate in [0, 1].
        er_low: Lower normalization boundary for engagement rate.
        er_high: Upper normalization boundary for engagement rate.
        pr_batch: Raw performance ratio for this batch.
        pr_norm: Normalized performance ratio in [0, 1].
        pr_low: Lower normalization boundary for performance ratio.
        pr_high: Upper normalization boundary for performance ratio.
        supply_batch: Raw creator authority sum for this batch.
        supply: Smoothed creator supply value.
        supply_norm: Normalized creator supply in [0, 1].
        supply_low: Lower normalization boundary for supply.
        supply_high: Upper normalization boundary for supply.
        demand_batch: Weighted demand combining normalized ER and PR.
        demand: Smoothed demand metric.
        demand_norm: Normalized demand metric in [0, 1].
        gap_score: Final calculated Gap Score: demand_norm * (1 - supply_norm).
    """

    schema_version: str
    timestamp: str
    topic: str
    batch_id: str
    batch_size: int
    creator_count: int
    qualifying_video_count: int

    total_views: int
    total_likes: int
    total_comments: int
    total_engagement: int

    mean_view_rate: float
    median_view_rate: float
    mean_creator_views_rate: float
    median_creator_views_rate: float
    mean_creator_authority: float
    median_creator_authority: float
    max_creator_authority: float

    expected_views_rate_mean: float
    expected_views_rate_median: float
    delta_hours: float
    decay_factor: float

    topic_rate_batch: float
    topic_rate: float

    er_batch: float
    er_norm: float
    er_low: float
    er_high: float

    pr_batch: float
    pr_norm: float
    pr_low: float
    pr_high: float

    supply_batch: float
    supply: float
    supply_norm: float
    supply_low: float
    supply_high: float

    demand_batch: float
    demand: float
    demand_norm: float

    gap_score: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize snapshot to dictionary."""
        return asdict(self)
