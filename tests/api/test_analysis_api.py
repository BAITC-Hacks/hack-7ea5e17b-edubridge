"""Diagnostics must remain associated with the immutable forecast revision."""
from datetime import datetime, timezone
import json
from hashlib import sha256

from fastapi.testclient import TestClient

from wind_agent.agent import AgentService
from wind_agent.api import create_app
from wind_agent.config import load_settings
from wind_agent.contracts import RunRequest


def test_analysis_of_new_and_preexisting_revision_does_not_change_forecast(tmp_path):
    settings = load_settings("config/demo.json")
    settings.artifact_dir = tmp_path
    service = AgentService(settings)
    request = RunRequest(issue_time=datetime(2026, 1, 31, 18, tzinfo=timezone.utc),
                         horizon_hours=24, turbine_ids=["turbine_1"])
    record = service.run(request)
    assert record.status == "completed", record.error
    directory = service.root / "runs" / record.run_id / "revisions" / "1"
    files = [directory / name for name in ("forecast.json", "weather.json", "inputs.json")]
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in files}
    with TestClient(create_app(service=service)) as client:
        response = client.get(f"/runs/{record.run_id}/analysis")
        assert response.status_code == 200
        report = response.json()
        assert report["run_id"] == record.run_id and report["revision"] == 1
        assert report["is_demo"] is True
        assert report["decision"] == "review_required"
        assert report["next_action"] == "review_inputs"
        assert report["limitations"]  # Diagnostics must not be presented as accuracy.
        event = next(event for event in service.get(record.run_id).events if "analysis" in event.details)
        assert event.details["analysis"] == report
        # Upgrade old diagnostic policy in memory without rewriting saved evidence.
        legacy = {key: value for key, value in report.items()
                  if key not in {"methodology_warnings", "review_warnings", "decision_basis"}}
        legacy_bytes = json.dumps(legacy).encode()
        (directory / "analysis.json").write_bytes(legacy_bytes)
        upgraded = client.get(f"/runs/{record.run_id}/analysis")
        assert upgraded.status_code == 200 and upgraded.json() == report
        assert (directory / "analysis.json").read_bytes() == legacy_bytes
        # Simulate a revision saved by the pre-diagnostics release.
        (directory / "analysis.json").unlink()
        recovered = client.get(f"/runs/{record.run_id}/analysis")
        assert recovered.status_code == 200
        assert recovered.json() == report
        assert not (directory / "analysis.json").exists()  # Read-only backwards compatibility.
    assert {path: sha256(path.read_bytes()).hexdigest() for path in files} == hashes
