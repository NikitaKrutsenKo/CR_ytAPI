from __future__ import annotations
import csv
import json
from pathlib import Path
import re
from uuid import uuid4
from research.domain import CollectionBundle, ExperimentConfig, encode, fingerprint, iso, now_utc


def write_json(path: Path, value, exclusive=False) -> None:
    """Write strict JSON atomically; immutable records use exclusive creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(encode(value), ensure_ascii=False, indent=2, allow_nan=False)
    if exclusive:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    else:
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read JSON file: {path}") from exc


def append_jsonl(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(encode(value), ensure_ascii=False, allow_nan=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise ValueError(f"Dataset missing: {path}")
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            try:
                result.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"Corrupt dataset {path.name}, line {number}") from exc
    return result


class TopicWorkspaceManager:
    """Immutable raw records per topic; run-scoped state prevents cross-experiment leakage."""
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def topic(self, topic_id: str) -> Path:
        if not re.fullmatch(r"[\w-]+", topic_id):
            raise ValueError("Invalid topic ID")
        return self.root / "data" / "topics" / topic_id

    def create(self, config: ExperimentConfig, parent=None) -> Path:
        experiment = self.root / "experiments" / ("exp_" + now_utc().strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:10])
        experiment.mkdir(parents=True)
        write_json(experiment / "experiment.json", {"schema_version": "2.0", "experiment_id": experiment.name,
                   "created_at": iso(now_utc()), "config_hash": fingerprint(config), "config": config, "replay_of": parent}, True)
        write_json(experiment / "topics.json", [r.topic for r in config.requests], True)
        for request in config.requests:
            path = self.topic(request.topic.topic_id)
            path.mkdir(parents=True, exist_ok=True)
            if not (path / "topic.json").exists():
                write_json(path / "topic.json", request.topic, True)
            write_json(path / "configs" / (experiment.name + ".json"), request, True)
        return experiment

    def save_bundle(self, experiment: Path, bundle: CollectionBundle) -> None:
        raw_path = self.topic(bundle.topic.topic_id) / "raw" / (bundle.metadata.batch_id + ".json")
        write_json(raw_path, bundle, True)
        append_jsonl(experiment / "inputs.jsonl", {"path": str(raw_path.relative_to(self.root)), "sha256": fingerprint(bundle)})
        for row in bundle.telemetry:
            append_jsonl(experiment / "telemetry.jsonl", row)

    def bundles(self, experiment: Path):
        """Reject tampering and unknown schema; never reinterpret old raw data."""
        for entry in read_jsonl(experiment / "inputs.jsonl"):
            path = (self.root / entry["path"]).resolve()
            if not path.is_relative_to(self.root / "data" / "topics"):
                raise ValueError("Raw reference outside topic storage")
            raw = read_json(path)
            if fingerprint(raw) != entry["sha256"]:
                raise ValueError("Raw observation checksum mismatch")
            yield CollectionBundle.from_dict(raw)

    def metric(self, experiment: Path, topic_id: str, kind: str, row: dict) -> None:
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
        write_json(experiment / "state" / topic_id / (kind + ".json"), state)

    def event(self, experiment: Path, operation: str, **fields) -> None:
        append_jsonl(experiment / "events.jsonl", {"timestamp": iso(now_utc()), "operation": operation, **fields})
