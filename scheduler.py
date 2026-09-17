from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from config import CollectorConfig, metric_config_from_env
from metrics import MetricEngine
from state import StateManager
from storage.csv_writer import MetricsCsvWriter
from storage.json_writer import write_json
from storage.raw_batch_store import RawBatchStore
from storage.validator import validate_batch, validate_snapshot
from youtube.collector import YouTubeCollector


def run_once(
    topic: str,
    collector: YouTubeCollector,
    engine: MetricEngine,
    state_manager: StateManager,
    raw_store: RawBatchStore,
    metrics_writer: MetricsCsvWriter,
    batch_output: str | None,
    max_videos: int,
) -> None:
    batch = collector.collect(topic, max_videos=max_videos)
    validate_batch(batch)

    if batch_output:
        write_json(batch.to_dict(), batch_output)
    raw_store.append(batch)

    state = state_manager.load()
    snapshot, new_state = engine.process(batch, state)
    validate_snapshot(snapshot)
    metrics_writer.append(snapshot)
    state_manager.save(new_state)

    print(
        f"[{batch.collection.current_time}] topic={topic!r} "
        f"videos={batch.collection.batch_size} gap={snapshot.gap_score:.6f}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CR:Correlator YouTube collection scheduler")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--max-videos", type=int, default=50)
    parser.add_argument("--interval-minutes", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="Run one collection cycle")
    parser.add_argument("--batch-output", default="data/latest_batch.json")
    parser.add_argument("--raw-output", default="data/raw_batches.jsonl")
    parser.add_argument("--metrics-output", default="data/metrics_timeseries.csv")
    parser.add_argument("--state-file", default="data/state.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_videos < 1 or args.max_videos > 50:
        raise ValueError("--max-videos must be between 1 and 50")
    if args.interval_minutes <= 0:
        raise ValueError("--interval-minutes must be positive")

    collector = YouTubeCollector(CollectorConfig.from_env())
    engine = MetricEngine(metric_config_from_env())
    state_manager = StateManager(args.state_file)
    raw_store = RawBatchStore(args.raw_output)
    writer = MetricsCsvWriter(args.metrics_output)

    while True:
        run_once(
            topic=args.topic,
            collector=collector,
            engine=engine,
            state_manager=state_manager,
            raw_store=raw_store,
            metrics_writer=writer,
            batch_output=args.batch_output,
            max_videos=args.max_videos,
        )
        if args.once:
            break
        time.sleep(args.interval_minutes * 60)


if __name__ == "__main__":
    main()
