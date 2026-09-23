"""Synthetic test-only archive-shaped inputs; no network or accuracy claim.

Operational provenance labels below deliberately exercise the real archive code
path. These in-memory fixtures are never written as real weather evidence.
"""

from datetime import datetime, timedelta, timezone
import hashlib

import pandas as pd
import pytest

from wind_agent.agent import AgentService
from wind_agent.agent.model_bridge import ModelBridge
from wind_agent.config import load_settings
from wind_agent.contracts import RunRequest, WeatherRecord
from wind_agent.data.hourly import HistorySemantics
from wind_agent.model.interface import predict, train


UTC = timezone.utc
CUTOFF = datetime(2025, 12, 31, 18, tzinfo=UTC)
ISSUE = datetime(2026, 1, 31, 18, tzinfo=UTC)
TEST_WARNING = "UNIT TEST ONLY: synthetic training targets and archive-shaped weather"


class SyntheticArchiveWeather:
    """Unit-test fixture injected directly; never selected by production settings."""

    def __init__(self, turbines):
        self.turbines = {item.turbine_id: item for item in turbines}

    def fetch(self, issue, horizon, turbine_ids):
        rows = []
        for turbine_id in turbine_ids:
            turbine = self.turbines[turbine_id]
            for lead in range(1, horizon + 1):
                rows.append(WeatherRecord(
                    turbine_id=turbine_id, latitude=turbine.latitude, longitude=turbine.longitude,
                    provider="NOAA GFS via AWS Open Data", weather_model="gfs_0p25",
                    model_run_time=issue - timedelta(hours=6),
                    forecast_available_at=issue - timedelta(hours=1),
                    availability_basis=TEST_WARNING,
                    retrieved_at=datetime(2026, 9, 23, tzinfo=UTC),
                    valid_time=issue + timedelta(hours=lead), lead_hours=lead + 6,
                    wind_speed_ms=float(lead % 12), wind_height_m=100.0,
                    temperature_c=float(lead % 7 - 4),
                    source_request="unit-test://synthetic-archive-weather",
                    raw_sha256=hashlib.sha256(TEST_WARNING.encode()).hexdigest(),
                    provenance_kind="operational_archive", availability_evidence=TEST_WARNING,
                ))
        return rows


@pytest.fixture
def archive_settings_and_weather(tmp_path):
    settings = load_settings("config/archive.json")
    provider = SyntheticArchiveWeather(settings.turbines)
    training_issue = CUTOFF - timedelta(hours=48)
    records = provider.fetch(training_issue, 48, ["turbine_1", "turbine_2"])
    weather = pd.DataFrame([row.model_dump() for row in records])
    weather["issue_time"] = training_issue
    history = weather[["turbine_id", "valid_time"]].copy()
    history["normalized_power"] = weather.wind_speed_ms / 20
    history["available_at"] = history.valid_time
    history["is_complete"] = True
    semantics = HistorySemantics("Etc/GMT-5", "interval_start", 0, (TEST_WARNING,))
    package = train(history, weather, CUTOFF, {
        "output_dir": tmp_path / "synthetic-test-package", "algorithm": "wind_curve",
        "model_version": "synthetic-test-only-v1", "history_semantics": semantics.as_metadata(),
        "source_sha256": {"turbine_1": "b" * 64, "turbine_2": "c" * 64},
        "selection_cutoff": CUTOFF.isoformat(), "calibration_cutoff": CUTOFF.isoformat(),
        "validation_provenance": {"kind": TEST_WARNING},
    })
    settings.artifact_dir = tmp_path / "test-service-runs"
    settings.history_timezone = "Etc/GMT-5"
    settings.train_cutoff = CUTOFF
    settings.model_artifact_path = package
    settings.model_adapter_module = "wind_agent.model.interface"
    return settings, provider


@pytest.mark.parametrize("horizon", [24, 48])
def test_archive_agent_uses_actual_model_bridge_and_preserves_full_model_contract(
    archive_settings_and_weather, horizon,
):
    settings, provider = archive_settings_and_weather
    service = AgentService(settings, weather=provider)
    request = RunRequest(
        issue_time=ISSUE, horizon_hours=horizon, turbine_ids=["turbine_1", "turbine_2"],
    )

    record = service.run(request)

    assert record.status == "completed", record.error
    assert isinstance(service.model, ModelBridge)
    assert record.revision == 1
    assert TEST_WARNING in record.warnings
    actual = service.forecast(record.run_id)
    assert len(actual) == 2 * horizon
    weather = pd.DataFrame([
        item.model_dump() for item in provider.fetch(ISSUE, horizon, request.turbine_ids)
    ])
    direct = predict(settings.model_artifact_path, weather, ISSUE, horizon)
    for row, expected in zip(actual, direct.to_dict(orient="records"), strict=True):
        assert row.turbine_id == expected["turbine_id"]
        assert row.valid_time == expected["valid_time"]
        assert row.prediction == expected["prediction"]
        assert row.lead_hours == expected["lead_hours"]
        assert row.weather_run_time == ISSUE - timedelta(hours=6)
        assert row.training_cutoff == CUTOFF
        assert row.target_unit == "normalized_power"
        assert row.model_version == "synthetic-test-only-v1"
        assert row.data_quality == "warning"
        assert set(expected["warnings"]).issubset(row.warnings)
        assert "Uncertainty intervals are not calibrated" in row.warnings
    assert actual[0].lead_hours == 1
    assert service.run(request).revision == 1
