import copy
import csv
import io
import unittest
from datetime import datetime, timedelta, timezone

from ui.presentation import (ForecastError, display_time, observed_points, validate_forecast,
                             validate_csv, validate_run_identity, collect_warnings, weather_provenance)


class ForecastValidationTests(unittest.TestCase):
    def setUp(self):
        self.request = {"issue_time": "2026-02-01T00:00:00Z", "horizon_hours": 24, "turbine_ids": ["turbine_1"]}
        origin = datetime(2026, 2, 1, tzinfo=timezone.utc)
        self.rows = [{"issue_time": self.request["issue_time"], "turbine_id": "turbine_1",
                      "valid_time": (origin + timedelta(hours=h)).isoformat(), "lead_hours": h,
                      "prediction": 0.2, "target_unit": "normalized_power"} for h in range(1, 25)]

    def test_valid_forecast_does_not_mutate_source(self):
        original = copy.deepcopy(self.rows)
        self.assertEqual(len(validate_forecast(self.rows, self.request)), 24)
        self.assertEqual(original, self.rows)

    def test_missing_duplicate_nonfinite_and_wrong_origin_rejected(self):
        cases = [self.rows[:-1], self.rows + [self.rows[0]],
                 [{**self.rows[0], "prediction": float("nan")}, *self.rows[1:]],
                 [{**self.rows[0], "issue_time": "2026-02-02T00:00:00Z"}, *self.rows[1:]],
                 [{**self.rows[0], "valid_time": "2026-02-01T01:00:00"}, *self.rows[1:]]]
        for rows in cases:
            with self.subTest(rows=rows[:1]), self.assertRaises(ForecastError):
                validate_forecast(rows, self.request)

    def test_units_future_training_and_weather_rejected(self):
        for changes in [{"target_unit": "MW"}, {"training_cutoff": "2026-02-02T00:00:00Z"},
                        {"forecast_available_at": "2026-02-01T06:00:00Z"},
                        {"weather_run_time": "2026-02-01T06:00:00Z"}]:
            with self.subTest(changes=changes), self.assertRaises(ForecastError):
                validate_forecast([{**self.rows[0], **changes}, *self.rows[1:]], self.request)

    def test_explicit_backend_fixture_units_are_accepted_without_rescaling(self):
        rows = [{**row, "target_unit": "fixture_dimensionless", "data_quality": "fixture",
                 "prediction": row["lead_hours"] / 10} for row in self.rows]
        original = copy.deepcopy(rows)
        validated = validate_forecast(rows, self.request)
        self.assertEqual(len(validated), 24)
        self.assertEqual([row["prediction"] for row in validated], [row["prediction"] for row in original])
        self.assertTrue(all(row["target_unit"] == "fixture_dimensionless" for row in validated))
        self.assertTrue(all(row["data_quality"] == "fixture" for row in validated))
        self.assertEqual(rows, original)

    def test_fixture_units_require_explicit_fixture_marker_on_every_row(self):
        rows = [{**row, "target_unit": "fixture_dimensionless", "data_quality": "fixture"}
                for row in self.rows]
        for marker in (None, "", "real", "synthetic", {"status": "fixture"}, True):
            invalid = copy.deepcopy(rows)
            invalid[-1]["data_quality"] = marker
            with self.subTest(marker=marker), self.assertRaises(ForecastError):
                validate_forecast(invalid, self.request)
        missing = copy.deepcopy(rows)
        del missing[-1]["data_quality"]
        with self.assertRaises(ForecastError):
            validate_forecast(missing, self.request)

    def test_mixed_normalized_and_fixture_units_are_rejected_in_either_order(self):
        for position in (0, len(self.rows) - 1):
            rows = copy.deepcopy(self.rows)
            rows[position].update(target_unit="fixture_dimensionless", data_quality="fixture")
            with self.subTest(position=position), self.assertRaises(ForecastError):
                validate_forecast(rows, self.request)

    def test_observations_need_explicit_source_and_real_mode(self):
        rows = [{"actual": .3}, {"actual": .4, "actual_source": "observed"}]
        self.assertEqual(len(observed_points(rows, False)), 1)
        self.assertEqual(observed_points(rows, True), [])

    def test_display_timezone_is_explicit(self):
        self.assertIn("05:00", display_time("2026-02-01T00:00:00Z", "Asia/Almaty"))
        self.assertEqual(display_time(None), "Не предоставлено")

    def test_cross_run_and_mixed_revision_rejected(self):
        for row in [{"run_id": "other", "revision": 1}, {"run_id": "run-1", "revision": 2}]:
            with self.subTest(row=row), self.assertRaises(ForecastError):
                validate_run_identity([row], {"run_id": "run-1", "revision": 1})

    def test_malformed_warning_does_not_crash_or_hide_diagnostic(self):
        warnings = collect_warnings({"warnings": 3}, [{"warnings": "normal"}])
        self.assertEqual(len(warnings), 2)
        self.assertIn("Некорректный формат", warnings[0])

    def test_provenance_uses_latest_matching_event_and_preserves_row_values(self):
        rows = [{**row, "weather_model": "gfs", "weather_run_time": "2026-01-31T12:00:00Z",
                 "provider": "row-provider", "source_mode": None} for row in self.rows]

        def event(**changes):
            metadata = {"weather_model": "gfs", "model_run_time": "2026-01-31T12:00:00Z",
                        "provider": "event-provider", "forecast_available_at": "2026-01-31T18:00:00Z",
                        "availability_basis": "Old basis", "raw_sha256_example": "old-hash",
                        "weather_feature_time_basis": "instant_at_end", "source_mode": "event-mode"}
            return {"details": {"weather_provenance": {"turbine_1": {**metadata, **changes}}}}

        run = {"events": [event(), event(model_run_time="2026-01-31T17:00:00+05:00",
                                         availability_basis="Latest matching basis", raw_sha256_example="new-hash"),
                          event(weather_model="different-model", raw_sha256_example="wrong-model-hash"),
                          event(model_run_time="2026-02-01T00:00:00Z", raw_sha256_example="wrong-run-hash")]}
        before = copy.deepcopy((run, rows))
        summary = weather_provenance(run, rows)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["provider"], "row-provider")
        self.assertIsNone(summary[0]["source_mode"])
        self.assertEqual(summary[0]["forecast_available_at"], "2026-01-31T18:00:00Z")
        self.assertEqual(summary[0]["availability_basis"], "Latest matching basis")
        self.assertEqual(summary[0]["raw_sha256"], "new-hash")
        self.assertEqual(summary[0]["time_basis"], "instant_at_end")
        self.assertEqual((run, rows), before)

    def test_provenance_does_not_borrow_from_other_turbines_or_invalid_times(self):
        rows = [{**self.rows[0], "weather_model": "gfs", "weather_run_time": "2026-01-31T12:00:00Z"},
                {**self.rows[0], "turbine_id": "turbine_2", "weather_model": "gfs",
                 "weather_run_time": "2026-01-31T12:00:00Z"}]
        run = {"events": [{"details": {"weather_provenance": {
            "turbine_1": {"weather_model": "gfs", "model_run_time": "2026-01-31T12:00:00",
                          "forecast_available_at": "2026-01-31T18:00:00Z"},
            "turbine_2": {"weather_model": "gfs", "model_run_time": "2026-01-31T12:00:00Z",
                          "forecast_available_at": "2026-02-02T00:00:00Z"},
        }}}]}
        summary = weather_provenance(run, rows)
        self.assertEqual(len(summary), 2)
        self.assertIsNone(summary[0]["forecast_available_at"])
        # This is an unmodified display value, not an assertion of eligibility.
        self.assertEqual(summary[1]["forecast_available_at"], "2026-02-02T00:00:00Z")
        self.assertNotIn("historical_eligibility_verified", summary[1])

    def test_provenance_handles_missing_and_malformed_events(self):
        row = {**self.rows[0], "weather_model": "gfs", "weather_run_time": "2026-01-31T12:00:00Z"}
        for events in (None, {}, "bad", [None, "bad", {"details": []}, {"details": {"weather_provenance": []}},
                                          {"details": {"weather_provenance": {"turbine_1": "bad"}}}]):
            with self.subTest(events=events):
                summary = weather_provenance({"events": events}, [row])
                self.assertEqual(summary[0]["weather_model"], "gfs")
                self.assertIsNone(summary[0]["forecast_available_at"])
        self.assertEqual(weather_provenance({}, []), [])
        self.assertIsNone(weather_provenance({}, [row])[0]["provider"])

    def test_csv_must_match_display_and_keep_synthetic_disclosure(self):
        rows = [{**row, "run_id": "run-1", "revision": 1, "source_mode": "synthetic"} for row in self.rows]
        run = {"run_id": "run-1", "revision": 1}

        def export(values):
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(values)
            return output.getvalue().encode()

        valid = export(rows)
        self.assertEqual(validate_csv(valid, rows, run), valid)
        cases = [rows[:-1], [{**rows[0], "prediction": .9}, *rows[1:]],
                 [{**rows[0], "source_mode": ""}, *rows[1:]],
                 [{**rows[0], "revision": 2}, *rows[1:]], rows + [rows[0]]]
        for values in cases:
            with self.subTest(values=values[:1]), self.assertRaises(ForecastError):
                validate_csv(export(values), rows, run)

    def test_csv_preserves_explicit_backend_fixture_disclosure(self):
        rows = [{**row, "run_id": "fixture-run", "revision": 1,
                 "target_unit": "fixture_dimensionless", "data_quality": "fixture"}
                for row in self.rows]
        run = {"run_id": "fixture-run", "revision": 1}
        validated = validate_forecast(rows, self.request)

        def export(values, omit_quality_column=False):
            columns = [key for key in rows[0] if key != "data_quality" or not omit_quality_column]
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(values)
            return output.getvalue().encode("utf-8")

        valid = export(rows)
        self.assertEqual(validate_csv(valid, validated, run), valid)
        for replacement in ("", "real", "synthetic", "FIXTURE"):
            changed = [*rows[:-1], {**rows[-1], "data_quality": replacement}]
            with self.subTest(replacement=replacement), self.assertRaises(ForecastError):
                validate_csv(export(changed), validated, run)
        missing_value = copy.deepcopy(rows)
        del missing_value[-1]["data_quality"]
        with self.assertRaises(ForecastError):
            validate_csv(export(missing_value), validated, run)
        with self.assertRaises(ForecastError):
            validate_csv(export(rows, omit_quality_column=True), validated, run)


if __name__ == "__main__":
    unittest.main()
