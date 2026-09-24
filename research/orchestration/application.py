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
from research.core.time import now_utc
from research.metrics.calculators import MetricConfig
from research.metrics.trend import TrendConfig
from research.orchestration.execution import ExperimentRun
from research.storage.db import DatabaseQuotaManager, DatabaseStorageAdapter


class ExperimentManager:
    """Validates parameters, resolves formula snapshots, estimates quota, and runs experiments."""

    def __init__(
        self,
        root: Path,
        profiles: ApiProfiles | None = None,
        client_factory: Callable = YouTubeClient,
    ) -> None:
        self.root = Path(root)
        self.store = DatabaseStorageAdapter()
        self.profiles = profiles or ApiProfiles(self.root / ".env")
        self.client_factory = client_factory
        self.last_summary: dict | None = None

    def quota(self, config: ExperimentConfig) -> DatabaseQuotaManager:
        """Create a DatabaseQuotaManager instance backed by the PostgreSQL quota_ledger table."""
        return DatabaseQuotaManager(
            self.store,
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

        # generate a unique run id (formerly experiment path)
        run_id = "exp_" + now_utc().strftime("%Y%m%dT%H%M%S")

        # Ensure candidate topics exist in the topics table for foreign keys
        for request in config.requests:
            self.store.register_topic(
                topic_id=request.topic.topic_id,
                canonical_name=request.topic.canonical_name,
                query=request.topic.canonical_name,
                category_id=getattr(request.topic, "category", None) or "tech_ai",
            )

        client = self.client_factory(
            secret,
            quota=quota,
            profile=config.api_profile_name,
            max_retries=config.max_retries,
            cancelled=stop.is_set,
            sleeper=stop.wait,
        )
        run = ExperimentRun(
            config,
            self.store,
            run_id,
            client,
            quota,
            estimate,
            stop,
            notify,
        )
        client.cancelled = lambda: (
            stop.is_set() or (not config.endless_mode and time.monotonic() >= run.deadline)
        )
        run.execute()
        self.last_summary = run.summary
        return run_id
