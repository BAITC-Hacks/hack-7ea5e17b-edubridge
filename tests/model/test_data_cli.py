import hashlib
import importlib
import json
import sys

import pandas as pd
import pytest


def setup_cli(tmp_path, monkeypatch):
    cli = importlib.import_module("wind_agent.data.__main__")
    header = (
        "ID,Статистическое время,Средняя скорость ветра(m/s),"
        "Нормализованная активная мощность,Средняя температура окружающей среды(°C)\n"
    )
    paths = []
    for number in (1, 2):
        path = tmp_path / f"turbine_{number}.csv"
        # Same ID at different times must remain two separate observations.
        path.write_text(header + f"1,2026-01-01 0:{(number-1)*10:02d}:00,5,0,2")
        paths.append(path)
    monkeypatch.setattr(cli, "SOURCES", {
        f"turbine_{index}": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for index, path in enumerate(paths, start=1)
    })
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", [
        "wind_agent.data", "--turbine-1", str(paths[0]), "--turbine-2", str(paths[1]),
        "--output-dir", str(output),
    ])
    return cli, paths, output


def test_cli_preserves_separate_observations_and_reproduces_report(tmp_path, monkeypatch):
    cli, paths, output = setup_cli(tmp_path, monkeypatch)
    originals = [path.read_bytes() for path in paths]

    cli.main()
    history = pd.read_csv(output / "history.csv")
    first_report = (output / "quality-report.json").read_bytes()
    cli.main()

    assert (output / "quality-report.json").read_bytes() == first_report
    assert [path.read_bytes() for path in paths] == originals
    assert history["turbine_id"].tolist() == ["turbine_1", "turbine_2"]
    assert history["source_timestamp"].nunique() == 2
    assert history["normalized_power"].tolist() == [0.0, 0.0]
    assert [r["rows"] for r in json.loads(first_report)["files"]] == [1, 1]


def test_cli_validates_both_inputs_before_writing(tmp_path, monkeypatch):
    cli, paths, output = setup_cli(tmp_path, monkeypatch)
    paths[1].write_text("modified input")

    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        cli.main()

    assert not output.exists()


def test_hourly_cli_requires_explicit_clock_assumptions(tmp_path, monkeypatch):
    cli, paths, output = setup_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--hourly"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert not output.exists()


def test_hourly_cli_writes_complete_targets_with_assumptions_and_source_hashes(tmp_path, monkeypatch):
    cli, paths, output = setup_cli(tmp_path, monkeypatch)
    for index, path in enumerate(paths, start=1):
        header = path.read_text().splitlines()[0]
        rows = [f"{i+1},2026-01-01 0:{i*10:02d}:00,5,0.4,2" for i in range(6)]
        path.write_text(header + "\n" + "\n".join(rows))
        cli.SOURCES[f"turbine_{index}"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(sys, "argv", [
        *sys.argv, "--hourly", "--history-timezone", "Etc/GMT-5",
        "--source-timestamp-convention", "interval_start", "--reporting-delay-seconds", "0",
        "--training-cutoff", "2025-12-31T20:00:00Z", "--assumption", "Test assumption",
    ])

    cli.main()

    targets = pd.read_csv(output / "hourly-training.csv")
    report = json.loads((output / "hourly-report.json").read_text())
    assert len(targets) == 2
    assert targets["sample_count"].tolist() == [6, 6]
    assert targets["normalized_power"].tolist() == pytest.approx([0.4, 0.4])
    assert pd.to_datetime(targets["valid_time"], utc=True).eq(
        pd.Timestamp("2025-12-31T20:00:00Z")
    ).all()
    assert report["semantics"]["source_semantics_status"] == "assumed"
    assert report["semantics"]["assumptions"] == ["Test assumption"]
    assert set(report["source_sha256"]) == {"turbine_1", "turbine_2"}


@pytest.mark.parametrize("alias_kind", ["source", "other_output"])
def test_cli_rejects_output_hard_links_before_overwrite(tmp_path, monkeypatch, alias_kind):
    cli, paths, output = setup_cli(tmp_path, monkeypatch)
    originals = [path.read_bytes() for path in paths]
    output.mkdir()
    history = output / "history.csv"
    if alias_kind == "source":
        history.hardlink_to(paths[0])
    else:
        history.write_text("previous history")
        (output / "quality-report.json").hardlink_to(history)

    before = history.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert history.read_bytes() == before
    assert [path.read_bytes() for path in paths] == originals
