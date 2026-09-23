import json
import math

import pandas as pd
import pytest

from wind_agent.evaluation import (
    EvaluationError,
    SplitConfig,
    assign_splits,
    ensure_model_cutoff,
    evaluate,
)


def forecast_row(issue, valid, *, turbine="turbine_1", target=0.2, prediction=0.4, **extra):
    row = {
        "issue_time": pd.Timestamp(issue),
        "valid_time": pd.Timestamp(valid),
        "turbine_id": turbine,
        "normalized_power": target,
        "prediction": prediction,
        "available_at": pd.Timestamp(valid),
    }
    row.update(extra)
    return row


def example_forecasts():
    return pd.DataFrame([
        forecast_row("2026-01-01T00:00Z", "2026-01-02T00:00Z", prediction=0.4),
        forecast_row("2025-12-31T00:00Z", "2026-01-02T00:00Z", prediction=0.6),
        forecast_row("2026-01-01T00:00Z", "2026-01-01T01:00Z",
                     turbine="turbine_2", prediction=0.0, target=0.0),
    ])


def test_split_config_records_explicit_utc_cutoffs_and_does_not_require_randomness():
    config = SplitConfig()

    assert config.train_cutoff == pd.Timestamp("2025-12-30T19:00Z")
    assert config.selection_cutoff == pd.Timestamp("2026-01-14T19:00Z")
    assert config.first_forecast_cutoff == pd.Timestamp("2026-01-31T18:00Z")
    assert config.selection_cutoff < config.windows["holdout"].issue_start
    assert config.windows["train"].target_start == pd.Timestamp("2025-11-30T19:00Z")
    assert config.windows["holdout"].target_end == pd.Timestamp("2026-01-31T18:00Z")
    assert config.to_dict()["selection_uses_holdout"] is False
    json.dumps(config.to_dict(), allow_nan=False)


def test_assignment_preserves_all_valid_vintages_with_no_target_overlap_or_input_mutation():
    rows = pd.DataFrame([
        forecast_row("2026-01-15T18:00Z", "2026-01-15T20:00Z"),
        forecast_row("2025-12-01T18:00Z", "2025-12-02T18:00Z"),
        forecast_row("2025-11-30T18:00Z", "2025-12-02T18:00Z"),
        forecast_row("2025-12-31T18:00Z", "2025-12-31T20:00Z"),
    ])
    original = rows.copy(deep=True)

    assignment = assign_splits(rows)

    assert assignment.train["split"].tolist() == ["train", "train"]
    assert assignment.tune["split"].tolist() == ["tune"]
    assert assignment.holdout["split"].tolist() == ["holdout"]
    assert assignment.excluded.empty
    assert assignment.report["splits"]["train"] == {"n_forecasts": 2, "n_unique_targets": 1}
    assert assignment.report["n_assigned_forecasts"] == 4
    assert assignment.report["target_overlap_between_splits"] is False
    pd.testing.assert_frame_equal(rows, original)
    json.dumps(assignment.report, allow_nan=False)


def test_target_end_labels_use_exclusive_start_and_inclusive_end_boundaries():
    rows = pd.DataFrame([
        forecast_row("2025-11-30T18:00Z", "2025-11-30T19:00Z"),
        forecast_row("2025-11-30T18:00Z", "2025-11-30T20:00Z"),
        forecast_row("2025-12-29T18:00Z", "2025-12-30T19:00Z"),
        forecast_row("2025-12-29T18:00Z", "2025-12-30T20:00Z"),
        forecast_row("2026-01-13T18:00Z", "2026-01-14T19:00Z"),
        forecast_row("2026-01-29T18:00Z", "2026-01-31T18:00Z"),
    ])

    assignment = assign_splits(rows)

    assert assignment.train["valid_time"].tolist() == [
        pd.Timestamp("2025-11-30T20:00Z"), pd.Timestamp("2025-12-30T19:00Z"),
    ]
    assert len(assignment.tune) == len(assignment.holdout) == 1
    assert assignment.report["exclusions"] == {"outside_declared_windows": 2}


def test_availability_after_each_stage_cutoff_is_explicitly_excluded():
    rows = pd.DataFrame([
        forecast_row("2025-11-30T18:00Z", "2025-11-30T20:00Z",
                     available_at=pd.Timestamp("2025-12-30T19:00:01Z")),
        forecast_row("2025-12-31T18:00Z", "2025-12-31T20:00Z",
                     available_at=pd.Timestamp("2026-01-14T19:00:01Z")),
        forecast_row("2026-01-15T18:00Z", "2026-01-15T20:00Z",
                     available_at=pd.Timestamp("2026-01-31T18:00:01Z")),
    ])

    assignment = assign_splits(rows)

    assert assignment.train.empty and assignment.tune.empty and assignment.holdout.empty
    assert assignment.report["exclusions"] == {"available_after_split_cutoff": 3}


def test_truth_and_availability_gaps_are_reported_without_invented_targets():
    rows = pd.DataFrame([
        forecast_row("2025-12-31T18:00Z", "2025-12-31T20:00Z", target=None),
        forecast_row("2025-12-31T18:00Z", "2025-12-31T21:00Z", available_at=pd.NaT),
    ])

    assignment = assign_splits(rows)

    assert assignment.tune.empty
    assert assignment.report["exclusions"] == {"missing_truth": 1, "missing_available_at": 1}


def test_undeclared_origin_is_not_added_to_a_split_even_when_target_is_in_window():
    rows = pd.DataFrame([
        forecast_row("2026-01-14T18:00Z", "2026-01-14T19:00Z"),
        forecast_row("2026-01-01T17:00Z", "2026-01-01T20:00Z"),
    ])

    assignment = assign_splits(rows)

    assert assignment.report["exclusions"] == {"outside_declared_windows": 2}


def test_selection_cutoff_cannot_reach_the_first_holdout_origin():
    with pytest.raises(EvaluationError, match=r"^INVALID_SPLIT"):
        SplitConfig(selection_cutoff="2026-01-15T18:00Z")


def test_target_cannot_be_recorded_available_before_its_hour_finishes():
    rows = pd.DataFrame([
        forecast_row("2025-12-31T18:00Z", "2025-12-31T20:00Z",
                     available_at=pd.Timestamp("2025-12-31T19:59Z")),
    ])

    with pytest.raises(EvaluationError, match=r"^INVALID_AVAILABILITY"):
        assign_splits(rows)


def test_mae_rmse_are_in_target_units_and_count_forecasts_separately_from_unique_truth():
    rows = example_forecasts()
    original = rows.copy(deep=True)

    report = evaluate(rows)

    assert report["status"] == "ok"
    assert report["target_unit"] == "normalized_power"
    assert report["overall"]["mae"] == pytest.approx(0.2)
    assert report["overall"]["rmse"] == pytest.approx(math.sqrt(0.2 / 3))
    assert report["n_forecasts"] == 3
    assert report["n_unique_targets"] == 2
    assert report["by_turbine"]["turbine_1"]["mae"] == pytest.approx(0.3)
    assert report["by_turbine"]["turbine_2"]["mae"] == 0.0
    assert report["by_lead_group"]["1-24"]["mae"] == pytest.approx(0.1)
    assert report["by_lead_group"]["25-48"]["mae"] == pytest.approx(0.4)
    assert report["by_turbine_and_lead_group"]["turbine_2"]["25-48"]["mae"] is None
    assert "accuracy" not in report
    json.dumps(report, allow_nan=False)
    pd.testing.assert_frame_equal(rows, original)


def test_unique_observed_table_can_join_multiple_forecast_origins_without_row_multiplication():
    rows = example_forecasts()
    truth = rows[["turbine_id", "valid_time", "normalized_power"]].drop_duplicates()

    report = evaluate(rows.drop(columns="normalized_power"), observed=truth)

    assert report["coverage"]["n_input_forecasts"] == 3
    assert report["n_forecasts"] == 3
    assert report["n_unique_targets"] == 2


def test_duplicate_truth_is_rejected_even_when_values_agree():
    rows = example_forecasts()
    truth = rows[["turbine_id", "valid_time", "normalized_power"]]

    with pytest.raises(EvaluationError, match=r"^DUPLICATE_TRUTH"):
        evaluate(rows.drop(columns="normalized_power"), observed=truth)


def test_duplicate_forecast_key_is_rejected_without_silently_averaging_predictions():
    rows = example_forecasts()
    rows = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)

    with pytest.raises(EvaluationError, match=r"^DUPLICATE_FORECAST"):
        evaluate(rows)


def test_missing_truth_and_nonfinite_truth_are_counted_separately():
    rows = example_forecasts()
    rows.loc[0, "normalized_power"] = float("nan")
    rows.loc[1, "normalized_power"] = float("inf")

    report = evaluate(rows)

    assert report["status"] == "partial_truth"
    assert report["n_forecasts"] == 1
    assert report["exclusions"]["missing_truth"] == 1
    assert report["exclusions"]["nonfinite_truth"] == 1
    assert report["coverage"]["n_excluded_forecasts"] == 2
    assert report["overall"]["mae"] == 0.0


@pytest.mark.parametrize("invalid_prediction", [float("nan"), float("inf"), True, 1 + 2j])
def test_invalid_predictions_are_exposed_and_cannot_be_selected(invalid_prediction):
    rows = example_forecasts()
    rows["prediction"] = rows["prediction"].astype(object)
    rows.loc[0, "prediction"] = invalid_prediction

    report = evaluate(rows)

    assert report["status"] == "invalid_predictions"
    assert report["eligible_for_selection"] is False
    assert report["exclusions"]["invalid_predictions"] == 1
    assert report["coverage"]["n_input_forecasts"] == 3
    assert report["coverage"]["n_scored_forecasts"] == 2
    json.dumps(report, allow_nan=False)


def test_no_truth_means_no_claimed_accuracy_or_zero_error():
    rows = example_forecasts()
    rows["normalized_power"] = float("nan")

    report = evaluate(rows)

    assert report["status"] == "no_ground_truth"
    assert report["overall"]["mae"] is None
    assert report["overall"]["rmse"] is None
    assert report["eligible_for_selection"] is False


def test_holdout_report_cannot_be_used_for_model_selection():
    rows = example_forecasts()
    rows["split"] = "holdout"

    report = evaluate(rows)

    assert report["overall"]["mae"] is not None
    assert report["eligible_for_selection"] is False
    assert report["supplied_splits"] == ["holdout"]


@pytest.mark.parametrize("column", ["issue_time", "valid_time"])
def test_naive_forecast_times_are_rejected(column):
    rows = example_forecasts()
    rows[column] = rows[column].dt.tz_localize(None)

    with pytest.raises(EvaluationError, match=r"^NAIVE_TIMESTAMP"):
        evaluate(rows)


def test_weather_lead_is_not_mistaken_for_forecast_lead():
    rows = example_forecasts()
    rows["lead_hours"] = [30, 54, 7]

    with pytest.raises(EvaluationError, match=r"^INVALID_HORIZON"):
        evaluate(rows)


def test_hour_49_is_outside_the_supported_forecast_horizon():
    rows = pd.DataFrame([forecast_row("2026-01-01T00:00Z", "2026-01-03T01:00Z")])

    with pytest.raises(EvaluationError, match=r"^INVALID_HORIZON"):
        evaluate(rows)


def test_declared_24_hour_forecast_cannot_contain_a_48_hour_prediction():
    rows = example_forecasts()
    rows["horizon_hours"] = 24

    with pytest.raises(EvaluationError, match=r"^INVALID_HORIZON"):
        evaluate(rows)


def test_evaluation_does_not_relabel_megawatts_as_normalized_power():
    rows = example_forecasts()
    rows["target_unit"] = "MW"

    with pytest.raises(EvaluationError, match=r"^INVALID_TARGET_UNIT"):
        evaluate(rows)


def test_model_cutoff_is_checked_against_every_origin():
    rows = example_forecasts()
    ensure_model_cutoff("2025-12-31T00:00Z", rows)

    with pytest.raises(EvaluationError, match=r"^MODEL_TRAINED_AFTER_ISSUE"):
        ensure_model_cutoff("2025-12-31T00:00:01Z", rows)
