"""Reproduce chronological selection, untouched holdout and final model packaging.

Run only after the complete declared NOAA archive collection. No network or LLM
is used here. Raw weather and source-clock assumptions remain explicit evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from numbers import Real
from pathlib import Path

import numpy as np
import pandas as pd

from wind_agent.data.sources import SOURCES
from wind_agent.evaluation.metrics import evaluate
from wind_agent.evaluation.splits import (
    SplitConfig, _utc_column, assign_splits, ensure_model_cutoff,
)
from wind_agent.evaluation.weather_dataset import load_weather_dataset, planned_origins
from .estimators import TURBINES, fit_estimators, predict_estimators
from .interface import prepare_training_pairs, train

KEY = ["turbine_id", "issue_time", "valid_time"]


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def score(models, rows, cutoff):
    ensure_model_cutoff(cutoff, rows)
    result = rows[[*KEY, "normalized_power", "split"]].copy()
    result["prediction"] = predict_estimators(models, rows)
    report = evaluate(result)
    if report["status"] != "ok" or set(report["by_turbine"]) != set(TURBINES):
        raise ValueError("Every candidate must score the identical complete two-turbine set")
    report["equal_turbine_mean_mae"] = float(np.mean([
        report["by_turbine"][tid]["mae"] for tid in TURBINES
    ]))
    return result, report


def select_candidate(candidates, reports):
    """First declared candidate wins an exact tie; only tuning reports are supplied."""
    if not candidates or len({row["name"] for row in candidates}) != len(candidates):
        raise ValueError("Candidates require unique names")
    for candidate in candidates:
        report = reports.get(candidate["name"], {})
        if (not isinstance(report, dict) or report.get("status") != "ok"
                or report.get("eligible_for_selection") is not True
                or report.get("supplied_splits") != ["tune"]):
            raise ValueError("Invalid tuning predictions cannot select a candidate")
        metric = report.get("equal_turbine_mean_mae")
        if (isinstance(metric, bool) or not isinstance(metric, Real)
                or not np.isfinite(metric) or metric < 0):
            raise ValueError("Selection metric must be finite and nonnegative")
    return min(candidates, key=lambda row: reports[row["name"]]["equal_turbine_mean_mae"])


def run_experiment(history_path, hourly_report_path, weather_dir, experiment_path,
                   output_dir, package_dir):
    output_dir, package_dir = Path(output_dir), Path(package_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Choose an empty experiment output directory to preserve prior results")
    if package_dir.exists() and any(package_dir.iterdir()):
        raise ValueError("Choose an empty model package directory")
    design = json.loads(Path(experiment_path).read_text("utf-8"))
    if (design["selection_metric"] != "equal_turbine_mean_mae"
            or design["tie_break"] != "candidate_order"):
        raise ValueError("Unsupported experiment selection policy")
    hourly_report = json.loads(Path(hourly_report_path).read_text("utf-8"))
    hashes = {tid: SOURCES[tid]["sha256"] for tid in TURBINES}
    if hourly_report["source_sha256"] != hashes:
        raise ValueError("Hourly history must derive from the audited official source files")
    semantics = hourly_report["semantics"]
    split_config = SplitConfig(history_timezone=semantics["history_timezone"])
    manifest_path = Path(weather_dir) / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    expected = {origin.issue_time.isoformat() for origin in planned_origins()}
    completed = {row["issue_time"] for row in manifest["origins"] if row["status"] == "completed"}
    if completed != expected or manifest["completed_origins"] != len(expected):
        raise ValueError("Complete all 60 declared weather origins before comparing models")
    weather = pd.DataFrame(load_weather_dataset(weather_dir, splits=["train", "tune", "holdout"]))
    history = pd.read_csv(history_path)
    for frame, columns in ((history, ["valid_time", "available_at"]),
                           (weather, ["valid_time", "issue_time"])):
        for name in columns:
            frame[name] = _utc_column(frame[name], name)
    # Validate archived semantics and historical truth before any fitting; the
    # left join below retains missing labels for exclusion/coverage accounting.
    prepare_training_pairs(history, weather, split_config.first_forecast_cutoff)
    paired = weather.drop(columns=["lead_hours"]).merge(
        history[["turbine_id", "valid_time", "normalized_power", "available_at", "is_complete"]],
        on=["turbine_id", "valid_time"], how="left", validate="many_to_one",
    )
    paired.loc[~paired.is_complete.eq(True), "normalized_power"] = np.nan
    splits = assign_splits(paired, split_config)
    for name in ("train", "tune", "holdout"):
        if set(getattr(splits, name).turbine_id) != set(TURBINES):
            raise ValueError(f"Both turbines require complete labels in {name}")

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": "1.0", "created_at": datetime.now(timezone.utc).isoformat(),
        "design": design, "experiment_config_sha256": sha_file(experiment_path),
        "hourly_history_sha256": sha_file(history_path),
        "hourly_report_sha256": sha_file(hourly_report_path),
        "weather_manifest_sha256": sha_file(manifest_path),
        "source_sha256": hashes, "history_semantics": semantics,
        "splits": splits.report,
        "weather": {key: manifest[key] for key in (
            "provider", "weather_model", "wind_height_m", "temperature_height_m",
            "feature_time_basis", "availability_basis", "completed_origins",
        )},
    }
    # Persist the declared design BEFORE looking at any candidate errors.
    save_json(output_dir / "design.json", evidence)
    tune_reports = {}
    for candidate in design["candidates"]:
        models = fit_estimators(splits.train, candidate["algorithm"], candidate["hyperparameters"])
        predictions, report = score(models, splits.tune, split_config.train_cutoff)
        tune_reports[candidate["name"]] = report
        predictions.to_csv(output_dir / f"tune-{candidate['name']}.csv", index=False)
    winner = select_candidate(design["candidates"], tune_reports)
    selection = {
        "selected_candidate": winner, "tune": tune_reports,
        "selection_cutoff": split_config.selection_cutoff.isoformat(),
        "uses_holdout": False,
    }
    # Selection is frozen and recorded before any holdout score is calculated.
    save_json(output_dir / "selection.json", selection)

    refit = pd.concat([splits.train, splits.tune], ignore_index=True)
    holdout_reports = {}
    comparison_names = {winner["name"], "constant_mean", "wind_curve"}
    for candidate in design["candidates"]:
        if candidate["name"] not in comparison_names:
            continue
        models = fit_estimators(refit, candidate["algorithm"], candidate["hyperparameters"])
        predictions, report = score(models, splits.holdout, split_config.selection_cutoff)
        holdout_reports[candidate["name"]] = report
        predictions.to_csv(output_dir / f"holdout-{candidate['name']}.csv", index=False)

    validation = {
        **evidence, **selection, "holdout": holdout_reports,
        "holdout_model_training_cutoff": split_config.selection_cutoff.isoformat(),
        "holdout_refit_rows": {tid: int((refit.turbine_id == tid).sum()) for tid in TURBINES},
        "holdout_used_for_selection": False,
        "production_training_cutoff": split_config.first_forecast_cutoff.isoformat(),
        "production_refit_includes_holdout": True,
        "holdout_metrics_apply_to": "train+tune refit frozen before holdout; not the final production refit",
        "february_ground_truth_available": False,
        "uncertainty_intervals": "not calibrated",
        "limitations": [
            "Evaluation covers one recent winter window; overlapping forecast origins are not independent samples.",
            "Both turbines use the same coarse GFS grid cell; models are fitted separately.",
            "SCADA timezone, interval convention, reporting delay and normalization remain provisional.",
            "S3 metadata proves archived-object availability under the declared policy, not original NOAA publication.",
            "No February truth is supplied; January MAE is not February accuracy.",
        ],
    }
    save_json(output_dir / "validation.json", validation)
    config = {
        "output_dir": str(package_dir), "algorithm": winner["algorithm"],
        "hyperparameters": winner["hyperparameters"], "model_version": design["model_version"],
        "history_semantics": semantics, "source_sha256": hashes,
        "selection_cutoff": split_config.selection_cutoff.isoformat(),
        "calibration_cutoff": split_config.selection_cutoff.isoformat(),
        "clipping": "none", "uncertainty_calibration": "none",
        "validation": validation,
    }
    train(history, weather, split_config.first_forecast_cutoff, config)
    save_json(package_dir / "validation.json", validation)
    result = {"selected_candidate": winner["name"], "package_dir": str(package_dir),
              "validation_path": str(output_dir / "validation.json"),
              "model_sha256": sha_file(package_dir / "model.joblib"),
              "tune_mae": tune_reports[winner["name"]]["equal_turbine_mean_mae"],
              "holdout_mae": holdout_reports[winner["name"]]["equal_turbine_mean_mae"]}
    save_json(output_dir / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=Path("artifacts/data/hourly-history.csv"))
    parser.add_argument("--hourly-report", type=Path, default=Path("artifacts/data/hourly-report.json"))
    parser.add_argument("--weather", type=Path, default=Path("artifacts/weather-training"))
    parser.add_argument("--experiment", type=Path, default=Path("config/model-experiment.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/model-experiment-v1"))
    parser.add_argument("--package", type=Path, default=Path("models/wind-power-v1"))
    args = parser.parse_args()
    result = run_experiment(args.history, args.hourly_report, args.weather, args.experiment,
                            args.output, args.package)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
