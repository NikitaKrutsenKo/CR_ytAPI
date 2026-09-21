"""Summary report generation for simulated product monitoring experiments."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from research.core.domain import ExperimentConfig
from research.storage.io import read_jsonl


class ProductReport:
    """Summarizes topic selections, monitoring coverage, and signal distributions."""

    def build(self, experiment: Path, config: ExperimentConfig, summary: dict) -> dict:
        """Construct the product evaluation report dictionary from experiment outputs."""
        events = read_jsonl(experiment / "events.jsonl")
        selections = [e for e in events if e["operation"] == "topic_selected"]
        report = {
            "label": "Research simulation; not production recommendations",
            "candidate_topics": [r.topic.canonical_name for r in config.requests],
            "topics_monitored": summary["topics_monitored"],
            "selection_allocation": dict(Counter(e["reason"] for e in selections)),
            "strong_trend_signals": [],
            "strong_gap_signals": [],
            "rising": [],
            "breakout": [],
            "decaying": [],
            "weak_signals": [],
            "coverage_gaps": [],
            "quota_allocation": {},
        }
        for request in config.requests:
            topic_id = request.topic.topic_id
            result = experiment / "results" / topic_id
            trend = read_jsonl(result / "trend.jsonl") if (result / "trend.jsonl").exists() else []
            gap = read_jsonl(result / "gap.jsonl") if (result / "gap.jsonl").exists() else []
            report["quota_allocation"][topic_id] = sum(
                e["quota_cost_estimate"] for e in selections if e["topic_id"] == topic_id
            )
            if not trend or any(r["research_gate"] == "INCOMPLETE_OR_CAPPED_COLLECTION" for r in trend):
                report["coverage_gaps"].append(topic_id)
            for row in trend:
                evidence = {
                    "topic_id": topic_id,
                    "timestamp": row["timestamp"],
                    "youtube_research_score": row["youtube_research_score"],
                }
                for state, key in (("RISING", "rising"), ("BREAKOUT", "breakout"), ("COOLING", "decaying")):
                    if row["lifecycle"] == state:
                        report[key].append(evidence)
                if row["youtube_research_score"] is not None:
                    key = "strong_trend_signals" if row["youtube_research_score"] >= 75 else "weak_signals"
                    report[key].append(evidence)
            report["strong_gap_signals"].extend(
                {"topic_id": topic_id, "timestamp": r["timestamp"], "gap_score": r["gap_score"]}
                for r in gap
                if r["gap_score"] >= 0.7
            )
        report["threshold_note"] = (
            "Report labels use research heuristics: YouTube score >=75; Gap >=0.7. "
            "Selection allocation counts planned search pages, not Google billing."
        )
        return report
