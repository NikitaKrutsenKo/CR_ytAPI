from __future__ import annotations

import argparse

from config import CollectorConfig, metric_config_from_env
from metrics import MetricEngine
from state import StateManager
from storage.csv_writer import MetricsCsvWriter
from storage.json_writer import write_json
from youtube.collector import YouTubeCollector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CR:Correlator YouTube PoC")
    parser.add_argument("--topic", required=True, help="Topic to collect")
    parser.add_argument("--max-videos", type=int, default=50, help="1..50 thematic videos")
    parser.add_argument("--batch-output", default="data/batch.json", help="Raw batch JSON output")
    parser.add_argument(
        "--metrics-output",
        default="data/metrics_timeseries.csv",
        help="Calculated metrics CSV output",
    )
    parser.add_argument(
        "--state-file",
        default="data/state.json",
        help="Persistent Metric Engine state",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    collector_config = CollectorConfig.from_env()
    metric_config = metric_config_from_env()

    collector = YouTubeCollector(collector_config)
    engine = MetricEngine(metric_config)
    state_manager = StateManager(args.state_file)
    csv_writer = MetricsCsvWriter(args.metrics_output)

    batch = collector.collect(args.topic, max_videos=args.max_videos)
    write_json(batch.to_dict(), args.batch_output)

    state = state_manager.load()
    snapshot, new_state = engine.process(batch, state)
    csv_writer.append(snapshot)
    state_manager.save(new_state)

    print(f"Collected {batch.collection.batch_size} thematic videos.")
    print(f"Batch: {batch.collection.batch_id}")
    print(f"Gap Score: {snapshot.gap_score:.6f}")
    print(f"Metrics CSV: {args.metrics_output}")
    print(f"State: {args.state_file}")


if __name__ == "__main__":
    main()
