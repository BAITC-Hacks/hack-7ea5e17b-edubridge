from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from threading import Event

import pytest

from wind_agent.agent import AgentService
from wind_agent.agent.fixtures import FixtureModel, FixtureWeather
from wind_agent.agent.monitor import AgentMonitor, acquire_monitor_owner
from wind_agent.config import load_settings


START = datetime(2026, 1, 31, 18, tzinfo=timezone.utc)


class MutableWeather(FixtureWeather):
    def __init__(self, turbines):
        super().__init__(turbines)
        self.version = 1
        self.calls = []
        self.failures_left = 0
        self.late = False

    def fetch(self, issue, horizon, turbine_ids):
        self.calls.append(issue)
        if self.failures_left:
            self.failures_left -= 1
            raise TimeoutError("temporary test outage")
        rows = super().fetch(issue, horizon, turbine_ids)
        for row in rows:
            row.wind_speed_ms += self.version
            row.raw_sha256 = sha256(str(self.version).encode()).hexdigest()
            if self.late:
                row.forecast_available_at = issue + timedelta(minutes=1)
        return rows


class Clock:
    def __init__(self, now=START):
        self.now = now
        self.waits = []

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += timedelta(seconds=seconds)
        return False


@pytest.fixture
def environment(tmp_path):
    settings = load_settings("config/demo.json")
    settings.artifact_dir = tmp_path
    weather = MutableWeather(settings.turbines)
    return AgentService(settings, weather=weather), weather


def test_poll_loop_automatically_checks_changed_inputs_and_preserves_revision(environment):
    service, weather = environment
    clock = Clock()
    states = []
    monitor = AgentMonitor(service, on_tick=states.append)
    def wait(seconds):
        clock.wait(seconds)
        if len(clock.waits) == 2:
            weather.version = 2  # source changes while the watcher is running
        return False
    report = monitor.run(5, Event(), max_cycles=3, clock=clock, wait=wait)
    assert report["status"] == "completed"
    assert report["executed_runs"] == 3
    assert [state["action"] for state in states] == ["created", "unchanged", "recalculated"]
    assert [state["revision"] for state in states] == [1, 1, 2]
    assert len({state["run_id"] for state in states}) == 1
    assert weather.calls == [START] * 3
    assert not (monitor.directory / "owner.lock").exists()
    lines = monitor.log_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["revision"] for line in lines] == [1, 1, 2]


def test_new_clock_hour_creates_new_origin_without_expanding_old_cutoff(environment):
    service, weather = environment
    clock = Clock(START + timedelta(minutes=59, seconds=58))
    states = []
    monitor = AgentMonitor(service, on_tick=states.append)
    monitor.run(5, Event(), max_cycles=2, clock=clock, wait=clock.wait)
    assert weather.calls == [START, START + timedelta(hours=1)]
    assert states[0]["run_id"] != states[1]["run_id"]
    assert states[1]["action"] == "new_issue"
    first = service.get(states[0]["run_id"])
    assert first.request.issue_time == START and first.revision == 1


def test_poll_loop_detects_model_update_without_weather_change(environment):
    service, weather = environment
    model = FixtureModel()
    model.artifact = model.artifact.model_copy(deep=True)
    service.model = model
    clock = Clock()
    states = []
    def wait(seconds):
        clock.wait(seconds)
        model.artifact = model.artifact.model_copy(update={"model_version": "synthetic-fixture-v2"})
        return False
    report = AgentMonitor(service, on_tick=states.append).run(
        5, Event(), max_cycles=2, clock=clock, wait=wait)
    assert report["completed_runs"] == 2 and weather.version == 1
    assert [state["action"] for state in states] == ["created", "recalculated"]
    assert [state["revision"] for state in states] == [1, 2]
    assert states[0]["input_fingerprint"] != states[1]["input_fingerprint"]


def test_failure_retries_back_off_and_success_resets_counter(environment):
    service, weather = environment
    weather.failures_left = 2
    clock = Clock()
    monitor = AgentMonitor(service, backoff_seconds=2, max_backoff_seconds=10)
    report = monitor.run(1, Event(), max_cycles=3, clock=clock, wait=clock.wait)
    assert report["completed_runs"] == 1 and report["failed_runs"] == 2
    assert clock.waits == [2, 4]
    assert report["last_state"]["consecutive_failures"] == 0
    assert report["last_state"]["next_retry_at"] is None


def test_retry_limit_halts_and_persists_until_next_origin(environment):
    service, weather = environment
    weather.failures_left = 100
    clock = Clock()
    monitor = AgentMonitor(service, max_failures=2, backoff_seconds=2)
    report = monitor.run(1, Event(), max_cycles=10, clock=clock, wait=clock.wait)
    assert report["status"] == "halted" and report["executed_runs"] == 2
    assert clock.waits == [2]
    restored = AgentMonitor(service, max_failures=2, backoff_seconds=2)
    assert restored.tick(clock.now)["status"] == "halted"
    assert len(weather.calls) == 2
    weather.failures_left = 0
    recovered = restored.tick(START + timedelta(hours=1))
    assert recovered["status"] == "completed"
    assert recovered["consecutive_failures"] == 0


def test_backoff_tick_does_not_call_provider_and_restart_rejects_clock_rollback(environment):
    service, weather = environment
    weather.failures_left = 1
    monitor = AgentMonitor(service, backoff_seconds=10)
    assert monitor.tick(START)["status"] == "failed"
    assert monitor.tick(START + timedelta(seconds=2))["status"] == "backoff"
    assert len(weather.calls) == 1
    restored = AgentMonitor(service, backoff_seconds=10)
    with pytest.raises(ValueError, match="backwards"):
        restored.tick(START)
    assert restored.tick(START + timedelta(seconds=10))["status"] == "completed"


def test_late_weather_fails_in_monitor_without_replacing_saved_revision(environment):
    service, weather = environment
    monitor = AgentMonitor(service, max_failures=1)
    initial = monitor.tick(START)
    weather.late = True
    failed = monitor.tick(START + timedelta(minutes=10))
    assert failed["status"] == "halted"
    assert failed["revision"] == initial["revision"] == 1
    assert "unavailable at issue_time" in failed["error"]
    directory = service.root / "runs" / initial["run_id"] / "revisions"
    assert (directory / "1" / "forecast.json").is_file()
    assert not (directory / "2").exists()


def test_history_consumes_aware_past_ticks_and_reports_counts(environment):
    service, weather = environment
    monitor = AgentMonitor(service)
    ticks = (START + timedelta(hours=hour) for hour in range(3))
    report = monitor.run_history(ticks, clock=lambda: START + timedelta(days=1))
    assert report["status"] == "completed"
    assert report["tick_count"] == report["completed_runs"] == 3
    assert weather.calls == [START + timedelta(hours=hour) for hour in range(3)]
    with pytest.raises(ValueError, match="later than"):
        monitor.run_history([START + timedelta(days=2)], clock=lambda: START + timedelta(days=1))
    with pytest.raises(ValueError, match="timezone-aware"):
        monitor.tick(datetime(2026, 2, 1))


def test_stop_event_interrupts_wait_without_extra_prediction(environment):
    service, weather = environment
    stopped = Event()
    def wait(seconds):
        stopped.set()
        return True
    report = AgentMonitor(service).run(30, stopped, clock=lambda: START, wait=wait)
    assert report["status"] == "stopped" and report["tick_count"] == 1
    assert len(weather.calls) == 1


def test_owner_lock_prevents_second_owner_and_can_be_acquired_before_service(environment):
    service, _ = environment
    with acquire_monitor_owner(service.root):
        with pytest.raises(RuntimeError, match="owner lock exists"):
            AgentMonitor(service).run(10, Event(), max_cycles=1, clock=lambda: START)
        assert AgentMonitor(service).run(10, Event(), max_cycles=1,
                                         clock=lambda: START, owner_lock=False)["status"] == "completed"
    assert not (service.root / "monitor/owner.lock").exists()


def test_log_rotation_and_failed_progress_callback_do_not_change_forecast(environment):
    service, _ = environment
    def bad_callback(state):
        raise OSError("reporting only")
    monitor = AgentMonitor(service, max_log_bytes=1024, on_tick=bad_callback)
    first = monitor.tick(START)
    second = monitor.tick(START + timedelta(seconds=1))
    third = monitor.tick(START + timedelta(seconds=2))
    assert first["status"] == second["status"] == third["status"] == "completed"
    assert first["revision"] == second["revision"] == third["revision"] == 1
    assert (monitor.directory / "callback-error.json").is_file()
    assert (monitor.directory / "events.previous.jsonl").is_file()


def test_invalid_poll_interval_cannot_busy_loop(environment):
    service, weather = environment
    for interval in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="interval"):
            AgentMonitor(service).run(interval, Event(), max_cycles=1)
    assert not weather.calls


def test_explicit_invalid_targets_do_not_silently_use_defaults(environment):
    service, _ = environment
    with pytest.raises(ValueError, match="horizon"):
        AgentMonitor(service, horizon_hours=0)
    with pytest.raises(ValueError, match="turbines"):
        AgentMonitor(service, turbine_ids=[])
