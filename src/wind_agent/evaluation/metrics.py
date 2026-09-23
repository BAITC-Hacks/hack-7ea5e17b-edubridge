"""Transparent error reports in normalized-power units, never accuracy percentages."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .splits import (
    TARGET_KEY,
    SplitConfig,
    _fail,
    _forecast_frame,
    _numeric,
    _utc_column,
)


def _summary(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"mae": None, "rmse": None, "n_forecasts": 0, "n_unique_targets": 0}
    errors = rows["prediction"].to_numpy() - rows["normalized_power"].to_numpy()
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(np.square(errors))))
    if not np.isfinite(mae) or not np.isfinite(rmse):
        _fail("NUMERIC_OVERFLOW", "finite input values overflowed the error calculation")
    return {
        "mae": mae,
        "rmse": rmse,
        "n_forecasts": int(len(rows)),
        "n_unique_targets": int(len(rows[TARGET_KEY].drop_duplicates())),
    }


def evaluate(
    predictions: pd.DataFrame,
    observed: pd.DataFrame | None = None,
    split_config: SplitConfig | None = None,
) -> dict[str, Any]:
    """Score supplied rows, retaining origin/lead and exposing all exclusions.

    Call separately on tune and holdout frames produced by assign_splits(); this
    function does not choose a model or retrospectively select a better vintage.
    With observed=None the frame contains normalized_power. Otherwise observed
    is the authoritative unique truth table keyed by turbine_id and valid_time.
    A status of invalid_predictions must never be used for candidate selection;
    its metrics describe only the explicitly counted finite subset.
    """
    if split_config is not None and not isinstance(split_config, SplitConfig):
        _fail("INVALID_SPLIT", "split_config must be a SplitConfig")
    horizon = split_config.horizon_hours if split_config is not None else 48
    work = _forecast_frame(predictions, horizon_hours=horizon).reset_index(drop=True)
    if "prediction" not in work:
        _fail("INVALID_SCHEMA", "predictions require a prediction column")
    if "target_unit" in work and (work["target_unit"] != "normalized_power").any():
        _fail("INVALID_TARGET_UNIT", "evaluation requires normalized_power, without unit conversion")
    if observed is not None:
        required = {*TARGET_KEY, "normalized_power"}
        if not isinstance(observed, pd.DataFrame) or not required <= set(observed.columns):
            _fail("INVALID_SCHEMA", "observed requires turbine_id, valid_time and normalized_power")
        truth = observed[list(required)].copy()
        truth["valid_time"] = _utc_column(truth["valid_time"], "observed.valid_time")
        if truth["turbine_id"].map(lambda value: not isinstance(value, str) or not value.strip()).any():
            _fail("INVALID_SCHEMA", "observed turbine_id must be a nonempty string")
        if truth.duplicated(TARGET_KEY).any():
            _fail("DUPLICATE_TRUTH", "truth must contain one value per turbine/valid-time key")
        work = work.drop(columns=["normalized_power"], errors="ignore").merge(
            truth, on=TARGET_KEY, how="left", validate="many_to_one",
        )
    elif "normalized_power" not in work:
        _fail("INVALID_SCHEMA", "normalized_power is required when observed is not supplied")

    truth_missing = work["normalized_power"].isna()
    truth_values = _numeric(work["normalized_power"])
    prediction_values = _numeric(work["prediction"])
    invalid_prediction = ~np.isfinite(prediction_values)
    invalid_truth = ~np.isfinite(truth_values) & ~truth_missing
    work["prediction"] = prediction_values
    work["normalized_power"] = truth_values
    scored = work.loc[~invalid_prediction & np.isfinite(truth_values)].copy()
    if invalid_prediction.any():
        status = "invalid_predictions"
    elif scored.empty:
        status = "no_ground_truth" if len(work) else "no_predictions"
    elif truth_missing.any() or invalid_truth.any():
        status = "partial_truth"
    else:
        status = "ok"

    groups = {"1-24": lambda rows: rows["lead_hours"] <= 24,
              "25-48": lambda rows: rows["lead_hours"] >= 25}
    turbines = sorted(work["turbine_id"].unique())
    report = {
        "status": status,
        "eligible_for_selection": status in ("ok", "partial_truth") and not scored.empty,
        "target_unit": "normalized_power",
        "forecast_weighting": "one weight per origin/turbine/valid-time forecast",
        "target_time_convention": "hour_end",
        "n_forecasts": int(len(scored)),
        "n_unique_targets": int(len(scored[TARGET_KEY].drop_duplicates())),
        "overall": _summary(scored),
        "by_turbine": {tid: _summary(scored.loc[scored["turbine_id"] == tid]) for tid in turbines},
        "by_lead_group": {name: _summary(scored.loc[mask(scored)]) for name, mask in groups.items()},
        "by_turbine_and_lead_group": {
            tid: {name: _summary(scored.loc[(scored["turbine_id"] == tid) & mask(scored)])
                  for name, mask in groups.items()}
            for tid in turbines
        },
        "coverage": {
            "n_input_forecasts": int(len(work)),
            "n_scored_forecasts": int(len(scored)),
            "n_excluded_forecasts": int(len(work) - len(scored)),
            "n_unique_input_targets": int(len(work[TARGET_KEY].drop_duplicates())),
            "n_unique_scored_targets": int(len(scored[TARGET_KEY].drop_duplicates())),
        },
        "exclusions": {
            "missing_truth": int(truth_missing.sum()),
            "nonfinite_truth": int(invalid_truth.sum()),
            "invalid_predictions": int(invalid_prediction.sum()),
            "reason_counts_may_overlap": True,
        },
        "selection_note": "Compare candidates only on identical tuning keys; holdout is not a selection set.",
    }
    if split_config is not None:
        report["split_config"] = split_config.to_dict()
    if "split" in work:
        report["supplied_splits"] = sorted(work["split"].dropna().unique().tolist())
        if set(report["supplied_splits"]) != {"tune"}:
            report["eligible_for_selection"] = False
    return report
