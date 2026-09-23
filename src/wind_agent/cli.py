"""Command-line entry points for a single forecast, replay and local API."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _issue_time(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use an ISO 8601 timestamp including UTC offset") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("issue-time must include an offset, for example 2026-01-31T18:00:00Z")
    return timestamp


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="wind-agent", description="Run historical wind forecasts or explicitly labelled synthetic demos."
    )
    result.add_argument(
        "--config", default=os.environ.get("WIND_AGENT_CONFIG"),
        help="JSON configuration file; defaults to archive mode. Use config/demo.json for the synthetic demo.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run one forecast synchronously and print its status")
    run.add_argument("--issue-time", required=True, type=_issue_time)
    run.add_argument("--horizon-hours", type=int, choices=(24, 48))
    run.add_argument("--turbine-ids", nargs="+", help="Turbine IDs separated by spaces; defaults to configured IDs")
    weather = commands.add_parser("weather-fetch", help="Download a real admissible archive release without a power model")
    weather.add_argument("--issue-time", required=True, type=_issue_time)
    weather.add_argument("--horizon-hours", type=int, choices=(24, 48))
    weather.add_argument("--turbine-ids", nargs="+", help="Defaults to configured turbine IDs")
    weather.add_argument("--output", type=Path, default=Path("artifacts/weather-fetch.json"))
    replay = commands.add_parser("replay", help="Run daily historical releases covering February 2026")
    replay.add_argument("--horizon-hours", type=int, choices=(24, 48))
    watch = commands.add_parser("watch", help="Autonomously poll inputs; use a separate artifact directory from API")
    watch.add_argument("--horizon-hours", type=int, choices=(24, 48))
    watch.add_argument("--turbine-ids", nargs="+")
    watch.add_argument("--interval-seconds", type=float, default=60)
    watch.add_argument("--max-cycles", type=int, help="Stop after this many polling cycles; default runs until Ctrl+C")
    watch.add_argument("--start-issue", type=_issue_time, help="Start of an explicit accelerated historical clock")
    watch.add_argument("--end-issue", type=_issue_time, help="Inclusive historical end, required with --start-issue")
    watch.add_argument("--step-hours", type=int, default=6, help="Historical clock increment; default 6 hours")
    serve = commands.add_parser("serve", help="Serve the local API with a single worker")
    serve.add_argument("--host", help="Defaults to the configured API host")
    serve.add_argument("--port", type=int, help="Defaults to the configured API port")
    commands.add_parser("health", help="Print service, weather and model readiness")
    return result


def _print_json(value: Any, *, stream: Any = None) -> None:
    from fastapi.encoders import jsonable_encoder

    print(json.dumps(jsonable_encoder(value), ensure_ascii=False, indent=2), file=stream)


def _weather_fetch(arguments: argparse.Namespace, settings: Any) -> int:
    from wind_agent.agent.storage import write_json
    from wind_agent.contracts import RunRequest
    from wind_agent.weather import GFSArchiveProvider
    from wind_agent.weather.gfs import WeatherUnavailable

    if settings.mode != "archive" or settings.weather_provider != "gfs_s3":
        raise ValueError("weather-fetch requires archive mode and the gfs_s3 provider; fixtures are not supported")
    request = RunRequest(
        issue_time=arguments.issue_time,
        horizon_hours=arguments.horizon_hours or settings.default_horizon_hours,
        turbine_ids=arguments.turbine_ids or [turbine.turbine_id for turbine in settings.turbines],
    )
    turbines = {tid: settings.turbine(tid) for tid in request.turbine_ids}
    try:
        with GFSArchiveProvider(
            turbines={tid: (turbine.latitude, turbine.longitude) for tid, turbine in turbines.items()},
            cache_dir=settings.cache_dir,
        ) as provider:
            rows = provider.fetch(request.issue_time, request.horizon_hours, request.turbine_ids)
    except WeatherUnavailable as exc:
        _print_json({"status": "failed", "error": str(exc)}, stream=sys.stderr)
        return 1
    payload = {
        "schema_version": "1.0", "mode": "archive",
        "request": request.model_dump(mode="json"),
        "records": [row.model_dump(mode="json") for row in rows],
    }
    write_json(arguments.output, payload)
    _print_json({
        "status": "completed", "mode": "archive", "output": str(arguments.output.resolve()),
        "record_count": len(rows),
        "weather_models": sorted({row.weather_model for row in rows}),
        "availability_basis": sorted({row.availability_basis for row in rows}),
    })
    return 0


def _watch(arguments: argparse.Namespace, settings: Any) -> int:
    from threading import Event

    from wind_agent.agent import AgentService
    from wind_agent.agent.monitor import AgentMonitor, acquire_monitor_owner

    if arguments.interval_seconds < 1 or not math.isfinite(arguments.interval_seconds):
        raise ValueError("watch interval must be finite and at least one second")
    if arguments.max_cycles is not None and arguments.max_cycles < 1:
        raise ValueError("max-cycles must be positive")
    historical = arguments.start_issue is not None or arguments.end_issue is not None
    if historical and (arguments.start_issue is None or arguments.end_issue is None):
        raise ValueError("Historical watch requires both --start-issue and --end-issue")
    if arguments.step_hours < 1:
        raise ValueError("step-hours must be positive")
    if historical and arguments.end_issue < arguments.start_issue:
        raise ValueError("end-issue must not precede start-issue")
    stop = Event()
    # The watch process must never mark an API owner's queued jobs interrupted.
    # Isolate it even when the caller reuses the API configuration by mistake.
    settings = settings.model_copy(update={"artifact_dir": Path(settings.artifact_dir) / "monitor-jobs"})
    root = Path(settings.artifact_dir) / settings.mode
    with acquire_monitor_owner(root):
        service = AgentService(settings)
        monitor = AgentMonitor(service, horizon_hours=arguments.horizon_hours,
                               turbine_ids=arguments.turbine_ids,
                               on_tick=lambda state: _print_json(state, stream=sys.stderr))
        try:
            if historical:
                def ticks():
                    tick = arguments.start_issue
                    count = 0
                    while tick <= arguments.end_issue:
                        if arguments.max_cycles is not None and count >= arguments.max_cycles:
                            break
                        yield tick
                        tick += timedelta(hours=arguments.step_hours)
                        count += 1
                report = monitor.run_history(ticks(), stop_event=stop, owner_lock=False)
            else:
                report = monitor.run(arguments.interval_seconds, stop,
                                     max_cycles=arguments.max_cycles, owner_lock=False)
            _print_json(report)
            return 0 if report["status"] in ("completed", "stopped") and not report.get("failed_runs") else 1
        except KeyboardInterrupt:
            stop.set()
            _print_json({"status": "stopped", "reason": "operator interrupt"})
            return 0
        finally:
            if service.weather is not None and hasattr(service.weather, "close"):
                service.weather.close()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        from wind_agent.agent import AgentService
        from wind_agent.config import load_settings
        from wind_agent.contracts import RunRequest, RunStatus

        settings = load_settings(arguments.config)
        if arguments.command == "weather-fetch":
            return _weather_fetch(arguments, settings)
        if arguments.command == "watch":
            return _watch(arguments, settings)
        service = AgentService(settings)
        if arguments.command == "serve":
            import uvicorn

            from wind_agent.api import create_app

            uvicorn.run(
                create_app(service=service),
                host=arguments.host or settings.api_host,
                port=arguments.port if arguments.port is not None else settings.api_port,
                workers=1,
            )
            return 0
        if arguments.command == "health":
            _print_json(service.health())
            return 0
        if arguments.command == "replay":
            report = service.replay(horizon_hours=arguments.horizon_hours)
            _print_json(report)
            return 0 if report.get("status") == "completed" else 1
        request = RunRequest(
            issue_time=arguments.issue_time,
            horizon_hours=arguments.horizon_hours or settings.default_horizon_hours,
            turbine_ids=arguments.turbine_ids or [turbine.turbine_id for turbine in settings.turbines],
        )
        record = service.run(request)
        _print_json(record)
        return 0 if record.status == RunStatus.completed else 1
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        _print_json({"status": "failed", "error": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
