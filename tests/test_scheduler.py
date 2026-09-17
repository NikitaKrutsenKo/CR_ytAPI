from datetime import datetime, timezone

from config import MetricConfig
from metrics import MetricEngine
from models.batch import CollectionInfo, Topic, YouTubeBatch
from models.raw_video import RawTopicVideo, RecentVideoStat
from state import MetricsState, StateManager
from storage.csv_writer import MetricsCsvWriter
from storage.raw_batch_store import RawBatchStore
from scheduler import run_once


class FakeCollector:
    def collect(self, topic: str, max_videos: int):
        stats = [RecentVideoStat(f"r-{i}", float(i + 1), (i + 1) * 100) for i in range(5)]
        video = RawTopicVideo("v1", "c1", "2026-09-14T08:00:00Z", 1000, 10, 2, 5, stats)
        return YouTubeBatch("1.0", Topic("topic", topic), CollectionInfo("2026-09-14T09:00:00Z", "b1", 1), [video])


def test_run_once_writes_raw_metrics_and_state(tmp_path):
    collector = FakeCollector()
    engine = MetricEngine(MetricConfig(half_life_hours=12, epsilon=1e-6, demand_weight_er=0.5, demand_weight_pr=0.5, extreme_percent=0.1))
    state_manager = StateManager(tmp_path / "state.json")
    raw_store = RawBatchStore(tmp_path / "raw.jsonl")
    metrics_writer = MetricsCsvWriter(tmp_path / "metrics.csv")

    run_once("topic", collector, engine, state_manager, raw_store, metrics_writer, None, 1)

    assert (tmp_path / "raw.jsonl").exists()
    assert (tmp_path / "metrics.csv").exists()
    assert (tmp_path / "state.json").exists()
