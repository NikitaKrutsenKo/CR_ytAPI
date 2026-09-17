from __future__ import annotations

import logging
import time
from uuid import uuid4

from research.domain import (
    CollectionBundle,
    CollectionMetadata,
    CollectionRequest,
    Status,
    VideoIdentity,
    VideoObservation,
    fingerprint,
    iso,
    now_utc,
    utc,
)
from research.quota import QuotaStopped
from youtube.client import RequestCancelled, YouTubeApiError, YouTubeQuotaExceeded
from youtube.search import YouTubeSearchService
from youtube.videos import YouTubeVideoService

log = logging.getLogger(__name__)


def observation(item: dict, timestamp: str) -> VideoObservation:
    """Validate observed counters; unavailable fields remain missing."""
    snippet, stats = item["snippet"], item.get("statistics", {})
    published = iso(utc(snippet["publishedAt"]))
    counters = [
        int(stats[k]) if stats.get(k) is not None else None
        for k in ("viewCount", "likeCount", "commentCount")
    ]
    if any(v is not None and v < 0 for v in counters):
        raise ValueError("Negative cumulative counter")
    return VideoObservation(
        VideoIdentity(item["id"], snippet["channelId"], published, snippet.get("title", "")),
        timestamp,
        *counters,
    )


class YouTubeDiscoveryCollector:
    """Collect unfiltered discovery once; preserve successful pages on interruption."""

    def __init__(self, client):
        self.client = client
        self.search = YouTubeSearchService(client)
        self.videos = YouTubeVideoService(client)

    def collect(self, request: CollectionRequest, last_success=None, now=None) -> CollectionBundle:
        now = now or now_utc()
        start, end, effective = request.bounds(now, last_success)
        started = time.monotonic()
        telemetry_start = len(self.client.telemetry)
        identities, observations, warnings = {}, [], []
        received = pages = requested = 0
        token = None
        tokens = set()
        status, truncated = Status.COMPLETE, False
        try:
            for _ in range(request.max_pages):
                requested += 1
                page = self.search.page(
                    request.topic.canonical_name,
                    request.page_size,
                    request.order,
                    iso(effective),
                    iso(end),
                    token,
                )
                pages += 1
                for item in page.get("items", []):
                    received += 1
                    try:
                        snippet = item["snippet"]
                        identity = VideoIdentity(
                            item["id"]["videoId"],
                            snippet["channelId"],
                            iso(utc(snippet["publishedAt"])),
                            snippet.get("title", ""),
                        )
                        identities.setdefault(identity.video_id, identity)
                    except (KeyError, ValueError, TypeError):
                        warnings.append("Malformed discovery item omitted")
                        status = Status.PARTIAL
                log.info(
                    "page_collected topic_id=%s page=%s items=%s",
                    request.topic.topic_id,
                    pages,
                    len(page.get("items", [])),
                )
                token = page.get("nextPageToken")
                if not token:
                    break
                if token in tokens:
                    warnings.append("Repeated page token; pagination stopped")
                    status = Status.PARTIAL
                    break
                tokens.add(token)
            truncated = bool(token)
            ids = list(identities)
            for offset in range(0, len(ids), 50):
                details = self.videos.get_videos(ids[offset : offset + 50])
                timestamp = iso(now_utc())
                for item in details:
                    try:
                        observations.append(observation(item, timestamp))
                    except (ValueError, KeyError, TypeError):
                        warnings.append("Malformed video statistics omitted")
            if len(observations) < len(identities):
                status = Status.PARTIAL
                warnings.append("Some discovered videos are unavailable")
        except (QuotaStopped, YouTubeQuotaExceeded) as exc:
            status = Status.QUOTA_STOPPED
            warnings.append(str(exc))
        except RequestCancelled:
            status = Status.CANCELLED
        except YouTubeApiError as exc:
            status = Status.PARTIAL if pages else Status.FAILED
            warnings.append(str(exc))
        finished = now_utc()
        metadata = CollectionMetadata(
            uuid4().hex,
            iso(now),
            request.topic.canonical_name,
            iso(start),
            iso(end),
            iso(effective),
            iso(end),
            request.window_mode,
            request.rolling_hours,
            request.page_size,
            request.max_pages,
            requested,
            pages,
            received,
            len(identities),
            max(0, received - len(identities) - sum(w.startswith("Malformed discovery") for w in warnings)),
            self.client.profile,
            iso(now),
            iso(finished),
            (time.monotonic() - started) * 1000,
            status,
            truncated=truncated,
            config_hash=fingerprint(request),
            aliases=request.topic.aliases,
            order=request.order,
        )
        log.info(
            "collection_completed batch_id=%s topic_id=%s status=%s unique=%s",
            metadata.batch_id,
            request.topic.topic_id,
            status,
            len(identities),
        )
        return CollectionBundle(
            request.topic,
            metadata,
            tuple(observations),
            tuple(identities.values()),
            tuple(self.client.telemetry[telemetry_start:]),
            tuple(warnings),
        )
