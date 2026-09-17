from __future__ import annotations

from dataclasses import dataclass

from research.domain import VideoObservation, encode, utc


@dataclass(frozen=True)
class CounterDelta:
    """Actual elapsed-time counter changes; corrections are flagged, not growth."""

    video_id: str
    timestamp: str
    elapsed_hours: float
    delta_views: int | None
    delta_likes: int | None
    delta_comments: int | None
    views_per_hour: float | None
    likes_per_hour: float | None
    comments_per_hour: float | None
    like_rate: float | None
    comment_rate: float | None
    correction: bool
    age_bucket: str


class TrackedVideoRegistry:
    """Bounded per-topic registry, preserving raw history outside this latest-state index."""

    def __init__(self, limit=100):
        self.limit = limit
        self.latest: dict[str, VideoObservation] = {}

    @property
    def ids(self):
        return tuple(self.latest)

    def observe(self, observations: tuple[VideoObservation, ...]) -> list[CounterDelta]:
        results = []
        for current in observations:
            identifier = current.identity.video_id
            previous = self.latest.get(identifier)
            if previous:
                hours = (utc(current.timestamp) - utc(previous.timestamp)).total_seconds() / 3600
                if hours <= 0:
                    continue
                changes = [
                    None if a is None or b is None else a - b
                    for a, b in zip(
                        (current.views, current.likes, current.comments),
                        (previous.views, previous.likes, previous.comments),
                    )
                ]
                correction = any(v is not None and v < 0 for v in changes)
                rates = [None if v is None or v < 0 else v / hours for v in changes]
                views, likes, comments = changes
                eligible = not correction and views is not None and views >= 100
                results.append(
                    CounterDelta(
                        identifier,
                        current.timestamp,
                        hours,
                        *changes,
                        *rates,
                        likes / views if eligible and likes is not None else None,
                        comments / views if eligible and comments is not None else None,
                        correction,
                        self.age_bucket(current),
                    )
                )
            self.latest[identifier] = current
        # Deterministic recency selection prevents unbounded growth in long runs.
        ranked = sorted(
            self.latest.values(), key=lambda v: (v.timestamp, v.views or 0, v.identity.video_id), reverse=True
        )
        self.latest = {v.identity.video_id: v for v in ranked[: self.limit]}
        return results

    @staticmethod
    def age_bucket(observation: VideoObservation) -> str:
        """Coarse, versioned same-age cohorts for reaction percentile comparisons."""
        hours = (utc(observation.timestamp) - utc(observation.identity.published_at)).total_seconds() / 3600
        return "0_24h" if hours < 24 else "24_168h" if hours < 168 else "168h_plus"

    def state(self) -> dict:
        return {identifier: encode(v) for identifier, v in self.latest.items()}
