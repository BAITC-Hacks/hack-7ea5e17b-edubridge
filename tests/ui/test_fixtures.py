"""The fixture is useful for UI flows and never pretends to be test truth."""

import csv
import io
import json
import math
import unittest
from datetime import datetime, timedelta

from ui.client import ApiError
from ui.fixtures import DEFAULT_TURBINE_IDS, DemoClient


class DemoClientTests(unittest.TestCase):
    def setUp(self):
        self.client = DemoClient()

    def complete(self, horizon=48, turbines=None, issue="2026-01-31T19:00:00Z"):
        run = self.client.create_run(issue, horizon, list(turbines or DEFAULT_TURBINE_IDS))
        while run["status"] != "completed":
            run = self.client.get_run(run["run_id"])
        return run

    def test_progress_and_unknown_run(self):
        run = self.client.create_run("2026-01-31T19:00:00Z", 24, ["turbine_1"])
        self.assertEqual(run["status"], "queued")
        with self.assertRaises(ApiError) as error:
            self.client.get_forecast(run["run_id"])
        self.assertEqual(error.exception.status_code, 409)
        statuses = [self.client.get_run(run["run_id"])["status"] for _ in range(6)]
        self.assertEqual(statuses, ["fetching_weather", "validating", "forecasting", "analysing", "completed", "completed"])
        final = self.client.get_run(run["run_id"])
        self.assertTrue(all(step["status"] == "completed" for step in final["steps"]))
        with self.assertRaises(ApiError) as error:
            self.client.get_run("missing")
        self.assertEqual(error.exception.status_code, 404)

    def test_hourly_utc_grid_counts_and_finite_values(self):
        for horizon in (24, 48):
            run = self.complete(horizon=horizon)
            records = self.client.get_forecast(run["run_id"])
            self.assertEqual(len(records), horizon * 2)
            keys = {(row["issue_time"], row["turbine_id"], row["valid_time"]) for row in records}
            self.assertEqual(len(keys), len(records))
            for turbine in DEFAULT_TURBINE_IDS:
                rows = [row for row in records if row["turbine_id"] == turbine]
                self.assertEqual([row["lead_hours"] for row in rows], list(range(1, horizon + 1)))
                issue = datetime.fromisoformat(run["issue_time"].replace("Z", "+00:00"))
                for row in rows:
                    self.assertTrue(row["valid_time"].endswith("Z"))
                    actual = datetime.fromisoformat(row["valid_time"].replace("Z", "+00:00"))
                    self.assertEqual(actual, issue + timedelta(hours=row["lead_hours"]))
                    self.assertTrue(math.isfinite(row["prediction"]))
                    self.assertTrue(0 <= row["prediction"] <= 1)
                    self.assertEqual(row["time_basis"], "interval_end")

    def test_synthetic_disclosure_and_no_truth_or_quality_metrics(self):
        run = self.complete()
        records = self.client.get_forecast(run["run_id"])
        self.assertEqual(run["source_mode"], "synthetic")
        self.assertTrue(run["warnings"])
        for row in records:
            self.assertEqual(row["source_mode"], "synthetic")
            self.assertEqual(row["weather_model"], "synthetic-demo")
            self.assertIsNone(row["training_cutoff"])
            self.assertIsNone(row["forecast_available_at"])
            self.assertEqual(row["data_quality"]["status"], "synthetic")
            self.assertTrue(row["warnings"])
            self.assertTrue(all(key not in row for key in ("actual", "observed", "lower", "upper", "mae", "rmse")))
        evaluation = self.client.get_evaluation()
        self.assertEqual(evaluation["status"], "unavailable")
        self.assertFalse(evaluation["test_truth_available"])
        self.assertIsNone(evaluation["metrics"])
        self.assertIsNone(evaluation["baseline"])
        self.assertFalse(self.client.health()["model_ready"])

    def test_curves_reproducible_distinct_and_revisions_do_not_overwrite(self):
        first = self.complete()
        original = self.client.get_forecast(first["run_id"])
        second = self.complete()
        repeated = self.client.get_forecast(second["run_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["previous_run_id"], first["run_id"])
        self.assertIn("смоделированные входные данные не изменились", second["recalculation_reason"])
        self.assertEqual([row["prediction"] for row in original], [row["prediction"] for row in repeated])
        self.assertEqual(self.client.get_forecast(first["run_id"]), original)
        first_curve = [row["prediction"] for row in original if row["turbine_id"] == "turbine_1"]
        second_curve = [row["prediction"] for row in original if row["turbine_id"] == "turbine_2"]
        self.assertNotEqual(first_curve, second_curve)
        self.assertGreater(len(set(first_curve)), 10)

    def test_csv_metadata_survives_export(self):
        run = self.complete(horizon=24, turbines=["turbine_1"])
        raw = self.client.get_forecast_csv(run["run_id"])
        records = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
        self.assertEqual(len(records), 24)
        self.assertEqual(records[0]["source_mode"], "synthetic")
        self.assertEqual(records[0]["target_unit"], "normalized_power")
        self.assertEqual(json.loads(records[0]["data_quality"])["status"], "synthetic")
        self.assertTrue(json.loads(records[0]["warnings"]))
        self.assertEqual(records[0]["forecast_available_at"], "")

    def test_client_and_returned_values_are_isolated(self):
        run = self.complete()
        run["warnings"].clear()
        run["turbine_ids"].append("mutated")
        current = self.client.get_run(run["run_id"])
        self.assertTrue(current["warnings"])
        self.assertNotIn("mutated", current["turbine_ids"])
        with self.assertRaises(ApiError):
            DemoClient().get_run(run["run_id"])

    def test_invalid_inputs_and_explicit_timezone(self):
        with self.assertRaises(ApiError):
            self.client.create_run("2026-01-31T00:00:00", 24, ["turbine_1"])
        with self.assertRaises(ApiError):
            self.client.create_run("2026-01-31T00:00:00Z", 24, [])
        run = self.complete(horizon=24, turbines=["turbine_1"], issue="2026-02-01T00:00:00+05:00")
        self.assertEqual(run["issue_time"], "2026-01-31T19:00:00Z")


if __name__ == "__main__":
    unittest.main()
