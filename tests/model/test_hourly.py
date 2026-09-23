import pandas as pd
import pytest

from wind_agent.data import DataValidationError
from wind_agent.data.hourly import HistorySemantics, prepare_hourly


FEATURES = ["normalized_power", "observed_wind_speed_ms", "observed_temperature_c"]
DEFAULT_CUTOFF = pd.Timestamp("2026-01-31T18:00:00Z")


def semantics(**overrides):
    values = {
        "history_timezone": "Etc/GMT-5",
        "source_timestamp_convention": "interval_start",
        "target_reporting_delay_seconds": 0,
        "assumptions": ("Explicit provisional assumption",),
    }
    values.update(overrides)
    return HistorySemantics(**values)


def source_history(timestamps, *, turbine="turbine_1", power=None, wind=None, temperature=None):
    timestamps = pd.DatetimeIndex(timestamps)
    count = len(timestamps)
    return pd.DataFrame({
        "turbine_id": [turbine] * count,
        "source_row_id": [str(index + 1) for index in range(count)],
        "source_timestamp": timestamps,
        "observed_wind_speed_ms": [8.0] * count if wind is None else wind,
        "normalized_power": [0.5] * count if power is None else power,
        "observed_temperature_c": [-2.0] * count if temperature is None else temperature,
    })


def test_six_complete_slots_produce_hourly_means_with_explicit_utc_boundaries():
    history = source_history(
        pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"),
        power=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        wind=[2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
        temperature=[-10.0, -8.0, -6.0, -4.0, -2.0, 0.0],
    )

    hourly = prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)

    assert len(hourly.frame) == len(hourly.training_frame) == 1
    row = hourly.frame.iloc[0]
    assert row["interval_start"] == pd.Timestamp("2026-01-01T19:00:00Z")
    assert row["valid_time"] == pd.Timestamp("2026-01-01T20:00:00Z")
    assert row["available_at"] == row["valid_time"]
    assert str(hourly.frame["valid_time"].dt.tz) == "UTC"
    assert row["sample_count"] == 6
    assert bool(row["is_complete"])
    assert bool(row["eligible_for_training"])
    assert row["normalized_power"] == pytest.approx(0.5)
    assert row["observed_wind_speed_ms"] == pytest.approx(7.0)
    assert row["observed_temperature_c"] == pytest.approx(-5.0)


@pytest.mark.parametrize("partial_count", [1, 2, 3, 4, 5])
def test_real_zero_missing_hour_and_partial_hour_remain_distinct(partial_count):
    observed_times = (
        list(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
        + list(pd.date_range("2026-01-02 02:00:00", periods=partial_count, freq="10min"))
        + list(pd.date_range("2026-01-02 03:00:00", periods=6, freq="10min"))
    )
    history = source_history(
        observed_times, power=[0.0] * 6 + [0.7] * partial_count + [1.0] * 6,
    )

    hourly = prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)

    assert hourly.frame["sample_count"].tolist() == [6, 0, partial_count, 6]
    assert hourly.frame["is_complete"].tolist() == [True, False, False, True]
    assert hourly.frame["eligible_for_training"].tolist() == [True, False, False, True]
    assert hourly.frame.iloc[0]["normalized_power"] == 0.0
    assert hourly.frame.iloc[3]["normalized_power"] == 1.0
    assert hourly.frame.iloc[1:3][FEATURES].isna().all().all()
    assert hourly.training_frame["normalized_power"].tolist() == [0.0, 1.0]


def test_start_and_end_source_labels_describe_the_same_hour_at_midnight_boundary():
    starts = pd.date_range("2026-01-31 23:00:00", periods=6, freq="10min")
    start_history = source_history(starts)
    end_history = source_history(starts + pd.Timedelta(minutes=10))
    cutoff = pd.Timestamp("2026-01-31T19:00:00Z")

    from_starts = prepare_hourly(
        start_history, semantics=semantics(), training_cutoff=cutoff,
    )
    from_ends = prepare_hourly(
        end_history,
        semantics=semantics(source_timestamp_convention="interval_end"),
        training_cutoff=cutoff,
    )

    pd.testing.assert_frame_equal(from_starts.frame, from_ends.frame)
    assert len(from_ends.training_frame) == 1
    row = from_ends.frame.iloc[0]
    assert row["interval_start"] == pd.Timestamp("2026-01-31T18:00:00Z")
    assert row["valid_time"] == pd.Timestamp("2026-01-31T19:00:00Z")
    assert row["sample_count"] == 6


def test_first_replay_issue_excludes_the_still_unavailable_final_january_hour():
    history = source_history(
        pd.date_range("2026-01-31 22:00:00", periods=12, freq="10min"),
        power=[0.2] * 6 + [0.8] * 6,
    )

    hourly = prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)

    assert hourly.frame["sample_count"].tolist() == [6, 6]
    assert hourly.frame["eligible_for_training"].tolist() == [True, False]
    assert hourly.training_frame["valid_time"].tolist() == [DEFAULT_CUTOFF]
    assert hourly.training_frame["normalized_power"].tolist() == pytest.approx([0.2])
    assert hourly.frame.iloc[1]["normalized_power"] == pytest.approx(0.8)


@pytest.mark.parametrize("cutoff, expected_eligible", [
    ("2026-01-01T20:14:59Z", False),
    ("2026-01-01T20:15:00Z", True),
])
def test_reporting_delay_controls_availability_including_exact_boundary(cutoff, expected_eligible):
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))

    hourly = prepare_hourly(
        history,
        semantics=semantics(target_reporting_delay_seconds=900),
        training_cutoff=pd.Timestamp(cutoff),
    )

    row = hourly.frame.iloc[0]
    assert row["valid_time"] == pd.Timestamp("2026-01-01T20:00:00Z")
    assert row["available_at"] == pd.Timestamp("2026-01-01T20:15:00Z")
    assert bool(row["is_complete"])
    assert bool(row["eligible_for_training"]) is expected_eligible
    assert len(hourly.training_frame) == int(expected_eligible)


def test_turbines_have_independent_hourly_coverage_and_source_ids():
    complete_times = pd.date_range("2026-01-02 00:00:00", periods=18, freq="10min")
    first = source_history(complete_times, turbine="turbine_1")
    second = source_history(
        list(complete_times[:6]) + list(complete_times[12:]), turbine="turbine_2",
    )
    history = pd.concat([first, second], ignore_index=True)

    hourly = prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)

    first_hours = hourly.frame[hourly.frame["turbine_id"] == "turbine_1"]
    second_hours = hourly.frame[hourly.frame["turbine_id"] == "turbine_2"]
    assert first_hours["sample_count"].tolist() == [6, 6, 6]
    assert second_hours["sample_count"].tolist() == [6, 0, 6]
    assert len(hourly.training_frame) == 5
    assert second_hours.iloc[1][FEATURES].isna().all()


def test_hourly_preparation_does_not_mutate_or_reorder_input_history():
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
    history = history.iloc[::-1].copy()
    original = history.copy(deep=True)

    prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)

    pd.testing.assert_frame_equal(history, original)


@pytest.mark.parametrize("cutoff, error_code", [
    (pd.Timestamp("2026-01-31 18:00:00"), "NAIVE_TIMESTAMP"),
    (None, "NAIVE_TIMESTAMP"),
    ("not-a-time", "INVALID_CUTOFF"),
])
def test_naive_or_invalid_cutoff_is_rejected(cutoff, error_code):
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))

    with pytest.raises(DataValidationError, match=rf"^{error_code}"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=cutoff)


def test_cutoff_after_local_january_training_period_is_rejected():
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))

    with pytest.raises(DataValidationError, match=r"^CUTOFF_AFTER_TRAINING_PERIOD"):
        prepare_hourly(
            history,
            semantics=semantics(),
            training_cutoff=pd.Timestamp("2026-01-31T19:00:01Z"),
        )


@pytest.mark.parametrize("delay", [-1, 0.5, 1.5])
def test_negative_or_fractional_reporting_delay_is_rejected(delay):
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))

    with pytest.raises(DataValidationError, match=r"^INVALID_REPORTING_DELAY"):
        prepare_hourly(
            history,
            semantics=semantics(target_reporting_delay_seconds=delay),
            training_cutoff=DEFAULT_CUTOFF,
        )


def test_unknown_timezone_is_rejected():
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))

    with pytest.raises(DataValidationError, match=r"^INVALID_TIMEZONE"):
        prepare_hourly(
            history,
            semantics=semantics(history_timezone="Unknown/Nowhere"),
            training_cutoff=DEFAULT_CUTOFF,
        )


@pytest.mark.parametrize("history_timezone, local_time", [
    ("Asia/Almaty", "2024-02-29 23:10:00"),
    ("America/New_York", "2025-03-09 02:10:00"),
])
def test_ambiguous_or_nonexistent_local_time_is_not_guessed(history_timezone, local_time):
    history = source_history([pd.Timestamp(local_time)])

    with pytest.raises(DataValidationError, match=r"^AMBIGUOUS_SOURCE_TIME"):
        prepare_hourly(
            history,
            semantics=semantics(history_timezone=history_timezone),
            training_cutoff=DEFAULT_CUTOFF,
        )


@pytest.mark.parametrize("column", ["source_timestamp", "normalized_power"])
def test_missing_required_history_column_is_rejected(column):
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
    history = history.drop(columns=column)

    with pytest.raises(DataValidationError, match=r"^INVALID_HISTORY"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)


def test_empty_history_is_rejected():
    history = source_history(pd.DatetimeIndex([]))

    with pytest.raises(DataValidationError, match=r"^INVALID_HISTORY"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)


def test_null_source_timestamp_is_rejected():
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
    history.loc[2, "source_timestamp"] = pd.NaT

    with pytest.raises(DataValidationError, match=r"^INVALID_HISTORY"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)


@pytest.mark.parametrize("column", FEATURES)
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_source_observations_cannot_form_a_complete_hour(column, value):
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
    history.loc[2, column] = value

    with pytest.raises(DataValidationError, match=r"^INVALID_HISTORY"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)


@pytest.mark.parametrize("value", [True, 1 + 2j], ids=["boolean", "complex"])
def test_boolean_or_complex_power_is_not_silently_converted_to_real_measurement(value):
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
    history["normalized_power"] = history["normalized_power"].astype(object)
    history.loc[2, "normalized_power"] = value

    with pytest.raises(DataValidationError, match=r"^INVALID_HISTORY"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)


def test_duplicate_timestamp_with_different_row_id_is_rejected():
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
    duplicate = history.iloc[[0]].copy()
    duplicate["source_row_id"] = "999"
    history = pd.concat([history, duplicate], ignore_index=True)

    with pytest.raises(DataValidationError, match=r"^DUPLICATE_TIMESTAMP"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)


def test_off_grid_source_timestamp_is_rejected():
    history = source_history(pd.date_range("2026-01-02 00:00:00", periods=6, freq="10min"))
    history.loc[2, "source_timestamp"] += pd.Timedelta(minutes=1)

    with pytest.raises(DataValidationError, match=r"^OFF_GRID_TIMESTAMP"):
        prepare_hourly(history, semantics=semantics(), training_cutoff=DEFAULT_CUTOFF)
