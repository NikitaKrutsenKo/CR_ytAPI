from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from metrics.calculators import MetricConfig
from research.domain import ExperimentConfig
from research.execution import ExperimentRun
from research.profiles import ApiProfiles
from research.quota import QuotaEstimate, QuotaManager, QuotaStopped
from research.storage import TopicWorkspaceManager
from research.trend import TrendConfig
from youtube.client import YouTubeClient


class ExperimentManager:
    """Validate and estimate a run before allocating storage or calling YouTube."""

    def __init__(self, root: Path, profiles: ApiProfiles | None = None, client_factory=YouTubeClient):
        self.root, self.store = Path(root), TopicWorkspaceManager(root)
        self.profiles = profiles or ApiProfiles(self.root / ".env")
        self.client_factory = client_factory

    def quota(self, config: ExperimentConfig) -> QuotaManager:
        return QuotaManager(
            self.root / "data" / "quota.sqlite",
            config.api_profile_name,
            config.search_budget,
            config.other_budget,
            config.reserve_percent,
        )

    def estimate(self, config: ExperimentConfig) -> QuotaEstimate:
        return self.quota(config).estimate(config)

    def run(
        self,
        config: ExperimentConfig,
        stop: Event | None = None,
        notify: Callable[[dict], None] = lambda event: None,
    ) -> Path:
        config.validate()
        secret = self.profiles.resolve(config.api_profile_name)
        quota = self.quota(config)
        estimate = quota.estimate(config)
        if estimate.status == "WOULD EXCEED BUDGET":
            raise QuotaStopped("Experiment would exceed configured quota budget")
        MetricConfig(**config.gap_formula).validate()
        TrendConfig(**config.trend_formula)
        stop = stop or Event()
        experiment = self.store.create(config)
        client = self.client_factory(
            secret,
            quota=quota,
            profile=config.api_profile_name,
            max_retries=config.max_retries,
            cancelled=stop.is_set,
        )
        run = ExperimentRun(config, self.store, experiment, client, quota, estimate, stop, notify)
        run.execute()
        return experiment
