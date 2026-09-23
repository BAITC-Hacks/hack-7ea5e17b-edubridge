"""Real cached NOAA -> participant #1 AgentService -> saved participant #2 model.

Run from the repository root after collection/training. All requests are offline;
fail if any required archived byte is absent. This is integration, not accuracy.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pandas as pd

from wind_agent.agent.service import AgentService
from wind_agent.config import load_settings
from wind_agent.model.interface import predict
from wind_agent.model.experiment import save_json, sha_file
from wind_agent.weather.gfs import GFSArchiveProvider


def canonical(frame):
    return frame.to_json(orient="records", date_format="iso", date_unit="ns", double_precision=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/archive-model.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/evidence/model-integration.json"))
    args = parser.parse_args()
    settings = load_settings(args.config)
    package = settings.model_artifact_path
    metadata = json.loads((package / "metadata.json").read_text("utf-8"))
    issue = datetime(2026, 1, 31, 18, tzinfo=timezone.utc)
    turbines = {t.turbine_id: (t.latitude, t.longitude) for t in settings.turbines}
    observations = []
    with GFSArchiveProvider(turbines, settings.cache_dir, offline=True) as weather:
        service = AgentService(settings, weather=weather)
        for horizon in (24, 48):
            request = {"issue_time": issue, "horizon_hours": horizon, "turbine_ids": list(turbines)}
            run = service.run(request)
            if run.status != "completed":
                raise RuntimeError(run.error)
            forecasts = service.forecast(run.run_id)
            assert len(forecasts) == horizon * len(turbines)
            assert all(r.data_quality == "warning" and r.target_unit == "normalized_power"
                       and r.warnings for r in forecasts)
            repeated = service.run(request)
            assert repeated.status == "completed" and repeated.revision == run.revision
            records = weather.fetch(issue, horizon, list(turbines))
            frame = pd.DataFrame([r.model_dump(mode="json") for r in records])
            direct = predict(package, frame, issue, horizon)
            assert direct.prediction.tolist() == [r.prediction for r in forecasts]
            with TemporaryDirectory(prefix="wind-model-verify-") as temp:
                source = Path(temp) / "weather.json"
                destination = Path(temp) / "prediction.json"
                source.write_text(json.dumps(frame.to_dict(orient="records"), allow_nan=False),
                                  encoding="utf-8")
                code = (
                    "import json,sys,pandas as pd; from pathlib import Path; "
                    "from wind_agent.model.interface import predict; "
                    "w=pd.DataFrame(json.loads(Path(sys.argv[2]).read_text())); "
                    "p=predict(sys.argv[1],w,sys.argv[4],int(sys.argv[5])); "
                    "Path(sys.argv[3]).write_text(p.to_json(orient='records',date_format='iso',"
                    "date_unit='ns',double_precision=15))"
                )
                subprocess.run([sys.executable, "-c", code, str(package), str(source),
                                str(destination), issue.isoformat(), str(horizon)], check=True)
                assert destination.read_text() == canonical(direct)
            observations.append({
                "issue_time": issue.isoformat(), "horizon_hours": horizon,
                "run_id": run.run_id, "status": run.status, "revision": run.revision,
                "rows": len(forecasts), "turbine_ids": list(turbines),
                "weather_model_runs": sorted({r.model_run_time.isoformat() for r in records}),
                "weather_available_at_max": max(r.forecast_available_at for r in records).isoformat(),
                "all_weather_available_by_issue": all(r.forecast_available_at <= issue for r in records),
                "fresh_process_predictions_identical": True,
                "unchanged_inputs_preserve_revision": True,
                "agent_predictions_equal_direct_model": True,
                "forecast_sha256": hashlib.sha256(canonical(direct).encode()).hexdigest(),
                "events": [event.step for event in run.events],
            })
        # Requesting a single turbine uses exactly the same public interface.
        single = frame[frame.turbine_id == "turbine_1"].copy()
        assert len(predict(package, single, issue, 48)) == 48
    report = {
        "status": "passed", "checked_at": datetime.now(timezone.utc).isoformat(),
        "is_fixture": False, "network_required": False,
        "scope": "Jan31 issue, real NOAA archived weather, actual ModelBridge/AgentService, both turbines and H24/H48",
        "model_version": metadata["model_version"],
        "model_sha256": sha_file(package / "model.joblib"),
        "metadata_sha256": sha_file(package / "metadata.json"),
        "training_cutoff": metadata["training_cutoff"],
        "runs": observations, "single_turbine_interface_passed": True,
        "february_replay_complete": False, "measures_forecast_accuracy": False,
        "source_semantics_status": metadata["metadata"]["source_semantics_status"],
    }
    save_json(args.output, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
