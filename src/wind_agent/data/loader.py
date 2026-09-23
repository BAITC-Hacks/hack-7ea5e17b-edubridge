"""Validate the supplied SCADA CSV without inventing units or time semantics."""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


COLUMNS = {
    "ID": "source_row_id",
    "Статистическое время": "source_timestamp",
    "Средняя скорость ветра(m/s)": "observed_wind_speed_ms",
    "Нормализованная активная мощность": "normalized_power",
    "Средняя температура окружающей среды(°C)": "observed_temperature_c",
}
NUMERIC_COLUMNS = (
    "observed_wind_speed_ms", "normalized_power", "observed_temperature_c",
)
TURBINE_IDS = ("turbine_1", "turbine_2")
STEP = pd.Timedelta(minutes=10)


class DataValidationError(ValueError):
    """Source data cannot be used without correcting the reported problem."""


@dataclass
class LoadedHistory:
    frame: pd.DataFrame
    report: dict


def _fail(code: str, message: str) -> None:
    raise DataValidationError(f"{code}: {message}")


def load_history(
    path: str | Path,
    turbine_id: str,
    *,
    expected_sha256: str | None = None,
) -> LoadedHistory:
    """Read one turbine; timestamps stay naive and observations stay unfilled.

    The caller explicitly identifies the turbine. Source ID is only a row counter,
    never a join key. Optional SHA256 pins the exact original bytes. Report coverage
    is over this file's observed min/max timestamps, not an assumed task boundary.
    No timezone conversion, clipping, interpolation, aggregation or fitting occurs.
    """
    if turbine_id not in TURBINE_IDS:
        _fail("UNSUPPORTED_TURBINE", f"expected one of {TURBINE_IDS}")
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        _fail("HASH_MISMATCH", f"{path.name} differs from the expected source bytes")

    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        header = next(reader, None)
        if header is None:
            _fail("EMPTY_DATA", "CSV has no header or observations")
        if len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
            _fail("INVALID_SCHEMA", "expected the five original SCADA column names")
        records = []
        for number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                _fail("INVALID_SCHEMA", f"CSV row {number} has {len(row)} fields, expected 5")
            records.append(row)
    except (UnicodeDecodeError, csv.Error) as exc:
        _fail("INVALID_SCHEMA", f"invalid UTF-8 CSV: {exc}")
    if not records:
        _fail("EMPTY_DATA", "CSV has a header but no observations")

    frame = pd.DataFrame(records, columns=header).rename(columns=COLUMNS)
    ids = frame["source_row_id"]
    if not ids.str.fullmatch(r"[1-9][0-9]*").all():
        _fail("INVALID_SCHEMA", "ID must be a positive row-counter integer")
    # Keep the counter as text: it has no numeric role in training or joining.
    timestamps = frame["source_timestamp"]
    pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2} (?:[01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    if not timestamps.str.fullmatch(pattern).all():
        _fail("INVALID_TIMESTAMP", "source timestamps must be naive YYYY-MM-DD H:MM:SS")
    parsed = pd.to_datetime(timestamps, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    if parsed.isna().any():
        _fail("INVALID_TIMESTAMP", "source contains an invalid calendar timestamp")
    if parsed.duplicated().any():
        _fail("DUPLICATE_TIMESTAMP", "a turbine has more than one row at the same time")
    if ((parsed.dt.minute % 10 != 0) | (parsed.dt.second != 0)).any():
        _fail("OFF_GRID_TIMESTAMP", "observations must fall on the 10-minute source grid")
    frame["source_timestamp"] = parsed
    input_was_sorted = bool(parsed.is_monotonic_increasing)

    for column in NUMERIC_COLUMNS:
        values = pd.to_numeric(frame[column], errors="coerce").astype("float64")
        if not np.isfinite(values.to_numpy()).all():
            _fail("INVALID_NUMERIC", f"{column} contains missing, invalid or nonfinite values")
        frame[column] = values
    if (frame["observed_wind_speed_ms"] < 0).any():
        _fail("NEGATIVE_WIND", "wind speed cannot be negative")

    frame.insert(0, "turbine_id", turbine_id)
    frame = frame[["turbine_id", *COLUMNS.values()]]
    frame = frame.sort_values("source_timestamp", kind="stable").reset_index(drop=True)
    stamps = frame["source_timestamp"]
    expected = int((stamps.iloc[-1] - stamps.iloc[0]) / STEP) + 1
    gaps = []
    for index in stamps.diff()[lambda values: values > STEP].index:
        start = stamps.iloc[index - 1] + STEP
        end = stamps.iloc[index] - STEP
        gaps.append({
            "first_missing_timestamp": start.isoformat(),
            "last_missing_timestamp": end.isoformat(),
            "missing_slots": int((end - start) / STEP) + 1,
        })
    gaps.sort(key=lambda gap: (-gap["missing_slots"], gap["first_missing_timestamp"]))
    warnings = [
        "Source timezone, interval labels and target reporting delay are unknown.",
        "Normalization definition and rated capacities are unknown; target is not MW/MWh.",
        "Observed wind/temperature are historical measurements, not archived forecasts.",
    ]
    if gaps:
        warnings.append("Missing timestamps are reported, not inserted or filled with zero.")
    if not input_was_sorted:
        warnings.append("Rows were sorted by source timestamp; source IDs were preserved.")

    report = {
        "schema_version": "1.0",
        "turbine_id": turbine_id,
        "source_filename": path.name,
        "source_sha256": digest,
        "source_bytes": len(raw),
        "rows": len(frame),
        "timestamp_min": stamps.iloc[0].isoformat(),
        "timestamp_max": stamps.iloc[-1].isoformat(),
        "timestamp_timezone": "unknown",
        "original_timestamp_convention": "unknown",
        "source_interval_minutes": 10,
        "coverage_basis": "inclusive observed min/max; naive source labels",
        "expected_10min_slots": expected,
        "missing_10min_slots": expected - len(frame),
        "coverage_fraction": len(frame) / expected,
        "missing_spans_count": len(gaps),
        "longest_missing_spans": gaps[:10],
        "input_was_sorted": input_was_sorted,
        "duplicate_source_ids": int(frame["source_row_id"].duplicated().sum()),
        "zero_power_rows": int((frame["normalized_power"] == 0).sum()),
        "power_outside_observed_reference_0_1": int(
            ((frame["normalized_power"] < 0) | (frame["normalized_power"] > 1)).sum()
        ),
        "numeric_summary": {
            column: {"min": float(frame[column].min()), "max": float(frame[column].max())}
            for column in NUMERIC_COLUMNS
        },
        "scope": "Source audit only; no training selection, hourly conversion or evaluation.",
        "warnings": warnings,
    }
    return LoadedHistory(frame=frame, report=report)
