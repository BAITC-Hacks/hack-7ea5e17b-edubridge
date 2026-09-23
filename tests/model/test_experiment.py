"""Offline orchestration tests using explicitly synthetic weather and target rows."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from wind_agent.model import experiment


def tuning_report(metric=0.2, **changes):
    report = {
        "status": "ok",
        "eligible_for_selection": True,
        "supplied_splits": ["tune"],
        "equal_turbine_mean_mae": metric,
    }
    report.update(changes)
    return report


def candidate(name):
    return {"name": name, "algorithm": "constant_mean", "hyperparameters": {}}


def test_exact_tie_uses_declared_candidate_order_not_report_insertion_order():
    candidates = [candidate("first"), candidate("second")]
    reports = {"second": tuning_report(0.1), "first": tuning_report(0.1)}

    assert experiment.select_candidate(candidates, reports) is candidates[0]


def test_selection_uses_the_tuning_score_not_candidate_order_when_scores_differ():
    candidates = [candidate("first"), candidate("second")]

    assert experiment.select_candidate(candidates, {
        "first": tuning_report(0.2), "second": tuning_report(0.1),
    }) is candidates[1]


@pytest.mark.parametrize("report", [
    tuning_report(supplied_splits=["holdout"]),
    tuning_report(supplied_splits=["tune", "holdout"]),
    tuning_report(eligible_for_selection=False),
    tuning_report(status="partial_truth"),
    tuning_report(status="invalid_predictions"),
    tuning_report(float("nan")),
    tuning_report(float("inf")),
    tuning_report(-0.1),
    tuning_report(True),
    tuning_report("0.1"),
    tuning_report(None),
    {},
    None,
    [],
])
def test_malformed_or_non_tuning_reports_cannot_select_a_candidate(report):
    with pytest.raises(ValueError):
        experiment.select_candidate([candidate("only")], {"only": report})


def test_missing_tuning_report_is_a_controlled_error():
    with pytest.raises(ValueError):
        experiment.select_candidate([candidate("only")], {})


def test_duplicate_candidate_names_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        experiment.select_candidate([candidate("same"), candidate("same")], {
            "same": tuning_report(),
        })


@pytest.fixture
def synthetic_experiment(tmp_path, monkeypatch):
    """Patch only weather loading/source manifests, keeping numeric learning real.

    The mock weather loader stands in for already validated full origin files.
    Its deliberately small returned table exercises each chronological window;
    it is never used as evidence of real GFS coverage or forecasting accuracy.
    """
    hashes = {tid: hashlib.sha256(f"synthetic history {tid}".encode()).hexdigest()
              for tid in experiment.TURBINES}
    monkeypatch.setattr(experiment, "SOURCES", {
        tid: {"sha256": digest} for tid, digest in hashes.items()
    })
    original_design = json.loads(
        (Path(__file__).resolve().parents[2] / "config/model-experiment.json").read_text("utf-8")
    )

    def create(name, *, reverse_holdout=False):
        directory = tmp_path / name
        directory.mkdir()
        history_rows, weather_rows = [], []
        for split, issue_text in (
            ("train", "2025-11-30T18:00Z"),
            ("tune", "2025-12-31T18:00Z"),
            ("holdout", "2026-01-15T18:00Z"),
        ):
            issue = pd.Timestamp(issue_text)
            run = issue - pd.Timedelta(hours=6)
            targets = [0.9, 0.5, 0.1] if split == "holdout" and reverse_holdout else [0.1, 0.5, 0.9]
            for turbine in experiment.TURBINES:
                for lead, wind, power in zip((2, 3, 4), (2.5, 6.5, 10.5), targets, strict=True):
                    valid = issue + pd.Timedelta(hours=lead)
                    history_rows.append({
                        "turbine_id": turbine, "valid_time": valid,
                        "available_at": valid, "normalized_power": power, "is_complete": True,
                    })
                    weather_rows.append({
                        "turbine_id": turbine, "issue_time": issue.isoformat(),
                        "valid_time": valid.isoformat(), "split": split,
                        "wind_speed_ms": wind, "temperature_c": -2.0, "wind_height_m": 100.0,
                        "model_run_time": run.isoformat(),
                        "forecast_available_at": (issue - pd.Timedelta(hours=1)).isoformat(),
                        "availability_basis": "synthetic test fixture; not real archive evidence",
                        "provider": "NOAA GFS via AWS Open Data", "weather_model": "gfs_0p25",
                        "raw_sha256": hashlib.sha256(f"synthetic {split} {lead}".encode()).hexdigest(),
                        "lead_hours": lead + 6,
                    })
        # One unmatched target demonstrates visible exclusion accounting without
        # changing any candidate's common scored rows.
        missing = copy.deepcopy(next(row for row in weather_rows if row["split"] == "tune"))
        missing["valid_time"] = "2025-12-31T23:00:00+00:00"
        missing["lead_hours"] = 11
        weather_rows.append(missing)
        history_path = directory / "hourly.csv"
        pd.DataFrame(history_rows).to_csv(history_path, index=False)
        hourly_report_path = directory / "hourly-report.json"
        hourly_report_path.write_text(json.dumps({
            "source_sha256": hashes,
            "semantics": {
                "history_timezone": "Etc/GMT-5",
                "source_timestamp_convention": "interval_start",
                "target_reporting_delay_seconds": 0,
                "assumptions": ["Synthetic unit-test data; not a historical forecast claim"],
            },
        }), encoding="utf-8")
        weather_dir = directory / "weather"
        weather_dir.mkdir()
        origins = experiment.planned_origins()
        manifest = {
            "origins": [{"issue_time": origin.issue_time.isoformat(), "status": "completed"}
                        for origin in origins],
            "completed_origins": len(origins),
            "provider": "synthetic unit-test manifest", "weather_model": "gfs_0p25",
            "wind_height_m": 100, "temperature_height_m": 2,
            "feature_time_basis": "instant_at_end", "availability_basis": "synthetic test fixture",
        }
        (weather_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        design = copy.deepcopy(original_design)
        design["model_version"] = f"synthetic-test-{name}"
        design["candidates"] = [row for row in design["candidates"]
                                if row["name"] in {"constant_mean", "wind_curve"}]
        design_path = directory / "experiment.json"
        design_path.write_text(json.dumps(design), encoding="utf-8")

        def load_weather(path, splits):
            assert Path(path) == weather_dir
            assert splits == ["train", "tune", "holdout"]
            return copy.deepcopy(weather_rows)

        monkeypatch.setattr(experiment, "load_weather_dataset", load_weather)
        return SimpleNamespace(
            history_path=history_path, hourly_report_path=hourly_report_path,
            weather_dir=weather_dir, experiment_path=design_path,
            output_dir=directory / "results", package_dir=directory / "package",
            weather_rows=weather_rows,
        )

    return create


def run_fixture(fixture):
    return experiment.run_experiment(
        fixture.history_path, fixture.hourly_report_path, fixture.weather_dir,
        fixture.experiment_path, fixture.output_dir, fixture.package_dir,
    )


def test_end_to_end_selection_is_saved_before_holdout_and_final_artifact_has_correct_cutoffs(
    synthetic_experiment, monkeypatch,
):
    fixture = synthetic_experiment("normal")
    original_score = experiment.score
    original_fit = experiment.fit_estimators
    scored_stages = []
    fit_stages = []

    def score_with_frozen_selection_check(models, rows, cutoff):
        stage = set(rows["split"])
        if stage == {"holdout"}:
            selection = json.loads((fixture.output_dir / "selection.json").read_text("utf-8"))
            assert selection["selected_candidate"]["name"] == "wind_curve"
            assert selection["uses_holdout"] is False
            assert pd.Timestamp(cutoff) == pd.Timestamp("2026-01-14T19:00Z")
        else:
            assert stage == {"tune"}
            assert not (fixture.output_dir / "selection.json").exists()
            assert pd.Timestamp(cutoff) == pd.Timestamp("2025-12-30T19:00Z")
        scored_stages.append(stage)
        return original_score(models, rows, cutoff)

    def fit_without_holdout(rows, algorithm, hyperparameters):
        stage = set(rows["split"])
        assert "holdout" not in stage
        if (fixture.output_dir / "selection.json").exists():
            assert stage == {"train", "tune"}
            assert rows["available_at"].max() <= pd.Timestamp("2026-01-14T19:00Z")
        else:
            assert stage == {"train"}
            assert rows["available_at"].max() <= pd.Timestamp("2025-12-30T19:00Z")
        fit_stages.append(stage)
        return original_fit(rows, algorithm, hyperparameters)

    monkeypatch.setattr(experiment, "score", score_with_frozen_selection_check)
    monkeypatch.setattr(experiment, "fit_estimators", fit_without_holdout)

    result = run_fixture(fixture)

    assert result["selected_candidate"] == "wind_curve"
    assert scored_stages == [{"tune"}, {"tune"}, {"holdout"}, {"holdout"}]
    assert fit_stages == [{"train"}, {"train"}, {"train", "tune"}, {"train", "tune"}]
    validation = json.loads((fixture.output_dir / "validation.json").read_text("utf-8"))
    assert validation["holdout_used_for_selection"] is False
    assert validation["production_refit_includes_holdout"] is True
    assert validation["february_ground_truth_available"] is False
    assert validation["splits"]["exclusions"] == {"missing_truth": 1}
    assert all(report["eligible_for_selection"] is False for report in validation["holdout"].values())
    artifact = json.loads((fixture.package_dir / "metadata.json").read_text("utf-8"))
    assert artifact["metadata"]["algorithm"] == "wind_curve"
    assert pd.Timestamp(artifact["training_cutoff"]) == pd.Timestamp("2026-01-31T18:00Z")
    assert pd.Timestamp(artifact["metadata"]["selection_cutoff"]) == pd.Timestamp("2026-01-14T19:00Z")
    assert pd.Timestamp(artifact["metadata"]["calibration_cutoff"]) == pd.Timestamp("2026-01-14T19:00Z")
    assert pd.Timestamp(artifact["metadata"]["training_target_available_at_max"]) <= pd.Timestamp(
        artifact["training_cutoff"]
    )
    assert artifact["metadata"]["training_rows"] == {"turbine_1": 9, "turbine_2": 9}
    assert result["model_sha256"] == hashlib.sha256((fixture.package_dir / "model.joblib").read_bytes()).hexdigest()


def test_reversing_holdout_labels_cannot_change_the_tuning_winner(synthetic_experiment):
    original = synthetic_experiment("original")
    first_result = run_fixture(original)
    first_selection = json.loads((original.output_dir / "selection.json").read_text("utf-8"))

    changed = synthetic_experiment("reversed", reverse_holdout=True)
    second_result = run_fixture(changed)
    second_selection = json.loads((changed.output_dir / "selection.json").read_text("utf-8"))
    changed_validation = json.loads((changed.output_dir / "validation.json").read_text("utf-8"))

    assert first_result["selected_candidate"] == second_result["selected_candidate"] == "wind_curve"
    assert first_selection == second_selection
    assert first_result["holdout_mae"] < second_result["holdout_mae"]
    assert (changed_validation["holdout"]["constant_mean"]["equal_turbine_mean_mae"]
            < changed_validation["holdout"]["wind_curve"]["equal_turbine_mean_mae"])


def test_incomplete_weather_collection_cannot_start_candidate_fitting(synthetic_experiment, monkeypatch):
    fixture = synthetic_experiment("missing-origin")
    manifest_path = fixture.weather_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["origins"][0]["status"] = "failed"
    manifest["completed_origins"] -= 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def unexpected_fit(*args, **kwargs):
        pytest.fail("Fitting must not start with an incomplete declared weather collection")

    monkeypatch.setattr(experiment, "fit_estimators", unexpected_fit)
    with pytest.raises(ValueError, match="Complete all"):
        run_fixture(fixture)
    assert not fixture.output_dir.exists()


@pytest.mark.parametrize("column", ["valid_time", "available_at"])
def test_naive_hourly_history_cannot_be_silently_relabelled_utc(synthetic_experiment, column):
    fixture = synthetic_experiment(f"naive-{column}")
    history = pd.read_csv(fixture.history_path)
    history[column] = pd.to_datetime(history[column], utc=True).dt.tz_localize(None)
    history.to_csv(fixture.history_path, index=False)

    with pytest.raises(ValueError, match="NAIVE_TIMESTAMP"):
        run_fixture(fixture)
    assert not fixture.output_dir.exists()


def test_a_turbine_with_no_tuning_truth_prevents_unbalanced_comparison(synthetic_experiment):
    fixture = synthetic_experiment("missing-turbine-truth")
    history = pd.read_csv(fixture.history_path)
    dates = pd.to_datetime(history.valid_time, utc=True)
    history = history.loc[~(history.turbine_id.eq("turbine_2") & dates.dt.month.eq(12)
                            & dates.dt.day.eq(31))]
    history.to_csv(fixture.history_path, index=False)

    with pytest.raises(ValueError, match="Both turbines require complete labels in tune"):
        run_fixture(fixture)
    assert not fixture.output_dir.exists()
