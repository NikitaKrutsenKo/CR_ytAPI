from datetime import timedelta
from threading import Event

from research.api.profiles import ApiProfiles
from research.api.quota import QuotaEstimate, QuotaStopped
from research.core.domain import CandidateTopic, CollectionRequest, ExperimentConfig, Mode
from research.core.time import now_utc
from research.orchestration.application import ExperimentManager
from research.storage.io import read_json, read_jsonl
from tests.test_research_flows import RunClient


class ResetImmediatelyQuota:
    def __init__(self):
        self.reset_checks = 0

    def estimate(self, config):
        return QuotaEstimate(24, 48, "WOULD EXCEED BUDGET", endless=True)

    def usage(self):
        return {"day": "test", "actual_local_call_count": 0}

    def next_reset(self):
        return now_utc()

    def seconds_until_reset(self):
        self.reset_checks += 1
        return 0


def test_endless_quota_pause_resumes_same_experiment_and_stop_works(tmp_path):
    stop = Event()
    clients = []

    class QuotaThenSuccess(RunClient):
        searches = 0

        def get(self, resource, params):
            if resource == "search":
                self.searches += 1
                if self.searches == 1:
                    raise QuotaStopped("Synthetic daily quota exhausted")
            result = super().get(resource, params)
            if resource == "videos":
                stop.set()
            return result

    def factory(*args, **kwargs):
        client = QuotaThenSuccess()
        clients.append(client)
        return client

    config = ExperimentConfig(
        Mode.TREND,
        (CollectionRequest(CandidateTopic.named("AI")),),
        duration_hours=0.000001,
        endless_mode=True,
        discovery_minutes=0.00001,
        tracking_minutes=1,
    )
    manager = ExperimentManager(
        tmp_path, ApiProfiles(tmp_path / "absent", {"YOUTUBE_API_KEY_DEFAULT": "fake"}), factory
    )
    quota = ResetImmediatelyQuota()
    manager.quota = lambda _: quota
    experiment = manager.run(config, stop)

    events = read_jsonl(experiment / "events.jsonl")
    operations = [row["operation"] for row in events]
    assert operations.count("quota_pause") == 1
    assert operations.count("quota_resume") == 1
    assert clients[0].searches == 2
    assert quota.reset_checks == 1
    assert read_json(experiment / "summary.json")["status"] == "CANCELLED"
    assert read_json(experiment / "runtime.json")["experiment_started_at"]
    assert len(list((tmp_path / "experiments").glob("exp_*"))) == 1


def test_endless_estimate_is_per_hour_and_pacific_day(tmp_path):
    from research.api.quota import QuotaManager

    config = ExperimentConfig(
        Mode.TREND,
        (CollectionRequest(CandidateTopic.named("AI")),),
        endless_mode=True,
        discovery_minutes=30,
        tracking_minutes=15,
        max_retries=0,
        search_budget=1000,
        other_budget=100000,
    )
    estimate = QuotaManager(tmp_path / "quota.sqlite", "DEFAULT", 1000, 100000).estimate(config)
    assert estimate.endless
    assert estimate.search_calls_per_hour == 2
    assert estimate.search_calls_per_pacific_day == 48
    assert "per Pacific day" in estimate.label


def test_pacific_reset_uses_timezone_boundary_and_dst(tmp_path):
    from datetime import UTC, datetime

    from research.api.quota import QuotaManager

    before_spring_dst = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    quota = QuotaManager(tmp_path / "quota.sqlite", "DEFAULT", clock=lambda: before_spring_dst)
    reset = quota.next_reset()
    assert reset.tzinfo is not None
    assert reset > before_spring_dst
    assert reset - before_spring_dst < timedelta(hours=24)
