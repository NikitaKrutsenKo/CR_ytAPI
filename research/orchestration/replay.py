"""Offline replay of stored raw observations through metric calculation engines."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from research.core.configuration import resolve_formulas
from research.core.domain import ExperimentConfig, fingerprint
from research.metrics.processing import MetricProcessor
from research.storage.io import read_json, read_jsonl, write_json
from research.storage.raw_batch_store import batch_from_dict
from research.storage.workspace import TopicWorkspaceManager


class ReplayService:
    """Re-executes calculations over verified stored raw batches with zero network access."""

    def __init__(self, root: Path) -> None:
        self.store = TopicWorkspaceManager(root)

    def run(
        self,
        source: Path,
        gap_formula: dict | None = None,
        trend_formula: dict | None = None,
        notify=lambda event: None,
    ) -> Path:
        """Run replay on an existing experiment, producing a new derived experiment workspace."""
        metadata = read_json(source / "experiment.json")
        if fingerprint(metadata["config"]) != metadata["config_hash"]:
            raise ValueError("Experiment configuration checksum mismatch")
        config = ExperimentConfig.from_dict(metadata["config"])
        config = replace(
            config,
            gap_formula=gap_formula if gap_formula is not None else config.gap_formula,
            trend_formula=trend_formula if trend_formula is not None else config.trend_formula,
        )
        config = resolve_formulas(config)
        bundles = list(self.store.bundles(source))
        if not bundles:
            raise ValueError("Replay dataset is empty")
        manifest_path = source / "enrichment_manifest.jsonl"
        enrichment_hashes = (
            {v["batch_id"]: v["sha256"] for v in read_jsonl(manifest_path)} if manifest_path.exists() else {}
        )
        for batch_id, expected in enrichment_hashes.items():
            if fingerprint(read_json(source / "enrichment" / (batch_id + ".json"))) != expected:
                raise ValueError("Creator baseline checksum mismatch")
        experiment = self.store.create(config, parent=source.name)
        processor = MetricProcessor(config, self.store, experiment)
        for bundle in bundles:
            enrichment = source / "enrichment" / (bundle.metadata.batch_id + ".json")
            batch = batch_from_dict(read_json(enrichment)) if enrichment.exists() else None
            if batch:
                write_json(experiment / "enrichment" / enrichment.name, batch.to_dict(), True)
            rows = processor.process(bundle, batch)
            notify({"operation": "replay_batch", "experiment_id": experiment.name, "rows": rows})
        if manifest_path.exists():
            (experiment / "enrichment_manifest.jsonl").write_bytes(manifest_path.read_bytes())
        (experiment / "inputs.jsonl").write_bytes((source / "inputs.jsonl").read_bytes())
        write_json(experiment / "quota.json", {"label": "Offline replay", "experiment_calls": 0})
        write_json(
            experiment / "summary.json", {"status": "COMPLETE", "replay_of": source.name, **processor.counts}
        )
        self.store.event(experiment, "replay_completed", source=source.name)
        return experiment
