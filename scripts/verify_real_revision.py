"""Controlled revision replay using two genuine, historically eligible GFS releases.

This checks changed-input orchestration, not natural wall-clock arrival or accuracy.
The first two ticks deliberately retain yesterday's still-eligible 48-hour release;
the third exposes today's latest eligible release for the same 24-hour request.
No weather values, timestamps, model weights or predictions are fabricated.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wind_agent.agent import AgentService  # noqa: E402
from wind_agent.agent.monitor import AgentMonitor  # noqa: E402
from wind_agent.config import load_settings  # noqa: E402
from wind_agent.weather import GFSArchiveProvider  # noqa: E402


class ReleaseReplay:
    def __init__(self, provider):
        self.provider = provider
        self.latest = False

    def fetch(self, issue_time, horizon_hours, turbine_ids):
        if horizon_hours != 24:
            raise ValueError("This controlled overlap replay requires a 24-hour horizon")
        if self.latest:
            return self.provider.fetch(issue_time, horizon_hours, turbine_ids)
        rows = self.provider.fetch(issue_time - timedelta(days=1), 48, turbine_ids)
        return [row for row in rows
                if issue_time < row.valid_time <= issue_time + timedelta(hours=24)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/archive-model.json")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Use a new or empty output directory")
    settings = load_settings(args.config)
    if settings.mode != "archive":
        raise ValueError("Real archive configuration required")
    settings = settings.model_copy(update={"artifact_dir": args.output, "cache_dir": args.cache})
    package = settings.model_artifact_path
    protected = {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                 for name in ("model.joblib", "metadata.json", "validation.json")}
    turbines = {row.turbine_id: (row.latitude, row.longitude) for row in settings.turbines}
    issue = datetime(2026, 1, 31, 18, tzinfo=timezone.utc)
    with GFSArchiveProvider(turbines, args.cache, offline=args.offline) as provider:
        releases = ReleaseReplay(provider)
        service = AgentService(settings, weather=releases)
        monitor = AgentMonitor(service, horizon_hours=24)
        states = [monitor.tick(issue)]
        first_path = (service.root / "runs" / states[0]["run_id"] / "revisions" / "1"
                      / "forecast.json")
        first_bytes = first_path.read_bytes()
        states.append(monitor.tick(issue + timedelta(minutes=5)))
        if first_path.read_bytes() != first_bytes:
            raise AssertionError("Unchanged inputs rewrote the saved forecast")
        releases.latest = True
        states.append(monitor.tick(issue + timedelta(minutes=10)))
    if [state["revision"] for state in states] != [1, 1, 2]:
        raise AssertionError(f"Expected revisions 1, 1, 2: {states}")
    if [state["action"] for state in states] != ["created", "unchanged", "recalculated"]:
        raise AssertionError(f"Unexpected monitor actions: {states}")
    if len({state["run_id"] for state in states}) != 1:
        raise AssertionError("A revision must retain the same run ID")
    revisions = service.root / "runs" / states[0]["run_id"] / "revisions"
    forecasts, weather_runs, fingerprints = [], [], []
    for revision in (1, 2):
        folder = revisions / str(revision)
        rows = json.loads((folder / "forecast.json").read_text())
        weather = json.loads((folder / "weather.json").read_text())
        if len(rows) != 48 or len(weather) != 48:
            raise AssertionError("Expected 24 hours for each of two turbines")
        for row in weather:
            if row["provenance_kind"] != "operational_archive":
                raise AssertionError("Synthetic weather is forbidden")
            available = datetime.fromisoformat(row["forecast_available_at"].replace("Z", "+00:00"))
            if available > issue:
                raise AssertionError("Weather was unavailable at issue time")
            raw = args.cache / "raw" / (row["raw_sha256"] + ".bin")
            if hashlib.sha256(raw.read_bytes()).hexdigest() != row["raw_sha256"]:
                raise AssertionError("Raw GFS bundle hash mismatch")
        if any(row["data_quality"] == "fixture" for row in rows):
            raise AssertionError("Synthetic predictions are forbidden")
        forecasts.append({(row["turbine_id"], row["valid_time"]): row["prediction"] for row in rows})
        weather_runs.append(sorted({row["model_run_time"] for row in weather}))
        fingerprints.append(hashlib.sha256((folder / "forecast.json").read_bytes()).hexdigest())
    if forecasts[0].keys() != forecasts[1].keys():
        raise AssertionError("Revision target hours changed")
    changed = sum(value != forecasts[1][key] for key, value in forecasts[0].items())
    if not changed or weather_runs[0] == weather_runs[1]:
        raise AssertionError("Expected a genuine weather release and prediction change")
    analysis = service.analysis(states[-1]["run_id"])
    if analysis["previous_comparison"]["changed_count"] != changed:
        raise AssertionError("Stored revision comparison differs from predictions")
    if any(hashlib.sha256((package / name).read_bytes()).hexdigest() != digest
           for name, digest in protected.items()):
        raise AssertionError("Verification modified the model package")
    report = {
        "status": "passed", "checked_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Controlled exposure of two genuine archived GFS releases at a fixed issue time",
        "is_demo": False, "weather_values_modified": False, "model_weights_modified": False,
        "natural_archive_arrival_demonstrated": False, "measures_forecast_accuracy": False,
        "revisions": [state["revision"] for state in states],
        "actions": [state["action"] for state in states], "run_id": states[0]["run_id"],
        "weather_model_runs": weather_runs, "forecast_sha256": fingerprints,
        "changed_predictions": changed, "rows_per_revision": 48,
        "all_raw_hashes_verified": True, "all_weather_available_by_issue": True,
        "unchanged_tick_preserves_forecast_bytes": True, "model_package_sha256": protected,
        "verification_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "analysis_decision": analysis["decision"],
        "limitations": ["Release selection is deliberately controlled for this integration check.",
                        "No observed February generation is used; this is not an accuracy evaluation."],
    }
    destination = args.output / "report.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
