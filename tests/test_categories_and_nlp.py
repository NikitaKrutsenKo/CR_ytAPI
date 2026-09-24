from research.categories.service import (
    DEFAULT_CATEGORIES,
    compute_category_rollups,
    seed_default_categories,
)
from research.core.time import now_utc
from research.nlp.classifier import SemanticCentroidClassifier
from research.nlp.triage import NLPTriageWorker
from research.storage.db import DatabaseStorageAdapter


def test_seed_default_categories(tmp_path):
    db = DatabaseStorageAdapter(tmp_path / "test.db")
    seed_default_categories(db)

    categories = db.list_categories()
    assert len(categories) == len(DEFAULT_CATEGORIES)
    category_ids = {c["id"] for c in categories}
    assert "gaming" in category_ids
    assert "tech_ai" in category_ids
    assert "finance_crypto" in category_ids
    assert "science_space" in category_ids
    assert "entertainment" in category_ids
    assert "uncategorized" in category_ids


def test_category_rollups_computation(tmp_path):
    db = DatabaseStorageAdapter(tmp_path / "test.db")
    seed_default_categories(db)

    # Register two gaming topics: one BREAKOUT with trend 80.0, one WATCHING with trend 40.0
    db.register_topic(
        "topic_breakout", "Minecraft", "minecraft", category_id="gaming", lifecycle_state="BREAKOUT"
    )
    db.register_topic("topic_watching", "CS2", "cs2", category_id="gaming", lifecycle_state="WATCHING")

    now = now_utc()
    db.save_trend_metric("topic_breakout", None, now, {"youtube_trend_score": 80.0, "gate_pass": True})
    db.save_trend_metric("topic_watching", None, now, {"youtube_trend_score": 40.0, "gate_pass": False})

    rollups = compute_category_rollups(db)
    gaming = rollups["gaming"]
    assert gaming["breakout_count"] == 1
    assert gaming["topic_count"] == 2
    assert gaming["aggregate_momentum"] == 60.0  # (80 + 40) / 2

    # Verify category table updated
    cat = db.get_category("gaming")
    assert cat["breakout_count"] == 1
    assert cat["aggregate_momentum"] == 60.0


def test_nlp_classifier_accuracy_and_fallback():
    classifier = SemanticCentroidClassifier()

    # Gaming
    res_gaming = classifier.classify("Minecraft Speedrun World Record")
    assert res_gaming.category_id == "gaming"
    assert res_gaming.confidence >= 0.65

    # Tech AI
    res_ai = classifier.classify("ChatGPT 5 and LLM Reasoning")
    assert res_ai.category_id == "tech_ai"
    assert res_ai.confidence >= 0.65

    # Finance
    res_crypto = classifier.classify("Bitcoin ETF Trading Inflows")
    assert res_crypto.category_id == "finance_crypto"
    assert res_crypto.confidence >= 0.65

    # Science
    res_science = classifier.classify("NASA James Webb Space Telescope Discovery")
    assert res_science.category_id == "science_space"
    assert res_science.confidence >= 0.65

    # Entertainment
    res_ent = classifier.classify("New Hollywood Movie Box Office Trailer")
    assert res_ent.category_id == "entertainment"
    assert res_ent.confidence >= 0.65

    # Unrecognized topic -> graceful fallback to uncategorized
    res_unknown = classifier.classify("Xylophone Zookeeper Kitchen Recipe")
    assert res_unknown.category_id == "uncategorized"
    assert res_unknown.confidence <= 0.25


def test_nlp_triage_worker_promotes_pending_topics(tmp_path):
    db = DatabaseStorageAdapter(tmp_path / "test.db")
    seed_default_categories(db)

    # Register topic in PENDING_CLASSIFICATION state without category
    db.register_topic(
        topic_id="deadlock_topic",
        canonical_name="Deadlock",
        query="deadlock valve gameplay",
        category_id=None,
        status="PENDING_CLASSIFICATION",
    )

    topic_before = db.get_topic("deadlock_topic")
    assert topic_before.status == "PENDING_CLASSIFICATION"
    assert topic_before.category_id is None

    # Run triage worker
    worker = NLPTriageWorker(db=db)
    triaged = worker.step()

    assert len(triaged) == 1
    assert triaged[0]["topic_id"] == "deadlock_topic"
    assert triaged[0]["category_id"] == "gaming"

    # Verify topic is now ACTIVE and assigned to gaming
    topic_after = db.get_topic("deadlock_topic")
    assert topic_after.status == "ACTIVE"
    assert topic_after.category_id == "gaming"
    assert topic_after.classification_confidence is not None
