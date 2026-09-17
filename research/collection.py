from __future__ import annotations

import logging
from uuid import uuid4

from research.discovery import YouTubeDiscoveryCollector, observation
from research.domain import (
    CollectionBundle,
    CollectionMetadata,
    Status,
    iso,
    now_utc,
)
from research.quota import QuotaStopped
from youtube.client import RequestCancelled, YouTubeApiError, YouTubeQuotaExceeded
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
                for item in YouTubeVideoService(self.client).get_videos(ids[offset : offset + 50]):
                    try:
                        rows.append(observation(item, iso(now_utc())))
                    except (KeyError, ValueError, TypeError):
                        warnings.append("Malformed tracking observation")
            if len(rows) < len(ids):
                status = Status.PARTIAL
                warnings.append("Some tracked videos unavailable")
        except (QuotaStopped, YouTubeQuotaExceeded) as exc:
            status = Status.QUOTA_STOPPED
            warnings.append(str(exc))
        except RequestCancelled:
            status = Status.CANCELLED
        except YouTubeApiError as exc:
            status = Status.PARTIAL if rows else Status.FAILED
            warnings.append(str(exc))
        finished = now_utc()
        meta = CollectionMetadata(
            uuid4().hex,
            iso(now),
            request.topic.canonical_name,
            iso(now),
            iso(now),
            iso(now),
            iso(now),
            "TRACKING",
            0,
            50,
            0,
            0,
            0,
            len(rows),
            len(rows),
            0,
            self.client.profile,
            iso(now),
            iso(finished),
            (finished - now).total_seconds() * 1000,
            status,
            operation="tracking",
        )
        return CollectionBundle(
            request.topic,
            meta,
            tuple(rows),
            telemetry=tuple(self.client.telemetry[telemetry_start:]),
            warnings=tuple(warnings),
        )
