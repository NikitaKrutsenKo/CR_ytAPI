"""High-level application experiment management and preflight execution."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from threading import Event

from research.api.client import YouTubeClient
from research.api.profiles import ApiProfiles
from research.api.quota import QuotaEstimate, QuotaManager, QuotaStopped
from research.core.configuration import resolve_formulas
from research.core.domain import ExperimentConfig
from research.metrics.calculators import MetricConfig
from research.metrics.trend import TrendConfig
from research.orchestration.execution import ExperimentRun
from research.storage.workspace import TopicWorkspaceManager


class ExperimentManager:
    """Validates parameters, resolves formula snapshots, estimates quota, and runs experiments."""

    def __init__(
        self,
        root: Path,
        profiles: ApiProfiles | None = None,
        client_factory: Callable = YouTubeClient,
    ) -> None:
        self.root = Path(root)
        self.store = TopicWorkspaceManager(root)
        self.profiles = profiles or ApiProfiles(self.root / ".env")
        self.client_factory = client_factory

    def quota(self, config: ExperimentConfig) -> QuotaManager:
        """Create a QuotaManager instance for the profile specified in the experiment config."""
        return QuotaManager(
            self.root / "data" / "quota.sqlite",
            config.api_profile_name,
            config.search_budget,
            config.other_budget,
            config.reserve_percent,
        )

    def estimate(self, config: ExperimentConfig) -> QuotaEstimate:
        """Calculate worst-case quota estimate for this experiment configuration."""
        return self.quota(config).estimate(config)

    def run(
        self,
        config: ExperimentConfig,
        stop: Event | None = None,
        notify: Callable[[dict], None] = lambda event: None,
    ) -> Path:
        """Validate, estimate, instantiate workspace, and execute the experiment.

        Returns:
            The Path to the completed experiment directory.
        """
        config.validate()
        config = resolve_formulas(config)
        secret = self.profiles.resolve(config.api_profile_name)
        quota = self.quota(config)
        estimate = quota.estimate(config)
        if estimate.status == "WOULD EXCEED BUDGET" and not config.endless_mode:
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
            sleeper=stop.wait,
        )
        run = ExperimentRun(config, self.store, experiment, client, quota, estimate, stop, notify)
        client.cancelled = lambda: stop.is_set() or (not config.endless_mode and time.monotonic() >= run.deadline)
        run.execute()
        return experiment
