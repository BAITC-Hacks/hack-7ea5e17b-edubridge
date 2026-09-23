"""Run the real archive/model replay and independently audit its saved artifacts.

This script only wraps AgentService.run for progress logging. It does not change
weather, predictions, run selection, overlap policy, or model metadata.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wind_agent.agent.service import AgentService
from wind_agent.config import load_settings
from wind_agent.contracts import ForecastRecord, RunRequest, WeatherRecord


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(service, report, horizon, export_dir):
    """Check full saved horizons and recompute the declared February selection."""
    require(report["status"] == "completed", f"Replay incomplete: {report['status']}")
    require(not report["is_demo"], "Synthetic replay cannot pass this audit")
    require(report["failed_runs"] == 0 and not report["missing_hours"], "Failed or missing hours")
    require(len(report["runs"]) == 29, "Expected all 29 daily origins")
    zone = ZoneInfo(service.settings.replay_timezone)
    utc = timezone.utc
    first = datetime(2026, 1, 31, service.settings.replay_issue_hour, tzinfo=zone)
    start = datetime(2026, 2, 1, tzinfo=zone).astimezone(utc)
    end = datetime(2026, 3, 1, tzinfo=zone).astimezone(utc)
    turbine_ids = sorted(t.turbine_id for t in service.settings.turbines)
    expected_selected = {(tid, start + timedelta(hours=hour))
                         for tid in turbine_ids for hour in range(1, 673)}
    recomputed, receipts, all_grids, warning_text = {}, [], set(), set()
    forecast_count = weather_count = 0
    model_hashes, raw_hashes = set(), set()
    for offset, run_info in enumerate(report["runs"]):
        expected_issue = (first + timedelta(days=offset)).astimezone(utc)
        record = service.get(run_info["run_id"])
        require(record.status == "completed", f"Failed origin {expected_issue}")
        require(record.request.issue_time == expected_issue, "Wrong daily origin/order")
        require(record.request.horizon_hours == horizon, "Wrong horizon")
        directory = service.root / "runs" / record.run_id / "revisions" / str(record.revision)
        weather = [WeatherRecord.model_validate(row) for row in read(directory / "weather.json")]
        forecast = [ForecastRecord.model_validate(row) for row in read(directory / "forecast.json")]
        inputs = read(directory / "inputs.json")
        require(inputs["is_demo"] is False, "Fixture artifact in real replay")
        model_cutoff = datetime.fromisoformat(inputs["model"]["training_cutoff"].replace("Z", "+00:00"))
        model_metadata = inputs["model"]["metadata"]
        information_cutoffs = {name: model_metadata[name] for name in ("selection_cutoff", "calibration_cutoff")
                               if name in model_metadata}
        for name, value in information_cutoffs.items():
            cutoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
            require(cutoff.tzinfo is not None and cutoff <= model_cutoff <= expected_issue,
                    f"Model {name} exceeds historical boundary")
        expected = {(tid, expected_issue + timedelta(hours=hour))
                    for tid in turbine_ids for hour in range(1, horizon + 1)}
        keys = [(row.turbine_id, row.valid_time) for row in forecast]
        weather_keys = [(row.turbine_id, row.valid_time) for row in weather]
        require(len(keys) == len(set(keys)) and set(keys) == expected, "Forecast horizon not complete")
        require(len(weather_keys) == len(set(weather_keys)) and set(weather_keys) == expected,
                "Weather horizon not complete")
        grid_points, weather_runs = set(), set()
        for row in weather:
            require(row.provenance_kind == "operational_archive", "Non-operational weather")
            require(row.model_run_time <= row.forecast_available_at <= expected_issue,
                    "Weather availability leakage")
            require(row.wind_height_m == 100, "Unexpected weather height")
            require(row.weather_model == "gfs_0p25", "Unexpected weather model")
            require(row.availability_basis == "s3_last_modified_all_required_singlepart_grib_and_index_objects",
                    "Unexpected historical availability policy")
            provenance = json.loads(row.source_request)
            grid = provenance["grid"]
            grid_points.add((grid["latitude"], grid["longitude"]))
            weather_runs.add(row.model_run_time)
            raw_hashes.add(row.raw_sha256)
            require((service.settings.cache_dir / "raw" / (row.raw_sha256 + ".bin")).is_file(),
                    "Raw weather bundle missing from audit cache")
        require(len(weather_runs) == 1, "Weather cycles stitched inside one horizon")
        all_grids.update(grid_points)
        for row in forecast:
            require(row.issue_time == expected_issue, "Wrong prediction issue_time")
            require(row.training_cutoff <= expected_issue, "Training-cutoff leakage")
            require(row.training_cutoff == model_cutoff, "Forecast/model cutoff mismatch")
            require(row.model_version == inputs["model"]["model_version"], "Forecast/model version mismatch")
            require(row.weather_run_time in weather_runs, "Forecast/weather cycle mismatch")
            require(row.target_unit == "normalized_power", "Unexpected target unit")
            require(math.isfinite(row.prediction), "Nonfinite prediction")
            require(row.data_quality != "fixture", "Fixture prediction")
            warning_text.update(row.warnings)
            if start < row.valid_time <= end:
                key = (row.turbine_id, row.valid_time)
                if key not in recomputed or row.issue_time > recomputed[key].issue_time:
                    recomputed[key] = row
        model_hashes.add(inputs["model_file_sha256"])
        weather_count += len(weather)
        forecast_count += len(forecast)
        receipts.append({
            "run_id": record.run_id, "revision": record.revision,
            "issue_time": expected_issue.isoformat(), "status": "completed",
            "weather_rows": len(weather), "forecast_rows": len(forecast),
            "model_version": forecast[0].model_version,
            "training_cutoff": forecast[0].training_cutoff.isoformat(),
            "information_cutoffs": information_cutoffs,
            "weather_run_time": next(iter(weather_runs)).isoformat(),
            "max_forecast_available_at": max(row.forecast_available_at for row in weather).isoformat(),
            "first_valid_time": min(row.valid_time for row in forecast).isoformat(),
            "last_valid_time": max(row.valid_time for row in forecast).isoformat(),
            "grid_points": [list(point) for point in sorted(grid_points)],
            "input_fingerprint": record.input_fingerprint,
            "unchanged_inputs_event_present": any("Inputs unchanged" in event.message for event in record.events),
            "files": {name: {"path": str(directory / name), "sha256": file_sha(directory / name)}
                      for name in ("weather.json", "forecast.json", "inputs.json")},
        })
    require(set(recomputed) == expected_selected, "February coverage is not exactly 672 hours per turbine")
    replay_dir = service.root / "replay" / f"2026-02-{horizon}h"
    selected_json = read(replay_dir / "february.json")
    expected_json = [recomputed[key].model_dump(mode="json") for key in sorted(recomputed)]
    require(selected_json == expected_json, "Saved overlap selection differs from latest eligible issue rule")
    require(len(model_hashes) == 1, "Model changed during replay")
    require(all_grids == {(43.75, 78.5)}, "Unexpected nearest grid cells")
    require(forecast_count == 29 * horizon * len(turbine_ids), "Unexpected full forecast count")
    raw_bytes = 0
    for raw_hash in raw_hashes:
        raw_path = service.settings.cache_dir / "raw" / (raw_hash + ".bin")
        require(file_sha(raw_path) == raw_hash, f"Corrupted raw weather bundle: {raw_hash}")
        raw_bytes += raw_path.stat().st_size
    export_dir.mkdir(parents=True, exist_ok=True)
    json_path = export_dir / f"february-2026-{horizon}h.json"
    csv_path = export_dir / f"february-2026-{horizon}h.csv"
    write(json_path, selected_json)
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ForecastRecord.model_fields))
        writer.writeheader()
        for row in selected_json:
            writer.writerow({**row, "warnings": json.dumps(row["warnings"], ensure_ascii=False)})
    return {
        "schema_version": "1.0", "status": "passed",
        "checked_at": datetime.now(utc).isoformat(), "is_demo": False,
        "scope": "Real archived NOAA weather + delivered model; full historical replay and temporal/coverage audit, not February accuracy evaluation",
        "settings": service.settings.model_dump(mode="json"),
        "timestamp_convention": "hour_end", "weather_feature_time_basis": "instant_at_end",
        "origin_count": len(receipts), "horizon_hours": horizon,
        "full_forecast_rows": forecast_count, "full_weather_rows": weather_count,
        "selected_rows": len(selected_json),
        "selected_rows_per_turbine": dict(Counter(row["turbine_id"] for row in selected_json)),
        "first_selected_valid_time_utc": min(row["valid_time"] for row in selected_json),
        "last_selected_valid_time_utc": max(row["valid_time"] for row in selected_json),
        "first_selected_valid_time_local": min(recomputed[key].valid_time for key in recomputed).astimezone(zone).isoformat(),
        "last_selected_valid_time_local": max(recomputed[key].valid_time for key in recomputed).astimezone(zone).isoformat(),
        "overlap_policy": report["overlap_policy"], "failed_runs": 0, "missing_hours": 0,
        "all_weather_eligible_at_issue": True, "all_model_cutoffs_eligible_at_issue": True,
        "grid_points": [list(point) for point in sorted(all_grids)],
        "unique_weather_bundle_count": len(raw_hashes),
        "raw_bundle_content_sha256_verified": True, "raw_bundle_bytes": raw_bytes,
        "model_file_sha256": next(iter(model_hashes)),
        "test_truth_available": False, "february_accuracy_metrics": None,
        "warnings": sorted(warning_text),
        "exports": {"json": {"path": str(json_path), "sha256": file_sha(json_path)},
                    "csv": {"path": str(csv_path), "sha256": file_sha(csv_path)}},
        "replay_report": {"path": str(replay_dir / "report.json"), "sha256": file_sha(replay_dir / "report.json")},
        "runs": receipts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/replay-february.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/weather-cache"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("artifacts/february-integration"))
    parser.add_argument("--export-dir", type=Path, default=Path("outputs/forecast"))
    parser.add_argument("--evidence", type=Path, default=Path("docs/evidence/february-replay.json"))
    parser.add_argument("--horizon", type=int, choices=(24, 48), default=48)
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="Audit saved replay; no weather requests or model calls")
    args = parser.parse_args()
    settings = load_settings(args.config).model_copy(update={"cache_dir": args.cache_dir,
                                                           "artifact_dir": args.artifact_dir})
    require(settings.mode == "archive", "This verification requires archive mode")
    service = AgentService(settings)
    original_run = service.run
    completed = 0

    def traced_run(request):
        nonlocal completed
        started = time.monotonic()
        print(f"START {request.issue_time.isoformat()} horizon={request.horizon_hours}", flush=True)
        record = original_run(request)
        elapsed = time.monotonic() - started
        completed += record.status == "completed"
        print(f"DONE {request.issue_time.isoformat()} status={record.status} revision={record.revision} "
              f"completed={completed} elapsed_seconds={elapsed:.1f} error={record.error}", flush=True)
        return record

    service.run = traced_run
    try:
        if args.smoke_only:
            record = service.run(RunRequest(issue_time=datetime(2026, 1, 31, 18, tzinfo=timezone.utc),
                                           horizon_hours=args.horizon,
                                           turbine_ids=[t.turbine_id for t in settings.turbines]))
            print(f"SMOKE PASSED rows={len(service.forecast(record.run_id))} run_id={record.run_id}", flush=True)
            return
        if args.verify_only:
            report = read(service.root / "replay" / f"2026-02-{args.horizon}h" / "report.json")
        else:
            report = service.replay(args.horizon)
        evidence = audit(service, report, args.horizon, args.export_dir)
        write(args.evidence, evidence)
        print(f"AUDIT PASSED origins={evidence['origin_count']} full_rows={evidence['full_forecast_rows']} "
              f"selected={evidence['selected_rows']} evidence={args.evidence}", flush=True)
    finally:
        if service.weather is not None and hasattr(service.weather, "close"):
            service.weather.close()


if __name__ == "__main__":
    main()
