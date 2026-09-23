"""Verify autonomous historical monitoring with the real archived weather/model.

Run from a checkout with the weather/model/dev dependencies installed. The
output directory must be new or empty: no existing forecast is overwritten.
The monitor initiates calculations; the in-process API client performs GET only.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from wind_agent.agent import AgentService  # noqa: E402
from wind_agent.agent.analysis import analyse_forecast  # noqa: E402
from wind_agent.agent.monitor import AgentMonitor, acquire_monitor_owner  # noqa: E402
from wind_agent.api import create_app  # noqa: E402
from wind_agent.config import load_settings  # noqa: E402
from wind_agent.contracts import ForecastRecord, WeatherRecord  # noqa: E402


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "A cutoff has no timezone")
    return result


def exact_csv(content, expected):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    require(reader.fieldnames == list(ForecastRecord.model_fields), "CSV columns differ from contract")
    rows = list(reader)
    require(len(rows) == len(expected), "CSV row count mismatch")
    for exported, source in zip(rows, expected, strict=True):
        require(set(exported) == set(source), "CSV/JSON field mismatch")
        for field, value in source.items():
            actual = json.loads(exported[field]) if isinstance(value, (dict, list)) else exported[field]
            wanted = value if isinstance(value, (dict, list)) else "" if value is None else str(value)
            require(actual == wanted, f"CSV/JSON value mismatch: {field}")


def audit_run(service, client, run_id, output):
    record = service.get(run_id)
    require(record.status == "completed" and record.revision == 1, "Expected a completed first revision")
    request = record.request
    directory = service.root / "runs" / run_id / "revisions" / str(record.revision)
    forecasts = [ForecastRecord.model_validate(row) for row in read(directory / "forecast.json")]
    weather = [WeatherRecord.model_validate(row) for row in read(directory / "weather.json")]
    inputs = read(directory / "inputs.json")
    expected = {(tid, request.issue_time + timedelta(hours=lead))
                for tid in request.turbine_ids for lead in range(1, 49)}
    require(len(forecasts) == len(weather) == 96, "Expected 48 hours for both turbines")
    require({(row.turbine_id, row.valid_time) for row in forecasts} == expected,
            "Forecast grid differs from the requested horizon")
    require({(row.turbine_id, row.valid_time) for row in weather} == expected,
            "Weather grid differs from the requested horizon")
    require(inputs["is_demo"] is False, "Fixture model cannot establish archive integration")
    cutoff = timestamp(inputs["model"]["training_cutoff"])
    require(cutoff <= request.issue_time, "Training cutoff exceeds origin")
    require(cutoff <= service.settings.train_cutoff, "Training cutoff exceeds configured January boundary")
    metadata = inputs["model"]["metadata"]
    for field in ("selection_cutoff", "calibration_cutoff"):
        if field in metadata:
            require(timestamp(metadata[field]) <= cutoff, f"{field} exceeds training cutoff")
    for row in forecasts:
        require(row.issue_time == request.issue_time and row.training_cutoff == cutoff,
                "Forecast provenance differs from run/model")
        require(row.data_quality != "fixture", "Synthetic prediction found")
    for row in weather:
        require(row.provenance_kind == "operational_archive", "Non-operational weather found")
        require(row.model_run_time <= row.forecast_available_at <= request.issue_time,
                "Weather was not available at this forecast origin")
        require(bool(row.availability_evidence), "Weather availability evidence is missing")
    raw_hashes = sorted({row.raw_sha256 for row in weather})
    for raw_hash in raw_hashes:
        require(sha(service.settings.cache_dir / "raw" / f"{raw_hash}.bin") == raw_hash,
                "Archived weather raw bundle hash mismatch")

    forecast_response = client.get(f"/runs/{run_id}/forecast")
    analysis_response = client.get(f"/runs/{run_id}/analysis")
    csv_response = client.get(f"/runs/{run_id}/forecast.csv")
    require(all(response.status_code == 200 for response in
                (forecast_response, analysis_response, csv_response)), "A GET endpoint failed")
    require(forecast_response.json() == read(directory / "forecast.json"),
            "API predictions differ from immutable artifact")
    exact_csv(csv_response.content, forecast_response.json())
    diagnostics = analysis_response.json()
    require(diagnostics == service.analysis(run_id), "API analysis differs from stored diagnostics")
    require(diagnostics["run_id"] == run_id and diagnostics["revision"] == 1,
            "Analysis is associated with the wrong revision")
    require(diagnostics["is_demo"] is False, "Real analysis is mislabeled")
    require(diagnostics["decision"] in ("review_required", "monitor_updates"), "Invalid decision")
    require(diagnostics["limitations"], "Analysis must disclose its non-accuracy scope")
    for turbine_id in request.turbine_ids:
        values = [row.prediction for row in forecasts if row.turbine_id == turbine_id]
        summary = diagnostics["per_turbine"][turbine_id]
        require(summary["prediction_min"] == min(values) and
                summary["prediction_max"] == max(values), "Analysis range mismatch")
        require(math.isclose(summary["prediction_mean"], math.fsum(values) / len(values),
                             rel_tol=1e-12, abs_tol=1e-12), "Analysis mean mismatch")
        require(summary["forecast_hours"] == 48 and
                summary["weather_wind_speed_ms"]["matched_hours"] == 48,
                "Analysis omitted forecast/weather hours")
    export = output / "exports" / run_id
    write(export / "analysis.json", diagnostics)
    (export / "forecast.csv").write_bytes(csv_response.content)
    return {
        "run_id": run_id, "issue_time": request.issue_time.isoformat(), "revision": 1,
        "model_version": inputs["model"]["model_version"], "training_cutoff": cutoff.isoformat(),
        "rows": 96, "horizon_hours": 48, "turbine_ids": request.turbine_ids,
        "forecast_sha256": sha(directory / "forecast.json"),
        "analysis_sha256": sha(directory / "analysis.json"),
        "csv_sha256": sha(export / "forecast.csv"),
        "weather_model_runs": sorted({row.model_run_time.isoformat() for row in weather}),
        "weather_available_at_max": max(row.forecast_available_at for row in weather).isoformat(),
        "raw_bundle_count": len(raw_hashes), "raw_bundle_hashes_verified": True,
        "all_weather_available_by_origin": True, "model_cutoffs_valid": True,
        "analysis_api_matches_saved_revision": True, "json_csv_all_fields_equal": True,
        "decision": diagnostics["decision"], "next_action": diagnostics["next_action"],
        "reasons": diagnostics["reasons"], "per_turbine": diagnostics["per_turbine"],
    }, forecasts, weather


def verify(arguments):
    settings = load_settings(arguments.config)
    require(settings.mode == "archive", "This verification requires archive mode")
    require(settings.model_artifact_path is not None, "A real model package is required")
    output = arguments.output
    require(not output.exists() or not any(output.iterdir()), "Use a new or empty --output directory")
    settings = settings.model_copy(update={"artifact_dir": output, "cache_dir": arguments.cache_dir})
    package = settings.model_artifact_path
    package = package if package.is_dir() else package.parent
    protected = [package / name for name in ("model.joblib", "metadata.json", "validation.json")]
    protected_hashes = {str(path): sha(path) for path in protected}
    source_files = [Path("scripts/verify_agent_monitor.py"), Path("src/wind_agent/agent/monitor.py"),
                    Path("src/wind_agent/agent/analysis.py"), Path("src/wind_agent/agent/service.py"),
                    Path("src/wind_agent/api/app.py"), Path("src/wind_agent/weather/gfs.py"),
                    Path("src/wind_agent/model/interface.py"), arguments.config]
    source_hashes = {str(path): sha(path) for path in source_files}
    write(output / "effective-config.json", settings.model_dump(mode="json"))
    ticks = [datetime(2026, 1, 31, 18, tzinfo=timezone.utc),
             datetime(2026, 1, 31, 18, 5, tzinfo=timezone.utc),
             datetime(2026, 2, 1, 18, tzinfo=timezone.utc)]
    states, tick_forecast_hashes = [], []
    with acquire_monitor_owner(output / settings.mode):
        service = AgentService(settings)

        def capture(state):
            states.append(state)
            if state["status"] == "completed":
                directory = service.root / "runs" / state["run_id"] / "revisions" / str(state["revision"])
                tick_forecast_hashes.append(sha(directory / "forecast.json"))
            print(json.dumps({key: state[key] for key in ("as_of", "status", "action", "run_id", "revision")}),
                  file=sys.stderr, flush=True)

        monitor = AgentMonitor(service, horizon_hours=48, on_tick=capture)
        try:
            session = monitor.run_history(ticks, owner_lock=False)
            require(session["status"] == "completed" and session["failed_runs"] == 0,
                    f"Monitor did not complete: {session}")
            require([state["action"] for state in states] == ["created", "unchanged", "new_issue"],
                    "Unexpected automatic monitor actions")
            require(states[0]["run_id"] == states[1]["run_id"] != states[2]["run_id"],
                    "Origin/revision identity did not behave as expected")
            require(tick_forecast_hashes[0] == tick_forecast_hashes[1],
                    "Unchanged inputs modified the saved forecast")
            with TestClient(create_app(service=service)) as client:
                first, prior, _ = audit_run(service, client, states[0]["run_id"], output)
                second, current, weather = audit_run(service, client, states[2]["run_id"], output)
            require(max(second["weather_model_runs"]) > max(first["weather_model_runs"]),
                    "New origin did not select a newer eligible GFS model run")
            comparison = analyse_forecast(current, weather, prior)["previous_comparison"]
            require(comparison["common_hours"] == 48, "Expected 24 overlapping hours for each turbine")
        finally:
            if service.weather is not None and hasattr(service.weather, "close"):
                service.weather.close()
    require(all(sha(path) == digest for path, digest in protected_hashes.items()),
            "Verification modified the model package")
    require(all(sha(path) == digest for path, digest in source_hashes.items()),
            "Code/config changed during verification; rerun with a stable checkout")
    evidence = {
        "schema_version": "1.0", "status": "passed",
        "checked_at": datetime.now(timezone.utc).isoformat(), "is_demo": False,
        "scope": "Real automatic historical origin advancement and unchanged-input retention; no HTTP-triggered calculations",
        "transport": "AgentMonitor + AgentService; in-process TestClient GET checks after monitoring",
        "model_and_weather_substituted": False,
        "real_revision_2_demonstrated": False, "measures_forecast_accuracy": False,
        "test_truth_available": False, "source_semantics_status": "assumed",
        "configuration": str(arguments.config), "artifact_directory": str(output),
        "cache_directory": str(arguments.cache_dir),
        "effective_config_sha256": sha(output / "effective-config.json"),
        "source_sha256": source_hashes, "model_package_sha256": protected_hashes,
        "ticks": states, "tick_forecast_sha256": tick_forecast_hashes,
        "session_counts": {key: session[key] for key in
                           ("tick_count", "executed_runs", "completed_runs", "failed_runs", "deferred_ticks")},
        "runs": [first, second], "overlapping_origin_prediction_changes": comparison,
        "limitations": [
            "Historical ticks are accelerated; this check does not measure wall-clock scheduling reliability.",
            "The unchanged check repeats the original issue cutoff, not the later retrieval time.",
            "A newer issue selects newer eligible archived weather; this is not a revision-2 claim.",
            "Prediction changes and diagnostics do not measure errors against observed generation.",
            "SCADA timezone, interval convention and reporting delay remain explicit source assumptions.",
        ],
    }
    write(output / "verification.json", evidence)
    write(arguments.evidence, evidence)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/monitor-model.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/monitor-verification"))
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/weather-cache"))
    parser.add_argument("--evidence", type=Path, default=Path("docs/evidence/agent-monitor-integration.json"))
    arguments = parser.parse_args()
    os.chdir(ROOT)
    result = verify(arguments)
    print(json.dumps({"status": result["status"], "evidence": str(arguments.evidence),
                      "actions": [state["action"] for state in result["ticks"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
