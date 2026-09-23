"""Advisory forecast diagnostics, without truth or changes to predictions."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction

from wind_agent.contracts import ForecastRecord, WeatherRecord


FLAT_FORECAST_HOURS = 24


def _number(value: Fraction, reasons: list[str]) -> float | None:
    try:
        result = float(value)
    except OverflowError:
        result = math.inf
    if math.isfinite(result):
        return result
    reasons.append("A derived diagnostic exceeds the JSON numeric range; it is reported as null.")
    return None


def _mean(values: list[float], reasons: list[str]) -> float | None:
    if not values:
        return None
    total = sum((Fraction.from_float(value) for value in values), Fraction(0))
    return _number(total / len(values), reasons)


def _difference(current: float, previous: float) -> Fraction:
    return Fraction.from_float(current) - Fraction.from_float(previous)


def _comparison(
    current: list[ForecastRecord], previous: list[ForecastRecord] | None, reasons: list[str]
) -> dict:
    result = {
        "status": "unavailable" if previous is None else "no_common_hours",
        "meaning": "Changes between predictions for common turbine/hour keys, not forecast errors.",
        "common_hours": 0,
        "changed_count": 0,
        "per_turbine": {},
    }
    if previous is None:
        return result
    prior = {(row.turbine_id, row.valid_time): row for row in previous}
    pairs = defaultdict(list)
    for row in current:
        old = prior.get((row.turbine_id, row.valid_time))
        if old is not None:
            pairs[row.turbine_id].append((row, old))
    for turbine_id, turbine_pairs in sorted(pairs.items()):
        units = {row.target_unit for pair in turbine_pairs for row in pair}
        if len(units) != 1:
            reasons.append(f"{turbine_id}: previous predictions use incompatible target units.")
            result["per_turbine"][turbine_id] = {
                "status": "incompatible_units", "overlapping_hours": len(turbine_pairs)
            }
            continue
        deltas = [_difference(new.prediction, old.prediction) for new, old in turbine_pairs]
        absolute = [abs(delta) for delta in deltas]
        changed = sum(new.prediction != old.prediction for new, old in turbine_pairs)
        result["per_turbine"][turbine_id] = {
            "status": "compared",
            "target_unit": next(iter(units)),
            "common_hours": len(deltas),
            "changed_count": changed,
            "mean_absolute_delta": _number(sum(absolute, Fraction(0)) / len(deltas), reasons),
            "max_absolute_delta": _number(max(absolute), reasons),
            "mean_signed_delta": _number(sum(deltas, Fraction(0)) / len(deltas), reasons),
            "previous_revisions": sorted({old.revision for _, old in turbine_pairs}),
            "current_revisions": sorted({new.revision for new, _ in turbine_pairs}),
        }
        result["common_hours"] += len(deltas)
        result["changed_count"] += changed
    if result["common_hours"]:
        result["status"] = "compared"
    elif pairs:
        result["status"] = "incompatible_units"
    return result


def analyse_forecast(
    forecasts: list[ForecastRecord],
    weather: list[WeatherRecord],
    previous: list[ForecastRecord] | None = None,
) -> dict:
    """Describe validated records; the service owns duplicate/grid validation.

    Only exact constants lasting at least 24 consecutive hourly predictions
    trigger a shape warning. Ramps and revision deltas have no assumed power
    bounds or calibrated alarm threshold. All decisions are advisory.
    """
    reasons: list[str] = []
    model_warnings = sorted({warning for row in forecasts for warning in row.warnings if warning})
    if model_warnings:
        reasons.append("Model or input warnings require review; see model_warnings.")
    is_demo = any(row.data_quality == "fixture" for row in forecasts) or any(
        row.provenance_kind == "fixture" or row.provider == "synthetic_fixture" for row in weather
    )
    if is_demo:
        reasons.append("SYNTHETIC FIXTURE: diagnostics do not describe a real operational forecast.")
    if not forecasts:
        reasons.append("No forecast records are available for analysis.")

    by_turbine = defaultdict(list)
    weather_by_key = {(row.turbine_id, row.valid_time): row for row in weather}
    for row in forecasts:
        by_turbine[row.turbine_id].append(row)
    per_turbine = {}
    source_warnings: list[str] = []
    for turbine_id, rows in sorted(by_turbine.items()):
        rows.sort(key=lambda row: row.valid_time)
        units = {row.target_unit for row in rows}
        if len(units) != 1:
            raise ValueError(f"{turbine_id}: forecast target units must be consistent")
        values = [row.prediction for row in rows]
        constant_hours = longest_constant_hours = 1
        ramps = []
        for old, new in zip(rows, rows[1:]):
            consecutive = new.valid_time - old.valid_time == timedelta(hours=1)
            if consecutive:
                ramps.append((_difference(new.prediction, old.prediction), old, new))
            constant_hours = (
                constant_hours + 1 if consecutive and new.prediction == old.prediction else 1
            )
            longest_constant_hours = max(longest_constant_hours, constant_hours)
        flat = longest_constant_hours >= FLAT_FORECAST_HOURS
        if flat:
            reasons.append(
                f"{turbine_id}: exactly constant predictions for {longest_constant_hours} "
                "consecutive hours; inspect inputs or operating conditions."
            )
        largest_ramp = None
        if ramps:
            delta, old, new = max(ramps, key=lambda item: abs(item[0]))
            largest_ramp = {
                "from_valid_time": old.valid_time.isoformat(),
                "to_valid_time": new.valid_time.isoformat(),
                "delta": _number(delta, reasons),
                "absolute_delta": _number(abs(delta), reasons),
            }
        matched_weather = [
            weather_by_key[(row.turbine_id, row.valid_time)]
            for row in rows if (row.turbine_id, row.valid_time) in weather_by_key
        ]
        if len(matched_weather) != len(rows):
            reasons.append(f"{turbine_id}: weather is missing for some forecast hours.")
        if any(row.provenance_kind != "operational_archive" for row in matched_weather):
            source_warnings.append(
                f"{turbine_id}: weather includes fixture, hindcast or unspecified provenance."
            )
        if any(
            row.provenance_kind == "operational_archive" and not row.availability_evidence
            for row in matched_weather
        ):
            source_warnings.append(f"{turbine_id}: archived weather availability evidence is missing.")
        wind = [row.wind_speed_ms for row in matched_weather]
        per_turbine[turbine_id] = {
            "target_unit": next(iter(units)),
            "forecast_hours": len(rows),
            "first_valid_time": rows[0].valid_time.isoformat(),
            "last_valid_time": rows[-1].valid_time.isoformat(),
            "prediction_min": min(values),
            "prediction_max": max(values),
            "prediction_mean": _mean(values, reasons),
            "largest_hourly_ramp": largest_ramp,
            "flat_forecast": flat,
            "longest_constant_hours": longest_constant_hours,
            "flat_forecast_minimum_hours": FLAT_FORECAST_HOURS,
            "weather_wind_speed_ms": {
                "matched_hours": len(wind),
                "min": min(wind) if wind else None,
                "max": max(wind) if wind else None,
                "mean": _mean(wind, reasons),
            },
            "model_warnings": sorted({warning for row in rows for warning in row.warnings if warning}),
        }
    reasons.extend(source_warnings)
    comparison = _comparison(forecasts, previous, reasons)
    reasons = list(dict.fromkeys(reasons))
    return {
        "schema_version": "1.0",
        "decision": "review_required" if reasons else "monitor_updates",
        "next_action": "review_inputs" if reasons else "monitor_updates",
        "reasons": reasons,
        "is_demo": is_demo,
        "model_warnings": model_warnings,
        "source_warnings": source_warnings,
        "per_turbine": per_turbine,
        "previous_comparison": comparison,
        "limitations": [
            "No observed generation is used; these diagnostics do not measure forecast accuracy.",
            "Revision deltas compare predictions, not predictions against truth.",
            "Exact equality across at least 24 consecutive hours is an advisory flatness flag; "
            "constant generation can be legitimate.",
            "Ramp magnitudes have no calibrated alarm threshold or assumed target bounds.",
            "Turbines are analysed separately; no conversion to MW/MWh or station total is inferred.",
            "Review decisions do not modify forecasts, retrain models or request unavailable future data.",
        ],
    }
