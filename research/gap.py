from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import logging
from research.domain import CollectionBundle, ExperimentConfig, utc, iso, now_utc
from research.discovery import observation
from research.storage import read_json, write_json
from models.batch import Topic, CollectionInfo, YouTubeBatch
from models.raw_video import RawTopicVideo, RecentVideoStat
from metrics.engine import MetricEngine
from metrics.calculators import MetricConfig
from state.manager import MetricsState
from youtube.videos import YouTubeVideoService

log = logging.getLogger(__name__)


class GapEngine(MetricEngine):
    """Named boundary around the original, unchanged Gap mathematics."""


class GapEnricher:
    """Creator cache with TTL; exclusion applies only to Gap and never edits a bundle.

    Cache stores raw baseline observations with their original counter timestamps.
    Baseline age is evaluated at its own observation time, so cached counters are
    never made to appear newly observed. Every run persists the selected batch.
    """
    def __init__(self, client, cache_path: Path, config: ExperimentConfig):
        self.client, self.cache_path, self.config = client, cache_path, config
        self.cache = read_json(cache_path) if cache_path.exists() else {}
        self.videos = YouTubeVideoService(client)

    def enrich(self, bundle: CollectionBundle) -> YouTubeBatch:
        now = now_utc()
        channels = sorted({v.identity.channel_id for v in bundle.observations})
        excluded = {v.video_id for v in bundle.discovery}
        stale = [c for c in channels if c not in self.cache or
                 now - utc(self.cache[c]["last_refresh"]) >= timedelta(hours=self.config.baseline_ttl_hours)]
        uploads = {}
        for offset in range(0, len(stale), 50):
            response = self.client.get("channels", {"part": "contentDetails", "id": ",".join(stale[offset:offset+50])})
            for item in response.get("items", []):
                uploads[item["id"]] = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        selected = {}
        for channel in stale:
            ids, token = [], None
            for _ in range(self.config.baseline_pages):
                if not uploads.get(channel):
                    break
                params = {"part": "contentDetails", "playlistId": uploads[channel], "maxResults": 50}
                if token:
                    params["pageToken"] = token
                page = self.client.get("playlistItems", params)
                for item in page.get("items", []):
                    identifier = item.get("contentDetails", {}).get("videoId")
                    if identifier and identifier not in excluded and identifier not in ids:
                        ids.append(identifier)
                token = page.get("nextPageToken")
                if len(ids) >= self.config.baseline_max or not token:
                    break
            selected[channel] = ids[:self.config.baseline_max]
        details = {item["id"]: item for item in self.videos.get_videos_batched(v for ids in selected.values() for v in ids)}
        timestamp = iso(now_utc())
        for channel, identifiers in selected.items():
            records = []
            for identifier in identifiers:
                if identifier in details:
                    try:
                        records.append(observation(details[identifier], timestamp))
                    except (KeyError, ValueError, TypeError):
                        log.warning("baseline_video_invalid channel_id=%s video_id=%s", channel, identifier)
            self.cache[channel] = {"uploads_playlist_id": uploads.get(channel), "recent_video_ids": identifiers,
                                   "last_refresh": timestamp, "observations": records}
        write_json(self.cache_path, self.cache)
        # Reload into a single JSON shape whether entries came from memory or disk.
        self.cache = read_json(self.cache_path)
        videos = []
        for current in bundle.observations:
            identity = current.identity
            baseline = []
            for previous in self.cache.get(identity.channel_id, {}).get("observations", []):
                if previous["identity"]["video_id"] in excluded or previous["views"] is None:
                    continue
                age = (utc(previous["timestamp"]) - utc(previous["identity"]["published_at"])).total_seconds()/3600
                baseline.append(RecentVideoStat(previous["identity"]["video_id"], max(age, 0), previous["views"]))
            if len(baseline) < self.config.baseline_min or any(v is None for v in (current.views, current.likes, current.comments)):
                continue
            videos.append(RawTopicVideo(identity.video_id, identity.channel_id, identity.published_at,
                                       current.views, current.likes, current.comments, len(baseline), baseline))
        return YouTubeBatch("1.0", Topic(bundle.topic.topic_id, bundle.topic.canonical_name),
                            CollectionInfo(bundle.metadata.finished_at, bundle.metadata.batch_id, len(videos)), videos)
