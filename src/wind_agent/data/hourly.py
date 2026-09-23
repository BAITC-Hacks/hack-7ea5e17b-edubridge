"""Complete hourly targets with explicit source-clock assumptions and cutoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from .loader import NUMERIC_COLUMNS, STEP, TURBINE_IDS, DataValidationError


def _fail(code: str, message: str) -> None:
    raise DataValidationError(f"{code}: {message}")


@dataclass(frozen=True)
class HistorySemantics:
    history_timezone: str
    source_timestamp_convention: str
    target_reporting_delay_seconds: int
    assumptions: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.history_timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            _fail("INVALID_TIMEZONE", "history_timezone must be an installed IANA timezone")
        if self.source_timestamp_convention not in ("interval_start", "interval_end"):
            _fail("INVALID_SOURCE_CONVENTION", "choose interval_start or interval_end explicitly")
        delay = self.target_reporting_delay_seconds
        if type(delay) is not int or delay < 0:
            _fail("INVALID_REPORTING_DELAY", "reporting delay must be nonnegative integer seconds")
        if (
            not isinstance(self.assumptions, (list, tuple)) or not self.assumptions
            or any(not isinstance(item, str) or not item.strip() for item in self.assumptions)
        ):
            _fail("MISSING_ASSUMPTIONS", "record the unresolved source-clock assumptions")
        object.__setattr__(self, "assumptions", tuple(self.assumptions))

    def as_metadata(self) -> dict:
        return {
            "history_timezone": self.history_timezone,
            "source_timestamp_convention": self.source_timestamp_convention,
            "target_reporting_delay_seconds": self.target_reporting_delay_seconds,
            "source_semantics_status": "assumed",
            "timestamp_convention": "hour_end",
            "source_interval_minutes": 10,
            "source_value_convention": "ten-minute mean assumed for each interval",
            "target_unit": "normalized_power",
            "target_definition": "hourly mean normalized active power; normalization unresolved",
            "assumptions": list(self.assumptions),
        }


@dataclass
class HourlyHistory:
    frame: pd.DataFrame
    training_frame: pd.DataFrame
    report: dict


def prepare_hourly(
    history: pd.DataFrame,
    *,
    semantics: HistorySemantics,
    training_cutoff: datetime | str | pd.Timestamp,
) -> HourlyHistory:
    """Aggregate six disjoint 10-minute intervals; never fill missing targets.

    Return all hours between each turbine's observed interval bounds. Incomplete
    hours have null target/features, count 0..5 and an explicit quality flag.
    Only complete hours whose end plus declared delay is <= cutoff enter training.
    The full audit table can include post-cutoff observations; use training_frame
    for fitting. No fitted statistics or model selection occur in this function.
    """
    if not isinstance(semantics, HistorySemantics):
        _fail("INVALID_HISTORY", "explicit HistorySemantics is required")
    try:
        cutoff = pd.Timestamp(training_cutoff)
    except (ValueError, TypeError):
        _fail("INVALID_CUTOFF", "training_cutoff must be an aware timestamp")
    if pd.isna(cutoff) or cutoff.tzinfo is None:
        _fail("NAIVE_TIMESTAMP", "training_cutoff must include its timezone")
    cutoff = cutoff.tz_convert("UTC")
    february_start = pd.Timestamp("2026-02-01").tz_localize(
        semantics.history_timezone
    ).tz_convert("UTC")
    if cutoff > february_start:
        _fail("CUTOFF_AFTER_TRAINING_PERIOD", "cutoff cannot exceed February 1 in history timezone")

    required = {"turbine_id", "source_timestamp", *NUMERIC_COLUMNS}
    if (
        not isinstance(history, pd.DataFrame) or history.empty
        or not history.columns.is_unique or not required.issubset(history.columns)
    ):
        _fail("INVALID_HISTORY", "expected nonempty validated source history")
    frame = history.copy(deep=True)
    if not frame["turbine_id"].isin(TURBINE_IDS).all():
        _fail("INVALID_HISTORY", "history contains an unknown turbine")
    stamps = frame["source_timestamp"]
    if (
        not pd.api.types.is_datetime64_dtype(stamps.dtype) or stamps.isna().any()
        or stamps.dt.tz is not None
    ):
        _fail("INVALID_HISTORY", "source_timestamp must contain parsed naive source labels")
    if frame.duplicated(["turbine_id", "source_timestamp"]).any():
        _fail("DUPLICATE_TIMESTAMP", "duplicate turbine/time observations")
    if (
        (stamps.dt.minute % 10 != 0) | (stamps.dt.second != 0)
        | (stamps.dt.microsecond != 0) | (stamps.dt.nanosecond != 0)
    ).any():
        _fail("OFF_GRID_TIMESTAMP", "source timestamps must align to ten-minute intervals")
    for column in NUMERIC_COLUMNS:
        if (
            not pd.api.types.is_numeric_dtype(frame[column])
            or pd.api.types.is_bool_dtype(frame[column])
            or pd.api.types.is_complex_dtype(frame[column])
            or frame[column].isna().any()
            or not np.isfinite(frame[column].to_numpy(dtype=float)).all()
        ):
            _fail("INVALID_HISTORY", f"{column} must contain finite numeric observations")
    if (frame["observed_wind_speed_ms"] < 0).any():
        _fail("INVALID_HISTORY", "wind speed cannot be negative")

    try:
        # Reject DST/offset ambiguities instead of inferring which source row was meant.
        utc_labels = stamps.dt.tz_localize(
            semantics.history_timezone, ambiguous="raise", nonexistent="raise"
        ).dt.tz_convert("UTC")
    except Exception as exc:
        _fail("AMBIGUOUS_SOURCE_TIME", f"source clock cannot be mapped unambiguously: {exc}")
    starts = utc_labels
    if semantics.source_timestamp_convention == "interval_end":
        starts = utc_labels - STEP
    if ((starts.dt.minute % 10 != 0) | (starts.dt.second != 0)).any():
        _fail("OFF_GRID_TIMESTAMP", "localized intervals do not tile the UTC ten-minute grid")
    frame["valid_time"] = starts.dt.floor("h") + pd.Timedelta(hours=1)

    # Unique aligned 10-minute intervals guarantee that count == 6 is a full hour.
    outputs = []
    for turbine, observed in frame.groupby("turbine_id", sort=True):
        grouped = observed.groupby("valid_time", sort=True)
        means = grouped[list(NUMERIC_COLUMNS)].mean()
        counts = grouped.size()
        grid = pd.date_range(means.index.min(), means.index.max(), freq="h")
        hours = means.reindex(grid)
        hours.index.name = "valid_time"
        hours["sample_count"] = counts.reindex(grid, fill_value=0).astype(int)
        hours["is_complete"] = hours["sample_count"].eq(6)
        hours.loc[~hours["is_complete"], list(NUMERIC_COLUMNS)] = np.nan
        if not np.isfinite(hours.loc[hours["is_complete"], list(NUMERIC_COLUMNS)].to_numpy()).all():
            _fail("INVALID_HISTORY", "hourly aggregation produced nonfinite values")
        hours = hours.reset_index()
        hours.insert(0, "turbine_id", turbine)
        hours["interval_start"] = hours["valid_time"] - pd.Timedelta(hours=1)
        hours["available_at"] = hours["valid_time"] + pd.Timedelta(
            seconds=semantics.target_reporting_delay_seconds
        )
        hours["eligible_for_training"] = hours["is_complete"] & hours["available_at"].le(cutoff)
        hours["data_quality"] = np.select(
            [hours["is_complete"], hours["sample_count"].eq(0)],
            ["complete", "missing"], default="partial",
        )
        outputs.append(hours)
    result = pd.concat(outputs, ignore_index=True)
    training = result.loc[result["eligible_for_training"]].copy().reset_index(drop=True)

    summaries = []
    for turbine, hours in result.groupby("turbine_id", sort=True):
        summaries.append({
            "turbine_id": turbine,
            "hourly_rows": len(hours),
            "complete_hours": int(hours["is_complete"].sum()),
            "partial_hours": int(hours["data_quality"].eq("partial").sum()),
            "missing_hours": int(hours["data_quality"].eq("missing").sum()),
            "training_hours": int(hours["eligible_for_training"].sum()),
            "complete_hours_after_cutoff": int(
                (hours["is_complete"] & ~hours["eligible_for_training"]).sum()
            ),
            "first_interval_start": hours["interval_start"].min().isoformat(),
            "last_interval_end": hours["valid_time"].max().isoformat(),
        })
    report = {
        "schema_version": "1.0",
        "semantics": semantics.as_metadata(),
        "training_cutoff": cutoff.isoformat(),
        "runtime_versions": {
            "pandas": pd.__version__, "pytz": version("pytz"), "tzdata": version("tzdata"),
        },
        "coverage_basis": "UTC hourly grid over each turbine's observed interval extent",
        "eligibility_rule": "six observations and valid_time + reporting_delay <= training_cutoff",
        "files": summaries,
        "warnings": [
            "Source-clock semantics are assumptions, not organizer confirmations.",
            "available_at is policy-derived; actual historical SCADA publication is unknown.",
            "Hourly observed weather is diagnostic history, not a forecast input.",
            "All-hours table includes incomplete and post-cutoff rows; fit only training_frame.",
        ],
        "scope": "Hourly preparation only; no fitting, selection or forecast accuracy measurement.",
    }
    return HourlyHistory(frame=result, training_frame=training, report=report)
