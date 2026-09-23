"""Observable fetch → validate → predict → analyse → revise orchestration."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from importlib.util import find_spec
from pathlib import Path
import re
from threading import Lock, RLock
from zoneinfo import ZoneInfo

import pandas as pd

from wind_agent.contracts import AgentEvent, ForecastRecord, RunRecord, RunRequest, WeatherRecord
from .analysis import analyse_forecast
from .fixtures import FixtureModel, FixtureWeather
from .model_bridge import ModelBridge
from .storage import read_json, write_json


def utcnow():
    return datetime.now(timezone.utc)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


class AgentService:
    def __init__(self, settings, weather=None, model=None):
        self.settings = settings
        self.root = Path(settings.artifact_dir) / settings.mode
        self.root.mkdir(parents=True, exist_ok=True)
        self.weather = weather
        self.model = model
        self._guard = RLock()
        self._locks = {}
        # The MVP has one owner process. Interrupted jobs are visible and may be resubmitted.
        for path in self.root.glob("runs/*/run.json"):
            record = RunRecord.model_validate(read_json(path))
            if record.status not in ("completed", "failed"):
                record.status = "failed"
                record.error = "Service restarted before the job completed; resubmit to retry"
                record.updated_at = utcnow()
                write_json(path, record.model_dump(mode="json"))

    def _path(self, run_id):
        if not re.fullmatch(r"[0-9a-f]{24}", run_id):
            raise KeyError(run_id)
        return self.root / "runs" / run_id

    def get(self, run_id):
        path = self._path(run_id) / "run.json"
        if not path.exists():
            raise KeyError(run_id)
        return RunRecord.model_validate(read_json(path))

    def _save(self, record):
        write_json(self._path(record.run_id) / "run.json", record.model_dump(mode="json"))

    def _event(self, record, status, message, **details):
        record.status = status
        record.updated_at = utcnow()
        record.events.append(AgentEvent(at=record.updated_at, step=status,
                                       message=message, details=details))
        self._save(record)

    def submit(self, request):
        request = RunRequest.model_validate(request)
        configured = {t.turbine_id for t in self.settings.turbines}
        if not set(request.turbine_ids) <= configured:
            raise ValueError("Unknown turbine_id; use configured turbine identifiers")
        if request.issue_time.minute or request.issue_time.second or request.issue_time.microsecond:
            raise ValueError("issue_time must align to an hour")
        canonical = request.model_dump(mode="json")
        canonical["turbine_ids"] = sorted(canonical["turbine_ids"])
        run_id = digest(canonical)[:24]
        with self._guard:
            try:
                record = self.get(run_id)
                if record.status not in ("completed", "failed"):
                    return record
                record.error = None
            except KeyError:
                record = RunRecord(run_id=run_id, request=request, status="queued", revision=0,
                                   created_at=utcnow(), updated_at=utcnow())
            self._locks.setdefault(run_id, Lock())
            self._event(record, "queued", "Run accepted; inputs will be checked for changes")
        return record

    def _providers(self):
        if self.settings.mode == "demo":
            self.weather = self.weather or FixtureWeather(self.settings.turbines)
            self.model = self.model or FixtureModel()
        else:
            if self.weather is None:
                if self.settings.weather_provider != "gfs_s3":
                    raise ValueError("No supported operational archive provider configured")
                from wind_agent.weather import GFSArchiveProvider
                self.weather = GFSArchiveProvider(
                    turbines={t.turbine_id: (t.latitude, t.longitude) for t in self.settings.turbines},
                    cache_dir=self.settings.cache_dir,
                )
            if self.model is None:
                self.model = ModelBridge(self.settings.model_adapter_module,
                                         self.settings.model_artifact_path)
        return self.weather, self.model

    def _validate_weather(self, records, request):
        weather = [WeatherRecord.model_validate(row) for row in records]
        expected = {(tid, request.issue_time + timedelta(hours=h))
                    for tid in request.turbine_ids for h in range(1, request.horizon_hours + 1)}
        actual = [(r.turbine_id, r.valid_time) for r in weather]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("Weather must contain each requested turbine/hour exactly once")
        runs = {}
        for row in weather:
            location = self.settings.turbine(row.turbine_id)
            if (abs(row.latitude - location.latitude) > 1e-7 or
                    abs(row.longitude - location.longitude) > 1e-7):
                raise ValueError("Weather coordinates do not match the configured turbine")
            if row.forecast_available_at > request.issue_time or row.model_run_time > request.issue_time:
                raise ValueError("Weather leakage: forecast was unavailable at issue_time")
            if self.settings.mode != "demo" and row.provenance_kind != "operational_archive":
                raise ValueError("Production requires operational archived forecasts")
            runs.setdefault(row.turbine_id, set()).add((row.provider, row.weather_model, row.model_run_time))
        if any(len(items) != 1 for items in runs.values()):
            raise ValueError("A turbine horizon cannot stitch different weather runs")
        return sorted(weather, key=lambda r: (r.turbine_id, r.valid_time))

    def execute(self, run_id):
        with self._guard:
            lock = self._locks.setdefault(run_id, Lock())
        with lock:
            record = self.get(run_id)
            if record.status != "queued":
                return record
            try:
                if self.settings.mode == "archive" and self.settings.train_cutoff is None:
                    raise ValueError("Set train_cutoff after confirming the source dataset timezone and January boundary")
                if self.settings.mode == "archive":
                    if self.settings.history_timezone is None:
                        raise ValueError("Set history_timezone after confirming the source dataset timezone")
                    january_end = datetime(2026, 2, 1, tzinfo=ZoneInfo(self.settings.history_timezone))
                    if self.settings.train_cutoff > january_end:
                        raise ValueError("Training boundary cannot include February observations")
                weather_provider, model = self._providers()
                if isinstance(model, ModelBridge):
                    model.reload()
                artifact = model.artifact
                request = record.request
                if artifact.training_cutoff > request.issue_time:
                    raise ValueError("Model training cutoff is after issue_time")
                if self.settings.train_cutoff and artifact.training_cutoff > self.settings.train_cutoff:
                    raise ValueError("Model training cutoff exceeds configured training boundary")
                if self.settings.mode != "demo" and artifact.metadata.get("is_fixture"):
                    raise ValueError("Fixture model forbidden in archive mode")
                for name in ("selection_cutoff", "calibration_cutoff"):
                    if name in artifact.metadata:
                        cutoff = datetime.fromisoformat(str(artifact.metadata[name]).replace("Z", "+00:00"))
                        if cutoff.tzinfo is None or cutoff > artifact.training_cutoff:
                            raise ValueError(f"Model {name} must be aware and no later than training_cutoff")
                self._event(record, "fetching_weather", "Selecting an archived run available at issue_time")
                raw_weather = weather_provider.fetch(request.issue_time, request.horizon_hours,
                                                     request.turbine_ids)
                self._event(record, "validating", "Checking availability, provenance and hourly coverage")
                weather = self._validate_weather(raw_weather, request)
                semantics = {"timestamp_convention": self.settings.timestamp_convention,
                             "weather_feature_time_basis": "instant_at_end"}
                if self.settings.history_timezone:
                    semantics["history_timezone"] = self.settings.history_timezone
                for item in weather:
                    expected_semantics = {**semantics, "weather_provider": item.provider,
                                          "weather_model": item.weather_model,
                                          "wind_height_m": item.wind_height_m}
                    for key, value in expected_semantics.items():
                        if key in artifact.metadata and artifact.metadata[key] != value:
                            raise ValueError(f"Model feature semantics mismatch: {key}")
                provenance = {}
                for item in weather:
                    provenance[item.turbine_id] = {
                        "provider": item.provider, "weather_model": item.weather_model,
                        "model_run_time": item.model_run_time.isoformat(),
                        "forecast_available_at": item.forecast_available_at.isoformat(),
                        "availability_basis": item.availability_basis,
                        "availability_evidence": item.availability_evidence,
                        "wind_height_m": item.wind_height_m,
                        "weather_feature_time_basis": "instant_at_end",
                        "raw_sha256_example": item.raw_sha256,
                    }
                self._event(record, "validating", "Weather eligibility verified",
                            weather_provenance=provenance, rows=len(weather))
                weather_json = [w.model_dump(mode="json") for w in weather]
                stable_weather = [{k: v for k, v in row.items()
                                   if k not in ("retrieved_at", "source_request")}
                                  for row in weather_json]
                artifact_json = artifact.model_dump(mode="json")
                artifact_hash = None
                if artifact.artifact_path:
                    artifact_hash = sha256(Path(artifact.artifact_path).read_bytes()).hexdigest()
                fingerprint = digest({"weather": stable_weather, "model": artifact_json,
                                      "model_file_sha256": artifact_hash, "semantics": semantics})
                if record.input_fingerprint == fingerprint and record.revision:
                    analysis = self._revision_analysis(record)
                    self._event(record, "completed", "Inputs unchanged; retained existing revision",
                                revision=record.revision, analysis=analysis)
                    return record
                revision = record.revision + 1
                reason = "initial forecast" if record.revision == 0 else "eligible inputs changed"
                self._event(record, "forecasting", "Calling the configured model adapter", reason=reason)
                frame = pd.DataFrame([r.model_dump() for r in weather])
                result = model.predict(artifact, frame, request.issue_time, request.horizon_hours)
                if not isinstance(result, pd.DataFrame):
                    raise ValueError("Model predict must return a pandas DataFrame")
                required = {"turbine_id", "valid_time", "prediction"}
                if not required <= set(result.columns):
                    raise ValueError("Model output requires turbine_id, valid_time, prediction")
                warnings = (["SYNTHETIC FIXTURE: not a real forecast or validation result"]
                            if self.settings.mode == "demo" else [])
                warnings.append("Uncertainty intervals are not calibrated")
                lookup = {(w.turbine_id, w.valid_time): w for w in weather}
                forecasts = []
                for row in result.to_dict(orient="records"):
                    valid = pd.Timestamp(row["valid_time"])
                    if valid.tzinfo is None:
                        raise ValueError("Model valid_time must be timezone-aware")
                    valid = valid.to_pydatetime().astimezone(timezone.utc)
                    source = lookup.get((row["turbine_id"], valid))
                    if source is None:
                        raise ValueError("Model returned an unrequested turbine or hour")
                    expected_row = dict(
                        schema_version="1.0", run_id=run_id, revision=revision,
                        issue_time=request.issue_time, turbine_id=row["turbine_id"], valid_time=valid,
                        lead_hours=int((valid - request.issue_time).total_seconds() / 3600),
                        prediction=row["prediction"], target_unit=artifact.target_unit,
                        weather_model=source.weather_model, weather_run_time=source.model_run_time,
                        model_version=artifact.model_version, training_cutoff=artifact.training_cutoff,
                        data_quality="fixture" if self.settings.mode == "demo" else "validated",
                        warnings=warnings,
                    )
                    # Full model output from participant #2 is accepted, but metadata cannot
                    # silently disagree with the selected weather or the audited artifact.
                    candidate = {**expected_row, **{k: v for k, v in row.items()
                                                   if k in ForecastRecord.model_fields}}
                    candidate["run_id"], candidate["revision"] = run_id, revision
                    candidate["warnings"] = list(dict.fromkeys(warnings + row.get("warnings", [])))
                    if self.settings.mode == "demo":
                        candidate["data_quality"] = "fixture"
                    forecast = ForecastRecord.model_validate(candidate)
                    expected_record = ForecastRecord.model_validate(expected_row)
                    for field in ForecastRecord.model_fields:
                        if field not in {"prediction", "warnings", "data_quality"}:
                            if getattr(forecast, field) != getattr(expected_record, field):
                                raise ValueError(f"Model output {field} disagrees with audited inputs")
                    forecasts.append(forecast)
                self._event(record, "analysing", "Checking model output and preserving revision provenance")
                keys = [(r.turbine_id, r.valid_time) for r in forecasts]
                if len(keys) != len(set(keys)) or set(keys) != set(lookup):
                    raise ValueError("Model output has duplicate or missing hourly predictions")
                bounds = artifact.metadata.get("target_bounds")
                if bounds is not None:
                    lower, upper = bounds
                    if any(not lower <= f.prediction <= upper for f in forecasts):
                        raise ValueError("Prediction violates confirmed target_bounds in model metadata")
                forecasts.sort(key=lambda r: (r.turbine_id, r.valid_time))
                previous = None
                if record.revision:
                    previous = [ForecastRecord.model_validate(row) for row in read_json(
                        self._path(run_id) / "revisions" / str(record.revision) / "forecast.json"
                    )]
                analysis = analyse_forecast(forecasts, weather, previous)
                analysis.update(run_id=run_id, revision=revision,
                                issue_time=request.issue_time.isoformat())
                self._event(record, "analysing", "Forecast diagnostics and next action recorded",
                            analysis=analysis)
                directory = self._path(run_id) / "revisions" / str(revision)
                write_json(directory / "analysis.json", analysis)
                write_json(directory / "weather.json", weather_json)
                write_json(directory / "forecast.json", [f.model_dump(mode="json") for f in forecasts])
                write_json(directory / "inputs.json", {
                    "fingerprint": fingerprint, "reason": reason, "model": artifact_json,
                    "model_file_sha256": artifact_hash, "request": request.model_dump(mode="json"),
                    "is_demo": self.settings.mode == "demo", "semantics": semantics,
                })
                record.revision = revision
                record.input_fingerprint = fingerprint
                record.recalculation_reason = reason
                record.warnings = list(dict.fromkeys(w for f in forecasts for w in f.warnings))
                self._event(record, "completed", "Forecast saved", rows=len(forecasts), revision=revision)
            except Exception as exc:
                record.error = f"{type(exc).__name__}: {exc}"
                self._event(record, "failed", "Run failed; no replacement forecast produced", error=record.error)
            return record

    def run(self, request):
        return self.execute(self.submit(request).run_id)

    def forecast(self, run_id):
        record = self.get(run_id)
        if record.status != "completed":
            raise ValueError("Forecast is available only for completed runs")
        path = self._path(run_id) / "revisions" / str(record.revision) / "forecast.json"
        return [ForecastRecord.model_validate(row) for row in read_json(path)]

    def analysis(self, run_id):
        record = self.get(run_id)
        if record.status != "completed":
            raise ValueError("Analysis is available only for completed runs")
        return self._revision_analysis(record)

    def _revision_analysis(self, record):
        directory = self._path(record.run_id) / "revisions" / str(record.revision)
        path = directory / "analysis.json"
        if path.exists():
            return read_json(path)
        # Older immutable revisions remain readable without rerunning the model
        # or fetching weather. GET does not modify the saved forecast or inputs.
        forecasts = [ForecastRecord.model_validate(row) for row in read_json(directory / "forecast.json")]
        weather = [WeatherRecord.model_validate(row) for row in read_json(directory / "weather.json")]
        previous = None
        if record.revision > 1:
            previous = [ForecastRecord.model_validate(row) for row in read_json(
                directory.parent / str(record.revision - 1) / "forecast.json")]
        report = analyse_forecast(forecasts, weather, previous)
        report.update(run_id=record.run_id, revision=record.revision,
                      issue_time=record.request.issue_time.isoformat())
        return report

    def health(self):
        demo = self.settings.mode == "demo"
        metadata = self.settings.model_artifact_path
        return {"status": "ok", "mode": self.settings.mode, "is_demo": demo,
                "weather_ready": bool(self.weather) or demo or bool(
                    self.settings.turbines and self.settings.weather_provider == "gfs_s3"
                    and find_spec("eccodes") is not None),
                "model_ready": demo or bool(self.settings.train_cutoff and self.settings.history_timezone and (
                    self.model or (self.settings.model_adapter_module and metadata and Path(metadata).exists()))),
                "training_boundary_configured": bool(self.settings.train_cutoff and self.settings.history_timezone),
                "timestamp_convention": self.settings.timestamp_convention,
                "turbine_ids": [t.turbine_id for t in self.settings.turbines]}

    def evaluation(self):
        from .evaluation_report import evaluation_report
        return evaluation_report(self.settings)

    def replay(self, horizon_hours=None):
        horizon = horizon_hours or self.settings.default_horizon_hours
        zone = ZoneInfo(self.settings.replay_timezone)
        first = datetime(2026, 1, 31, self.settings.replay_issue_hour, tzinfo=zone)
        start = datetime(2026, 2, 1, tzinfo=zone).astimezone(timezone.utc)
        end = datetime(2026, 3, 1, tzinfo=zone).astimezone(timezone.utc)
        turbine_ids = [t.turbine_id for t in self.settings.turbines]
        if not turbine_ids:
            raise ValueError("Configure at least one turbine before replay")
        runs, selected = [], {}
        for offset in range(29):
            issue = (first + timedelta(days=offset)).astimezone(timezone.utc)
            record = self.run(RunRequest(issue_time=issue, horizon_hours=horizon, turbine_ids=turbine_ids))
            runs.append({"run_id": record.run_id, "issue_time": issue.isoformat(),
                         "status": record.status, "revision": record.revision, "error": record.error})
            if record.status == "completed":
                for row in self.forecast(record.run_id):
                    if start < row.valid_time <= end:
                        key = (row.turbine_id, row.valid_time)
                        # Predeclared overlap policy: latest issue, never selected using truth/error.
                        if key not in selected or selected[key].issue_time < row.issue_time:
                            selected[key] = row
        expected = {(tid, start + timedelta(hours=h)) for tid in turbine_ids for h in range(1, 673)}
        missing = sorted(expected - set(selected))
        failed = sum(r["status"] != "completed" for r in runs)
        status = "completed" if not failed and not missing else ("failed" if failed == len(runs) else "partial")
        report = {"status": status, "is_demo": self.settings.mode == "demo",
                  "timezone": self.settings.replay_timezone, "issue_hour": self.settings.replay_issue_hour,
                  "horizon_hours": horizon, "overlap_policy": "latest issue_time for each target hour",
                  "timestamp_convention": "hour_end", "expected_hours_per_turbine": 672,
                  "selected_rows": len(selected), "failed_runs": failed, "runs": runs,
                  "missing_hours": [{"turbine_id": t, "valid_time": v.isoformat()} for t, v in missing]}
        directory = self.root / "replay" / f"2026-02-{horizon}h"
        write_json(directory / "report.json", report)
        write_json(directory / "february.json", [selected[k].model_dump(mode="json") for k in sorted(selected)])
        return report
