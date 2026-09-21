"""Topic and experiment workspace filesystem layout manager."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from uuid import uuid4

from research.core.domain import CollectionBundle, ExperimentConfig, fingerprint
from research.core.time import iso, now_utc
from research.storage.io import append_jsonl, cleanup_temporary_files, iter_jsonl, read_json, write_json


class TopicWorkspaceManager:
    """Manages the topic and experiment directory structures and raw observation provenance.

    Maintains immutable raw observations per topic and isolates experiment-specific
    state and results to avoid cross-experiment contamination.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        cleanup_temporary_files(self.root)

    def topic(self, topic_id: str) -> Path:
        """Return the workspace directory path for a topic ID."""
        if not re.fullmatch(r"[\w-]+", topic_id):
            raise ValueError("Invalid topic ID")
        return self.root / "data" / "topics" / topic_id

    def create(self, config: ExperimentConfig, parent: str | None = None) -> Path:
        """Create a new experiment directory snapshot with config and topic references."""
        experiment = (
            self.root
            / "experiments"
            / ("exp_" + now_utc().strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:10])
        )
        experiment.mkdir(parents=True)
        write_json(
            experiment / "experiment.json",
            {
                "schema_version": "2.0",
                "experiment_id": experiment.name,
                "created_at": iso(now_utc()),
                "config_hash": fingerprint(config),
                "config": config,
                "replay_of": parent,
            },
            True,
        )
        write_json(experiment / "topics.json", [r.topic for r in config.requests], True)
        for request in config.requests:
            path = self.topic(request.topic.topic_id)
            path.mkdir(parents=True, exist_ok=True)
            if not (path / "topic.json").exists():
                write_json(path / "topic.json", request.topic, True)
        return experiment

    def save_bundle(self, experiment: Path, bundle: CollectionBundle) -> None:
        """Persist an immutable CollectionBundle to the topic's raw directory."""
        raw_path = self.topic(bundle.topic.topic_id) / "raw" / (bundle.metadata.batch_id + ".json")
        write_json(raw_path, bundle, exclusive=True)
        append_jsonl(
            experiment / "inputs.jsonl",
            {"path": str(raw_path.relative_to(self.root)), "sha256": fingerprint(bundle)},
        )
        for row in bundle.telemetry:
            append_jsonl(experiment / "telemetry.jsonl", row)

    def bundles(self, experiment: Path):
        """Iterate over verified input raw CollectionBundles referenced by an experiment."""
        for entry in iter_jsonl(experiment / "inputs.jsonl"):
            path = (self.root / entry["path"]).resolve()
            if not path.is_relative_to(self.root / "data" / "topics"):
                raise ValueError("Raw reference outside topic storage")
            raw = read_json(path)
            if fingerprint(raw) != entry["sha256"]:
                raise ValueError("Raw observation checksum mismatch")
            yield CollectionBundle.from_dict(raw)

    def metric(self, experiment: Path, topic_id: str, kind: str, row: dict) -> None:
        """Append a metric result row to both JSONL and CSV output files."""
        path = experiment / "results" / topic_id / (kind + ".jsonl")
        append_jsonl(path, row)
        csv_path = path.with_suffix(".csv")
        scalar = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
        exists = csv_path.exists()
        with csv_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(scalar))
            if not exists:
                writer.writeheader()
            writer.writerow(scalar)

    def state(self, experiment: Path, topic_id: str, kind: str, state) -> None:
        """Persist engine state JSON for an experiment and topic."""
        write_json(experiment / "state" / topic_id / (kind + ".json"), state)

    def event(self, experiment: Path, operation: str, **fields) -> None:
        """Log an execution lifecycle event to experiment events.jsonl."""
        append_jsonl(
            experiment / "events.jsonl", {"timestamp": iso(now_utc()), "operation": operation, **fields}
        )
