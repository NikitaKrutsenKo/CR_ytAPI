from __future__ import annotations

import argparse

from config import metric_config_from_env
from metrics import MetricEngine
from models.metrics import MetricSnapshot
from state import MetricsState, StateManager
from storage.csv_writer import MetricsCsvWriter
from storage.raw_batch_store import RawBatchStore
from storage.validator import validate_batch, validate_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay raw CR:Correlator batches through Metric Engine")
    parser.add_argument("--raw-input", default="data/raw_batches.jsonl")
    parser.add_argument("--metrics-output", default="data/replay_metrics.csv")
    parser.add_argument("--state-file", default="data/replay_state.json")
    parser.add_argument("--reset-state", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    store = RawBatchStore(args.raw_input)
    engine = MetricEngine(metric_config_from_env())
    state_manager = StateManager(args.state_file)

    state = MetricsState() if args.reset_state else state_manager.load()
    writer = MetricsCsvWriter(args.metrics_output)
    count = 0

    for batch in store.read_all():
        validate_batch(batch)
        snapshot, state = engine.process(batch, state)
        validate_snapshot(snapshot)
        writer.append(snapshot)
        count += 1

    state_manager.save(state)
    print(f"Replayed {count} batches into {args.metrics_output}")


if __name__ == "__main__":
    main()
