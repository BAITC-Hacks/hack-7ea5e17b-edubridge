from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pandas as pd
import pytest

from wind_agent.agent import AgentService
from wind_agent.agent.fixtures import FixtureModel, FixtureWeather
from wind_agent.config import load_settings
from wind_agent.contracts import RunRequest


@pytest.fixture
def settings(tmp_path):
    config = load_settings("config/demo.json")
    config.artifact_dir = tmp_path
    return config


@pytest.fixture
def request_data():
    return RunRequest(issue_time=datetime(2026, 1, 31, 18, tzinfo=timezone.utc),
                      horizon_hours=24, turbine_ids=["turbine_1", "turbine_2"])


class MutableWeather(FixtureWeather):
    revision = 1
    late = False
    missing = False

    def fetch(self, *args):
        records = super().fetch(*args)
        for row in records:
            row.wind_speed_ms += self.revision
            row.raw_sha256 = sha256(str(self.revision).encode()).hexdigest()
            if self.late:
                row.forecast_available_at = args[0] + timedelta(hours=1)
        return records[:-1] if self.missing else records


def test_idempotency_then_revision_and_preserved_history(settings, request_data):
    weather = MutableWeather(settings.turbines)
    service = AgentService(settings, weather=weather)
    first = service.run(request_data)
    assert first.status == "completed", first.error
    assert first.revision == 1
    assert len(service.forecast(first.run_id)) == 48
    same = service.run(request_data)
    assert same.run_id == first.run_id
    assert same.revision == 1  # new retrieved_at does not make a revision
    weather.revision = 2
    changed = service.run(request_data)
    assert changed.run_id == first.run_id
    assert changed.revision == 2
    assert changed.recalculation_reason == "eligible inputs changed"
    revisions = service.root / "runs" / first.run_id / "revisions"
    assert (revisions / "1" / "weather.json").exists()
    assert (revisions / "2" / "weather.json").exists()
    assert {str(e.step.value) for e in first.events} >= {
        "queued", "fetching_weather", "validating", "forecasting", "analysing", "completed"}


@pytest.mark.parametrize("fault", ["late", "missing"])
def test_forbidden_weather_cannot_produce_forecast(settings, request_data, fault):
    weather = MutableWeather(settings.turbines)
    setattr(weather, fault, True)
    service = AgentService(settings, weather=weather)
    record = service.run(request_data)
    assert record.status == "failed"
    assert record.revision == 0
    with pytest.raises(ValueError):
        service.forecast(record.run_id)


def test_late_update_does_not_replace_earlier_revision(settings, request_data):
    weather = MutableWeather(settings.turbines)
    service = AgentService(settings, weather=weather)
    good = service.run(request_data)
    weather.late = True
    bad = service.run(request_data)
    assert bad.status == "failed"
    assert bad.revision == good.revision == 1
    assert (service.root / "runs" / good.run_id / "revisions/1/forecast.json").exists()


def test_model_future_training_rejected(settings, request_data):
    model = FixtureModel()
    model.artifact = model.artifact.model_copy(update={"training_cutoff": request_data.issue_time + timedelta(hours=1)})
    record = AgentService(settings, model=model).run(request_data)
    assert record.status == "failed"
    assert "cutoff" in record.error


@pytest.mark.parametrize("kind", ["missing", "duplicate", "nan", "naive", "bounds"])
def test_invalid_model_output_fails(settings, request_data, kind):
    class BadModel(FixtureModel):
        def predict(self, *args):
            frame = super().predict(*args)
            if kind == "missing":
                return frame.iloc[:-1]
            if kind == "duplicate":
                return pd.concat([frame.iloc[:-1], frame.iloc[:1]])
            if kind == "nan":
                frame.loc[0, "prediction"] = float("nan")
            if kind == "naive":
                frame["valid_time"] = frame["valid_time"].dt.tz_localize(None)
            if kind == "bounds":
                frame.loc[0, "prediction"] = 2.0
            return frame
    model = BadModel()
    model.artifact = model.artifact.model_copy(update={"metadata": {"target_bounds": [0, 1]}})
    record = AgentService(settings, model=model).run(request_data)
    assert record.status == "failed", kind
    assert record.revision == 0


def test_provider_failure_is_visible_and_retryable(settings, request_data):
    class Unavailable:
        def fetch(self, *args):
            raise TimeoutError("provider timeout")
    service = AgentService(settings, weather=Unavailable())
    record = service.run(request_data)
    assert record.status == "failed"
    assert "provider timeout" in record.error
    service.weather = FixtureWeather(settings.turbines)
    assert service.run(request_data).status == "completed"


@pytest.mark.parametrize("horizon", [24, 48])
def test_replay_full_february_and_march_separation(settings, horizon):
    service = AgentService(settings)
    report = service.replay(horizon)
    assert report["status"] == "completed"
    assert report["is_demo"] is True
    assert len(report["runs"]) == 29
    assert report["selected_rows"] == 2 * 672
    assert not report["missing_hours"]
    first = service.get(report["runs"][0]["run_id"])
    assert first.request.issue_time.isoformat() == "2026-01-31T18:00:00+00:00"
    last_forecast = service.forecast(report["runs"][-1]["run_id"])
    if horizon == 48:
        assert max(r.valid_time for r in last_forecast).isoformat() == "2026-03-02T18:00:00+00:00"


def test_restart_marks_interrupted_run_failed(settings, request_data):
    first = AgentService(settings).submit(request_data)
    new = AgentService(settings)
    assert new.get(first.run_id).status == "failed"
    assert new.run(request_data).status == "completed"


def test_coordinate_mismatch_fails(settings, request_data):
    class WrongPlace(FixtureWeather):
        def fetch(self, *args):
            records = super().fetch(*args)
            records[0].latitude += 1
            return records
    record = AgentService(settings, weather=WrongPlace(settings.turbines)).run(request_data)
    assert record.status == "failed"
    assert "coordinates" in record.error


def test_real_mode_requires_confirmed_training_boundary(settings, request_data):
    archive = load_settings("config/archive.json")
    archive.artifact_dir = settings.artifact_dir
    record = AgentService(archive).run(request_data)
    assert record.status == "failed"
    assert "train_cutoff" in record.error


def test_cache_location_and_retrieval_metadata_do_not_trigger_revision(settings, request_data):
    class MovedCache(FixtureWeather):
        calls = 0
        def fetch(self, *args):
            self.calls += 1
            records = super().fetch(*args)
            for row in records:
                row.source_request = f"fixture://extraction-manifest-{self.calls}"
            return records
    service = AgentService(settings, weather=MovedCache(settings.turbines))
    assert service.run(request_data).revision == 1
    assert service.run(request_data).revision == 1


def test_full_model_contract_preserves_warnings_and_rejects_metadata_mismatch(settings, request_data):
    class FullModel(FixtureModel):
        wrong_unit = False
        def predict(self, *args):
            frame = super().predict(*args)
            frame["warnings"] = [["Explicit SCADA timezone assumption"] for _ in range(len(frame))]
            frame["target_unit"] = "wrong" if self.wrong_unit else self.artifact.target_unit
            frame["issue_time"] = request_data.issue_time
            return frame
    model = FullModel()
    service = AgentService(settings, model=model)
    good = service.run(request_data)
    assert good.status == "completed", good.error
    assert "Explicit SCADA timezone assumption" in good.warnings
    assert "Explicit SCADA timezone assumption" in service.forecast(good.run_id)[0].warnings
    model.wrong_unit = True
    model.artifact = model.artifact.model_copy(update={"model_version": "new-fixture-version"})
    bad = service.run(request_data)
    assert bad.status == "failed"
    assert "target_unit" in bad.error


def test_package_directory_adapter_and_metadata_reload(settings, request_data, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from wind_agent.agent.model_bridge import ModelBridge
    package = tmp_path / "model-package"
    package.mkdir()
    (package / "model.joblib").write_bytes(b"test-double-only")
    metadata = FixtureModel.artifact.model_copy(update={"model_version": "fixture-package-v1"})
    (package / "metadata.json").write_text(metadata.model_dump_json(), encoding="utf-8")
    def predict(path, frame, issue, horizon):
        assert path == package
        return FixtureModel().predict(metadata, frame, issue, horizon)
    monkeypatch.setitem(sys.modules, "test_model_adapter", SimpleNamespace(predict=predict))
    bridge = ModelBridge("test_model_adapter", package)
    service = AgentService(settings, model=bridge)
    first = service.run(request_data)
    assert first.status == "completed", first.error
    assert first.revision == 1
    metadata.model_version = "fixture-package-v2"
    (package / "metadata.json").write_text(metadata.model_dump_json(), encoding="utf-8")
    changed = service.run(request_data)
    assert changed.status == "completed", changed.error
    assert changed.revision == 2
    assert service.forecast(first.run_id)[0].model_version == "fixture-package-v2"


def test_replay_end_labels_include_last_february_interval(settings):
    import json
    service = AgentService(settings)
    report = service.replay(24)
    rows = json.loads((service.root / "replay/2026-02-24h/february.json").read_text())
    labels = sorted({r["valid_time"] for r in rows})
    assert len(labels) == 672
    assert labels[0] == "2026-01-31T20:00:00Z"  # Feb 1 01:00 local = end of first Feb hour
    assert labels[-1] == "2026-02-28T19:00:00Z"  # Mar 1 00:00 local = end of last Feb hour
    assert report["timestamp_convention"] == "hour_end"


@pytest.mark.parametrize("metadata", [
    {"wind_height_m": 10},
    {"weather_feature_time_basis": "interval_mean"},
    {"timestamp_convention": "hour_start"},
    {"selection_cutoff": "2026-02-01T00:00:00Z"},
    {"calibration_cutoff": "2025-12-31T00:00:00"},
])
def test_model_feature_and_fitting_semantics_cannot_be_silently_changed(settings, request_data, metadata):
    model = FixtureModel()
    model.artifact = model.artifact.model_copy(update={"metadata": metadata})
    record = AgentService(settings, model=model).run(request_data)
    assert record.status == "failed"
    assert record.revision == 0


def test_configured_training_boundary_cannot_extend_into_february(settings, request_data):
    archive = load_settings("config/archive.json")
    archive.artifact_dir = settings.artifact_dir
    archive.history_timezone = "Asia/Almaty"
    archive.train_cutoff = datetime(2026, 2, 5, tzinfo=timezone.utc)
    record = AgentService(archive).run(request_data)
    assert record.status == "failed"
    assert "February" in record.error
