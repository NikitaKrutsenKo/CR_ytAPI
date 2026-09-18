from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event

from research.collection import CollectionService
from research.domain import CollectionBundle, CollectionRequest, ExperimentConfig, Mode, Status, fingerprint
from research.gap import GapEnricher
from research.processing import MetricProcessor
from research.quota import QuotaEstimate, QuotaManager, QuotaStopped
from research.scheduling import TopicScheduler
from research.storage import TopicWorkspaceManager, append_jsonl, write_json
from youtube.client import RequestCancelled, YouTubeApiError

log = logging.getLogger(__name__)


class ExperimentRun:
    """Encapsulate mutable scheduling and lifecycle of one already validated experiment."""

    def __init__(
        self,
        config: ExperimentConfig,
        store: TopicWorkspaceManager,
        experiment: Path,
        client,
        quota: QuotaManager,
        estimate: QuotaEstimate,
        stop: Event,
        notify: Callable,
    ):
        self.config, self.store, self.path = config, store, experiment
        self.client, self.quota, self.estimate = client, quota, estimate
        self.stop, self.notify = stop, notify
        self.collection = CollectionService(client)
        self.processor = MetricProcessor(config, store, experiment)
        self.gap = (
            GapEnricher(client, store.root / "data" / "creator_cache.json", config)
            if config.mode in (Mode.GAP, Mode.COMBINED, Mode.PRODUCT)
            else None
        )
        self.scheduler = TopicScheduler(tuple(r.topic for r in config.requests), config.exploration_fraction)
        self.summary = {
            "experiment_id": experiment.name,
            "mode": config.mode,
            "status": "RUNNING",
            "collections": 0,
            "partial_collections": 0,
            "failures": 0,
            "raw_observations": 0,
            "warnings": [],
            "topics_monitored": [],
            "unique_videos": 0,
            "unique_creators": 0,
            "duration_seconds": 0.0,
        }
        self.unique_videos, self.unique_creators, self.monitored = set(), set(), set()
        self.begin = time.monotonic()
        self.deadline = self.begin + config.duration_minutes * 60
        self.next_discovery, self.next_tracking = self.begin, self.begin + config.tracking_minutes * 60
        self.discovery_allowed, self.tracking_allowed = True, True

    def execute(self) -> None:
        """Run finite schedules; always save a terminal summary, including on failure."""
        self.store.event(self.path, "experiment_created", config_hash=fingerprint(self.config))
        self.notify({"operation": "experiment_created", "experiment_id": self.path.name})
        try:
            while not self.stop.is_set() and time.monotonic() < self.deadline:
                for operation, request in self._jobs(time.monotonic()):
                    if self.stop.is_set() or time.monotonic() >= self.deadline:
                        break
                    self._cycle(operation, request)
                if self.config.mode == Mode.HISTORICAL or self._no_work_remaining():
                    break
                self.stop.wait(min(0.25, max(0, self.deadline - time.monotonic())))
            self.summary["status"] = self._terminal_status()
        except Exception:
            self.summary["status"] = "FAILED"
            log.exception("experiment_failed experiment_id=%s", self.path.name)
            raise
        finally:
            try:
                self._finish()
            finally:
                if hasattr(self.client, "session"):
                    self.client.session.close()

    def _jobs(self, clock: float) -> list[tuple[str, CollectionRequest]]:
        jobs = []
        if self.discovery_allowed and clock >= self.next_discovery:
            selected = self.config.requests
            if self.config.mode == Mode.PRODUCT:
                selection = self.scheduler.choose({r.topic.topic_id: float(r.max_pages) for r in selected})
                self.store.event(self.path, "topic_selected", **asdict(selection))
                selected = tuple(r for r in selected if r.topic.topic_id == selection.topic_id)
            jobs.extend(("discovery", r) for r in selected)
            self.next_discovery = clock + self.config.discovery_minutes * 60
        if (
            self.tracking_allowed
            and self.config.mode not in (Mode.GAP, Mode.HISTORICAL)
            and clock >= self.next_tracking
        ):
            jobs.extend(
                ("tracking", r) for r in self.config.requests if self.processor.registry(r.topic.topic_id).ids
            )
            self.next_tracking = clock + self.config.tracking_minutes * 60
        return jobs

    def _cycle(self, operation: str, request: CollectionRequest) -> None:
        self.client.context = {
            "experiment_id": self.path.name,
            "topic_id": request.topic.topic_id,
            "operation": operation,
        }
        self.store.event(
            self.path, "collection_started", topic_id=request.topic.topic_id, collection_operation=operation
        )
        bundle = (
            self.collection.discover(request)
            if operation == "discovery"
            else self.collection.track(request, self.processor.registry(request.topic.topic_id).ids)
        )
        counts = dict(Counter(t.endpoint for t in bundle.telemetry))
        bundle = replace(
            bundle,
            metadata=replace(
                bundle.metadata,
                api_calls_by_endpoint=counts,
                actual_local_call_count=len(bundle.telemetry),
                estimated_quota_cost={
                    "search_calls": counts.get("search", 0),
                    "other_units": sum(n for endpoint, n in counts.items() if endpoint != "search"),
                },
                trend_formula_version=self.config.trend_formula.get(
                    "formula_version", "youtube_activity_research_v1"
                ),
                gap_formula_version="gap_original_v1",
            ),
        )
        self.store.save_bundle(self.path, bundle)
        self.store.state(self.path, request.topic.topic_id, "discovery", self.collection.watermarks)
        self._record(bundle)
        batch = (
            self._enrich(bundle) if self.gap and operation == "discovery" and not self.stop.is_set() else None
        )
        rows = self.processor.process(bundle, batch)
        for kind, row in rows:
            if kind == "trend":
                self.scheduler.feedback(request.topic.topic_id, row.get("youtube_research_score"))
        self.store.event(
            self.path,
            "collection_completed",
            topic_id=request.topic.topic_id,
            batch_id=bundle.metadata.batch_id,
            status=bundle.metadata.status,
        )
        self.notify(
            {
                "operation": "collection_completed",
                "experiment_id": self.path.name,
                "topic": request.topic.canonical_name,
                "status": bundle.metadata.status,
                "rows": rows,
                "quota": self.quota.usage(),
            }
        )
        if bundle.metadata.status == Status.QUOTA_STOPPED:
            if operation == "discovery":
                self.discovery_allowed = False
            else:
                self.tracking_allowed = False

    def _record(self, bundle: CollectionBundle) -> None:
        self.summary["collections"] += 1
        self.summary["raw_observations"] += len(bundle.observations)
        self.summary["partial_collections"] += int(
            bundle.metadata.status in (Status.PARTIAL, Status.QUOTA_STOPPED)
        )
        self.summary["failures"] += int(bundle.metadata.status == Status.FAILED)
        self.summary["warnings"].extend(bundle.warnings)
        self.monitored.add(bundle.topic.topic_id)
        self.unique_videos.update(v.identity.video_id for v in bundle.observations)
        self.unique_creators.update(v.identity.channel_id for v in bundle.observations)

    def _enrich(self, bundle: CollectionBundle):
        telemetry_start = len(self.client.telemetry)
        self.client.context["operation"] = "gap_enrichment"
        self.store.event(self.path, "gap_enrichment_started", batch_id=bundle.metadata.batch_id)
        try:
            batch = self.gap.enrich(bundle)
            write_json(self.path / "enrichment" / (bundle.metadata.batch_id + ".json"), batch.to_dict(), True)
            append_jsonl(
                self.path / "enrichment_manifest.jsonl",
                {"batch_id": bundle.metadata.batch_id, "sha256": fingerprint(batch.to_dict())},
            )
            if not batch.videos:
                self.summary["warnings"].append(
                    "Gap unavailable: no videos have a complete eligible creator baseline"
                )
            return batch
        except (YouTubeApiError, QuotaStopped, RequestCancelled) as exc:
            self.summary["warnings"].append(str(exc))
            self.store.event(
                self.path, "gap_enrichment_unavailable", batch_id=bundle.metadata.batch_id, reason=str(exc)
            )
            return None
        finally:
            for row in self.client.telemetry[telemetry_start:]:
                append_jsonl(self.path / "telemetry.jsonl", row)

    def _no_work_remaining(self) -> bool:
        return not self.discovery_allowed and (
            not self.tracking_allowed
            or self.config.mode == Mode.GAP
            or not any(r.ids for r in self.processor.registries.values())
        )

    def _terminal_status(self) -> str:
        if self.stop.is_set():
            return "CANCELLED"
        if not self.discovery_allowed or not self.tracking_allowed:
            return "QUOTA_STOPPED"
        if self.summary["failures"] or self.summary["partial_collections"]:
            return "PARTIAL"
        return "COMPLETE"

    def _finish(self) -> None:
        self.summary.update(self.processor.counts)
        self.summary.update(
            topics_monitored=sorted(self.monitored),
            unique_videos=len(self.unique_videos),
            unique_creators=len(self.unique_creators),
            duration_seconds=time.monotonic() - self.begin,
        )
        write_json(self.path / "summary.json", self.summary)
        write_json(
            self.path / "quota.json",
            {
                "label": "Local estimate",
                "profile_daily_usage": self.quota.usage(),
                "experiment_calls": len(self.client.telemetry),
                "estimate": self.estimate,
            },
        )
        if self.config.mode == Mode.PRODUCT:
            from research.reporting import ProductReport

            write_json(
                self.path / "product_report.json", ProductReport().build(self.path, self.config, self.summary)
            )
        self.store.event(self.path, "experiment_finished", status=self.summary["status"])
        self.notify({"operation": "experiment_finished", **self.summary})
