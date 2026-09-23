"""Offline train/save/load/predict contract with explicit historical availability."""

from datetime import datetime
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path
import re

import joblib
import numpy as np
import pandas as pd

from wind_agent.contracts import ModelArtifact
from wind_agent.data.hourly import HistorySemantics
from .estimators import FEATURES, TURBINES, fit_estimators, numeric_values, predict_estimators


PROVIDER = "NOAA GFS via AWS Open Data"
WEATHER_MODEL = "gfs_0p25"
WEATHER_BASIS = "instant_at_end"
WEATHER_REQUIRED = {
    "turbine_id", "valid_time", *FEATURES, "wind_height_m", "model_run_time",
    "forecast_available_at", "availability_basis", "provider", "weather_model", "raw_sha256",
}


def _fail(code, message):
    raise ValueError(f"{code}: {message}")


def _utc(value, name, *, hourly=False):
    if not isinstance(value, (str, datetime, pd.Timestamp)):
        _fail("NAIVE_TIMESTAMP", f"{name} must be an aware UTC timestamp")
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        _fail("INVALID_SCHEMA", f"invalid {name}: {exc}")
    if pd.isna(stamp) or stamp.tzinfo is None:
        _fail("NAIVE_TIMESTAMP", f"{name} must be aware UTC")
    if stamp.utcoffset().total_seconds() != 0:
        _fail("INVALID_SCHEMA", f"{name} must use UTC")
    if hourly and stamp != stamp.floor("h"):
        _fail("INVALID_SCHEMA", f"{name} must be on an exact hour")
    return stamp.tz_convert("UTC")


def _utc_column(frame, name, *, hourly=False):
    frame[name] = pd.to_datetime([_utc(value, name, hourly=hourly) for value in frame[name]], utc=True)


def _frame(frame, required):
    if (
        not isinstance(frame, pd.DataFrame) or frame.empty or not frame.columns.is_unique
        or not set(required).issubset(frame.columns)
    ):
        _fail("INVALID_SCHEMA", f"nonempty frame requires columns {sorted(required)}")
    copied = frame.copy(deep=True)
    if not copied.turbine_id.isin(TURBINES).all():
        _fail("UNSUPPORTED_TURBINE", "unknown turbine_id")
    return copied


def _weather(frame, issue=None):
    required = WEATHER_REQUIRED | ({"issue_time"} if issue is None else set())
    result = _frame(frame, required)
    for name in ("valid_time", "model_run_time", "forecast_available_at"):
        _utc_column(result, name, hourly=name != "forecast_available_at")
    if issue is None:
        _utc_column(result, "issue_time", hourly=True)
    else:
        if "issue_time" in result:
            _utc_column(result, "issue_time", hourly=True)
            if not result.issue_time.eq(issue).all():
                _fail("INVALID_SCHEMA", "weather issue_time differs from prediction issue")
        result["issue_time"] = issue
    numeric_values(result, [*FEATURES, "wind_height_m"])
    if (result.wind_speed_ms < 0).any():
        _fail("NONFINITE_FEATURE", "wind speed must be nonnegative")
    if (
        not result.provider.eq(PROVIDER).all() or not result.weather_model.eq(WEATHER_MODEL).all()
        or not result.wind_height_m.eq(100).all()
    ):
        _fail("FEATURE_SEMANTICS_MISMATCH", "expected NOAA GFS 0.25 degree, wind at 100 m")
    if "weather_feature_time_basis" in result and not result.weather_feature_time_basis.eq(
        WEATHER_BASIS
    ).all():
        _fail("FEATURE_SEMANTICS_MISMATCH", "expected instant_at_end weather")
    if "provenance_kind" in result and not result.provenance_kind.eq("operational_archive").all():
        _fail("FEATURE_SEMANTICS_MISMATCH", "only operational archived forecasts are supported")
    for column in ("availability_basis", "raw_sha256"):
        if not result[column].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
            _fail("INVALID_SCHEMA", f"{column} requires nonempty strings")
    if not result.raw_sha256.str.fullmatch(r"[0-9a-f]{64}").all():
        _fail("INVALID_SCHEMA", "raw_sha256 must be a SHA256 hex digest")
    if (
        (result.model_run_time > result.forecast_available_at).any()
        or (result.forecast_available_at > result.issue_time).any()
        or (result.valid_time <= result.issue_time).any()
    ):
        _fail("WEATHER_NOT_AVAILABLE", "require run <= availability <= issue < target end")
    if result.duplicated(["turbine_id", "issue_time", "valid_time"]).any():
        _fail("DUPLICATE_TARGET", "duplicate turbine/issue/valid_time weather rows")
    if result.groupby(["turbine_id", "issue_time"]).model_run_time.nunique().gt(1).any():
        _fail("FEATURE_SEMANTICS_MISMATCH", "one weather run per turbine/issue required")
    if "lead_hours" in result:
        numeric_values(result, ["lead_hours"])
        lead = (result.valid_time - result.model_run_time).dt.total_seconds() / 3600
        if not result.lead_hours.eq(lead).all():
            _fail("INVALID_SCHEMA", "weather lead_hours must be measured from model_run_time")
    return result


def prepare_training_pairs(history, weather, cutoff):
    """Join only complete available historical labels to forecasts issued before cutoff.

    Distinct forecast vintages may repeat a target hour; their issue keys are retained.
    The caller chooses its chronological validation policy before invoking fitting.
    """
    cutoff = _utc(cutoff, "cutoff")
    observed = _frame(history, {
        "turbine_id", "valid_time", "normalized_power", "available_at", "is_complete",
    })
    for name in ("valid_time", "available_at"):
        _utc_column(observed, name, hourly=name == "valid_time")
    if observed.duplicated(["turbine_id", "valid_time"]).any():
        _fail("DUPLICATE_TARGET", "duplicate historical target hour")
    if not pd.api.types.is_bool_dtype(observed.is_complete) or observed.is_complete.isna().any():
        _fail("INVALID_SCHEMA", "is_complete must contain boolean completeness flags")
    if (observed.available_at < observed.valid_time).any():
        _fail("INVALID_SCHEMA", "target availability cannot precede its interval end")
    observed = observed.loc[observed.is_complete & observed.available_at.le(cutoff), [
        "turbine_id", "valid_time", "normalized_power", "available_at",
    ]]
    numeric_values(observed, ["normalized_power"], code="INVALID_SCHEMA")
    forecast = _weather(weather)
    forecast = forecast.loc[forecast.issue_time.lt(cutoff)].drop(
        columns=["normalized_power", "available_at", "is_complete"], errors="ignore",
    )
    paired = forecast.merge(observed, on=["turbine_id", "valid_time"], how="inner",
                            validate="many_to_one")
    if paired.empty:
        _fail("INVALID_SCHEMA", "no complete available targets match the forecast vintages")
    return paired.sort_values(["turbine_id", "issue_time", "valid_time"]).reset_index(drop=True)


def _metadata(config, cutoff, paired):
    required = {"output_dir", "algorithm", "model_version", "history_semantics", "source_sha256",
                "selection_cutoff", "calibration_cutoff"}
    if not isinstance(config, dict) or not required.issubset(config):
        _fail("INVALID_SCHEMA", f"training config requires {sorted(required)}")
    source = config["history_semantics"]
    if not isinstance(source, dict):
        _fail("INVALID_SCHEMA", "history_semantics must be an explicit object")
    try:
        semantics = HistorySemantics(
            history_timezone=source["history_timezone"],
            source_timestamp_convention=source["source_timestamp_convention"],
            target_reporting_delay_seconds=source["target_reporting_delay_seconds"],
            assumptions=source["assumptions"],
        )
    except KeyError as exc:
        _fail("INVALID_SCHEMA", f"missing history semantics {exc}")
    limit = pd.Timestamp("2026-02-01").tz_localize(semantics.history_timezone).tz_convert("UTC")
    if cutoff > limit:
        _fail("MODEL_TRAINED_AFTER_ISSUE", "training information extends beyond January")
    selection = _utc(config["selection_cutoff"], "selection_cutoff")
    calibration = _utc(config["calibration_cutoff"], "calibration_cutoff")
    if selection > cutoff or calibration > cutoff:
        _fail("MODEL_TRAINED_AFTER_ISSUE", "selection/calibration information exceeds cutoff")
    if not isinstance(config["model_version"], str) or not config["model_version"].strip():
        _fail("INVALID_SCHEMA", "model_version must be a nonempty string")
    hashes = config["source_sha256"]
    if not isinstance(hashes, dict) or set(hashes) != set(TURBINES) or any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in hashes.values()
    ):
        _fail("INVALID_SCHEMA", "source_sha256 must identify both historical source files")
    clipping = config.get("clipping", "none")
    if clipping not in ("none", "training_range"):
        _fail("INVALID_SCHEMA", "clipping must be none or explicitly training_range")
    # Arbitrary experiment documentation stays under ModelArtifact.metadata.
    metadata = {key: value for key, value in config.items() if key not in required}
    feature_names = ["wind_speed_ms"] if config["algorithm"] == "wind_curve" else list(FEATURES)
    if config["algorithm"] == "constant_mean":
        feature_names = []
    metadata.update({
        "schema_version": "1.0", "algorithm": config["algorithm"],
        "hyperparameters": dict(config.get("hyperparameters", {})), "random_seed": 42,
        "turbine_ids": list(TURBINES), "feature_names": feature_names,
        "feature_dtypes": {name: "float64" for name in feature_names},
        "feature_units": {"wind_speed_ms": "m/s", "temperature_c": "degC"},
        "weather_provider": PROVIDER, "weather_model": WEATHER_MODEL, "wind_height_m": 100,
        "weather_feature_time_basis": WEATHER_BASIS, "timestamp_convention": "hour_end",
        "history_semantics": semantics.as_metadata(), **semantics.as_metadata(),
        "source_sha256": hashes, "selection_cutoff": selection.isoformat(),
        "calibration_cutoff": calibration.isoformat(), "clipping": clipping,
        "preprocessing": "no imputation, scaling or measured-weather substitution",
        "training_rows": {key: int(value) for key, value in paired.turbine_id.value_counts().items()},
        "training_target_range": {
            turbine: [float(rows.normalized_power.min()), float(rows.normalized_power.max())]
            for turbine, rows in paired.groupby("turbine_id", sort=True)
        },
        "training_target_start": paired.valid_time.min().isoformat(),
        "training_target_end": paired.valid_time.max().isoformat(),
        "training_target_available_at_max": paired.available_at.max().isoformat(),
        "training_weather_issue_max": paired.issue_time.max().isoformat(),
        "training_weather_raw_sha256": sorted(paired.raw_sha256.unique().tolist()),
        "runtime_versions": {name: version(name) for name in
                             ("numpy", "pandas", "scikit-learn", "joblib", "tzdata")},
        "warnings": [
            *semantics.assumptions,
            "Source-clock and target normalization semantics remain unconfirmed.",
            "Weather availability uses archive-object metadata, not original NOAA publication proof.",
            "Forecast target is normalized power; no conversion to MW/MWh is justified.",
        ],
    })
    return metadata


def train(history, weather_features, cutoff, config):
    """Train and create a new immutable model directory; never train inside predict."""
    cutoff = _utc(cutoff, "cutoff")
    paired = prepare_training_pairs(history, weather_features, cutoff)
    if set(paired.turbine_id) != set(TURBINES):
        _fail("UNSUPPORTED_TURBINE", "a package requires training pairs for both turbines")
    metadata = _metadata(config, cutoff, paired)
    declared_available = paired.valid_time + pd.Timedelta(
        seconds=metadata["target_reporting_delay_seconds"]
    )
    if not paired.available_at.eq(declared_available).all():
        _fail("INVALID_SCHEMA", "historical availability differs from declared reporting delay")
    package = Path(config["output_dir"])
    if any((package / name).exists() or (package / name).is_symlink()
           for name in ("model.joblib", "metadata.json")):
        _fail("INVALID_SCHEMA", "package already contains model files; choose a new output_dir")
    models = fit_estimators(paired, config["algorithm"], config.get("hyperparameters"))
    artifact = ModelArtifact(
        model_version=config["model_version"], training_cutoff=cutoff.to_pydatetime(),
        target_unit="normalized_power", metadata=metadata,
    ).model_dump(mode="json", exclude_none=True)
    # Bind all metadata to the weights, including information cutoffs and assumptions.
    buffer = io.BytesIO()
    joblib.dump({"models": models, "artifact": artifact}, buffer, compress=3)
    weights = buffer.getvalue()
    artifact_with_hash = {**artifact, "metadata": {
        **artifact["metadata"], "model_sha256": hashlib.sha256(weights).hexdigest(),
    }}
    encoded = json.dumps(artifact_with_hash, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    package.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents inadvertently replacing a previous model package.
    with (package / "model.joblib").open("xb") as stream:
        stream.write(weights)
    with (package / "metadata.json").open("x", encoding="utf-8") as stream:
        stream.write(encoded)
    return package


def _load_package(package):
    package = Path(package)
    try:
        artifact = ModelArtifact.model_validate_json(
            (package / "metadata.json").read_text("utf-8")
        ).model_dump(mode="json", exclude_none=True)
        weights = (package / "model.joblib").read_bytes()
    except (OSError, ValueError) as exc:
        _fail("INVALID_SCHEMA", f"cannot read model package: {exc}")
    digest = artifact["metadata"].get("model_sha256")
    if not isinstance(digest, str) or hashlib.sha256(weights).hexdigest() != digest:
        _fail("INVALID_SCHEMA", "model.joblib SHA256 differs from metadata")
    try:
        payload = joblib.load(io.BytesIO(weights))
    except Exception as exc:
        _fail("INVALID_SCHEMA", f"cannot load model weights: {exc}")
    bound_artifact = {**artifact, "metadata": {
        key: value for key, value in artifact["metadata"].items() if key != "model_sha256"
    }}
    if not isinstance(payload, dict) or payload.get("artifact") != bound_artifact:
        _fail("INVALID_SCHEMA", "model metadata differs from the metadata bound into weights")
    if not isinstance(payload.get("models"), dict) or set(payload["models"]) != set(TURBINES):
        _fail("INVALID_SCHEMA", "package must contain both turbine models")
    return artifact, payload["models"]


def predict(model_artifact, weather_frame, issue_time, horizon_hours):
    """Validate and forecast one or both turbines from archived weather only."""
    if type(horizon_hours) is not int or horizon_hours not in (24, 48):
        _fail("INVALID_HORIZON", "horizon_hours must be integer 24 or 48")
    issue = _utc(issue_time, "issue_time", hourly=True)
    artifact, models = _load_package(model_artifact)
    cutoff = _utc(artifact["training_cutoff"], "training_cutoff")
    if cutoff > issue:
        _fail("MODEL_TRAINED_AFTER_ISSUE", "training information exceeds forecast issue")
    metadata = artifact["metadata"]
    if (
        artifact["target_unit"] != "normalized_power"
        or metadata.get("weather_provider") != PROVIDER
        or metadata.get("weather_model") != WEATHER_MODEL
        or metadata.get("wind_height_m") != 100
        or metadata.get("weather_feature_time_basis") != WEATHER_BASIS
        or metadata.get("timestamp_convention") != "hour_end"
    ):
        _fail("FEATURE_SEMANTICS_MISMATCH", "unsupported model feature/target semantics")
    for name in ("selection_cutoff", "calibration_cutoff"):
        if _utc(metadata[name], name) > cutoff:
            _fail("MODEL_TRAINED_AFTER_ISSUE", f"{name} exceeds model cutoff")
    weather = _weather(weather_frame, issue)
    expected = pd.date_range(issue + pd.Timedelta(hours=1), periods=horizon_hours, freq="h")
    for _, rows in weather.groupby("turbine_id", sort=True):
        if len(rows) != horizon_hours or set(rows.valid_time) != set(expected):
            _fail("INCOMPLETE_HORIZON", "each supplied turbine needs exactly issue+1h through +Hh")
    weather = weather.sort_values(["turbine_id", "valid_time"]).reset_index(drop=True)
    values = predict_estimators(models, weather)
    if metadata["clipping"] == "training_range":
        for turbine, bounds in metadata["training_target_range"].items():
            mask = weather.turbine_id.eq(turbine).to_numpy()
            values[mask] = np.clip(values[mask], *bounds)
    output = weather[["turbine_id", "valid_time"]].copy()
    output["schema_version"] = "1.0"
    output["issue_time"] = issue
    output["lead_hours"] = ((output.valid_time - issue).dt.total_seconds() / 3600).astype(int)
    output["prediction"] = values
    output["target_unit"] = "normalized_power"
    output["weather_model"] = weather.weather_model
    output["weather_run_time"] = weather.model_run_time
    output["model_version"] = artifact["model_version"]
    output["training_cutoff"] = cutoff
    output["data_quality"] = "warning"
    output["warnings"] = [list(metadata["warnings"]) for _ in range(len(output))]
    return output[[
        "schema_version", "issue_time", "turbine_id", "valid_time", "lead_hours", "prediction",
        "target_unit", "weather_model", "weather_run_time", "model_version", "training_cutoff",
        "data_quality", "warnings",
    ]]


def evaluate(predictions, observed=None, split_config=None):
    """Delegate normalized-power scoring to the shared chronological evaluator."""
    from wind_agent.evaluation import evaluate as evaluate_predictions

    return evaluate_predictions(predictions, observed, split_config)
