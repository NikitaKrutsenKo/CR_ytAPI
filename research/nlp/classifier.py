"""Topic classification protocols and semantic category assignment models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ClassificationResult:
    """Result of NLP category classification and candidate alias extraction."""

    category_id: str
    confidence: float
    candidate_aliases: tuple[str, ...] = ()
    secondary_category_id: str | None = None


class TopicClassifier(Protocol):
    """Protocol contract for asynchronous topic category classifiers."""

    def classify(self, topic_name: str, context_text: str | None = None) -> ClassificationResult:
        """Classify a topic string and optional context into a category pool."""
        ...


CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "gaming": {
        "minecraft",
        "cs2",
        "counter strike",
        "gta",
        "fortnite",
        "valorant",
        "roblox",
        "elden ring",
        "gameplay",
        "speedrun",
        "esports",
        "twitch",
        "gaming",
        "steam",
        "playstation",
        "xbox",
        "nintendo",
        "boss fight",
        "rpg",
        "fps",
        "deadlock",
    },
    "tech_ai": {
        "chatgpt",
        "openai",
        "claude",
        "gemini",
        "deepseek",
        "llm",
        "neural",
        "ai",
        "artificial intelligence",
        "machine learning",
        "gpu",
        "nvidia",
        "software",
        "python",
        "programming",
        "coding",
        "robotics",
        "tech",
        "algorithm",
    },
    "finance_crypto": {
        "bitcoin",
        "ethereum",
        "crypto",
        "btc",
        "eth",
        "stock",
        "stocks",
        "market",
        "investing",
        "trading",
        "fed",
        "inflation",
        "finance",
        "economy",
        "dividend",
        "etf",
        "bull run",
    },
    "science_space": {
        "nasa",
        "spacex",
        "mars",
        "moon",
        "james webb",
        "physics",
        "quantum",
        "astronomy",
        "biology",
        "crispr",
        "relativity",
        "black hole",
        "telescope",
        "science",
    },
    "entertainment": {
        "movie",
        "trailer",
        "box office",
        "actor",
        "hollywood",
        "netflix",
        "anime",
        "cinema",
        "music",
        "song",
        "album",
        "concert",
        "pop",
        "rock",
        "celebrity",
        "series",
    },
}


class SemanticCentroidClassifier:
    """Lightweight, deterministic category classifier matching keywords and n-grams.

    Operates strictly offline without external network or heavy runtime dependencies.
    """

    def __init__(
        self,
        keywords: dict[str, set[str]] | None = None,
        fallback_threshold: float = 0.25,
        version: str = "centroid_v1",
    ) -> None:
        self.keywords = keywords or CATEGORY_KEYWORDS
        self.fallback_threshold = fallback_threshold
        self.version = version

    def classify(self, topic_name: str, context_text: str | None = None) -> ClassificationResult:
        """Classify a topic into the most suitable category or fallback to 'uncategorized'."""
        text = f"{topic_name} {context_text or ''}".lower()
        tokens = set(re.findall(r"\w+", text))

        best_category = "uncategorized"
        best_score = 0.0
        scores: dict[str, float] = {}

        for category, kws in self.keywords.items():
            matches = 0
            for kw in kws:
                if " " in kw:
                    if kw in text:
                        matches += 2
                elif kw in tokens:
                    matches += 1

            if matches > 0:
                score = min(0.95, 0.50 + matches * 0.15)
                scores[category] = score
                if score > best_score:
                    best_score = score
                    best_category = category

        # Extract candidate aliases (e.g. variations of topic name)
        aliases = []
        cleaned = re.sub(r"[^\w\s]", "", topic_name).strip()
        if cleaned.lower() != topic_name.lower():
            aliases.append(cleaned)
        if len(tokens) > 1:
            aliases.append("-".join(re.findall(r"\w+", topic_name.lower())))

        if best_score < self.fallback_threshold:
            return ClassificationResult(
                category_id="uncategorized",
                confidence=0.20,
                candidate_aliases=tuple(aliases),
            )

        sorted_categories = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        secondary = sorted_categories[1][0] if len(sorted_categories) > 1 else None

        return ClassificationResult(
            category_id=best_category,
            confidence=best_score,
            candidate_aliases=tuple(aliases),
            secondary_category_id=secondary,
        )
