from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from metrics.calculators import MetricConfig

load_dotenv()


@dataclass(frozen=True)
class CollectorConfig:
    api_key: str = field(repr=False)
    api_base_url: str = "https://www.googleapis.com/youtube/v3"
    recent_min_videos: int = 5
    recent_max_videos: int = 10
    default_search_max_results: int = 50

    @classmethod
    def from_env(cls) -> "CollectorConfig":
        from research.profiles import ApiProfiles
        profiles = ApiProfiles()
        api_key = profiles.resolve(profiles.default)
        if not api_key:
            raise ValueError(
                "YOUTUBE_API_KEY is not configured. Put it in .env or the environment."
            )
        return cls(api_key=api_key)


def metric_config_from_env() -> MetricConfig:
    return MetricConfig(
        half_life_hours=float(os.getenv("GAP_HALF_LIFE_HOURS", "12")),
        epsilon=float(os.getenv("GAP_EPSILON", "0.000001")),
        demand_weight_er=float(os.getenv("GAP_WEIGHT_ER", "0.5")),
        demand_weight_pr=float(os.getenv("GAP_WEIGHT_PR", "0.5")),
        extreme_percent=float(os.getenv("GAP_EXTREME_PERCENT", "0.10")),
    )
