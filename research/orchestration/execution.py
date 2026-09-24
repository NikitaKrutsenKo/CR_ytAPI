"""Experiment run lifecycle execution, thread loop, and quota pause handling."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event
from typing import Any

from research.api.client import RequestCancelled, YouTubeApiError
from research.api.quota import QuotaEstimate, QuotaManager, QuotaStopped
from research.core.domain import (
    CollectionBundle,
    CollectionRequest,
    ExperimentConfig,
    Mode,
    Status,
    WindowResolver,
    encode,
    fingerprint,
)
from research.core.time import iso, now_utc
from research.metrics.gap import GapEnricher
from research.metrics.processing import MetricProcessor
from research.orchestration.collection import CollectionService
from research.orchestration.scheduling import TopicScheduler
from research.storage.db import DatabaseStorageAdapter

log = logging.getLogger(__name__)


class ExperimentRun:
    """Encapsulates the runtime lifecycle, job scheduling, and execution of a validated experiment."""

    def __init__(
        self,
        config: ExperimentConfig,
        store: DatabaseStorageAdapter,
        run_id: str,
        client,
        quota: QuotaManager,
        estimate: QuotaEstimate,
        stop: Event,
        notify: Callable,
        cache_path: Path | None = None,
    ) -> None:
        self.config, self.store, self.run_id = config, store, run_id
        self.client, self.quota, self.estimate = client, quota, estimate
        self.stop, self.notify = stop, notify
        self.collection = CollectionService(client)
        self.processor = MetricProcessor(config, store, run_id)
        self.gap = (
            GapEnricher(client, store, config)
            if config.mode in (Mode.GAP, Mode.COMBINED, Mode.PRODUCT)
            else None
        )
        self.scheduler = TopicScheduler(tuple(r.topic for r in config.requests), config.exploration_fraction)
        self.summary: dict[str, Any] = {
            "experiment_id": self.run_id,
            "mode": config.mode,
            "status": "RUNNING",
            "collections": 0,
            "partial_collections": 0,
            "failures": 0,
            "raw_observations": 0,
            "discovered_video_count": 0,
            "eligible_video_count": 0,
            "excluded_too_young_count": 0,
            "excluded_low_views_count": 0,
            "excluded_missing_views_count": 0,
            "warnings": [],
            "topics_monitored": [],
            "unique_videos": 0,
            "unique_creators": 0,
            "duration_seconds": 0.0,
        }
        self.unique_videos: set[str] = set()
        self.unique_creators: set[str] = set()
        self.monitored: set[str] = set()
        self.begin = time.monotonic()
        self.started_at = now_utc()
        self.deadline = float("inf") if config.endless_mode else self.begin + config.duration_hours * 3600
        self.initial_windows = {
            request.topic.topic_id: WindowResolver.resolve(
                request, self.started_at, config.minimum_video_age_hours
            )
            for request in config.requests
        }
        self.next_discovery, self.next_tracking = self.begin, self.begin + config.tracking_minutes * 60
        self.discovery_allowed, self.tracking_allowed = True, True

    def execute(self) -> None:
        """Run the experiment execution loop until deadline, completion, or stop request."""
        self.notify({"operation": "experiment_created", "experiment_id": self.run_id})
        try:
            while not self.stop.is_set() and time.monotonic() < self.deadline:
                for operation, request in self._jobs(time.monotonic()):
                    if self.stop.is_set() or time.monotonic() >= self.deadline:
                        break
                    self._cycle(operation, request)
                if self.config.mode == Mode.HISTORICAL or (
                    not self.config.endless_mode and self._no_work_remaining()
                ):
                    break
                if self.config.endless_mode and self._no_work_remaining():
                    self._wait_for_quota()
                else:
                    self.stop.wait(min(0.25, max(0, self.deadline - time.monotonic())))
            self.summary["status"] = self._terminal_status()
        except Exception:
            self.summary["status"] = "FAILED"
            log.exception("experiment_failed experiment_id=%s", self.run_id)
            raise
        finally:
            try:
                self._finish()
            finally:
                if hasattr(self.client, "close"):
                    self.client.close()
                elif hasattr(self.client, "session"):
                    self.client.session.close()

    def _jobs(self, clock: float) -> list[tuple[str, CollectionRequest]]:
        jobs = []
        if self.discovery_allowed and clock >= self.next_discovery:
            selected = self.config.requests
            if self.config.mode == Mode.PRODUCT:
                selection = self.scheduler.choose({r.topic.topic_id: float(r.max_pages) for r in selected})
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
            "experiment_id": self.run_id,
            "topic_id": request.topic.topic_id,
            "operation": operation,
        }
        elapsed = time.monotonic() - self.begin
        window = WindowResolver.resolve(
            request,
            now_utc(),
            self.config.minimum_video_age_hours,
            elapsed_seconds=elapsed,
            initial=self.initial_windows[request.topic.topic_id],
        )
        bundle = (
            self.collection.discover(
                request,
                window,
                self.config.minimum_video_age_hours,
                self.config.minimum_views,
            )
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
        self.store.save_bundle(bundle, operation)
        self._record(bundle)
        batch = (
            self._enrich(bundle) if self.gap and operation == "discovery" and not self.stop.is_set() else None
        )
        rows = self.processor.process(bundle, batch)
        for kind, row in rows:
            if kind == "trend":
                self.scheduler.feedback(request.topic.topic_id, row.get("youtube_research_score"))
        self.notify(
            {
                "operation": "collection_completed",
                "experiment_id": self.run_id,
                "topic": request.topic.canonical_name,
                "status": bundle.metadata.status,
                "rows": rows,
                "quota": self.quota.usage(),
                "current_window": encode(window) if operation == "discovery" else None,
            }
        )
        if bundle.metadata.status == Status.QUOTA_STOPPED:
            if self.config.endless_mode:
                self.discovery_allowed = self.tracking_allowed = False
            elif operation == "discovery":
                self.discovery_allowed = False
            else:
                self.tracking_allowed = False

    def _wait_for_quota(self) -> None:
        resume = self.quota.next_reset()
        elapsed = time.monotonic() - self.begin
        current_windows = {
            request.topic.topic_id: encode(
                WindowResolver.resolve(
                    request,
                    now_utc(),
                    self.config.minimum_video_age_hours,
                    elapsed_seconds=elapsed,
                    initial=self.initial_windows[request.topic.topic_id],
                )
            )
            for request in self.config.requests
        }
        self.summary["status"] = Status.WAITING_FOR_QUOTA
        self.notify(
            {
                "operation": "quota_wait",
                "status": Status.WAITING_FOR_QUOTA,
                "expected_resume": iso(resume),
                "last_successful_collection": self.summary.get("last_successful_collection"),
                "current_windows": current_windows,
            }
        )
        while not self.stop.is_set() and self.quota.seconds_until_reset() > 0:
            self.stop.wait(min(1.0, self.quota.seconds_until_reset()))
        if not self.stop.is_set():
            self.discovery_allowed = self.tracking_allowed = True
            self.notify({"operation": "quota_resume", "status": "RUNNING"})

    def _add_warning(self, warning: str) -> None:
        if warning and warning not in self.summary["warnings"]:
            if len(self.summary["warnings"]) < 100:
                self.summary["warnings"].append(warning)

    def _record(self, bundle: CollectionBundle) -> None:
        self.summary["collections"] += 1
        self.summary["raw_observations"] += len(bundle.observations)
        for field in (
            "discovered_video_count",
            "eligible_video_count",
            "excluded_too_young_count",
            "excluded_low_views_count",
            "excluded_missing_views_count",
        ):
            self.summary[field] += getattr(bundle.metadata, field)
        self.summary["partial_collections"] += int(
            bundle.metadata.status in (Status.PARTIAL, Status.QUOTA_STOPPED)
        )
        self.summary["failures"] += int(bundle.metadata.status == Status.FAILED)
        for warning in bundle.warnings:
            self._add_warning(warning)
        if bundle.metadata.status == Status.COMPLETE:
            self.summary["last_successful_collection"] = bundle.metadata.finished_at
        self.monitored.add(bundle.topic.topic_id)
        self.unique_videos.update(v.identity.video_id for v in bundle.observations)
        self.unique_creators.update(v.identity.channel_id for v in bundle.observations)

    def _enrich(self, bundle: CollectionBundle):
        telemetry_start = len(self.client.telemetry)
        self.client.context["operation"] = "gap_enrichment"
        try:
            batch = self.gap.enrich(bundle)
            if not batch.videos:
                self._add_warning("Gap unavailable: no videos have a complete eligible creator baseline")
            return batch
        except (YouTubeApiError, QuotaStopped, RequestCancelled) as exc:
            self._add_warning(str(exc))
            return None

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
        if self.config.mode == Mode.PRODUCT:
            pass
        if hasattr(self.client, "close"):
            self.client.close()
        self.notify({"operation": "experiment_finished", **self.summary})
