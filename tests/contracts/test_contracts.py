from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from wind_agent.config import Settings, load_settings
from wind_agent.contracts import ForecastRecord, ModelArtifact, RunRequest, WeatherRecord


ISSUE = datetime(2026, 1, 31, 18, tzinfo=timezone.utc)


def weather_values():
    return {
        "turbine_id": "turbine_1",
        "latitude": 43.64515,
        "longitude": 78.535604,
        "provider": "test_fixture",
        "weather_model": "test_fixture",
        "model_run_time": ISSUE - timedelta(hours=6),
        "forecast_available_at": ISSUE - timedelta(hours=2),
        "availability_basis": "fixture timestamps, not publication evidence",
        "retrieved_at": ISSUE,
        "valid_time": ISSUE + timedelta(hours=1),
        "lead_hours": 7,
        "wind_speed_ms": 8.0,
        "wind_height_m": 100.0,
        "temperature_c": -1.0,
        "source_request": "fixture://contracts",
        "raw_sha256": "a" * 64,
        "provenance_kind": "fixture",
    }


def forecast_values():
    return {
        "run_id": "test-run",
        "revision": 1,
        "issue_time": ISSUE,
        "turbine_id": "turbine_1",
        "valid_time": ISSUE + timedelta(hours=1),
        "lead_hours": 1,
        "prediction": 0.4,
        "target_unit": "synthetic_normalized",
        "weather_model": "test_fixture",
        "weather_run_time": ISSUE - timedelta(hours=6),
        "model_version": "fixture-v1",
        "training_cutoff": ISSUE - timedelta(days=1),
    }


def test_offset_times_are_normalized_and_json_round_trips():
    request = RunRequest.model_validate(
        {"issue_time": "2026-01-31T23:00:00+05:00", "turbine_ids": ["turbine_1"]}
    )
    assert request.issue_time == ISSUE
    assert request.issue_time.tzinfo is timezone.utc
    assert RunRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("naive", ["2026-01-31T18:00:00", datetime(2026, 1, 31, 18)])
def test_naive_issue_is_rejected(naive):
    with pytest.raises(ValidationError, match="timezone"):
        RunRequest(issue_time=naive, turbine_ids=["turbine_1"])


def test_numeric_timestamp_does_not_silently_assume_utc():
    with pytest.raises(ValidationError, match="timezone"):
        RunRequest(issue_time=1769882400, turbine_ids=["turbine_1"])


@pytest.mark.parametrize("field", ["model_run_time", "forecast_available_at", "retrieved_at", "valid_time"])
def test_naive_weather_times_are_rejected(field):
    values = weather_values()
    values[field] = values[field].replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone"):
        WeatherRecord(**values)


def test_weather_and_prediction_use_different_lead_origins():
    weather = WeatherRecord(**weather_values())
    prediction = ForecastRecord(**forecast_values())
    assert weather.valid_time == prediction.valid_time
    assert weather.lead_hours == 7
    assert prediction.lead_hours == 1
    with pytest.raises(ValidationError, match="weather lead_hours"):
        WeatherRecord(**(weather_values() | {"lead_hours": 1}))
    with pytest.raises(ValidationError, match="forecast lead_hours"):
        ForecastRecord(**(forecast_values() | {"lead_hours": 7}))


@pytest.mark.parametrize("field", ["training_cutoff", "weather_run_time"])
def test_forecast_rejects_future_dependencies(field):
    with pytest.raises(ValidationError, match=field):
        ForecastRecord(**(forecast_values() | {field: ISSUE + timedelta(hours=1)}))


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_values_are_rejected(number):
    with pytest.raises(ValidationError):
        WeatherRecord(**(weather_values() | {"wind_speed_ms": number}))
    with pytest.raises(ValidationError):
        ForecastRecord(**(forecast_values() | {"prediction": number}))
    with pytest.raises(ValidationError):
        ModelArtifact(
            model_version="test", training_cutoff=ISSUE,
            target_unit="unknown", metadata={"nested": {"score": number}},
        )


def test_unknown_fields_and_numeric_strings_are_rejected():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        WeatherRecord(**(weather_values() | {"actual_wind": 8.0}))
    with pytest.raises(ValidationError):
        WeatherRecord(**(weather_values() | {"wind_speed_ms": "8.0"}))
    with pytest.raises(ValidationError):
        RunRequest(issue_time=ISSUE, horizon_hours="24", turbine_ids=["turbine_1"])
    with pytest.raises(ValidationError):
        RunRequest(issue_time=ISSUE, horizon_hours=24.0, turbine_ids=["turbine_1"])


def test_request_rejects_duplicate_targets_and_nonhourly_issue():
    with pytest.raises(ValidationError, match="unique"):
        RunRequest(issue_time=ISSUE, turbine_ids=["turbine_1", "turbine_1"])
    with pytest.raises(ValidationError, match="exact hour"):
        RunRequest(issue_time=ISSUE + timedelta(minutes=30), turbine_ids=["turbine_1"])


def test_fixture_cannot_be_configured_as_archive():
    with pytest.raises(ValidationError, match="fixture weather"):
        Settings(mode="archive", weather_provider="fixture")
    with pytest.raises(ValidationError, match="synthetic coordinates"):
        Settings(turbines=[{
            "turbine_id": "demo", "latitude": 1.0, "longitude": 2.0, "is_synthetic": True,
        }])


def test_shipped_configuration_keeps_demo_explicit_and_archive_model_unset():
    root = Path(__file__).resolve().parents[2]
    demo = load_settings(root / "config" / "demo.json")
    archive = load_settings(root / "config" / "archive.json")
    assert demo.mode == "demo"
    assert demo.weather_provider == "fixture"
    assert archive.mode == "archive"
    assert archive.model_adapter_module is None
    assert archive.train_cutoff is None
    assert archive.replay_issue_hour == 23
    assert archive.timestamp_convention == "hour_end"
    assert archive.history_timezone is None
    assert len(archive.turbines) == 2


def test_history_timezone_is_explicit_and_validated_as_iana_name():
    assert Settings().history_timezone is None
    assert Settings(history_timezone="Asia/Almaty").history_timezone == "Asia/Almaty"
    assert Settings(history_timezone="UTC").history_timezone == "UTC"
    with pytest.raises(ValidationError, match="IANA timezone"):
        Settings(history_timezone="not/a/timezone")
    with pytest.raises(ValidationError, match="IANA timezone"):
        Settings(history_timezone="+05:00")


def test_obsolete_hour_start_configuration_is_not_silently_reinterpreted():
    with pytest.raises(ValidationError, match="hour_end"):
        Settings(timestamp_convention="hour_start")


def test_model_metadata_allows_package_bridge_to_infer_weights_path():
    artifact = ModelArtifact(
        model_version="package-v1", training_cutoff=ISSUE,
        target_unit="normalized_power", metadata={"timestamp_convention": "hour_end"},
    )
    assert artifact.artifact_path is None
