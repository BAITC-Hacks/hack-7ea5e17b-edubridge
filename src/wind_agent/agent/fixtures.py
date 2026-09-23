"""Explicit synthetic test doubles; never a source of production forecasts."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pandas as pd

from wind_agent.contracts import ModelArtifact, WeatherRecord


class FixtureWeather:
    def __init__(self, turbines):
        self.turbines = {t.turbine_id: t for t in turbines}

    def fetch(self, issue_time, horizon_hours, turbine_ids):
        run_time = issue_time - timedelta(hours=12)
        return [
            WeatherRecord(
                turbine_id=tid, latitude=self.turbines[tid].latitude,
                longitude=self.turbines[tid].longitude, provider="synthetic_fixture",
                weather_model="fixture", model_run_time=run_time,
                forecast_available_at=run_time + timedelta(hours=6),
                availability_basis="Synthetic fixture, not historical evidence",
                retrieved_at=datetime.now(timezone.utc),
                valid_time=issue_time + timedelta(hours=lead), lead_hours=12 + lead,
                wind_speed_ms=7.0, wind_height_m=100.0, temperature_c=-3.0,
                source_request="fixture://constant-weather-v1",
                raw_sha256=sha256(b"synthetic-weather-v1").hexdigest(),
                provenance_kind="fixture",
            )
            for tid in turbine_ids for lead in range(1, horizon_hours + 1)
        ]


class FixtureModel:
    artifact = ModelArtifact(
        model_version="synthetic-fixture-v1",
        training_cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        target_unit="fixture_dimensionless",
        metadata={"is_fixture": True, "description": "No training performed; API test double"},
    )

    def predict(self, artifact, weather_frame, issue_time, horizon_hours):
        return pd.DataFrame({
            "turbine_id": weather_frame["turbine_id"],
            "valid_time": weather_frame["valid_time"],
            "prediction": [0.5] * len(weather_frame),
        })
