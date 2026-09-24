import json
import threading
import urllib.error
import urllib.request

import pytest

from research.categories.service import seed_default_categories
from research.gateway.server import create_gateway_server
from research.nlp.triage import NLPTriageWorker
from research.storage.db import DatabaseStorageAdapter
from research.workers.collector import IngestionWorker
from tests.test_research_ingestion import FakeClient


@pytest.fixture
def running_gateway(tmp_path):
    db_path = tmp_path / "e2e.db"
    db = DatabaseStorageAdapter(db_path)
    seed_default_categories(db)

    # Bind to port 0 to get an ephemeral free port
    server = create_gateway_server(db, host="127.0.0.1", port=0)
    host, port = server.server_address[0], server.server_address[1]
    base_url = f"http://{host}:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield {"db": db, "base_url": base_url, "server": server}

    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)


def http_get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def http_patch(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_e2e_full_lifecycle(running_gateway):
    base_url = running_gateway["base_url"]
    db = running_gateway["db"]
    client = FakeClient()

    # 1. Main App queries categories -> all 6 categories active, 0 topics
    categories = http_get(f"{base_url}/api/categories")
    assert len(categories) == 6
    gaming_cat = next(c for c in categories if c["id"] == "gaming")
    assert gaming_cat["topic_count"] == 0

    # 2. Main App registers a new topic: Deadlock (Valve gameplay)
    post_res = http_post(
        f"{base_url}/api/topics",
        {"canonical_name": "Deadlock", "query": "deadlock valve gameplay"},
    )
    assert post_res["status"] == "PENDING_CLASSIFICATION"
    topic_id = post_res["topic_id"]

    # 3. NLP Triage Worker classifies the pending topic
    triage_worker = NLPTriageWorker(db=db)
    triaged = triage_worker.step()
    assert len(triaged) == 1
    assert triaged[0]["topic_id"] == topic_id
    assert triaged[0]["category_id"] == "gaming"

    # Verify topic is now ACTIVE and belongs to Gaming category
    topic_record = db.get_topic(topic_id)
    assert topic_record.status == "ACTIVE"
    assert topic_record.category_id == "gaming"

    # 4. Ingestion Worker daemon claims the active topic and collects data
    ingestion_worker = IngestionWorker(db=db, client=client, worker_id="e2e_worker")
    worker_res = ingestion_worker.step()
    assert worker_res is not None
    assert worker_res["topic_id"] == topic_id
    assert worker_res["operation"] == "discovery"
    assert worker_res["status"] == "COMPLETE"

    # 5. Main App verifies updated category topics
    gaming_topics = http_get(f"{base_url}/api/categories/gaming/topics")
    assert len(gaming_topics) == 1
    assert gaming_topics[0]["id"] == topic_id
    assert gaming_topics[0]["canonical_name"] == "Deadlock"
    assert gaming_topics[0]["lifecycle_state"] in ("WATCHING", "RISING", "BREAKOUT")

    # 6. Main App queries historical metrics for plotting
    metrics_res = http_get(f"{base_url}/api/topics/{topic_id}/metrics")
    assert metrics_res["topic_id"] == topic_id
    assert len(metrics_res["trend_history"]) >= 1

    # 7. Main App manually updates cadence to FAST
    patch_res = http_patch(f"{base_url}/api/topics/{topic_id}/cadence", {"cadence": "FAST"})
    assert patch_res["cadence"] == "FAST"

    # Verify in db
    assert db.get_topic(topic_id).cadence == "FAST"
