"""Offline replay of stored raw observations through pure metric calculation engines."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from research.core.domain import CollectionBundle
from research.metrics.tracking import TrackedVideoRegistry
from research.metrics.trend import TrendConfig, TrendEngine
from research.storage.db import DatabaseStorageAdapter

log = logging.getLogger(__name__)


class ReplayService:
    """Re-executes calculations over stored raw batches with zero network access."""

    def __init__(self, db: DatabaseStorageAdapter | None = None) -> None:
        self.db = db or DatabaseStorageAdapter()

    def replay_bundles(
        self,
        bundles: list[CollectionBundle],
        trend_config: TrendConfig | None = None,
        notify: Callable[[dict[str, Any]], None] = lambda event: None,
    ) -> list[dict[str, Any]]:
        """Replay an ordered sequence of CollectionBundles through pure mathematical engines.

        Zero network calls. Deterministically computes tracking deltas, engagement,
        EWMA, velocity, acceleration, Growth, Burst, Breadth, and YouTube Trend Score.
        """
        if not bundles:
            return []

        config = trend_config or TrendConfig()
        trend_engine = TrendEngine(config)
        registry = TrackedVideoRegistry(limit=100)
        snapshots: list[dict[str, Any]] = []

        for bundle in bundles:
            deltas = registry.observe(bundle.observations, baseline=trend_engine.baseline)
            trend_engine.engagement(deltas)
            if bundle.metadata.operation == "discovery":
                step = trend_engine.process(bundle)
                if step:
                    snapshots.append(step)
                    notify({"operation": "replay_step", "batch_id": bundle.metadata.batch_id, "row": step})

        return snapshots

    def replay_topic(
        self,
        topic_id: str,
        trend_config: TrendConfig | None = None,
        notify: Callable[[dict[str, Any]], None] = lambda event: None,
    ) -> list[dict[str, Any]]:
        """Replay all stored raw batches for a topic from the PostgreSQL raw_batches table."""
        bundles = self.db.get_raw_batches(topic_id)
        if not bundles:
            return []
        return self.replay_bundles(bundles, trend_config=trend_config, notify=notify)

    def run(
        self,
        topic_id: str,
        trend_config: TrendConfig | None = None,
        gap_formula: dict | None = None,
        notify: Callable[[dict[str, Any]], None] = lambda event: None,
    ) -> list[dict[str, Any]]:
        """Run replay for a topic, returning calculated metric snapshots."""
        return self.replay_topic(topic_id=str(topic_id), trend_config=trend_config, notify=notify)
