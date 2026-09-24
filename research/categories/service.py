"""Category pool management, seeding, and aggregate metric rollups."""

from __future__ import annotations

from typing import Any

from research.storage.db import DatabaseStorageAdapter

DEFAULT_CATEGORIES = [
    ("gaming", "Gaming"),
    ("tech_ai", "AI & Technology"),
    ("finance_crypto", "Finance & Crypto"),
    ("science_space", "Science & Space"),
    ("entertainment", "Entertainment & Pop Culture"),
    ("uncategorized", "Uncategorized"),
]


def seed_default_categories(db: DatabaseStorageAdapter) -> None:
    """Seed canonical categories into database if not present."""
    for cat_id, cat_name in DEFAULT_CATEGORIES:
        db.seed_category(category_id=cat_id, name=cat_name, status="ACTIVE")


def compute_category_rollups(db: DatabaseStorageAdapter) -> dict[str, dict[str, Any]]:
    """Compute aggregate momentum and breakout count rollups for each category."""
    categories = db.list_categories()
    results = {}

    for cat in categories:
        cat_id = cat["id"]
        topics = db.list_topics(category_id=cat_id, status="ACTIVE")
        if not topics:
            db.update_category_stats(cat_id, aggregate_momentum=0.0, breakout_count=0)
            results[cat_id] = {"aggregate_momentum": 0.0, "breakout_count": 0, "topic_count": 0}
            continue

        scores = []
        breakout_count = 0

        for t in topics:
            if t.lifecycle_state == "BREAKOUT":
                breakout_count += 1

            latest_trend = db.get_latest_trend(t.id)
            if latest_trend and latest_trend.get("youtube_trend_score") is not None:
                scores.append(float(latest_trend["youtube_trend_score"]))
            elif latest_trend and latest_trend.get("velocity") is not None:
                scores.append(float(latest_trend["velocity"]))

        momentum = sum(scores) / len(scores) if scores else 0.0
        db.update_category_stats(cat_id, aggregate_momentum=momentum, breakout_count=breakout_count)
        results[cat_id] = {
            "aggregate_momentum": momentum,
            "breakout_count": breakout_count,
            "topic_count": len(topics),
        }

    return results
