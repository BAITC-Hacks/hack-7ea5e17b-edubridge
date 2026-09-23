"""Autonomous polling of the existing audited agent, without an HTTP server.

Polling never expands a saved origin's knowledge cutoff. Each UTC hour is a new
origin; within an hour the service's existing fingerprint controls revisions.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from threading import Event, Lock
from typing import Callable, Iterable

from wind_agent.contracts import RunRequest
from .service import digest, utcnow
from .storage import read_json, write_json


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Monitor times must be timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00"))) if value else None


@contextmanager
def acquire_monitor_owner(root: str | Path):
    """Acquire BEFORE AgentService construction when launching a watch process.

    This protects monitor ownership, not an independently started API. Use a
    separate artifact_dir for API and monitor. A stale lock is deliberately not
    removed automatically: the operator must first establish that its owner died.
    """
    directory = Path(root) / "monitor"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "owner.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError(f"Monitor owner lock exists: {path}; do not share an artifact_dir") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created_at": utcnow().isoformat()}, stream)
        yield path
    finally:
        path.unlink(missing_ok=True)


class AgentMonitor:
    """One owner, bounded retries and constant-memory persisted polling state."""

    def __init__(self, service, *, horizon_hours: int | None = None,
                 turbine_ids: list[str] | None = None, max_failures: int = 3,
                 backoff_seconds: float = 5, max_backoff_seconds: float = 300,
                 max_log_bytes: int = 5_000_000, on_tick: Callable[[dict], None] | None = None):
        self.service = service
        self.horizon_hours = (service.settings.default_horizon_hours
                              if horizon_hours is None else horizon_hours)
        self.turbine_ids = ([t.turbine_id for t in service.settings.turbines]
                            if turbine_ids is None else list(turbine_ids))
        if self.horizon_hours not in (24, 48) or not self.turbine_ids:
            raise ValueError("Monitor requires a 24/48-hour horizon and configured turbines")
        if len(self.turbine_ids) != len(set(self.turbine_ids)):
            raise ValueError("Monitor turbine IDs must be unique")
        configured = {t.turbine_id for t in service.settings.turbines}
        if not set(self.turbine_ids) <= configured:
            raise ValueError("Unknown monitor turbine IDs")
        if not isinstance(max_failures, int) or not 1 <= max_failures <= 20:
            raise ValueError("max_failures must be 1..20")
        if not (math.isfinite(backoff_seconds) and math.isfinite(max_backoff_seconds)
                and 1 <= backoff_seconds <= max_backoff_seconds <= 86400):
            raise ValueError("Backoff must be finite and 1 <= initial <= maximum <= 86400 seconds")
        if max_log_bytes < 1024:
            raise ValueError("max_log_bytes must be at least 1024")
        self.max_failures = max_failures
        self.backoff_seconds = backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.max_log_bytes = max_log_bytes
        self.on_tick = on_tick
        self.directory = Path(service.root) / "monitor"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "status.json"
        self.log_path = self.directory / "events.jsonl"
        self._guard = Lock()
        self._state = read_json(self.state_path) if self.state_path.exists() else {}
        self._last_now = _parse(self._state.get("as_of"))
        self._issue = _parse(self._state.get("issue_time"))
        self._failures = int(self._state.get("consecutive_failures", 0))
        self._retry_at = _parse(self._state.get("next_retry_at"))
        self._identity = {"horizon_hours": self.horizon_hours, "turbine_ids": sorted(self.turbine_ids)}
        if self._state and self._state.get("request_identity") != self._identity:
            raise ValueError("Existing monitor state uses different targets/horizon; use another artifact_dir")

    def _persist(self, state: dict) -> dict:
        self._state = state
        write_json(self.state_path, state)
        encoded = (json.dumps(state, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if self.log_path.exists() and self.log_path.stat().st_size + len(encoded) > self.max_log_bytes:
            self.log_path.replace(self.directory / "events.previous.jsonl")
        with self.log_path.open("ab") as stream:
            stream.write(encoded)
        if self.on_tick is not None:
            try:
                self.on_tick(dict(state))
            except Exception as error:
                # Reporting is advisory and must not turn a saved forecast into
                # a failed prediction or silently trigger an extra calculation.
                write_json(self.directory / "callback-error.json",
                           {"as_of": state["as_of"], "error": f"{type(error).__name__}: {error}"})
        return dict(state)

    def tick(self, now: datetime) -> dict:
        """Check inputs at this explicit as-of time; reject clock rollback.

        The original request's issue_time is its UTC hour, never the later tick.
        Newly published weather therefore first becomes eligible at a NEW hour.
        """
        now = _utc(now)
        with self._guard:
            if self._last_now is not None and now < self._last_now:
                raise ValueError("Monitor clock moved backwards; historical simulations need a separate artifact_dir")
            issue = now.replace(minute=0, second=0, microsecond=0)
            new_issue = self._issue is not None and issue > self._issue
            if issue != self._issue:
                self._failures, self._retry_at = 0, None
            self._last_now, self._issue = now, issue
            state = {
                "schema_version": "1.0", "as_of": now.isoformat(), "issue_time": issue.isoformat(),
                "request_identity": self._identity, "is_demo": self.service.settings.mode == "demo",
                "consecutive_failures": self._failures,
                "next_retry_at": self._retry_at.isoformat() if self._retry_at else None,
                "run_id": self._state.get("run_id") if not new_issue else None,
                "revision": self._state.get("revision", 0) if not new_issue else 0,
                "input_fingerprint": self._state.get("input_fingerprint") if not new_issue else None,
                "error": None, "executed": False, "issue_advanced": new_issue,
            }
            if self._failures >= self.max_failures:
                return self._persist({**state, "status": "halted", "action": "halt",
                                      "error": self._state.get("error")})
            if self._retry_at is not None and now < self._retry_at:
                return self._persist({**state, "status": "backoff", "action": "retry_deferred",
                                      "error": self._state.get("error")})
            request = RunRequest(issue_time=issue, horizon_hours=self.horizon_hours,
                                 turbine_ids=self.turbine_ids)
            canonical = request.model_dump(mode="json")
            canonical["turbine_ids"] = sorted(canonical["turbine_ids"])
            run_id = digest(canonical)[:24]
            try:
                previous = self.service.get(run_id)
            except (KeyError, FileNotFoundError):
                previous = None
            state["executed"] = True
            state["run_id"] = run_id
            try:
                record = self.service.run(request)
                state.update(run_id=record.run_id, revision=record.revision,
                             input_fingerprint=record.input_fingerprint)
                if record.status != "completed":
                    raise RuntimeError(record.error or f"Unexpected agent status: {record.status}")
                if previous is None or previous.revision == 0:
                    action = "new_issue" if new_issue else "created"
                elif (previous.revision == record.revision and
                      previous.input_fingerprint == record.input_fingerprint):
                    action = "unchanged"
                else:
                    action = "recalculated"
                analysis = getattr(self.service, "analysis", None)
                if callable(analysis):
                    try:
                        advisory = analysis(record.run_id)
                        state["analysis"] = {key: advisory[key] for key in ("decision", "next_action", "reasons")
                                             if key in advisory}
                    except Exception as error:
                        state["analysis"] = {"status": "unavailable", "error": str(error)}
                self._failures, self._retry_at = 0, None
                return self._persist({**state, "status": "completed", "action": action,
                                      "consecutive_failures": 0, "next_retry_at": None})
            except Exception as error:
                self._failures += 1
                delay = min(self.max_backoff_seconds, self.backoff_seconds * 2 ** (self._failures - 1))
                self._retry_at = now + timedelta(seconds=delay)
                halted = self._failures >= self.max_failures
                return self._persist({**state, "status": "halted" if halted else "failed",
                                      "action": "halt" if halted else "retry_scheduled",
                                      "consecutive_failures": self._failures,
                                      "next_retry_at": None if halted else self._retry_at.isoformat(),
                                      "error": f"{type(error).__name__}: {error}"})

    @staticmethod
    def _report() -> dict:
        return {"status": "completed", "tick_count": 0, "executed_runs": 0,
                "completed_runs": 0, "failed_runs": 0, "deferred_ticks": 0, "last_state": None}

    @staticmethod
    def _count(report: dict, state: dict):
        report["tick_count"] += 1
        report["last_state"] = state
        report["executed_runs"] += int(state["executed"])
        report["completed_runs"] += int(state["status"] == "completed")
        report["failed_runs"] += int(state["executed"] and state["status"] in ("failed", "halted"))
        report["deferred_ticks"] += int(not state["executed"])

    def run(self, interval_seconds: float, stop_event: Event, *, max_cycles: int | None = None,
            clock: Callable[[], datetime] = utcnow, wait: Callable[[float], bool] | None = None,
            owner_lock: bool = True) -> dict:
        """Poll until stopped or bounded consecutive failures; wait is injectable."""
        if not math.isfinite(interval_seconds) or interval_seconds < 1:
            raise ValueError("Polling interval must be finite and at least one second")
        if max_cycles is not None and (not isinstance(max_cycles, int) or max_cycles < 1):
            raise ValueError("max_cycles must be a positive integer")
        report = self._report()
        waiter = wait or stop_event.wait
        ownership = acquire_monitor_owner(self.service.root) if owner_lock else nullcontext()
        with ownership:
            while not stop_event.is_set() and (max_cycles is None or report["tick_count"] < max_cycles):
                state = self.tick(clock())
                self._count(report, state)
                if state["status"] == "halted":
                    report["status"] = "halted"
                    break
                if max_cycles is not None and report["tick_count"] >= max_cycles:
                    break
                delay = interval_seconds
                retry_at = _parse(state["next_retry_at"])
                if retry_at is not None:
                    delay = max(delay, (retry_at - _parse(state["as_of"])).total_seconds())
                if waiter(delay):
                    report["status"] = "stopped"
                    break
            if stop_event.is_set():
                report["status"] = "stopped"
        report["cycles"] = report["tick_count"]
        write_json(self.directory / "last_session.json", report)
        return report

    def run_history(self, ticks: Iterable[datetime], stop_event: Event | None = None, *,
                    clock: Callable[[], datetime] = utcnow, owner_lock: bool = True) -> dict:
        """Consume explicit past ticks lazily, without waiting or changing their times."""
        ceiling = _utc(clock())
        report = self._report()
        ownership = acquire_monitor_owner(self.service.root) if owner_lock else nullcontext()
        with ownership:
            for moment in ticks:
                if stop_event is not None and stop_event.is_set():
                    report["status"] = "stopped"
                    break
                moment = _utc(moment)
                if moment > ceiling:
                    raise ValueError("Historical monitor ticks cannot be later than the supplied clock")
                state = self.tick(moment)
                self._count(report, state)
                if state["status"] == "halted":
                    report["status"] = "halted"
                    break
        write_json(self.directory / "last_session.json", report)
        return report
