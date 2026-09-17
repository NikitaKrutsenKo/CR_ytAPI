from __future__ import annotations
from collections import defaultdict
from research.domain import CollectionBundle, KnownEvent, utc, iso


class HistoricalAnalyzer:
    """Publication history only: present-day counters never enter historical features."""
    def publications(self, bundle: CollectionBundle, window_hours=24.0) -> list[dict]:
        start, end = utc(bundle.metadata.requested_from), utc(bundle.metadata.requested_to)
        buckets = defaultdict(dict)
        for item in bundle.discovery:
            published = utc(item.published_at)
            if start <= published < end:
                index = int((published-start).total_seconds()/(window_hours*3600))
                buckets[index][item.video_id] = item
        rows = []
        for index, items in sorted(buckets.items()):
            timestamp = start.fromtimestamp(start.timestamp()+index*window_hours*3600, start.tzinfo)
            rows.append({"timestamp": iso(timestamp), "topic": bundle.topic.canonical_name,
                         "sampled_publication_count": len(items), "unique_creators": len({v.channel_id for v in items.values()}),
                         "analysis_type": "publication_history_sample", "counters_as_of": bundle.metadata.finished_at,
                         "truncated": bundle.metadata.truncated, "collection_status": bundle.metadata.status,
                         "trend_score": None, "velocity": None, "engagement": None,
                         "warning": "Search is a sample; absent publication buckets are not asserted to be zero"})
        return rows


class EventEvaluator:
    """Post-calculation annotations and lead times, never scoring inputs."""
    def evaluate(self, rows: list[dict], event: KnownEvent) -> dict:
        ordered = sorted(rows, key=lambda r: utc(r["timestamp"]))
        def first(state):
            return next((r["timestamp"] for r in ordered if r.get("lifecycle") == state), None)
        rising, breakout, peak = first("RISING"), first("BREAKOUT"), first("PEAK")
        active = [r for r in ordered if r.get("lifecycle") in ("RISING", "BREAKOUT", "PEAK")]
        return {"event_name": event.event_name, "first_rising_time": rising, "first_breakout_time": breakout,
                "peak_time": peak,
                "lead_time_to_event_hours": (utc(event.event_time)-utc(rising)).total_seconds()/3600 if rising else None,
                "peak_offset_from_event_hours": (utc(peak)-utc(event.event_time)).total_seconds()/3600 if peak else None,
                "signal_span_hours": (utc(active[-1]["timestamp"])-utc(active[0]["timestamp"])).total_seconds()/3600 if active else None,
                "note": "Span includes possible gaps; not a fabricated continuous duration"}

    def aligned(self, rows: list[dict], event: KnownEvent) -> list[dict]:
        return [{**r, "event_relative_hours": (utc(r["timestamp"])-utc(event.event_time)).total_seconds()/3600} for r in rows]
