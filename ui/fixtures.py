"""Deterministic, explicitly synthetic fixtures for development and demonstrations.

These records exercise the interface contract; they are not trained predictions,
historical weather, observations, or evidence of forecasting skill.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
from datetime import timedelta
from typing import Any

from ui.client import ApiError, parse_issue_time, validate_run_input


DEFAULT_TURBINE_IDS = ("turbine_1", "turbine_2")
DEMO_WARNING = (
    "Синтетическая демонстрация: графики и сведения о погоде смоделированы. "
    "Реальная погода не загружалась, модель не обучалась, доступность погодного выпуска "
    "на исторический момент не проверялась."
)
UNIT_WARNING = (
    "В демонстрации используется условная нормализованная шкала 0–1. "
    "Нормализацию и физические единицы исходных данных ещё нужно подтвердить. "
    "Эти значения не выражают МВт или МВт·ч."
)
_STATES = ("queued", "fetching_weather", "validating", "forecasting", "analysing", "completed")
_STEP_NAMES = ("fetching_weather", "validating", "forecasting", "analysing")
_STEP_MESSAGES = {
    "fetching_weather": "Имитация выбора архивного прогноза погоды. Запрос к провайдеру не выполняется.",
    "validating": "Проверка структуры примера и почасовой сетки. Историческая доступность погоды не подтверждается.",
    "forecasting": "Построение воспроизводимых демонстрационных кривых без обученной модели прогноза.",
    "analysing": "Проверка результата примера. Фактическая выработка и калиброванные интервалы прогноза отсутствуют.",
}


def _iso(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z")


class DemoClient:
    """Same six methods as ApiClient, with simulated progress on status polls.

    Keep the instance in UI session state so runs survive Streamlit rerenders.
    A repeated configuration gets a separate ID and revision; values stay
    deterministic, because a fixture rerun is not an updated weather forecast.
    """

    def __init__(self):
        self._runs: dict[str, dict[str, Any]] = {}
        self._revisions: dict[tuple[Any, ...], int] = {}
        self._latest: dict[tuple[Any, ...], str] = {}

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "source_mode": "synthetic",
            "demo_ready": True,
            "model_ready": False,
            "data_ready": False,
            "turbine_ids": list(DEFAULT_TURBINE_IDS),
            "warnings": [DEMO_WARNING],
        }

    def create_run(self, issue_time: str, horizon_hours: int, turbine_ids: list[str]) -> dict[str, Any]:
        params = validate_run_input(issue_time, horizon_hours, turbine_ids)
        key = (params["issue_time"], horizon_hours, tuple(sorted(turbine_ids)))
        revision = self._revisions.get(key, 0) + 1
        run_id = f"demo-{len(self._runs) + 1:04d}"
        previous_run_id = self._latest.get(key)
        self._runs[run_id] = {
            **params,
            "run_id": run_id,
            "revision": revision,
            "source_mode": "synthetic",
            "status": "queued",
            "warnings": [DEMO_WARNING, UNIT_WARNING, "Фактическая выработка и калиброванные интервалы прогноза не предоставлены."],
            "error": None,
            "previous_run_id": previous_run_id,
            "recalculation_reason": (
                "Повторный синтетический запуск: создана новая ревизия примера, смоделированные входные данные не изменились."
                if previous_run_id else "Первый запуск синтетической демонстрации."
            ),
        }
        self._revisions[key] = revision
        self._latest[key] = run_id
        return self._snapshot(run_id)

    def _known_run(self, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or run_id not in self._runs:
            raise ApiError("Демонстрационный расчёт не найден в этой сессии.", kind="http", status_code=404)
        return self._runs[run_id]

    def _snapshot(self, run_id: str) -> dict[str, Any]:
        result = copy.deepcopy(self._known_run(run_id))
        current = _STATES.index(result["status"])
        result["steps"] = [
            {
                "name": name,
                "status": "completed" if current > position else "running" if current == position else "pending",
                "message": _STEP_MESSAGES[name],
                "source_mode": "synthetic",
            }
            for position, name in enumerate(_STEP_NAMES, start=1)
        ]
        return result

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._known_run(run_id)
        position = _STATES.index(run["status"])
        run["status"] = _STATES[min(position + 1, len(_STATES) - 1)]
        return self._snapshot(run_id)

    def get_forecast(self, run_id: str) -> list[dict[str, Any]]:
        run = self._known_run(run_id)
        if run["status"] != "completed":
            raise ApiError("Синтетический расчёт ещё выполняется. Обновите статус перед загрузкой прогноза.", kind="http", status_code=409)
        issue = parse_issue_time(run["issue_time"])
        weather_run_time = _iso(issue - timedelta(hours=6))
        records = []
        for turbine_id in run["turbine_ids"]:
            seed = int.from_bytes(hashlib.sha256(turbine_id.encode("utf-8")).digest()[:4], "big")
            phase = (seed % 360) * math.pi / 180
            scale = 0.86 + (seed % 11) / 100
            for lead in range(1, run["horizon_hours"] + 1):
                valid = issue + timedelta(hours=lead)
                absolute_hour = valid.timestamp() / 3600
                synthetic_wind = 8.4 + 2.7 * math.sin(absolute_hour * math.pi / 18 + phase)
                synthetic_wind += 1.4 * math.cos(absolute_hour * math.pi / 7 + phase / 2)
                prediction = scale * min(1.0, max(0.0, (synthetic_wind ** 3 - 3 ** 3) / (12 ** 3 - 3 ** 3)))
                records.append({
                    "schema_version": "1.0",
                    "run_id": run_id,
                    "revision": run["revision"],
                    "issue_time": run["issue_time"],
                    "turbine_id": turbine_id,
                    "valid_time": _iso(valid),
                    "lead_hours": lead,
                    "prediction": round(prediction, 6),
                    "target_unit": "normalized_power",
                    "time_basis": "interval_end",
                    "weather_model": "synthetic-demo",
                    "weather_run_time": weather_run_time,
                    "model_version": "synthetic-fixture-v1",
                    "training_cutoff": None,
                    "data_quality": {"status": "synthetic", "reason": "Демонстрационные данные; реальные наблюдения не использовались."},
                    "warnings": [DEMO_WARNING, UNIT_WARNING],
                    "source_mode": "synthetic",
                    "provider": "synthetic fixture",
                    "forecast_available_at": None,
                    "availability_basis": "not_applicable_synthetic",
                    "raw_sha256": None,
                })
        return records

    def get_forecast_csv(self, run_id: str) -> bytes:
        records = self.get_forecast(run_id)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(records[0]))
        writer.writeheader()
        for record in records:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                for key, value in record.items()
            })
        return output.getvalue().encode("utf-8")

    def get_evaluation(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "source_mode": "synthetic",
            "reason": "Синтетический пример не позволяет оценить точность прогноза. Фактическая выработка и отчёт о валидации обученной модели не подключены.",
            "test_truth_available": False,
            "metrics": None,
            "baseline": None,
            "warnings": [DEMO_WARNING],
        }
