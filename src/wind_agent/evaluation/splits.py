"""Predeclared chronological windows for archived-forecast evaluation.

Targets are hourly means labelled by their interval END. Repeated targets from
different forecast origins may stay within one split, never across splits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


FORECAST_KEY = ["issue_time", "turbine_id", "valid_time"]
TARGET_KEY = ["turbine_id", "valid_time"]


class EvaluationError(ValueError):
    """An evaluation would violate its declared data contract."""


def _fail(code: str, message: str) -> None:
    raise EvaluationError(f"{code}: {message}")


def _aware(value: Any, name: str) -> pd.Timestamp:
    if not isinstance(value, (str, datetime, pd.Timestamp)):
        _fail("INVALID_TIMESTAMP", f"{name} must be an aware timestamp")
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        _fail("INVALID_TIMESTAMP", f"{name}: {error}")
    if pd.isna(stamp) or stamp.tzinfo is None:
        _fail("NAIVE_TIMESTAMP", f"{name} must include a timezone")
    return stamp.tz_convert("UTC")


def _utc_column(values: pd.Series, name: str, *, allow_missing: bool = False) -> pd.Series:
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        if not allow_missing and values.isna().any():
            _fail("INVALID_TIMESTAMP", f"{name} contains missing timestamps")
        return values.dt.tz_convert("UTC")
    parsed = []
    for value in values:
        if allow_missing and pd.isna(value):
            parsed.append(pd.NaT)
        else:
            parsed.append(_aware(value, name))
    return pd.Series(pd.to_datetime(parsed, utc=True), index=values.index, name=values.name)


def _numeric(values: pd.Series) -> pd.Series:
    # Booleans and complex values are not real-valued measurements, even when
    # pandas could coerce them to an apparently usable float.
    rejected = values.map(lambda value: isinstance(value, (bool, complex, np.bool_, np.complexfloating)))
    clean = values.mask(rejected, np.nan)
    return pd.to_numeric(clean, errors="coerce").astype(float)


def _forecast_frame(frame: pd.DataFrame, *, horizon_hours: int = 48) -> pd.DataFrame:
    required = set(FORECAST_KEY)
    if not isinstance(frame, pd.DataFrame) or not required <= set(frame.columns):
        _fail("INVALID_SCHEMA", f"forecast rows require {sorted(required)}")
    result = frame.copy(deep=True)
    if result["turbine_id"].map(lambda value: not isinstance(value, str) or not value.strip()).any():
        _fail("INVALID_SCHEMA", "turbine_id must be a nonempty string")
    for name in ("issue_time", "valid_time"):
        result[name] = _utc_column(result[name], name)
        if ((result[name].dt.minute != 0) | (result[name].dt.second != 0)
                | (result[name].dt.microsecond != 0) | (result[name].dt.nanosecond != 0)).any():
            _fail("INVALID_HORIZON", f"{name} must be aligned to an exact hour")
    if result.duplicated(FORECAST_KEY).any():
        _fail("DUPLICATE_FORECAST", "forecast origin/turbine/valid-time keys must be unique")
    leads = (result["valid_time"] - result["issue_time"]).dt.total_seconds() / 3600
    if ((leads < 1) | (leads > horizon_hours) | (leads % 1 != 0)).any():
        _fail("INVALID_HORIZON", f"forecast leads must be integers 1..{horizon_hours}")
    if "lead_hours" in result:
        supplied = _numeric(result["lead_hours"])
        if (supplied != leads).any():
            _fail("INVALID_HORIZON", "lead_hours must be measured from energy issue_time, not weather run")
    result["lead_hours"] = leads.astype("int64")
    if "horizon_hours" in result:
        declared = _numeric(result["horizon_hours"])
        if (~declared.isin([24, 48]) | (leads > declared)).any():
            _fail("INVALID_HORIZON", "row exceeds its declared 24/48-hour horizon")
    return result


@dataclass(frozen=True)
class SplitWindow:
    target_start: pd.Timestamp  # Exclusive: target is labelled by its end.
    target_end: pd.Timestamp  # Inclusive.
    issue_start: pd.Timestamp  # Inclusive.
    issue_end: pd.Timestamp  # Inclusive.
    availability_cutoff: pd.Timestamp

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name).isoformat() for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class SplitConfig:
    """Fixed experiment design declared before comparing candidate models.

    The fixed UTC+5 timezone is a provisional SCADA assumption, not an organizer
    confirmation. Cutoffs bound all information used by the respective stage.
    """

    history_timezone: str = "Etc/GMT-5"
    horizon_hours: int = 48
    train_cutoff: Any = "2025-12-30T19:00:00Z"
    selection_cutoff: Any = "2026-01-14T19:00:00Z"
    first_forecast_cutoff: Any = "2026-01-31T18:00:00Z"

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.history_timezone)
        except (TypeError, ValueError, KeyError):
            _fail("INVALID_TIMEZONE", "history_timezone must be an installed IANA timezone")
        if type(self.horizon_hours) is not int or self.horizon_hours not in (24, 48):
            _fail("INVALID_HORIZON", "horizon_hours must be 24 or 48")
        for name in ("train_cutoff", "selection_cutoff", "first_forecast_cutoff"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))
        windows = self.windows
        if self.train_cutoff > windows["tune"].issue_start:
            _fail("INVALID_SPLIT", "training information is later than the first tuning origin")
        if self.selection_cutoff >= windows["holdout"].issue_start:
            _fail("INVALID_SPLIT", "selection must finish before the first holdout origin")
        if not self.train_cutoff <= self.selection_cutoff <= self.first_forecast_cutoff:
            _fail("INVALID_SPLIT", "cutoffs must be chronological")
        january_end = self._local("2026-02-01 00:00:00")
        if self.first_forecast_cutoff > january_end:
            _fail("INVALID_SPLIT", "production training cannot include February observations")

    def _local(self, value: str) -> pd.Timestamp:
        return pd.Timestamp(value).tz_localize(self.history_timezone).tz_convert("UTC")

    @property
    def windows(self) -> dict[str, SplitWindow]:
        return {
            "train": SplitWindow(
                self._local("2025-12-01 00:00:00"), self._local("2025-12-31 00:00:00"),
                pd.Timestamp("2025-11-30T18:00:00Z"), pd.Timestamp("2025-12-29T18:00:00Z"),
                self.train_cutoff,
            ),
            "tune": SplitWindow(
                self._local("2026-01-01 00:00:00"), self._local("2026-01-15 00:00:00"),
                pd.Timestamp("2025-12-31T18:00:00Z"), pd.Timestamp("2026-01-13T18:00:00Z"),
                self.selection_cutoff,
            ),
            "holdout": SplitWindow(
                self._local("2026-01-16 00:00:00"), self._local("2026-01-31 23:00:00"),
                pd.Timestamp("2026-01-15T18:00:00Z"), pd.Timestamp("2026-01-29T18:00:00Z"),
                self.first_forecast_cutoff,
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "history_timezone": self.history_timezone,
            "timezone_status": "explicit_provisional_assumption",
            "horizon_hours": self.horizon_hours,
            "target_time_convention": "hour_end",
            "target_start_exclusive": True,
            "target_end_inclusive": True,
            "origin_schedule": "daily at 18:00 UTC",
            "train_cutoff": self.train_cutoff.isoformat(),
            "selection_cutoff": self.selection_cutoff.isoformat(),
            "first_forecast_cutoff": self.first_forecast_cutoff.isoformat(),
            "windows": {name: window.to_dict() for name, window in self.windows.items()},
            "selection_uses_holdout": False,
        }


@dataclass
class SplitAssignment:
    train: pd.DataFrame
    tune: pd.DataFrame
    holdout: pd.DataFrame
    excluded: pd.DataFrame
    report: dict[str, Any]


def assign_splits(frame: pd.DataFrame, config: SplitConfig | None = None) -> SplitAssignment:
    """Filter whole vintage rows into fixed windows; never shuffle or interpolate."""
    config = config or SplitConfig()
    if not isinstance(config, SplitConfig):
        _fail("INVALID_SPLIT", "config must be a SplitConfig")
    work = _forecast_frame(frame, horizon_hours=config.horizon_hours).reset_index(drop=True)
    if not {"normalized_power", "available_at"} <= set(work.columns):
        _fail("INVALID_SCHEMA", "split assignment requires normalized_power and available_at")
    work["available_at"] = _utc_column(work["available_at"], "available_at", allow_missing=True)
    if (work["available_at"] < work["valid_time"]).any():
        _fail("INVALID_AVAILABILITY", "an hourly target cannot be available before its interval ends")
    target = _numeric(work["normalized_power"])
    target_missing = work["normalized_power"].isna()
    target_invalid = ~np.isfinite(target)
    work["normalized_power"] = target
    labels = pd.Series("", index=work.index, dtype=object)
    reasons = pd.Series("outside_declared_windows", index=work.index, dtype=object)
    frames = {}
    for name, window in config.windows.items():
        in_window = (
            (work["valid_time"] > window.target_start)
            & (work["valid_time"] <= window.target_end)
            & (work["issue_time"] >= window.issue_start)
            & (work["issue_time"] <= window.issue_end)
            & (work["issue_time"].dt.hour == 18)
        )
        late = work["available_at"] > window.availability_cutoff
        unavailable = work["available_at"].isna()
        reasons.loc[in_window & late] = "available_after_split_cutoff"
        reasons.loc[in_window & unavailable] = "missing_available_at"
        reasons.loc[in_window & target_invalid] = "nonfinite_target"
        reasons.loc[in_window & target_missing] = "missing_truth"
        eligible = in_window & ~late & ~unavailable & ~target_invalid
        if (labels.loc[eligible] != "").any():
            _fail("INVALID_SPLIT", "a forecast was assigned to more than one split")
        labels.loc[eligible] = name
        selected = work.loc[eligible].copy()
        selected["split"] = name
        frames[name] = selected.sort_values(FORECAST_KEY).reset_index(drop=True)

    target_sets = {name: set(map(tuple, rows[TARGET_KEY].drop_duplicates().to_numpy()))
                   for name, rows in frames.items()}
    if any(target_sets[left] & target_sets[right]
           for left, right in (("train", "tune"), ("train", "holdout"), ("tune", "holdout"))):
        _fail("TARGET_SPLIT_LEAKAGE", "one target appears in different chronological splits")
    excluded = work.loc[labels == ""].copy()
    excluded["exclusion_reason"] = reasons.loc[excluded.index]
    report = {
        "split_config": config.to_dict(),
        "n_input_forecasts": int(len(work)),
        "n_assigned_forecasts": int(sum(len(rows) for rows in frames.values())),
        "n_excluded_forecasts": int(len(excluded)),
        "exclusions": {str(reason): int(count) for reason, count in
                       excluded["exclusion_reason"].value_counts().items()},
        "splits": {name: {"n_forecasts": int(len(rows)),
                          "n_unique_targets": int(len(target_sets[name]))}
                   for name, rows in frames.items()},
        "target_overlap_between_splits": False,
    }
    return SplitAssignment(**frames, excluded=excluded.reset_index(drop=True), report=report)


def ensure_model_cutoff(training_cutoff: Any, rows: pd.DataFrame) -> None:
    """Reject using a model whose information cutoff is later than any origin."""
    cutoff = _aware(training_cutoff, "training_cutoff")
    if not isinstance(rows, pd.DataFrame) or "issue_time" not in rows:
        _fail("INVALID_SCHEMA", "rows require issue_time")
    issues = _utc_column(rows["issue_time"], "issue_time")
    if (issues < cutoff).any():
        _fail("MODEL_TRAINED_AFTER_ISSUE", "model information cutoff is after a forecast origin")
