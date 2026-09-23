"""Validate the forecast before presenting it as an hourly result."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import io
from math import isclose, isfinite
from typing import Any
from zoneinfo import ZoneInfo


class ForecastError(ValueError):
    """The returned forecast cannot safely be displayed."""


def utc_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ForecastError("Некорректная дата в ответе сервиса.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastError("В ответе есть время без часового пояса.")
    return parsed.astimezone(timezone.utc)


def display_time(value: Any, zone: str = "UTC") -> str:
    if value is None or value == "":
        return "Не предоставлено"
    try:
        return utc_time(value).astimezone(ZoneInfo(zone)).strftime("%d.%m.%Y %H:%M %Z")
    except (ForecastError, KeyError):
        return "Некорректное время"


def turbine_label(value: str) -> str:
    return {"turbine_1": "Турбина 1", "turbine_2": "Турбина 2"}.get(value, value)


def validate_forecast(records: list[dict], request: dict) -> list[dict]:
    """Require a complete, finite, single-origin result with explicit units.

    The API uses the interval-end convention: leads 1..H.
    Backend fixture units require the fixture marker on every row.
    Partial/duplicate/mixed-unit results remain visible as errors, never zero-filled.
    """
    if not records:
        raise ForecastError("Сервис завершил расчёт, но вернул пустой прогноз.")
    origin = utc_time(request["issue_time"])
    horizon = int(request["horizon_hours"])
    turbines = set(request["turbine_ids"])
    seen: set[tuple[str, int]] = set()
    units: set[str] = set()
    validated = []
    for row in records:
        if not isinstance(row, dict):
            raise ForecastError("Прогноз должен содержать записи JSON.")
        try:
            turbine = str(row["turbine_id"])
            valid = utc_time(row["valid_time"])
            issue = utc_time(row["issue_time"])
            lead_value = row["lead_hours"]
            lead = int(lead_value)
            value = float(row["prediction"])
            unit = row["target_unit"]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ForecastError("В прогнозе отсутствуют обязательные поля или неверные значения.") from exc
        if isinstance(row["prediction"], bool) or not isfinite(value):
            raise ForecastError("Прогноз содержит нечисловую или бесконечную мощность.")
        if unit != "normalized_power" and not (unit == "fixture_dimensionless" and row.get("data_quality") == "fixture"):
            raise ForecastError(f"Неподдерживаемые единицы: {unit}. Ожидается normalized_power или явно помеченный fixture.")
        units.add(unit)
        if len(units) > 1:
            raise ForecastError("Нельзя отображать разные единицы на одной шкале прогноза.")
        if turbine not in turbines or issue != origin:
            raise ForecastError("Прогноз относится к другому выпуску или набору турбин.")
        if isinstance(lead_value, bool) or lead != float(lead_value) or not 1 <= lead <= horizon:
            raise ForecastError("Некорректный горизонт строки прогноза.")
        if valid != origin + timedelta(hours=lead):
            raise ForecastError("Временная сетка не соответствует часовому горизонту выпуска.")
        key = (turbine, lead)
        if key in seen:
            raise ForecastError("В прогнозе повторяется час одной турбины.")
        seen.add(key)
        cutoff = row.get("training_cutoff")
        if cutoff is not None and utc_time(cutoff) > origin:
            raise ForecastError("Модель обучена с данными, доступными после момента выпуска.")
        weather = row.get("weather_run_time")
        if weather is not None and utc_time(weather) > origin:
            raise ForecastError("Погодный выпуск находится в будущем относительно расчёта.")
        available = row.get("forecast_available_at")
        if available is not None and utc_time(available) > origin:
            raise ForecastError("Погодный прогноз стал доступен после момента расчёта.")
        validated.append({**row, "prediction": value, "lead_hours": lead,
                          "_valid_utc": valid, "_issue_utc": issue})
    expected = {(turbine, lead) for turbine in turbines for lead in range(1, horizon + 1)}
    if seen != expected:
        raise ForecastError(f"Неполный прогноз: получено {len(seen)} из {len(expected)} часов по турбинам.")
    return sorted(validated, key=lambda row: (row["turbine_id"], row["_valid_utc"]))


def collect_warnings(run: dict, rows: list[dict]) -> list[str]:
    result = []
    for record in [run, *rows]:
        warnings = record.get("warnings") or []
        if isinstance(warnings, str):
            warnings = [warnings]
        elif not isinstance(warnings, list):
            warnings = [f"Некорректный формат предупреждения сервиса: {warnings}"]
        for warning in warnings:
            text = str(warning)
            if text not in result:
                result.append(text)
    return result


def weather_provenance(run: dict, rows: list[dict]) -> list[dict]:
    """Build a display summary, enriching missing fields from matching events.

    The backend stores weather provenance in agent events instead of forecast
    records. Only the newest event for the same turbine, weather model and
    aware model-run timestamp is eligible to fill missing display fields.
    Explicit row values, including None, take precedence. This function neither
    changes records nor certifies historical availability of the weather.
    """
    fields = (
        "turbine_id", "weather_model", "weather_run_time", "model_version", "training_cutoff",
        "forecast_available_at", "availability_basis", "provider", "raw_sha256", "source_mode", "time_basis",
    )
    events = run.get("events", []) if isinstance(run, dict) else []
    if not isinstance(events, list):
        events = []
    summaries = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        turbine = row.get("turbine_id")
        if not isinstance(turbine, str) or not turbine or turbine in seen:
            continue
        seen.add(turbine)
        matching = {}
        model = row.get("weather_model")
        for event in reversed(events):
            if not isinstance(event, dict) or not isinstance(event.get("details"), dict):
                continue
            metadata = event["details"].get("weather_provenance")
            candidate = metadata.get(turbine) if isinstance(metadata, dict) else None
            if not isinstance(candidate, dict) or not isinstance(model, str) or not model:
                continue
            if candidate.get("weather_model") != model:
                continue
            try:
                if utc_time(candidate.get("model_run_time")) != utc_time(row.get("weather_run_time")):
                    continue
            except ForecastError:
                continue
            # Display metadata has textual values. Ignore malformed nested
            # values rather than letting them break a dataframe renderer.
            matching = {key: value for key, value in candidate.items() if isinstance(value, str) or value is None}
            if "raw_sha256" not in matching:
                matching["raw_sha256"] = matching.get("raw_sha256_example")
            if "time_basis" not in matching:
                matching["time_basis"] = matching.get("weather_feature_time_basis")
            break
        summaries.append({key: row[key] if key in row else matching.get(key) for key in fields})
    return summaries


def validate_run_identity(rows: list[dict], run: dict) -> None:
    for row in rows:
        if row.get("run_id") != run.get("run_id"):
            raise ForecastError("Ответ содержит прогноз другого запуска.")
        if run.get("revision") is not None and row.get("revision") != run["revision"]:
            raise ForecastError("Ревизия прогноза не совпадает с ревизией расчёта.")


def validate_csv(content: bytes, rows: list[dict], run: dict) -> bytes:
    """Reject an export that disagrees with the validated on-screen forecast."""
    try:
        exported = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        expected = {(row["turbine_id"], utc_time(row["valid_time"])): row for row in rows}
        seen = set()
        for row in exported:
            key = (row["turbine_id"], utc_time(row["valid_time"]))
            source = expected[key]
            if key in seen or row["run_id"] != run["run_id"]:
                raise ValueError("Duplicate or wrong run")
            if (row["target_unit"] != source["target_unit"]
                    or utc_time(row["issue_time"]) != utc_time(source["issue_time"])
                    or float(row["lead_hours"]) != source["lead_hours"]
                    or not isclose(float(row["prediction"]), source["prediction"], rel_tol=1e-8, abs_tol=1e-9)
                    or (run.get("revision") is not None and row.get("revision") != str(run["revision"]))):
                raise ValueError("Values or metadata differ")
            if source.get("source_mode") == "synthetic" and row.get("source_mode") != "synthetic":
                raise ValueError("Synthetic disclosure missing")
            if source.get("data_quality") == "fixture" and row.get("data_quality") != "fixture":
                raise ValueError("Fixture disclosure missing")
            seen.add(key)
        if seen != set(expected):
            raise ValueError("Incomplete export")
    except (KeyError, TypeError, ValueError, UnicodeError, csv.Error) as exc:
        raise ForecastError("CSV не соответствует показанному прогнозу или не содержит обязательные метаданные.") from exc
    return content


def observed_points(rows: list[dict], synthetic: bool) -> list[dict]:
    """Optional extension: only explicitly sourced actual observations are drawn."""
    if synthetic:
        return []
    result = []
    for row in rows:
        if row.get("actual_source") != "observed" or row.get("actual") is None:
            continue
        try:
            actual = float(row["actual"])
        except (TypeError, ValueError):
            continue
        if not isinstance(row["actual"], bool) and isfinite(actual):
            result.append({**row, "actual": actual})
    return result
