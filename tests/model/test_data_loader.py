import csv
import hashlib
import json

import pandas as pd
import pytest

from wind_agent.data import DataValidationError, load_history


HEADERS = [
    "ID",
    "Статистическое время",
    "Средняя скорость ветра(m/s)",
    "Нормализованная активная мощность",
    "Средняя температура окружающей среды(°C)",
]
OUTPUT_COLUMNS = [
    "turbine_id",
    "source_row_id",
    "source_timestamp",
    "observed_wind_speed_ms",
    "normalized_power",
    "observed_temperature_c",
]


def write_source(tmp_path, rows, *, headers=None, name="history.csv"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADERS if headers is None else headers)
        writer.writerows(rows)
    return path


def source_row(
    row_id="1",
    timestamp="2026-01-01 00:00:00",
    wind="5.2",
    power="0.4",
    temperature="-3.1",
):
    return [row_id, timestamp, wind, power, temperature]


def test_zero_output_is_an_observation_and_missing_timestamp_is_not_filled(tmp_path):
    path = write_source(tmp_path, [
        source_row("1", "2026-01-01 00:00:00", power="0"),
        source_row("3", "2026-01-01 00:20:00", power="0.5"),
    ])
    original_bytes = path.read_bytes()
    digest = hashlib.sha256(original_bytes).hexdigest()

    loaded = load_history(path, "turbine_1", expected_sha256=digest)

    assert list(loaded.frame.columns) == OUTPUT_COLUMNS
    assert loaded.frame["source_timestamp"].tolist() == [
        pd.Timestamp("2026-01-01 00:00:00"),
        pd.Timestamp("2026-01-01 00:20:00"),
    ]
    assert loaded.frame["source_timestamp"].dt.tz is None
    assert loaded.frame["normalized_power"].tolist() == [0.0, 0.5]
    assert loaded.report["rows"] == 2
    assert loaded.report["expected_10min_slots"] == 3
    assert loaded.report["missing_10min_slots"] == 1
    assert loaded.report["zero_power_rows"] == 1
    assert loaded.report["input_was_sorted"] is True
    assert loaded.report["source_sha256"] == digest
    assert loaded.report["timestamp_timezone"] == "unknown"
    assert loaded.report["original_timestamp_convention"] == "unknown"
    assert loaded.report["duplicate_source_ids"] == 0
    assert loaded.report["numeric_summary"]
    assert loaded.report["longest_missing_spans"]
    json.dumps(loaded.report, allow_nan=False)
    assert path.read_bytes() == original_bytes


def test_unsorted_observations_keep_their_original_ids_and_values(tmp_path):
    path = write_source(tmp_path, [
        source_row("30", "2026-01-01 00:20:00", power="0.3"),
        source_row("10", "2026-01-01 00:00:00", power="0.1"),
        source_row("20", "2026-01-01 00:10:00", power="0.2"),
    ])

    loaded = load_history(path, "turbine_2")

    assert loaded.frame["source_timestamp"].is_monotonic_increasing
    assert loaded.frame["source_row_id"].astype(str).tolist() == ["10", "20", "30"]
    assert loaded.frame["normalized_power"].tolist() == [0.1, 0.2, 0.3]
    assert loaded.frame["turbine_id"].tolist() == ["turbine_2"] * 3
    assert loaded.report["input_was_sorted"] is False
    assert loaded.report["missing_10min_slots"] == 0


def test_unpadded_hour_and_missing_final_newline_are_valid(tmp_path):
    path = write_source(tmp_path, [source_row(timestamp="2026-01-01 9:10:00")])
    source_bytes = path.read_bytes().rstrip(b"\r\n")
    path.write_bytes(source_bytes)

    loaded = load_history(path, "turbine_1")

    assert loaded.frame["source_timestamp"].tolist() == [pd.Timestamp("2026-01-01 09:10:00")]
    assert loaded.frame["source_timestamp"].dt.tz is None
    assert loaded.report["rows"] == 1
    assert loaded.report["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert path.read_bytes() == source_bytes


def test_reordered_source_headers_preserve_meanings_and_canonical_output_order(tmp_path):
    order = [4, 0, 3, 1, 2]
    row = source_row(row_id="71", wind="8.5", power="0.63", temperature="-12.4")
    path = write_source(
        tmp_path,
        [[row[index] for index in order]],
        headers=[HEADERS[index] for index in order],
    )

    loaded = load_history(path, "turbine_2")

    assert list(loaded.frame.columns) == OUTPUT_COLUMNS
    assert loaded.frame["source_row_id"].astype(str).tolist() == ["71"]
    assert loaded.frame["source_timestamp"].tolist() == [pd.Timestamp("2026-01-01 00:00:00")]
    assert loaded.frame["observed_wind_speed_ms"].tolist() == [8.5]
    assert loaded.frame["normalized_power"].tolist() == [0.63]
    assert loaded.frame["observed_temperature_c"].tolist() == [-12.4]


def test_identical_source_ids_do_not_merge_different_turbines(tmp_path):
    first = write_source(tmp_path, [source_row(power="0.2")], name="first.csv")
    second = write_source(tmp_path, [source_row(power="0.8")], name="second.csv")

    first_loaded = load_history(first, "turbine_1")
    second_loaded = load_history(second, "turbine_2")
    combined = pd.concat([first_loaded.frame, second_loaded.frame], ignore_index=True)

    assert len(combined) == 2
    assert combined["source_row_id"].astype(str).tolist() == ["1", "1"]
    assert combined.set_index("turbine_id")["normalized_power"].to_dict() == {
        "turbine_1": 0.2,
        "turbine_2": 0.8,
    }


def test_repeated_row_id_is_reported_but_is_not_a_duplicate_timestamp(tmp_path):
    path = write_source(tmp_path, [
        source_row("7", "2026-01-01 00:00:00"),
        source_row("7", "2026-01-01 00:10:00"),
    ])

    loaded = load_history(path, "turbine_1")

    assert len(loaded.frame) == 2
    assert loaded.report["duplicate_source_ids"] > 0


def test_duplicate_timestamp_is_rejected_even_with_different_row_ids(tmp_path):
    path = write_source(tmp_path, [source_row("1"), source_row("2", power="0.7")])

    with pytest.raises(DataValidationError, match=r"^DUPLICATE_TIMESTAMP"):
        load_history(path, "turbine_1")


@pytest.mark.parametrize("headers", [
    HEADERS[:-1],
    [*HEADERS, "extra"],
    ["unexpected ID", *HEADERS[1:]],
    [*HEADERS[:-1], HEADERS[-2]],
])
def test_missing_extra_renamed_or_duplicate_header_is_rejected(tmp_path, headers):
    path = write_source(tmp_path, [source_row()], headers=headers)

    with pytest.raises(DataValidationError, match=r"^INVALID_SCHEMA"):
        load_history(path, "turbine_1")


@pytest.mark.parametrize("row", [source_row()[:-1], [*source_row(), "extra"]])
def test_malformed_row_width_is_rejected(tmp_path, row):
    path = write_source(tmp_path, [row])

    with pytest.raises(DataValidationError, match=r"^INVALID_SCHEMA"):
        load_history(path, "turbine_1")


@pytest.mark.parametrize("timestamp", [
    "not-a-date",
    "2026-02-30 00:00:00",
    "2026-01-01 00:09:60",
    "",
])
def test_invalid_timestamp_is_rejected(tmp_path, timestamp):
    path = write_source(tmp_path, [source_row(timestamp=timestamp)])

    with pytest.raises(DataValidationError, match=r"^INVALID_TIMESTAMP"):
        load_history(path, "turbine_1")


@pytest.mark.parametrize("timestamp", ["2026-01-01 00:05:00", "2026-01-01 00:10:01"])
def test_timestamp_outside_the_ten_minute_grid_is_rejected(tmp_path, timestamp):
    path = write_source(tmp_path, [source_row(timestamp=timestamp)])

    with pytest.raises(DataValidationError, match=r"^OFF_GRID_TIMESTAMP"):
        load_history(path, "turbine_1")


@pytest.mark.parametrize("column_index", [2, 3, 4], ids=["wind", "power", "temperature"])
@pytest.mark.parametrize("token", ["invalid", "NaN", "Inf", "-Inf", ""])
def test_invalid_or_nonfinite_numeric_value_is_rejected(tmp_path, column_index, token):
    row = source_row()
    row[column_index] = token
    path = write_source(tmp_path, [row])

    with pytest.raises(DataValidationError, match=r"^INVALID_NUMERIC"):
        load_history(path, "turbine_1")


def test_negative_wind_is_rejected(tmp_path):
    path = write_source(tmp_path, [source_row(wind="-0.1")])

    with pytest.raises(DataValidationError, match=r"^NEGATIVE_WIND"):
        load_history(path, "turbine_1")


def test_unknown_normalization_does_not_justify_clipping_power(tmp_path):
    path = write_source(tmp_path, [
        source_row("1", "2026-01-01 00:00:00", power="-0.25"),
        source_row("2", "2026-01-01 00:10:00", power="1.4"),
    ])

    loaded = load_history(path, "turbine_1")

    assert loaded.frame["normalized_power"].tolist() == [-0.25, 1.4]
    assert loaded.report["rows"] == 2


def test_source_hash_mismatch_is_rejected(tmp_path):
    path = write_source(tmp_path, [source_row()])

    with pytest.raises(DataValidationError, match=r"^HASH_MISMATCH"):
        load_history(path, "turbine_1", expected_sha256="0" * 64)


def test_header_without_observations_is_rejected(tmp_path):
    path = write_source(tmp_path, [])

    with pytest.raises(DataValidationError, match=r"^EMPTY_DATA"):
        load_history(path, "turbine_1")


@pytest.mark.parametrize("turbine_id", ["turbine_3", "1", ""])
def test_unsupported_turbine_is_rejected(tmp_path, turbine_id):
    path = write_source(tmp_path, [source_row()])

    with pytest.raises(DataValidationError, match=r"^UNSUPPORTED_TURBINE"):
        load_history(path, turbine_id)


def test_validation_error_is_compatible_with_value_error_handlers():
    assert issubclass(DataValidationError, ValueError)
