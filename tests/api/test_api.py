import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wind_agent.agent import AgentService
from wind_agent.api import create_app
from wind_agent.config import load_settings
from wind_agent.contracts import RunRequest


@pytest.fixture
def service(tmp_path):
    settings = load_settings(Path(__file__).parents[2] / "config" / "demo.json")
    settings.artifact_dir = tmp_path / "runs"
    settings.cache_dir = tmp_path / "cache"
    return AgentService(settings)


@pytest.fixture
def request_body():
    return {
        "issue_time": "2026-01-31T18:00:00Z",
        "horizon_hours": 24,
        "turbine_ids": ["turbine_1", "turbine_2"],
    }


def test_http_run_exposes_complete_forecast_and_equivalent_csv(service, request_body):
    with TestClient(create_app(service=service)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["mode"] == "demo"
        submitted = client.post("/runs", json=request_body)
        assert submitted.status_code == 202
        assert submitted.json()["status"] == "queued"
        run_id = submitted.json()["run_id"]
        status = client.get(f"/runs/{run_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "completed"
        assert status.json()["warnings"]  # Synthetic inputs must stay visible to the UI.
        result = client.get(f"/runs/{run_id}/forecast")
        assert result.status_code == 200
        forecasts = result.json()
        assert len(forecasts) == 48
        assert {row["lead_hours"] for row in forecasts} == set(range(1, 25))
        assert {row["turbine_id"] for row in forecasts} == {"turbine_1", "turbine_2"}
        exported = client.get(f"/runs/{run_id}/forecast.csv")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("text/csv")
        rows = list(csv.DictReader(io.StringIO(exported.text)))
        assert len(rows) == len(forecasts)
        for csv_row, record in zip(rows, forecasts, strict=True):
            assert set(csv_row) == set(record)
            for key, value in record.items():
                if isinstance(value, (dict, list)):
                    assert json.loads(csv_row[key]) == value
                elif value is None:
                    assert csv_row[key] == ""
                else:
                    assert csv_row[key] == str(value)


@pytest.mark.parametrize("suffix", ["", "/forecast", "/forecast.csv"])
def test_unknown_run_is_404(service, suffix):
    with TestClient(create_app(service=service)) as client:
        assert client.get(f"/runs/missing{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", ["/forecast", "/forecast.csv"])
def test_forecast_before_completion_is_409(service, request_body, suffix):
    record = service.submit(RunRequest.model_validate(request_body))
    with TestClient(create_app(service=service)) as client:
        response = client.get(f"/runs/{record.run_id}{suffix}")
        assert response.status_code == 409
        assert response.json()["detail"]


@pytest.mark.parametrize("change", [
    {"issue_time": "2026-01-31T18:00:00"},
    {"issue_time": "2026-01-31T18:30:00Z"},
    {"horizon_hours": 12},
    {"turbine_ids": []},
    {"turbine_ids": ["turbine_1", "turbine_1"]},
    {"turbine_ids": ["unknown_turbine"]},
])
def test_invalid_requests_are_rejected(service, request_body, change):
    with TestClient(create_app(service=service)) as client:
        response = client.post("/runs", json=request_body | change)
        assert response.status_code == 422


def test_evaluation_does_not_invent_demo_metrics(service):
    with TestClient(create_app(service=service)) as client:
        response = client.get("/evaluation")
        assert response.status_code == 200
        report = response.json()
        assert report
        assert "mae" not in report and "rmse" not in report


def test_archive_without_model_reports_failure_and_refuses_export(tmp_path, request_body):
    settings = load_settings(Path(__file__).parents[2] / "config" / "archive.json")
    settings.train_cutoff = datetime(2026, 1, 30, tzinfo=timezone.utc)
    settings.history_timezone = "Asia/Almaty"
    settings.artifact_dir = tmp_path / "runs"
    settings.cache_dir = tmp_path / "cache"
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/health").json()["model_ready"] is False
        submitted = client.post("/runs", json=request_body)
        assert submitted.status_code == 202
        run_id = submitted.json()["run_id"]
        record = client.get(f"/runs/{run_id}").json()
        assert record["status"] == "failed"
        assert "Model unavailable" in record["error"]
        assert client.get(f"/runs/{run_id}/forecast").status_code == 409
