from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from research.domain import ExperimentConfig, encode
from research.processing import MetricProcessor
from research.storage import TopicWorkspaceManager, read_json, write_json, append_jsonl
from storage.raw_batch_store import batch_from_dict


class ReplayService:
    """Offline reconstruction. No client, credentials, collector or quota dependency."""
    def __init__(self, root: Path):
        self.store = TopicWorkspaceManager(root)

    def run(self, source: Path, gap_formula: dict | None = None, trend_formula: dict | None = None,
            notify=lambda event: None) -> Path:
        metadata = read_json(source / "experiment.json")
        config = ExperimentConfig.from_dict(metadata["config"])
        config = replace(config, gap_formula=gap_formula if gap_formula is not None else config.gap_formula,
                         trend_formula=trend_formula if trend_formula is not None else config.trend_formula)
        # Validate all input references before allocating a derived experiment.
        bundles = list(self.store.bundles(source))
        if not bundles:
            raise ValueError("Replay dataset is empty")
        experiment = self.store.create(config, parent=source.name)
        processor = MetricProcessor(config, self.store, experiment)
        # Reuse verified raw references, never duplicate/rewrite raw observations.
        for bundle in bundles:
            enrichment = source / "enrichment" / (bundle.metadata.batch_id + ".json")
            batch = batch_from_dict(read_json(enrichment)) if enrichment.exists() else None
            if batch:
                write_json(experiment / "enrichment" / enrichment.name, batch.to_dict(), True)
            rows = processor.process(bundle, batch)
            notify({"operation": "replay_batch", "experiment_id": experiment.name, "rows": rows})
        (experiment / "inputs.jsonl").write_bytes((source / "inputs.jsonl").read_bytes())
        write_json(experiment / "quota.json", {"label": "Offline replay", "experiment_calls": 0})
        write_json(experiment / "summary.json", {"status": "COMPLETE", "replay_of": source.name, **processor.counts})
        self.store.event(experiment, "replay_completed", source=source.name)
        return experiment
