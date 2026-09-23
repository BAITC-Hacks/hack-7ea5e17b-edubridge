"""Synthetic contract fixtures; these tests do not measure wind-farm accuracy."""

import hashlib
import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from wind_agent.contracts import ModelArtifact
from wind_agent.data.hourly import HistorySemantics
from wind_agent.model.estimators import fit_estimators, predict_estimators
from wind_agent.model.interface import predict, prepare_training_pairs, train


CUTOFF = pd.Timestamp("2025-12-31T18:00:00Z")
ISSUE = pd.Timestamp("2026-01-31T18:00:00Z")


def weather(issue=ISSUE, horizon=24, turbines=("turbine_1", "turbine_2")):
    records = []
    for turbine in turbines:
        for lead in range(1, horizon + 1):
            records.append({
                "turbine_id": turbine, "issue_time": issue,
                "valid_time": issue + pd.Timedelta(hours=lead),
                "wind_speed_ms": float(lead % 13), "temperature_c": float(lead % 8 - 5),
                "wind_height_m": 100.0, "model_run_time": issue - pd.Timedelta(hours=6),
                "forecast_available_at": issue - pd.Timedelta(hours=1),
                "availability_basis": "synthetic unit-test provenance",
                "provider": "NOAA GFS via AWS Open Data", "weather_model": "gfs_0p25",
                "raw_sha256": "a" * 64, "lead_hours": lead + 6,
                "provenance_kind": "operational_archive",
            })
    return pd.DataFrame(records)


def training_data():
    features = pd.concat([
        weather(pd.Timestamp("2025-12-27T18:00:00Z"), 48),
        weather(pd.Timestamp("2025-12-28T18:00:00Z"), 48),
    ], ignore_index=True)
    history = features[["turbine_id", "valid_time"]].drop_duplicates().copy()
    history["normalized_power"] = np.array([
        stamp.hour / 24 + (0.1 if turbine == "turbine_2" else 0.0)
        for turbine, stamp in history.itertuples(index=False, name=None)
    ])
    history["available_at"] = history.valid_time
    history["is_complete"] = True
    return history, features


def config(path, algorithm="wind_curve"):
    return {
        "output_dir": path, "algorithm": algorithm, "model_version": "synthetic-contract-v1",
        "history_semantics": HistorySemantics(
            "Etc/GMT-5", "interval_start", 0,
            ("Provisional source-clock assumption for unit tests",),
        ).as_metadata(),
        "source_sha256": {"turbine_1": "b" * 64, "turbine_2": "c" * 64},
        "selection_cutoff": CUTOFF.isoformat(), "calibration_cutoff": CUTOFF.isoformat(),
        "validation_provenance": {"kind": "synthetic contract fixture, no accuracy claim"},
        "hyperparameters": {"max_iter": 5, "min_samples_leaf": 2}
        if algorithm == "hist_gradient_boosting" else {},
    }


@pytest.fixture
def package(tmp_path):
    history, features = training_data()
    return train(history, features, CUTOFF, config(tmp_path / "package"))


@pytest.mark.parametrize("horizon", [24, 48])
@pytest.mark.parametrize("turbines", [("turbine_1",), ("turbine_1", "turbine_2")])
def test_complete_contract_has_issue_relative_leads_and_does_not_mutate(package, horizon, turbines):
    inputs = weather(horizon=horizon, turbines=turbines).sample(frac=1, random_state=8)
    original = inputs.copy(deep=True)
    result = predict(package, inputs, ISSUE, horizon)

    assert len(result) == horizon * len(turbines)
    assert result.groupby("turbine_id").lead_hours.apply(list).tolist() == [
        list(range(1, horizon + 1)) for _ in turbines
    ]
    assert result.prediction.map(np.isfinite).all()
    assert result.target_unit.eq("normalized_power").all()
    assert result.training_cutoff.eq(CUTOFF).all()
    assert result.data_quality.eq("warning").all()
    assert all(result.warnings.map(bool))
    pd.testing.assert_frame_equal(inputs, original)
    pd.testing.assert_frame_equal(result, predict(package, inputs, ISSUE, horizon))


@pytest.mark.parametrize("algorithm", ["wind_curve", "hist_gradient_boosting", "constant_mean"])
def test_all_algorithms_save_load_with_verified_weights(tmp_path, algorithm):
    history, features = training_data()
    history_before, weather_before = history.copy(deep=True), features.copy(deep=True)
    package = train(history, features, CUTOFF, config(tmp_path / algorithm, algorithm))
    result = predict(package, weather(), ISSUE, 24)
    metadata = json.loads((package / "metadata.json").read_text())

    ModelArtifact.model_validate(metadata)
    assert set(metadata) == {"model_version", "training_cutoff", "target_unit", "metadata"}
    assert metadata["metadata"]["algorithm"] == algorithm
    assert metadata["metadata"]["model_sha256"] == hashlib.sha256(
        (package / "model.joblib").read_bytes()
    ).hexdigest()
    assert metadata["metadata"]["weather_feature_time_basis"] == "instant_at_end"
    assert len(result) == 48
    pd.testing.assert_frame_equal(history, history_before)
    pd.testing.assert_frame_equal(features, weather_before)


def test_fresh_python_process_reloads_identical_predictions(package, tmp_path):
    inputs = weather()
    path = tmp_path / "weather.json"
    inputs.to_json(path, orient="records", date_format="iso")
    expected = predict(package, inputs, ISSUE, 24).prediction.tolist()
    script = (
        "import json,sys,pandas as pd; from wind_agent.model.interface import predict; "
        "frame=pd.DataFrame(json.load(open(sys.argv[2]))); "
        "result=predict(sys.argv[1],frame,sys.argv[3],24); "
        "print(json.dumps(result.prediction.tolist()))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(package), str(path), ISSUE.isoformat()],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(completed.stdout) == expected


def test_curve_interpolation_and_constant_endpoints_are_learned():
    frame = pd.DataFrame({
        "turbine_id": ["turbine_1"] * 4,
        "wind_speed_ms": [0.1, 0.9, 2.1, 2.9], "temperature_c": [0.0] * 4,
        "normalized_power": [0.1, 0.3, 0.7, 0.9],
    })
    models = fit_estimators(frame, "wind_curve")
    inputs = pd.DataFrame({
        "turbine_id": ["turbine_1"] * 3,
        "wind_speed_ms": [0.0, 1.5, 20.0], "temperature_c": [999.0] * 3,
    }, index=[4, 4, 1])
    np.testing.assert_allclose(predict_estimators(models, inputs), [0.2, 0.5, 0.8])


def test_training_pairs_filter_incomplete_and_future_targets_without_future_features():
    history, features = training_data()
    history.loc[history.index[0], "is_complete"] = False
    history.loc[history.index[0], "normalized_power"] = np.nan
    history.loc[history.index[1], "available_at"] = CUTOFF + pd.Timedelta(hours=1)
    future_features = weather(CUTOFF, 24)
    pairs = prepare_training_pairs(history, pd.concat([features, future_features]), CUTOFF)

    assert pairs.available_at.le(CUTOFF).all()
    assert pairs.issue_time.lt(CUTOFF).all()
    assert pairs.normalized_power.notna().all()
    assert "observed_wind_speed_ms" not in pairs
    assert len(pairs) < len(features)


@pytest.mark.parametrize("field,value,code", [
    ("forecast_available_at", ISSUE + pd.Timedelta(seconds=1), "WEATHER_NOT_AVAILABLE"),
    ("model_run_time", ISSUE + pd.Timedelta(hours=1), "WEATHER_NOT_AVAILABLE"),
    ("provider", "actual observed weather", "FEATURE_SEMANTICS_MISMATCH"),
    ("weather_model", "reanalysis", "FEATURE_SEMANTICS_MISMATCH"),
    ("wind_height_m", 10.0, "FEATURE_SEMANTICS_MISMATCH"),
    ("wind_speed_ms", np.nan, "NONFINITE_FEATURE"),
    ("temperature_c", np.inf, "NONFINITE_FEATURE"),
    ("turbine_id", "turbine_3", "UNSUPPORTED_TURBINE"),
    ("raw_sha256", "not-a-hash", "INVALID_SCHEMA"),
    ("availability_basis", "", "INVALID_SCHEMA"),
    ("provenance_kind", "hindcast", "FEATURE_SEMANTICS_MISMATCH"),
])
def test_invalid_weather_fails_entire_prediction(package, field, value, code):
    inputs = weather()
    inputs.loc[0, field] = value
    with pytest.raises(ValueError, match=f"^{code}"):
        predict(package, inputs, ISSUE, 24)


def test_missing_duplicate_and_naive_target_times_are_rejected(package):
    inputs = weather()
    with pytest.raises(ValueError, match="^INCOMPLETE_HORIZON"):
        predict(package, inputs.iloc[:-1], ISSUE, 24)
    with pytest.raises(ValueError, match="^DUPLICATE_TARGET"):
        predict(package, pd.concat([inputs, inputs.iloc[[0]]]), ISSUE, 24)
    inputs["valid_time"] = inputs.valid_time.dt.tz_localize(None)
    with pytest.raises(ValueError, match="^NAIVE_TIMESTAMP"):
        predict(package, inputs, ISSUE, 24)


@pytest.mark.parametrize("horizon", [0, 23, 24.0, True, "24"])
def test_invalid_horizon_is_rejected(package, horizon):
    with pytest.raises(ValueError, match="^INVALID_HORIZON"):
        predict(package, weather(), ISSUE, horizon)


def test_training_information_cutoff_is_enforced_at_inference(package):
    with pytest.raises(ValueError, match="^MODEL_TRAINED_AFTER_ISSUE"):
        predict(package, weather(), CUTOFF - pd.Timedelta(hours=1), 24)


@pytest.mark.parametrize("name", ["selection_cutoff", "calibration_cutoff"])
def test_future_declared_selection_or_calibration_is_rejected(tmp_path, name):
    history, features = training_data()
    settings = config(tmp_path / "bad")
    settings[name] = (CUTOFF + pd.Timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="^MODEL_TRAINED_AFTER_ISSUE"):
        train(history, features, CUTOFF, settings)
    assert not (tmp_path / "bad").exists()


def test_declared_reporting_delay_matches_target_availability(tmp_path):
    history, features = training_data()
    settings = config(tmp_path / "bad")
    settings["history_semantics"]["target_reporting_delay_seconds"] = 600
    with pytest.raises(ValueError, match="^INVALID_SCHEMA.*reporting delay"):
        train(history, features, CUTOFF, settings)


def test_model_weights_and_metadata_cannot_be_changed_silently(package):
    weights = package / "model.joblib"
    original = weights.read_bytes()
    weights.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="^INVALID_SCHEMA.*SHA256"):
        predict(package, weather(), ISSUE, 24)
    weights.write_bytes(original)
    path = package / "metadata.json"
    metadata = json.loads(path.read_text())
    metadata["training_cutoff"] = "2025-12-01T00:00:00Z"
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="^INVALID_SCHEMA.*bound"):
        predict(package, weather(), ISSUE, 24)


def test_existing_package_is_not_overwritten(package):
    history, features = training_data()
    original = (package / "model.joblib").read_bytes()
    with pytest.raises(ValueError, match="^INVALID_SCHEMA.*already"):
        train(history, features, CUTOFF, config(package))
    assert (package / "model.joblib").read_bytes() == original


def test_only_declared_forecast_features_affect_predictions(package):
    inputs = weather()
    baseline = predict(package, inputs, ISSUE, 24)
    inputs["observed_wind_speed_ms"] = 1e9
    inputs["observed_temperature_c"] = -1e9
    inputs["normalized_power"] = 0.999
    repeated = predict(package, inputs, ISSUE, 24)
    pd.testing.assert_frame_equal(baseline, repeated)
