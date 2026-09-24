import concurrent.futures
from datetime import timedelta

from research.core.time import iso, now_utc
from research.storage.db import DatabaseStorageAdapter
from research.workers.collector import (
    IngestionWorker,
    resolve_effective_cadence,
)
from tests.test_research_ingestion import FakeClient


def test_resolve_effective_cadence():
    # Normal > 20%
    assert resolve_effective_cadence("FAST", 100.0) == "FAST"
    assert resolve_effective_cadence("REGULAR", 50.0) == "REGULAR"
    assert resolve_effective_cadence("SLOW", 30.0) == "SLOW"

    # <= 20% Throttled down
    assert resolve_effective_cadence("FAST", 20.0) == "REGULAR"
    assert resolve_effective_cadence("REGULAR", 15.0) == "SLOW"
    assert resolve_effective_cadence("SLOW", 5.0) == "SLOW"

    # 0% Halts discovery
    assert resolve_effective_cadence("FAST", 0.0) == "THROTTLED_HALT"
    assert resolve_effective_cadence("REGULAR", 0.0) == "THROTTLED_HALT"


def test_worker_single_step_lifecycle(tmp_path):
    db_path = tmp_path / "fleet.db"
    db = DatabaseStorageAdapter(db_path)
    client = FakeClient()

    db.seed_category("gaming", "Gaming")
    db.register_topic(
        topic_id="minecraft",
        canonical_name="Minecraft",
        query="minecraft",
        category_id="gaming",
        cadence="REGULAR",
    )

    worker = IngestionWorker(db=db, client=client, worker_id="worker_alpha")
    result = worker.step()

    assert result is not None
    assert result["worker_id"] == "worker_alpha"
    assert result["topic_id"] == "minecraft"
    assert result["operation"] == "discovery"
    assert result["status"] == "COMPLETE"

    # Verify bundle was saved into database
    bundles = db.list_bundles("minecraft")
    assert len(bundles) == 1
    bundle = db.get_bundle(bundles[0]["batch_id"])
    assert bundle is not None
    assert bundle.topic.canonical_name == "Minecraft"

    # Verify trend metric was recorded
    trend = db.get_latest_trend("minecraft")
    assert trend is not None

    # Verify lease was cleared and next_discovery_at moved to future
    topic_after = db.get_topic("minecraft")
    assert topic_after.locked_by is None
    assert topic_after.locked_until is None


def test_worker_adaptive_quota_throttle(tmp_path):
    db_path = tmp_path / "fleet.db"
    db = DatabaseStorageAdapter(db_path)
    client = FakeClient()

    db.seed_category("gaming", "Gaming")
    db.register_topic(
        topic_id="fast_topic",
        canonical_name="Fast Topic",
        query="fast topic",
        category_id="gaming",
        cadence="FAST",
    )

    # Debit quota so that remaining search quota is <= 20%
    # Search budget = 10, debit 8 -> 2 remaining = 20%
    db.debit_quota("DEFAULT", "search", units=8, search_budget=10, reserve_percent=0.0)
    assert db.get_remaining_search_quota_percent("DEFAULT", search_budget=10) == 20.0

    worker = IngestionWorker(db=db, client=client, worker_id="worker_beta", search_budget=10)
    result = worker.step()

    assert result is not None
    # Cadence was throttled down from FAST to REGULAR
    assert result["effective_cadence"] == "REGULAR"


def test_worker_halts_discovery_when_quota_exhausted(tmp_path):
    db_path = tmp_path / "fleet.db"
    db = DatabaseStorageAdapter(db_path)
    client = FakeClient()

    db.seed_category("gaming", "Gaming")
    db.register_topic(
        topic_id="halted_topic",
        canonical_name="Halted Topic",
        query="halted topic",
        category_id="gaming",
        cadence="REGULAR",
    )

    # Exhaust all quota (budget 10, debit 10 -> 0 remaining)
    db.debit_quota("DEFAULT", "search", units=10, search_budget=10, reserve_percent=0.0)
    assert db.get_remaining_search_quota_percent("DEFAULT", search_budget=10) == 0.0

    # Ensure tracking is in future so only discovery is due
    future = now_utc() + timedelta(hours=1)
    with db.transaction() as conn:
        conn.execute("UPDATE topics SET next_tracking_at = %s WHERE id = 'halted_topic'", (iso(future),))

    worker = IngestionWorker(db=db, client=client, worker_id="worker_gamma", search_budget=10)
    result = worker.step()

    assert result is not None
    assert result["status"] == "SKIPPED_QUOTA_EXHAUSTION"


def test_multi_worker_fleet_parallel_execution(tmp_path):
    db_path = tmp_path / "fleet.db"
    db = DatabaseStorageAdapter(db_path)
    client = FakeClient()

    db.seed_category("tech_ai", "AI & Tech")
    for i in range(8):
        db.register_topic(
            topic_id=f"topic_{i}",
            canonical_name=f"Topic {i}",
            query=f"query {i}",
            category_id="tech_ai",
            cadence="REGULAR",
            priority=float(i),
        )

    processed_topics = []

    def run_worker_cycle(worker_num: int):
        worker = IngestionWorker(db=db, client=client, worker_id=f"fleet_worker_{worker_num}")
        # Run up to 4 cycles per worker
        cycles = 0
        while cycles < 4:
            res = worker.step()
            if not res:
                break
            processed_topics.append((worker_num, res["topic_id"]))
            cycles += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run_worker_cycle, i) for i in range(4)]
        concurrent.futures.wait(futures)

    # Verify all 8 topics were processed
    distinct_topics_processed = {t[1] for t in processed_topics}
    assert len(distinct_topics_processed) == 8
