"""Unit-test records below are stubs, not historical evidence."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from wind_agent.agent.analysis import analyse_forecast
from wind_agent.contracts import ForecastRecord, WeatherRecord


ISSUE = datetime(2026, 1, 31, 18, tzinfo=timezone.utc)


def forecast(values, turbine_id="turbine_1", revision=1, unit="normalized_power"):
    return [
        ForecastRecord(
            run_id="analysis-unit-test", revision=revision, issue_time=ISSUE,
            turbine_id=turbine_id, valid_time=ISSUE + timedelta(hours=lead),
            lead_hours=lead, prediction=float(value), target_unit=unit,
            weather_model="test-weather", weather_run_time=ISSUE - timedelta(hours=12),
            model_version="test-model", training_cutoff=ISSUE - timedelta(hours=24),
        )
        for lead, value in enumerate(values, start=1)
    ]


def archive_record_stubs(forecasts):
    return [
        WeatherRecord(
            turbine_id=row.turbine_id, latitude=43.0, longitude=78.0,
            provider="unit-test-archive-stub", weather_model="test-weather",
            model_run_time=ISSUE - timedelta(hours=12),
            forecast_available_at=ISSUE - timedelta(hours=6),
            availability_basis="Unit test stub, not historical evidence",
            retrieved_at=ISSUE + timedelta(days=1), valid_time=row.valid_time,
            lead_hours=row.lead_hours + 12, wind_speed_ms=float(row.lead_hours),
            wind_height_m=100.0, temperature_c=-3.0,
            source_request="test://archive-stub", raw_sha256="a" * 64,
            provenance_kind="operational_archive", availability_evidence="test://stub-evidence",
        )
        for row in forecasts
    ]


def test_summary_ramp_and_wind_are_per_turbine_chronological_and_do_not_modify_inputs():
    first = forecast([0.1, 0.7, 0.4])
    second = forecast([10, 30, 20], turbine_id="turbine_2", unit="another_unit")
    records = list(reversed(first + second))
    weather = archive_record_stubs(records)
    original = [row.model_dump(mode="json") for row in records]

    result = analyse_forecast(records, weather)

    assert result["decision"] == "monitor_updates"
    assert result["next_action"] == "monitor_updates"
    one = result["per_turbine"]["turbine_1"]
    assert one["prediction_min"] == 0.1
    assert one["prediction_max"] == 0.7
    assert one["prediction_mean"] == pytest.approx(0.4)
    assert one["largest_hourly_ramp"]["delta"] == pytest.approx(0.6)
    assert one["largest_hourly_ramp"]["from_valid_time"] == first[0].valid_time.isoformat()
    assert one["largest_hourly_ramp"]["to_valid_time"] == first[1].valid_time.isoformat()
    assert one["weather_wind_speed_ms"] == {
        "matched_hours": 3, "min": 1.0, "max": 3.0, "mean": 2.0,
    }
    assert result["per_turbine"]["turbine_2"]["prediction_mean"] == 20.0
    assert result["previous_comparison"]["status"] == "unavailable"
    assert [row.model_dump(mode="json") for row in records] == original
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("hours,expected_flat", [(23, False), (24, True), (48, True)])
def test_flat_forecast_requires_at_least_24_exact_consecutive_hours(hours, expected_flat):
    records = forecast([0.0] * hours)
    result = analyse_forecast(records, archive_record_stubs(records))
    one = result["per_turbine"]["turbine_1"]
    assert one["flat_forecast"] is expected_flat
    assert one["longest_constant_hours"] == hours
    assert one["flat_forecast_minimum_hours"] == 24
    assert (result["decision"] == "review_required") is expected_flat


def test_gap_breaks_flat_sequence_and_is_not_treated_as_hourly_ramp():
    records = forecast([0] * 25 + [100])
    records = records[:12] + records[13:25] + [
        records[-1].model_copy(update={"valid_time": ISSUE + timedelta(hours=27), "lead_hours": 27})
    ]
    result = analyse_forecast(records, archive_record_stubs(records))
    one = result["per_turbine"]["turbine_1"]
    assert one["flat_forecast"] is False
    assert one["longest_constant_hours"] == 12
    assert one["largest_hourly_ramp"]["absolute_delta"] == 0.0


def test_revision_comparison_aligns_common_hours_and_reports_deltas_not_accuracy():
    previous = forecast([0.2, 0.4, 0.6, 999])
    current = forecast([0.3, 0.4, 0.4], revision=2)
    previous += forecast([100], turbine_id="only_in_previous")
    result = analyse_forecast(current, archive_record_stubs(current), list(reversed(previous)))
    comparison = result["previous_comparison"]
    assert comparison["status"] == "compared"
    assert comparison["common_hours"] == 3
    assert comparison["changed_count"] == 2
    one = comparison["per_turbine"]["turbine_1"]
    assert one["mean_absolute_delta"] == pytest.approx(0.1)
    assert one["max_absolute_delta"] == pytest.approx(0.2)
    assert one["mean_signed_delta"] == pytest.approx(-0.1 / 3)
    assert one["previous_revisions"] == [1]
    assert one["current_revisions"] == [2]
    assert "not forecast errors" in comparison["meaning"]
    assert result["decision"] == "monitor_updates"


def test_unchanged_revision_and_nonoverlapping_previous_are_distinguished():
    records = forecast([0.1, 0.2, 0.3])
    weather = archive_record_stubs(records)
    unchanged = analyse_forecast(records, weather, records)["previous_comparison"]
    assert unchanged["changed_count"] == 0
    assert unchanged["per_turbine"]["turbine_1"]["max_absolute_delta"] == 0
    other = forecast([0.1], turbine_id="other_turbine")
    absent = analyse_forecast(records, weather, other)["previous_comparison"]
    assert absent["status"] == "no_common_hours"
    assert absent["common_hours"] == 0


def test_previous_with_different_units_cannot_be_subtracted():
    current = forecast([0.1, 0.2])
    previous = forecast([100, 200], unit="kW")
    result = analyse_forecast(current, archive_record_stubs(current), previous)
    assert result["decision"] == "review_required"
    assert result["previous_comparison"]["status"] == "incompatible_units"
    one = result["previous_comparison"]["per_turbine"]["turbine_1"]
    assert one["overlapping_hours"] == 2
    assert "mean_absolute_delta" not in one


def test_model_warnings_and_fixture_label_are_preserved():
    records = forecast([0.1, 0.2])
    for row in records:
        row.warnings = ["History timezone is assumed"]
        row.data_quality = "fixture"
    weather = archive_record_stubs(records)
    weather[0].provenance_kind = "fixture"
    result = analyse_forecast(records, weather)
    assert result["is_demo"] is True
    assert result["model_warnings"] == ["History timezone is assumed"]
    assert result["decision"] == "review_required"
    assert result["next_action"] == "review_inputs"
    assert any("SYNTHETIC FIXTURE" in reason for reason in result["reasons"])
    assert result["source_warnings"]


@pytest.mark.parametrize("problem", ["missing_hour", "missing_evidence", "hindcast"])
def test_source_problems_generate_explicit_advisory_reasons(problem):
    records = forecast([0.1, 0.2])
    weather = archive_record_stubs(records)
    if problem == "missing_hour":
        weather.pop()
    elif problem == "missing_evidence":
        weather[0].availability_evidence = None
    else:
        weather[0].provenance_kind = "hindcast"
    result = analyse_forecast(records, weather)
    assert result["decision"] == "review_required"
    assert result["reasons"]
    assert result["is_demo"] is False


def test_empty_input_has_no_fabricated_metrics():
    result = analyse_forecast([], [])
    assert result["per_turbine"] == {}
    assert result["decision"] == "review_required"
    assert result["previous_comparison"]["status"] == "unavailable"
    json.dumps(result, allow_nan=False)


def test_finite_input_overflow_remains_json_safe_and_explicit():
    records = forecast([-1e308, 1e308])
    previous = forecast([1e308, -1e308])
    result = analyse_forecast(records, archive_record_stubs(records), previous)
    json.dumps(result, allow_nan=False)
    assert result["per_turbine"]["turbine_1"]["prediction_mean"] == 0.0
    assert result["per_turbine"]["turbine_1"]["largest_hourly_ramp"]["absolute_delta"] is None
    assert result["decision"] == "review_required"
    assert any("JSON numeric range" in reason for reason in result["reasons"])
