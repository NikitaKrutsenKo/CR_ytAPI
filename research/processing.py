from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from research.domain import CollectionBundle, ExperimentConfig, Mode
from research.gap import GapEngine
from research.trend import TrendConfig, TrendEngine
from research.tracking import TrackedVideoRegistry
from research.evaluation import HistoricalAnalyzer
from research.storage import TopicWorkspaceManager
from metrics.calculators import MetricConfig
from state.manager import MetricsState


class MetricProcessor:
    """Network-free calculation boundary shared by live experiments and offline replay."""
    def __init__(self, config: ExperimentConfig, store: TopicWorkspaceManager, experiment: Path):
        self.config, self.store, self.experiment = config, store, experiment
        self.gap = GapEngine(MetricConfig(**config.gap_formula))
        self.gap_states, self.trends, self.registries = {}, {}, {}
        self.counts = {"gap_snapshots": 0, "trend_snapshots": 0, "counter_deltas": 0, "historical_buckets": 0}

    def registry(self, topic_id):
        return self.registries.setdefault(topic_id, TrackedVideoRegistry(self.config.tracked_limit))

    def process(self, bundle: CollectionBundle, gap_batch=None) -> list[tuple[str, dict]]:
        topic_id = bundle.topic.topic_id
        rows = []
        if self.config.mode == Mode.HISTORICAL:
            rows = [("historical", row) for row in HistoricalAnalyzer().publications(bundle)]
            self.counts["historical_buckets"] += len(rows)
        elif self.config.mode != Mode.GAP:
            trend = self.trends.setdefault(topic_id, TrendEngine(TrendConfig(**self.config.trend_formula)))
            deltas = self.registry(topic_id).observe(bundle.observations)
            engagement = trend.engagement(deltas)
            for delta in deltas:
                rows.append(("tracking", {**asdict(delta), "topic": bundle.topic.canonical_name, "engagement": engagement}))
            self.counts["counter_deltas"] += len(deltas)
            if bundle.metadata.operation == "discovery":
                snapshot = trend.process(bundle)
                if snapshot:
                    rows.append(("trend", snapshot))
                    self.counts["trend_snapshots"] += 1
            self.store.state(self.experiment, topic_id, "trend", trend.state)
            self.store.state(self.experiment, topic_id, "tracking", self.registry(topic_id).state())
        if gap_batch is not None and gap_batch.videos:
            snapshot, state = self.gap.process(gap_batch, self.gap_states.get(topic_id))
            self.gap_states[topic_id] = state
            rows.append(("gap", {**snapshot.to_dict(), "formula_version": "gap_original_v1"}))
            self.counts["gap_snapshots"] += 1
            self.store.state(self.experiment, topic_id, "gap", state.to_dict())
        for kind, row in rows:
            self.store.metric(self.experiment, topic_id, kind, row)
        return rows
