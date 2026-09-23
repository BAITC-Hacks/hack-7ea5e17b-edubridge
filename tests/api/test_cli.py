import json
from pathlib import Path

import pytest

from wind_agent.cli import main


@pytest.fixture
def demo_config(tmp_path):
    source = Path(__file__).parents[2] / "config" / "demo.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    data["artifact_dir"] = str(tmp_path / "runs")
    data["cache_dir"] = str(tmp_path / "cache")
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_cli_demo_runs_and_reports_artifact(demo_config, capsys):
    code = main([
        "--config", demo_config, "run", "--issue-time", "2026-01-31T18:00:00Z",
        "--horizon-hours", "24", "--turbine-ids", "turbine_1",
    ])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "completed"
    assert report["request"]["turbine_ids"] == ["turbine_1"]
    assert report["warnings"]


def test_cli_reads_environment_config(demo_config, monkeypatch, capsys):
    monkeypatch.setenv("WIND_AGENT_CONFIG", demo_config)
    assert main(["health"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "demo"


def test_cli_missing_config_returns_nonzero(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.json"), "health"]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "failed"


def test_cli_rejects_naive_time(demo_config, capsys):
    with pytest.raises(SystemExit) as error:
        main(["--config", demo_config, "run", "--issue-time", "2026-01-31T18:00:00"])
    assert error.value.code == 2
    assert "include an offset" in capsys.readouterr().err


def test_weather_fetch_refuses_synthetic_demo(demo_config, capsys):
    assert main([
        "--config", demo_config, "weather-fetch", "--issue-time", "2026-01-31T18:00:00Z",
    ]) == 2
    assert "requires archive mode" in json.loads(capsys.readouterr().err)["error"]


def test_cli_failed_archive_run_is_nonzero(tmp_path, capsys):
    source = Path(__file__).parents[2] / "config" / "archive.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    data["train_cutoff"] = "2026-01-30T00:00:00Z"
    data["history_timezone"] = "Asia/Almaty"
    data["artifact_dir"] = str(tmp_path / "runs")
    data["cache_dir"] = str(tmp_path / "cache")
    config = tmp_path / "archive.json"
    config.write_text(json.dumps(data), encoding="utf-8")
    assert main([
        "--config", str(config), "run", "--issue-time", "2026-01-31T18:00:00Z",
    ]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record["status"] == "failed"
    assert "Model unavailable" in record["error"]
