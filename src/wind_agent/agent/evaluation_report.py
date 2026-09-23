"""Expose an associated validation report without loading or training the model."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any

from wind_agent.config import Settings
from wind_agent.contracts import ModelArtifact


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reject_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON number: {value}")


def _read(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw, parse_constant=_reject_constant)
    _require(isinstance(value, dict), f"{path.name} must contain a JSON object")
    return value, sha256(raw).hexdigest()


def _time(value: Any, label: str) -> datetime:
    _require(isinstance(value, str), f"{label} must be an aware ISO timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(result.tzinfo is not None and result.utcoffset() is not None,
             f"{label} must include a timezone")
    return result.astimezone(timezone.utc)


def _number(value: Any, label: str) -> float:
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
             f"{label} must be a finite nonnegative number")
    return float(value)


def _count(value: Any, label: str) -> int:
    _require(type(value) is int and value > 0, f"{label} must be a positive integer")
    return value


def _score(node: dict[str, Any], label: str) -> None:
    _require(isinstance(node, dict), f"{label} must be an object")
    _number(node["mae"], label + ".mae")
    _number(node["rmse"], label + ".rmse")
    count = _count(node["n_forecasts"], label + ".n_forecasts")
    unique = _count(node["n_unique_targets"], label + ".n_unique_targets")
    _require(unique <= count, f"{label} unique target count exceeds forecast count")


def _metrics(card: dict[str, Any], phase: str, artifact: ModelArtifact) -> None:
    _require(isinstance(card, dict) and card["status"] == "ok", "Invalid metric status")
    _require(card["target_unit"] == artifact.target_unit, "Metric target unit mismatch")
    _require(card["target_time_convention"] == "hour_end", "Metric time convention mismatch")
    _require(card["supplied_splits"] == [phase], f"Metric belongs to a different split: {phase}")
    _require(card["eligible_for_selection"] is (phase == "tune"),
             f"Incorrect selection eligibility in {phase}")
    _score(card["overall"], phase + ".overall")
    _require(card["n_forecasts"] == card["overall"]["n_forecasts"]
             and card["n_unique_targets"] == card["overall"]["n_unique_targets"],
             f"Inconsistent metric counts in {phase}")
    turbines = card["by_turbine"]
    _require(set(turbines) == set(artifact.metadata["turbine_ids"]),
             f"Metric turbine IDs mismatch in {phase}")
    for name, score in turbines.items():
        _score(score, f"{phase}.{name}")
    for name, score in card["by_lead_group"].items():
        _score(score, f"{phase}.lead.{name}")
    for name, groups in card.get("by_turbine_and_lead_group", {}).items():
        for lead, score in groups.items():
            _score(score, f"{phase}.{name}.{lead}")
    _require(sum(row["n_forecasts"] for row in turbines.values()) == card["n_forecasts"],
             f"Inconsistent turbine counts in {phase}")
    mean = sum(row["mae"] for row in turbines.values()) / len(turbines)
    recorded = _number(card["equal_turbine_mean_mae"], phase + ".equal_turbine_mean_mae")
    _require(math.isclose(mean, recorded, rel_tol=1e-10, abs_tol=1e-12),
             f"Inconsistent equal-turbine MAE in {phase}")


def _validate(report: dict[str, Any], artifact: ModelArtifact) -> None:
    meta = artifact.metadata
    _require(report["schema_version"] == "1.0", "Unsupported validation schema")
    _require(report["design"]["model_version"] == artifact.model_version,
             "Validation model_version differs from the configured package")
    _require(meta.get("validation") == report,
             "validation.json differs from the report embedded in package metadata")
    _require(report["source_sha256"] == meta["source_sha256"], "Source hash association mismatch")
    selected = report["selected_candidate"]
    _require(selected["algorithm"] == meta["algorithm"]
             and selected["hyperparameters"] == meta["hyperparameters"],
             "Selected algorithm/hyperparameters differ from production metadata")
    _require(report["design"]["selection_metric"] == "equal_turbine_mean_mae",
             "Unsupported model-selection metric")
    _require(report["holdout_used_for_selection"] is False
             and report["uses_holdout"] is False,
             "Report does not describe an independent holdout")
    _require(report["february_ground_truth_available"] is False,
             "February-truth claims are unsupported by this evaluation endpoint")
    _require(type(report["production_refit_includes_holdout"]) is bool,
             "production_refit_includes_holdout must be boolean")
    _require(isinstance(report["holdout_metrics_apply_to"], str)
             and bool(report["holdout_metrics_apply_to"]), "Missing holdout model scope")
    production_cutoff = _time(report["production_training_cutoff"], "production cutoff")
    selection_cutoff = _time(report["selection_cutoff"], "selection cutoff")
    holdout_cutoff = _time(report["holdout_model_training_cutoff"], "holdout model cutoff")
    _require(production_cutoff == artifact.training_cutoff, "Production training cutoff mismatch")
    _require(selection_cutoff == _time(meta["selection_cutoff"], "metadata selection cutoff"),
             "Selection cutoff association mismatch")
    _require(selection_cutoff <= holdout_cutoff <= production_cutoff,
             "Invalid selection/holdout/production cutoff order")
    split = report["splits"]["split_config"]
    _require(split["selection_uses_holdout"] is False
             and report["splits"]["target_overlap_between_splits"] is False,
             "Split definition does not establish separate tuning and holdout")
    _require(split["target_start_exclusive"] is True and split["target_end_inclusive"] is True
             and split["target_time_convention"] == "hour_end", "Invalid interval-end boundaries")
    _require(split["history_timezone"] == meta["history_timezone"], "History timezone mismatch")
    previous_end = None
    for phase in ("train", "tune", "holdout"):
        window = split["windows"][phase]
        start, end = (_time(window[key], f"{phase}.{key}") for key in ("target_start", "target_end"))
        issue_start = _time(window["issue_start"], f"{phase}.issue_start")
        issue_end = _time(window["issue_end"], f"{phase}.issue_end")
        _require(start < end and issue_start <= issue_end, f"Invalid {phase} window")
        _require(previous_end is None or previous_end <= start, "Target windows overlap")
        if phase == "holdout":
            _require(holdout_cutoff <= issue_start and end <= production_cutoff,
                     "Holdout window conflicts with model cutoffs")
        previous_end = end
    for phase in ("tune", "holdout"):
        cards = report[phase]
        _require(selected["name"] in cards and {"constant_mean", "wind_curve"} <= cards.keys(),
                 f"Missing selected model or baseline in {phase}")
        counts = set()
        for card in cards.values():
            _metrics(card, phase, artifact)
            counts.add((card["n_forecasts"], card["n_unique_targets"]))
        _require(len(counts) == 1, f"Candidates have different evaluation counts in {phase}")
    _require(isinstance(report["limitations"], list)
             and all(isinstance(item, str) for item in report["limitations"]),
             "Validation limitations must be a list of strings")


def evaluation_report(settings: Settings) -> dict[str, Any]:
    """Read the configured package; return ready, unavailable, or invalid JSON data.

    This verifies local file association, not the authorship or statistical
    correctness of the training experiment. Model code/weights are never loaded.
    """
    result: dict[str, Any] = {
        "status": "unavailable", "metrics": None, "baseline": None, "periods": None,
        "model_version": None, "training_cutoff": None, "test_truth_available": False,
        "is_demo": settings.mode == "demo", "warnings": [], "provenance": {}, "reason": None,
    }
    if result["is_demo"]:
        result["reason"] = "Synthetic demo has no real validation metrics"
        return result
    if settings.model_artifact_path is None:
        result["reason"] = "No model package configured"
        return result
    configured = Path(settings.model_artifact_path)
    package = configured.parent if configured.suffix.lower() == ".json" else configured
    metadata_path = configured if configured.suffix.lower() == ".json" else package / "metadata.json"
    report_path, weights_path = package / "validation.json", package / "model.joblib"
    missing = [path.name for path in (metadata_path, report_path, weights_path) if not path.is_file()]
    if missing:
        result["reason"] = "Missing model evaluation files: " + ", ".join(missing)
        return result
    try:
        metadata, metadata_hash = _read(metadata_path)
        artifact = ModelArtifact.model_validate(metadata)
        report, report_hash = _read(report_path)
        weights_hash = sha256(weights_path.read_bytes()).hexdigest()
        expected = artifact.metadata["model_sha256"]
        _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
                 "Missing or invalid model_sha256")
        _require(expected == weights_hash, "Model weight SHA256 differs from package metadata")
        _validate(report, artifact)
        selected = report["selected_candidate"]["name"]
        tuning, holdout = report["tune"][selected], report["holdout"][selected]
        baseline = report["holdout"]["wind_curve"]
        selected_mae = holdout["equal_turbine_mean_mae"]
        baseline_mae = baseline["equal_turbine_mean_mae"]
        warnings = list(report["limitations"])
        warnings.extend(artifact.metadata.get("warnings", []))
        warnings.append("Holdout scores apply to the frozen train+tune refit, not the final production refit")
        if baseline_mae < selected_mae:
            warnings.append(
                f"Wind-curve baseline has lower independent-holdout MAE ({baseline_mae:.6f}) "
                f"than {selected} ({selected_mae:.6f}); selection remains the predeclared tuning choice"
            )
        result.update({
            "status": "ready", "model_version": artifact.model_version,
            "training_cutoff": artifact.training_cutoff.isoformat(), "target_unit": artifact.target_unit,
            "selected_candidate": report["selected_candidate"],
            "metrics": {
                "tuning": {"selected_candidate": selected, "scores": tuning,
                           "selection_metric": report["design"]["selection_metric"],
                           "selection_cutoff": report["selection_cutoff"]},
                "independent_holdout": {
                    "selected_candidate": selected, "scores": holdout,
                    "training_cutoff": report["holdout_model_training_cutoff"],
                    "used_for_selection": False, "applies_to": report["holdout_metrics_apply_to"],
                },
                "production_refit": {
                    "training_cutoff": report["production_training_cutoff"],
                    "includes_holdout": report["production_refit_includes_holdout"],
                    "independent_metrics": None,
                    "reason": "No independent evaluation of the final production refit is supplied",
                },
            },
            "baseline": {
                "tuning": {name: report["tune"][name] for name in ("constant_mean", "wind_curve")},
                "independent_holdout": {
                    name: report["holdout"][name] for name in ("constant_mean", "wind_curve")
                },
                "comparison": {"metric": "equal_turbine_mean_mae", "selected_mae": selected_mae,
                               "wind_curve_mae": baseline_mae,
                               "wind_curve_better": baseline_mae < selected_mae},
            },
            "periods": report["splits"]["split_config"],
            "warnings": list(dict.fromkeys(warnings)),
            "provenance": {
                "package_directory": str(package), "validation_path": str(report_path),
                "metadata_sha256": metadata_hash, "validation_sha256": report_hash,
                "model_sha256": weights_hash, "report_created_at": report["created_at"],
                "experiment_config_sha256": report["experiment_config_sha256"],
                "hourly_history_sha256": report["hourly_history_sha256"],
                "weather_manifest_sha256": report["weather_manifest_sha256"],
                "source_sha256": report["source_sha256"],
                "association": "validation.json equals embedded metadata report; model SHA256 verified",
                "source_files_rechecked": False,
            },
        })
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError) as error:
        result.update(status="invalid", reason=f"Invalid model evaluation report: {error}")
    return result
