from __future__ import annotations
from dataclasses import replace, asdict
import logging
from pathlib import Path
from threading import Event
import time
from uuid import uuid4
from research.domain import *
from research.discovery import YouTubeDiscoveryCollector, observation
from research.gap import GapEnricher
from research.processing import MetricProcessor
from research.profiles import ApiProfiles
from research.quota import QuotaManager, QuotaStopped
from research.scheduling import TopicScheduler
from research.storage import TopicWorkspaceManager, write_json, append_jsonl
from youtube.client import YouTubeClient, YouTubeApiError, RequestCancelled
from youtube.videos import YouTubeVideoService

log = logging.getLogger(__name__)


class CollectionService:
    """Discovery watermarks advance only after a complete, uncapped collection."""
    def __init__(self, client):
        self.collector, self.client = YouTubeDiscoveryCollector(client), client
        self.watermarks = {}

    def discover(self, request):
        bundle = self.collector.collect(request, self.watermarks.get(request.topic.topic_id))
        if bundle.metadata.status == Status.COMPLETE and not bundle.metadata.truncated:
            self.watermarks[request.topic.topic_id] = bundle.metadata.effective_to
        return bundle

    def track(self, request, identifiers):
        now = now_utc()
        rows, warnings = [], []
        status = Status.COMPLETE
        telemetry_start = len(self.client.telemetry)
        try:
            ids = list(identifiers)
            for offset in range(0, len(ids), 50):
                for item in YouTubeVideoService(self.client).get_videos(ids[offset:offset+50]):
                    try:
                        rows.append(observation(item, iso(now_utc())))
                    except (KeyError, ValueError, TypeError):
                        warnings.append("Malformed tracking observation")
            if len(rows) < len(ids):
                status = Status.PARTIAL
                warnings.append("Some tracked videos unavailable")
        except QuotaStopped as exc:
            status = Status.QUOTA_STOPPED
            warnings.append(str(exc))
        except RequestCancelled:
            status = Status.CANCELLED
        except YouTubeApiError as exc:
            status = Status.PARTIAL if rows else Status.FAILED
            warnings.append(str(exc))
        finished = now_utc()
        meta = CollectionMetadata(uuid4().hex, iso(now), request.topic.canonical_name, iso(now), iso(now),
            iso(now), iso(now), "TRACKING", 0, 50, 0, 0, 0, len(rows), len(rows), 0, self.client.profile,
            iso(now), iso(finished), (finished-now).total_seconds()*1000, status, operation="tracking")
        return CollectionBundle(request.topic, meta, tuple(rows), telemetry=tuple(self.client.telemetry[telemetry_start:]), warnings=tuple(warnings))


class ExperimentManager:
    """Owns one finite run. GUI receives events; domain engines never see widgets."""
    def __init__(self, root: Path, profiles: ApiProfiles | None = None, client_factory=YouTubeClient):
        self.root, self.store = Path(root), TopicWorkspaceManager(root)
        self.profiles = profiles or ApiProfiles(self.root / ".env")
        self.client_factory = client_factory

    def quota(self, config):
        return QuotaManager(self.root / "data" / "quota.sqlite", config.api_profile_name,
                            config.search_budget, config.other_budget, config.reserve_percent)

    def estimate(self, config):
        return self.quota(config).estimate(config)

    def run(self, config: ExperimentConfig, stop: Event | None = None, notify=lambda event: None) -> Path:
        config.validate()
        # Resolve and validate everything before creating a run or making API calls.
        secret = self.profiles.resolve(config.api_profile_name)
        quota = self.quota(config)
        estimate = quota.estimate(config)
        if estimate.status == "WOULD EXCEED BUDGET":
            raise QuotaStopped("Experiment would exceed configured quota budget")
        from metrics.calculators import MetricConfig
        from research.trend import TrendConfig
        MetricConfig(**config.gap_formula).validate()
        TrendConfig(**config.trend_formula)
        stop = stop or Event()
        experiment = self.store.create(config)
        client = self.client_factory(secret, quota=quota, profile=config.api_profile_name,
                                     max_retries=config.max_retries, cancelled=stop.is_set)
        collection = CollectionService(client)
        processor = MetricProcessor(config, self.store, experiment)
        gap = GapEnricher(client, self.root / "data" / "creator_cache.json", config) if config.mode in (Mode.GAP, Mode.COMBINED, Mode.PRODUCT) else None
        scheduler = TopicScheduler(tuple(r.topic for r in config.requests), config.exploration_fraction)
        summary = {"experiment_id": experiment.name, "mode": config.mode, "status": "RUNNING", "collections": 0,
                   "partial_collections": 0, "failures": 0, "raw_observations": 0, "warnings": [], "topics_monitored": [],
                   "unique_videos": 0, "unique_creators": 0, "duration_seconds": 0.0}
        unique_videos, unique_creators, monitored = set(), set(), set()
        begin = time.monotonic()
        deadline = begin+config.duration_minutes*60
        next_discovery, next_tracking = begin, begin+config.tracking_minutes*60
        discovery_allowed, tracking_allowed = True, True
        self.store.event(experiment, "experiment_created", config_hash=fingerprint(config))
        notify({"operation": "experiment_created", "experiment_id": experiment.name})
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                clock = time.monotonic()
                jobs = []
                if discovery_allowed and clock >= next_discovery:
                    selected = config.requests
                    if config.mode == Mode.PRODUCT:
                        selection = scheduler.choose({r.topic.topic_id: float(r.max_pages) for r in config.requests})
                        self.store.event(experiment, "topic_selected", **asdict(selection))
                        selected = tuple(r for r in config.requests if r.topic.topic_id == selection.topic_id)
                    jobs.extend(("discovery", r) for r in selected)
                    next_discovery = clock+config.discovery_minutes*60
                if tracking_allowed and config.mode not in (Mode.GAP, Mode.HISTORICAL) and clock >= next_tracking:
                    jobs.extend(("tracking", r) for r in config.requests if processor.registry(r.topic.topic_id).ids)
                    next_tracking = clock+config.tracking_minutes*60
                for operation, request in jobs:
                    if stop.is_set():
                        break
                    bundle = collection.discover(request) if operation == "discovery" else collection.track(request, processor.registry(request.topic.topic_id).ids)
                    self.store.save_bundle(experiment, bundle)
                    self.store.state(experiment, request.topic.topic_id, "discovery", collection.watermarks)
                    summary["collections"] += 1
                    summary["raw_observations"] += len(bundle.observations)
                    summary["partial_collections"] += int(bundle.metadata.status in (Status.PARTIAL, Status.QUOTA_STOPPED))
                    summary["failures"] += int(bundle.metadata.status == Status.FAILED)
                    summary["warnings"].extend(bundle.warnings)
                    monitored.add(request.topic.topic_id)
                    unique_videos.update(v.identity.video_id for v in bundle.observations)
                    unique_creators.update(v.identity.channel_id for v in bundle.observations)
                    gap_batch = None
                    if gap and operation == "discovery" and not stop.is_set():
                        telemetry_start = len(client.telemetry)
                        try:
                            gap_batch = gap.enrich(bundle)
                            write_json(experiment / "enrichment" / (bundle.metadata.batch_id + ".json"), gap_batch.to_dict(), True)
                        except (YouTubeApiError, QuotaStopped, RequestCancelled) as exc:
                            summary["warnings"].append(str(exc))
                            self.store.event(experiment, "gap_enrichment_unavailable", batch_id=bundle.metadata.batch_id, reason=str(exc))
                        finally:
                            for row in client.telemetry[telemetry_start:]:
                                append_jsonl(experiment / "telemetry.jsonl", row)
                    rows = processor.process(bundle, gap_batch)
                    for kind, row in rows:
                        if kind == "trend":
                            scheduler.feedback(request.topic.topic_id, row.get("youtube_research_score"))
                    self.store.event(experiment, "collection_completed", topic_id=request.topic.topic_id, batch_id=bundle.metadata.batch_id, status=bundle.metadata.status)
                    notify({"operation": "collection_completed", "experiment_id": experiment.name, "topic": request.topic.canonical_name,
                            "status": bundle.metadata.status, "rows": rows, "quota": quota.usage()})
                    if bundle.metadata.status == Status.QUOTA_STOPPED:
                        if operation == "discovery":
                            discovery_allowed = False
                        else:
                            tracking_allowed = False
                if config.mode == Mode.HISTORICAL:
                    break
                if not discovery_allowed and (not tracking_allowed or config.mode == Mode.GAP or not any(r.ids for r in processor.registries.values())):
                    summary["status"] = "QUOTA_STOPPED"
                    break
                stop.wait(min(.25, max(0, deadline-time.monotonic())))
            if summary["status"] == "RUNNING":
                summary["status"] = "CANCELLED" if stop.is_set() else "COMPLETE"
        except Exception:
            summary["status"] = "FAILED"
            log.exception("experiment_failed experiment_id=%s", experiment.name)
            raise
        finally:
            summary.update(processor.counts)
            summary.update(topics_monitored=sorted(monitored), unique_videos=len(unique_videos), unique_creators=len(unique_creators), duration_seconds=time.monotonic()-begin)
            write_json(experiment / "summary.json", summary)
            write_json(experiment / "quota.json", {"label": "Local estimate", "profile_daily_usage": quota.usage(),
                       "experiment_calls": len(client.telemetry), "estimate": estimate})
            self.store.event(experiment, "experiment_finished", status=summary["status"])
            notify({"operation": "experiment_finished", **summary})
            if hasattr(client, "session"):
                client.session.close()
        return experiment
