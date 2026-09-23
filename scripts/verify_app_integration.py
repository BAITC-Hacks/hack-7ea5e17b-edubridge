"""Verify the real model API through participant 3's unmodified HTTP client.

This is an explicit integration probe, not a network-dependent unit test.
It preserves forecasts/CSV and a report below its own artifact directory.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ui.client import ApiClient, ApiError  # noqa: E402
from ui.presentation import (  # noqa: E402
    collect_warnings,
    utc_time,
    validate_csv,
    validate_forecast,
    validate_run_identity,
    weather_provenance,
)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_for_run(client, run_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get_run(run_id)
        if run["status"] in ("completed", "failed"):
            if run["status"] != "completed":
                raise AssertionError(f"Agent failed: {run.get('error')}")
            return run
        time.sleep(0.2)
    raise TimeoutError(f"Run {run_id} did not finish within {timeout} seconds")


@contextmanager
def local_backend(config_path, output, cache_dir, timeout):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("mode") != "archive":
        raise ValueError("This probe requires archive mode and a trained model")
    config["artifact_dir"] = str((output / "agent").resolve())
    config["cache_dir"] = str(cache_dir.resolve())
    private_config = output / "api-config.json"
    save_json(private_config, config)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log_path = output / "api.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "wind_agent", "--config", str(private_config),
             "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + timeout
            client = ApiClient(url, timeout=10)
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"API exited unexpectedly; inspect {log_path}")
                try:
                    client.health()
                    break
                except ApiError:
                    time.sleep(0.2)
            else:
                raise TimeoutError(f"API did not start; inspect {log_path}")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def exact_csv_match(content, records):
    """Check every field, including warnings, rather than only predictions."""
    csv_rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert len(csv_rows) == len(records)
    for csv_row, record in zip(csv_rows, records, strict=True):
        assert set(csv_row) == set(record)
        for key, value in record.items():
            if isinstance(value, (dict, list)):
                assert json.loads(csv_row[key]) == value, key
            else:
                assert csv_row[key] == ("" if value is None else str(value)), key


def check_run(client, request, output, timeout):
    submitted = client.create_run(**request)
    run = wait_for_run(client, submitted["run_id"], timeout)
    records = client.get_forecast(run["run_id"])
    rows = validate_forecast(records, request)
    validate_run_identity(rows, run)
    content = client.get_forecast_csv(run["run_id"])
    assert validate_csv(content, rows, run) == content
    exact_csv_match(content, records)
    assert len(rows) == request["horizon_hours"] * len(request["turbine_ids"])
    assert {row["target_unit"] for row in rows} == {"normalized_power"}
    assert {row["weather_model"] for row in rows} == {"gfs_0p25"}
    assert all(row["data_quality"] != "fixture" for row in rows)
    warnings = collect_warnings(run, rows)
    assert warnings and all("SYNTHETIC FIXTURE" not in warning for warning in warnings)
    provenance = weather_provenance(run, rows)
    assert len(provenance) == len(request["turbine_ids"])
    for source in provenance:
        assert source["forecast_available_at"] and source["availability_basis"]
        assert source["raw_sha256"] and len(source["raw_sha256"]) == 64
        assert source["time_basis"] == "instant_at_end"
        assert utc_time(source["forecast_available_at"]) <= utc_time(request["issue_time"])
    repeated = client.create_run(**request)
    repeated_run = wait_for_run(client, repeated["run_id"], timeout)
    assert repeated_run["run_id"] == run["run_id"]
    assert repeated_run["revision"] == run["revision"]
    assert client.get_forecast(run["run_id"]) == records
    run_output = output / "exports" / run["run_id"]
    save_json(run_output / "run.json", run)
    save_json(run_output / "forecast.json", records)
    (run_output / "forecast.csv").write_bytes(content)
    return {
        "request": request, "run_id": run["run_id"], "revision": run["revision"],
        "rows": len(rows), "status": run["status"], "json_csv_all_fields_equal": True,
        "repeat_idempotent": True, "warnings": warnings, "provenance": provenance,
        "csv_sha256": hashlib.sha256(content).hexdigest(),
        "first_valid_time": rows[0]["valid_time"], "last_valid_time": rows[-1]["valid_time"],
    }


def check_dashboard(url, issue_time, turbine_ids, timeout):
    """Render the actual app against the real API, without mocking UI or HTTP."""
    from unittest.mock import patch

    from streamlit.testing.v1 import AppTest

    def click(app, label):
        next(button for button in app.button if button.label == label).click().run()
        assert not app.exception, [item.message for item in app.exception]

    with patch.dict(os.environ, {"WIND_API_BASE_URL": url, "WIND_API_TIMEOUT": "10"}):
        app = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=30).run()
        app.get("button_group")[0].set_value("API").run()
        click(app, "Проверить подключение")
        assert app.session_state["health_result"]["mode"] == "archive"
        issue = utc_time(issue_time)
        app.date_input[0].set_value(issue.date())
        next(item for item in app.selectbox if item.label == "Время выпуска (UTC)").set_value(issue.hour)
        app.multiselect[0].set_value(turbine_ids)
        app.radio[0].set_value(48)
        click(app, "Рассчитать прогноз")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entry = app.session_state["history"][app.session_state["current_id"]]
            if entry["run"]["status"] in ("completed", "failed"):
                break
            time.sleep(0.2)
            app.run()
        assert not app.exception, [item.message for item in app.exception]
        assert entry["run"]["status"] == "completed", entry["run"]
        assert not entry["error"] and not entry["csv_error"] and not entry["evaluation_error"]
        assert len(entry["rows"]) == 48 * len(turbine_ids)
        assert entry["csv"]
        assert entry["evaluation"]["status"] == "ready"
        assert entry["evaluation"]["test_truth_available"] is False
        displayed_warnings = {item.value for item in app.warning}
        expected_warnings = collect_warnings(entry["run"], entry["rows"])
        assert set(expected_warnings) <= displayed_warnings
        frames = [item.value for item in app.dataframe]
        source = next(frame for frame in frames if "forecast_available_at" in frame.columns)
        assert source["forecast_available_at"].notna().all()
        assert source["availability_basis"].notna().all()
        assert len(app.get("plotly_chart")) == 1
        assert len(app.get("download_button")) == 1
        rendered_json = [json.loads(item.value) for item in app.json]
        assert entry["evaluation"] in rendered_json
        return {
            "status": "passed", "transport": "real local HTTP; no UI/network mocks",
            "run_id": entry["run"]["run_id"], "rows": len(entry["rows"]),
            "warnings_displayed": len(expected_warnings), "provenance_rows_displayed": len(source),
            "forecast_chart_count": len(app.get("plotly_chart")), "csv_download_available": True,
            "validation_report_displayed": True, "february_truth_present": False,
        }


def verify(url, arguments):
    client = ApiClient(url, timeout=10)
    health = client.health()
    assert health["mode"] == "archive" and health["is_demo"] is False
    assert health["model_ready"] is True and health["weather_ready"] is True
    turbine_ids = health["turbine_ids"]
    assert len(turbine_ids) == 2
    selections = [[tid] for tid in turbine_ids] + [turbine_ids]
    results = []
    for horizon in (24, 48):
        for selection in selections:
            request = {"issue_time": arguments.issue_time,
                       "horizon_hours": horizon, "turbine_ids": selection}
            result = check_run(client, request, arguments.output, arguments.timeout_seconds)
            results.append(result)
            print(f"Passed {selection}, {horizon}h: {result['rows']} rows", flush=True)
    evaluation = client.get_evaluation()
    assert evaluation["status"] == "ready", evaluation
    assert evaluation["test_truth_available"] is False
    assert evaluation["metrics"] and evaluation["baseline"] and evaluation["periods"]
    assert evaluation["metrics"]["independent_holdout"]["used_for_selection"] is False
    assert evaluation["metrics"]["production_refit"]["independent_metrics"] is None
    save_json(arguments.output / "evaluation.json", evaluation)
    dashboard = check_dashboard(url, arguments.issue_time, turbine_ids, arguments.timeout_seconds) if arguments.app_test else None
    files = [
        "scripts/verify_app_integration.py", "src/wind_agent/agent/service.py",
        "src/wind_agent/agent/evaluation_report.py", "src/wind_agent/api/app.py",
        "ui/app.py", "ui/client.py", "ui/presentation.py",
        "models/wind-power-v1/metadata.json", "models/wind-power-v1/model.joblib",
        "models/wind-power-v1/validation.json",
    ]
    return {
        "status": "passed", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_worktree_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).splitlines(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
        "packages": {name: importlib.metadata.version(name) for name in (
            "fastapi", "pydantic", "streamlit", "plotly", "scikit-learn", "numpy", "eccodes",
        )},
        "base_url": url, "health": health, "runs": results,
        "evaluation_status": evaluation["status"], "test_truth_available": False,
        "baseline_comparison": evaluation["baseline"]["comparison"],
        "dashboard": dashboard,
        "scope": "Real trained model and archived weather through the UI HTTP client; does not establish February accuracy.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/archive-model.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/weather-cache"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/app-integration"))
    parser.add_argument("--issue-time", default="2026-01-31T18:00:00Z")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--base-url", help="Use an existing API instead of starting a private process")
    parser.add_argument("--app-test", action="store_true", help="Also render Streamlit against real HTTP")
    arguments = parser.parse_args(argv)
    arguments.output = arguments.output.resolve()
    arguments.output.mkdir(parents=True, exist_ok=True)
    if arguments.base_url:
        report = verify(arguments.base_url, arguments)
    else:
        with local_backend(arguments.config.resolve(), arguments.output, arguments.cache_dir, arguments.timeout_seconds) as url:
            report = verify(url, arguments)
    save_json(arguments.output / "report.json", report)
    print(json.dumps({"status": report["status"], "runs": len(report["runs"]),
                      "report": str(arguments.output / "report.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
