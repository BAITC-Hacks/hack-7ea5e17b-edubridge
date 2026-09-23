"""Exercise user-facing dashboard transitions without contacting a service."""

import copy
import csv
import io
import json
import os
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import Mock, patch

from ui.client import ApiClient, ApiError
from ui.fixtures import DemoClient

UI_DEPENDENCIES_AVAILABLE = all(find_spec(package) is not None for package in ("streamlit", "plotly"))
if UI_DEPENDENCIES_AVAILABLE:
    from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[2] / "ui" / "app.py"


@unittest.skipUnless(
    UI_DEPENDENCIES_AVAILABLE,
    "Dashboard tests require optional Streamlit and Plotly; install ui/requirements.txt.",
)
class DashboardAppTests(unittest.TestCase):
    def setUp(self):
        # A missed mock must fail locally instead of reaching an external API.
        network = patch("ui.client.request.urlopen", side_effect=AssertionError("Unexpected network request"))
        self.urlopen = network.start()
        self.addCleanup(network.stop)
        env = patch.dict(os.environ, {"WIND_API_BASE_URL": "http://127.0.0.1:8000", "WIND_API_TIMEOUT": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.app = AppTest.from_file(str(APP), default_timeout=15).run()
        self.assert_no_exception()

    def tearDown(self):
        self.urlopen.assert_not_called()

    def assert_no_exception(self):
        self.assertEqual([error.message for error in self.app.exception], [])

    def entry(self):
        return self.app.session_state["history"][self.app.session_state["current_id"]]

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def select_api(self):
        self.app.get("button_group")[0].set_value("API").run()
        self.assert_no_exception()

    def test_initial_demo_is_complete_and_explicitly_synthetic(self):
        entry = self.entry()
        self.assertEqual(entry["run"]["status"], "completed")
        self.assertEqual(len(entry["rows"]), 96)
        self.assertEqual({row["source_mode"] for row in entry["rows"]}, {"synthetic"})
        self.assertTrue(any("Синтетические данные" in item.value for item in self.app.markdown))
        self.assertTrue(any("Точность демо не измеряется" in item.value for item in self.app.info))
        self.assertTrue(entry["csv"].startswith(b"schema_version,"))
        self.assertEqual(len(self.app.get("plotly_chart")), 1)
        self.assertEqual(self.app.dataframe[0].value.shape[0], 96)

    def test_submit_one_turbine_24_hours_and_poll_to_completion(self):
        previous_id = self.app.session_state["current_id"]
        self.app.radio[0].set_value(24)
        self.app.multiselect[0].set_value(["turbine_1"])
        self.button("Рассчитать прогноз").click().run()
        self.assert_no_exception()
        self.assertNotEqual(self.app.session_state["current_id"], previous_id)
        self.assertEqual(self.entry()["request"]["horizon_hours"], 24)
        self.assertEqual(self.entry()["request"]["turbine_ids"], ["turbine_1"])
        self.assertIsNone(self.entry()["rows"])
        states = [self.entry()["run"]["status"]]
        for _ in range(5):
            self.app.run()
            self.assert_no_exception()
            states.append(self.entry()["run"]["status"])
            if states[-1] == "completed":
                break
        self.assertIn("forecasting", states)
        self.assertEqual(states[-1], "completed")
        rows = self.entry()["rows"]
        self.assertEqual(len(rows), 24)
        self.assertEqual({row["turbine_id"] for row in rows}, {"turbine_1"})
        self.assertEqual([row["lead_hours"] for row in rows], list(range(1, 25)))
        metrics = {metric.label: metric.value for metric in self.app.metric}
        self.assertEqual(metrics["Горизонт"], "24 ч")
        self.assertEqual(metrics["Почасовых значений"], "24")

    def test_empty_turbine_selection_reports_error_without_creating_run(self):
        before = copy.deepcopy(self.app.session_state["history"])
        run_id = self.app.session_state["current_id"]
        self.app.multiselect[0].set_value([])
        self.button("Рассчитать прогноз").click().run()
        self.assert_no_exception()
        self.assertTrue(any("Выберите хотя бы одну турбину" in error.value for error in self.app.error))
        self.assertEqual(self.app.session_state["current_id"], run_id)
        self.assertEqual(self.app.session_state["history"], before)

    def test_api_mode_clears_demo_result_and_history(self):
        self.assertTrue(self.entry()["rows"])
        self.select_api()
        self.assertIsInstance(self.app.session_state["client"], ApiClient)
        self.assertEqual(self.app.session_state["history"], {})
        self.assertIsNone(self.app.session_state["current_id"])
        self.assertEqual(len(self.app.metric), 0)
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertEqual(len(self.app.get("plotly_chart")), 0)
        self.assertFalse(any("Синтетические данные" in item.value for item in self.app.markdown))

    def test_same_revision_in_progress_refresh_removes_completed_artifacts(self):
        old_run = copy.deepcopy(self.entry()["run"])
        self.assertTrue(self.entry()["rows"])
        self.assertTrue(self.entry()["csv"])
        self.assertIsNotNone(self.entry()["evaluation"])
        in_progress = {**old_run, "status": "forecasting"}
        with patch.object(DemoClient, "get_run", return_value=in_progress) as get_run:
            self.button("Обновить статус").click().run()
        self.assert_no_exception()
        get_run.assert_called_with(old_run["run_id"])
        self.assertEqual(self.entry()["run"]["revision"], old_run["revision"])
        self.assertEqual(self.entry()["run"]["status"], "forecasting")
        self.assertIsNone(self.entry()["rows"])
        self.assertIsNone(self.entry()["csv"])
        self.assertIsNone(self.entry()["evaluation"])
        self.assertEqual(len(self.app.metric), 0)
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertEqual(len(self.app.get("plotly_chart")), 0)
        self.assertEqual(len(self.app.get("download_button")), 0)
        self.assertTrue(any("Прогнозирование" in item.value for item in self.app.info))

    def test_unreachable_api_health_and_create_show_errors_without_demo_fallback(self):
        self.select_api()
        with patch.object(ApiClient, "health", side_effect=ApiError("Health connection refused", kind="network")) as health:
            self.button("Проверить подключение").click().run()
            health.assert_called_once_with()
        self.assert_no_exception()
        self.assertTrue(any("Health connection refused" in error.value for error in self.app.error))
        self.assertIsNone(self.app.session_state["health_result"])
        with patch.object(ApiClient, "create_run", side_effect=ApiError("Run connection refused", kind="network")) as create:
            self.button("Рассчитать прогноз").click().run()
            create.assert_called_once()
        self.assert_no_exception()
        self.assertTrue(any("Run connection refused" in error.value for error in self.app.error))
        self.assertEqual(self.app.session_state["history"], {})
        self.assertIsNone(self.app.session_state["current_id"])
        self.assertEqual(len(self.app.dataframe), 0)
        self.assertEqual(len(self.app.get("plotly_chart")), 0)

    def test_timezone_changes_labels_without_changing_forecast_or_export(self):
        before = copy.deepcopy(self.entry())
        run_id = self.app.session_state["current_id"]
        utc_table = self.app.dataframe[0].value.copy()
        self.app.selectbox(key="display_zone").set_value("Asia/Almaty").run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state["current_id"], run_id)
        self.assertEqual(self.entry(), before)
        local_table = self.app.dataframe[0].value
        self.assertEqual(utc_table.iloc[0, 0], "01.02.2026 01:00 UTC")
        self.assertEqual(local_table.columns[0], "Время (Asia/Almaty)")
        self.assertEqual(local_table.iloc[0, 0], "01.02.2026 06:00 +05")
        self.assertEqual(local_table.iloc[:, 1:].to_dict(), utc_table.iloc[:, 1:].to_dict())
        self.assertTrue(any("Выпуск 01.02.2026 05:00 +05" in item.value for item in self.app.markdown))

    def test_already_completed_api_run_loads_results_without_manual_refresh(self):
        self.select_api()
        fixture = DemoClient()
        run = fixture.create_run("2026-02-01T00:00:00Z", 48, ["turbine_1", "turbine_2"])
        for _ in range(5):
            run = fixture.get_run(run["run_id"])
        methods = {
            "create_run": Mock(return_value=run),
            "get_run": Mock(side_effect=AssertionError("Already completed run should not need polling")),
            "get_forecast": Mock(side_effect=fixture.get_forecast),
            "get_forecast_csv": Mock(side_effect=fixture.get_forecast_csv),
            "get_evaluation": Mock(side_effect=fixture.get_evaluation),
        }
        with patch.multiple(ApiClient, **methods):
            self.button("Рассчитать прогноз").click().run()
        self.assert_no_exception()
        methods["get_run"].assert_not_called()
        methods["get_forecast"].assert_called_once_with(run["run_id"])
        methods["get_forecast_csv"].assert_called_once_with(run["run_id"])
        self.assertEqual(self.entry()["run"]["status"], "completed")
        self.assertIsNone(self.entry()["error"])
        self.assertEqual(len(self.entry()["rows"]), 96)
        self.assertIsNotNone(self.entry()["csv"])
        self.assertEqual(len(self.app.get("plotly_chart")), 1)
        self.assertTrue(any("API вернул синтетические данные" in item.value for item in self.app.warning))

    def test_backend_fixture_units_events_and_csv_are_presented_as_demo(self):
        self.select_api()
        fixture = DemoClient()
        run = fixture.create_run("2026-02-01T00:00:00Z", 48, ["turbine_1", "turbine_2"])
        for _ in range(5):
            run = fixture.get_run(run["run_id"])
        # Backend fixtures identify themselves through units/quality and provide
        # events, without requiring the frontend DemoClient's extra fields.
        run.pop("source_mode")
        run.pop("steps")
        events = [
            {"at": "2026-02-01T00:00:00Z", "step": "fetching_weather",
             "message": "Loaded explicit fixture weather", "details": {"provider": "fixture"}},
            {"at": "2026-02-01T00:00:01Z", "step": "forecasting",
             "message": "Generated backend fixture values", "details": {"rows": 96}},
            {"at": "2026-02-01T00:00:02Z", "step": "completed",
             "message": "Fixture run complete", "details": {"revision": run["revision"]}},
        ]
        run["events"] = events
        rows = fixture.get_forecast(run["run_id"])
        for row in rows:
            row.pop("source_mode")
            row.update(target_unit="fixture_dimensionless", data_quality="fixture")
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: json.dumps(value) if isinstance(value, (dict, list)) else value
                          for key, value in row.items()} for row in rows)
        csv_bytes = csv_buffer.getvalue().encode("utf-8")
        with patch.multiple(ApiClient,
                            create_run=Mock(return_value=run),
                            get_forecast=Mock(return_value=rows),
                            get_forecast_csv=Mock(return_value=csv_bytes),
                            get_evaluation=Mock(return_value={"status": "unavailable", "test_truth_available": False})):
            self.button("Рассчитать прогноз").click().run()
        self.assert_no_exception()
        self.assertIsNone(self.entry()["error"])
        self.assertIsNone(self.entry()["csv_error"])
        self.assertTrue(any("API вернул синтетические данные" in item.value for item in self.app.warning))
        table = self.app.dataframe[0].value
        self.assertEqual(table.shape[0], 96)
        self.assertIn("Демонстрационное значение", table.columns)
        self.assertNotIn("Нормализованная мощность", table.columns)
        self.assertEqual(set(table["Источник"]), {"Синтетический пример"})
        self.assertTrue(any("Демонстрационное значение" in item.value for item in self.app.caption))
        steps = [item.value for item in self.app.markdown if 'class="step"' in item.value]
        self.assertTrue(any("Получение погоды" in step for step in steps))
        self.assertTrue(any("Прогнозирование" in step for step in steps))
        self.assertTrue(any("Расчёт завершён" in step for step in steps))
        self.assertTrue(any(json.loads(item.value) == events for item in self.app.json))
        self.assertEqual(self.entry()["csv"], csv_bytes)
        self.assertEqual(len(self.app.get("download_button")), 1)
        self.assertEqual(len(self.app.get("plotly_chart")), 1)

    def test_invalid_api_address_stays_error_on_repeated_rerenders(self):
        # Start from populated demo history, then fail the first API construction.
        # A partially changed context must not expose the old demo on later runs.
        with patch.dict(os.environ, {"WIND_API_BASE_URL": "://invalid-api"}):
            self.select_api()
            for _ in range(3):
                self.assertTrue(any("HTTP(S)" in error.value for error in self.app.error))
                self.assertEqual(len(self.app.metric), 0)
                self.assertEqual(len(self.app.dataframe), 0)
                self.assertEqual(len(self.app.get("plotly_chart")), 0)
                self.app.run()
                self.assert_no_exception()


if __name__ == "__main__":
    unittest.main()
