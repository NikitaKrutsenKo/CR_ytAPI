"""Calculation pipeline coordinating Gap, Trend, Tracking, and Historical evaluation."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from research.core.domain import CollectionBundle, ExperimentConfig, Mode
from research.metrics.calculators import MetricConfig
from research.metrics.evaluation import HistoricalAnalyzer
from research.metrics.gap import GapEngine
from research.metrics.tracking import TrackedVideoRegistry
from research.metrics.trend import TrendConfig, TrendEngine
from research.storage.db import DatabaseStorageAdapter


class MetricProcessor:
    """Network-free calculation boundary shared by live experiment runs and offline replay."""

    def __init__(self, config: ExperimentConfig, store: DatabaseStorageAdapter, run_id: str) -> None:
        self.config, self.store, self.run_id = config, store, run_id
        self.gap = GapEngine(MetricConfig(**config.gap_formula))
        self.gap_states: dict[str, Any] = {}
        self.trends: dict[str, TrendEngine] = {}
        self.registries: dict[str, TrackedVideoRegistry] = {}
        self.counts = {"gap_snapshots": 0, "trend_snapshots": 0, "counter_deltas": 0, "historical_buckets": 0}

    def registry(self, topic_id: str) -> TrackedVideoRegistry:
        """Get or initialize the TrackedVideoRegistry for a topic."""
        return self.registries.setdefault(topic_id, TrackedVideoRegistry(self.config.tracked_limit))

    def process(self, bundle: CollectionBundle, gap_batch=None) -> list[tuple[str, dict]]:
        """Process an ingested collection bundle and write results to the workspace store."""
        topic_id = bundle.topic.topic_id
        rows = []
        if self.config.mode == Mode.HISTORICAL:
            rows = [("historical", row) for row in HistoricalAnalyzer().publications(bundle)]
            self.counts["historical_buckets"] += len(rows)
        elif self.config.mode != Mode.GAP:
            trend = self.trends.setdefault(topic_id, TrendEngine(TrendConfig(**self.config.trend_formula)))
            deltas = self.registry(topic_id).observe(bundle.observations, baseline=trend.baseline)
            engagement = trend.engagement(deltas)
            for delta in deltas:
                rows.append(
                    (
                        "tracking",
                        {
                            **asdict(delta),
                            "topic": bundle.topic.canonical_name,
                            "engagement": engagement,
                            "engagement_E_YT": engagement,
                        },
                    )
                )
            self.counts["counter_deltas"] += len(deltas)
            if bundle.metadata.operation == "discovery":
                snapshot = trend.process(bundle)
                if snapshot:
                    rows.append(("trend", snapshot))
                    self.counts["trend_snapshots"] += 1
        if gap_batch is not None and gap_batch.videos:
            snapshot, state = self.gap.process(gap_batch, self.gap_states.get(topic_id))
            self.gap_states[topic_id] = state
            rows.append(
                (
                    "gap",
                    {
                        **snapshot.to_dict(),
                        "formula_version": "gap_original_v1",
                        "collection_status": bundle.metadata.status,
                        "raw_batch_size": bundle.metadata.unique_video_count,
                    },
                )
            )
            self.counts["gap_snapshots"] += 1
        for kind, row in rows:
            if kind == "trend":
                self.store.save_trend_metric(topic_id, bundle.metadata.batch_id, bundle.metadata.finished_at, row)
            elif kind == "gap":
                self.store.save_gap_metric(
                    topic_id,
                    bundle.metadata.batch_id,
                    bundle.metadata.finished_at,
                    row.get("gap_score", 0.0),
                    row.get("demand_norm", 0.0),
                    row.get("supply_norm", 0.0),
                    row.get("creator_authority", 0.0),
                    row
                )
        return rows
