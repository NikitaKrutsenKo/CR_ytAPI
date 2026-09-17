from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MetricSnapshot:
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
        return asdict(self)
