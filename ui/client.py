"""Small, bounded HTTP adapter for the participant 1 forecast API.

Only the Python standard library is needed. Failures are explicit: this adapter
never substitutes fixture data when a request or an API response is invalid.
"""

from __future__ import annotations

import json
import math
import socket
from datetime import datetime, timezone
from http.client import HTTPException
from typing import Any
from urllib import error, parse, request


RUN_STATUSES = frozenset(
    {"queued", "fetching_weather", "validating", "forecasting", "analysing", "completed", "failed"}
)
MAX_TIMEOUT_SECONDS = 60.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class ApiError(RuntimeError):
    """A user-displayable API failure with an optional HTTP status code."""

    def __init__(self, message: str, *, kind: str = "response", status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


def parse_issue_time(value: str) -> datetime:
    """Require an explicit timezone; naive timestamps must not become UTC silently."""
    if not isinstance(value, str) or not value.strip():
        raise ApiError("issue_time должен содержать дату и время в формате ISO 8601 с часовым поясом.", kind="input")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError("issue_time должен содержать корректную дату и время в формате ISO 8601.", kind="input") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApiError("Укажите часовой пояс в issue_time, например +00:00 или Z.", kind="input")
    return parsed.astimezone(timezone.utc)


def validate_run_input(issue_time: str, horizon_hours: int, turbine_ids: list[str]) -> dict[str, Any]:
    """Validate the shared input contract before making a network request."""
    parsed = parse_issue_time(issue_time)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ApiError("issue_time должен приходиться на начало часа по UTC.", kind="input")
    if isinstance(horizon_hours, bool) or not isinstance(horizon_hours, int) or horizon_hours not in (24, 48):
        raise ApiError("Горизонт horizon_hours должен составлять 24 или 48 часов.", kind="input")
    if not isinstance(turbine_ids, list) or not turbine_ids:
        raise ApiError("Выберите хотя бы одну турбину.", kind="input")
    for turbine_id in turbine_ids:
        if (
            not isinstance(turbine_id, str)
            or not turbine_id.strip()
            or len(turbine_id) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in turbine_id)
        ):
            raise ApiError("ID турбины должен быть непустой строкой без управляющих символов.", kind="input")
    if len(set(turbine_ids)) != len(turbine_ids):
        raise ApiError("Каждую турбину можно выбрать только один раз.", kind="input")
    return {
        "issue_time": parsed.isoformat().replace("+00:00", "Z"),
        "horizon_hours": horizon_hours,
        "turbine_ids": list(turbine_ids),
    }


def _quoted_run_id(run_id: str) -> str:
    if (
        not isinstance(run_id, str)
        or not run_id.strip()
        or run_id in (".", "..")
        or len(run_id) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in run_id)
    ):
        raise ApiError("run_id должен быть непустым идентификатором без управляющих символов.", kind="input")
    return parse.quote(run_id, safe="")


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


class ApiClient:
    """Client for the six API routes in IMPLEMENTATION_PLAN.md.

    ``base_url`` can include a path prefix such as ``http://localhost:8000/api``.
    Credentials, query parameters and fragments belong outside this setting.
    ``timeout`` is per request and must be within (0, 60] seconds.
    """

    def __init__(self, base_url: str, timeout: float = 10):
        if not isinstance(base_url, str):
            raise ApiError("Адрес API должен быть строкой.", kind="input")
        base_url = base_url.strip()
        try:
            parsed = parse.urlsplit(base_url)
            port = parsed.port  # Validate malformed or out-of-range ports now.
        except ValueError as exc:
            raise ApiError("Адрес API должен быть корректным HTTP(S) URL.", kind="input") from exc
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in base_url)
            or any(ord(char) < 32 or ord(char) == 127 for char in base_url)
            or (port is not None and port < 1)
        ):
            raise ApiError("Укажите HTTP(S) адрес API без логина, пароля, параметров запроса и фрагмента.", kind="input")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= MAX_TIMEOUT_SECONDS
        ):
            raise ApiError("Тайм-аут API должен быть больше 0 и не больше 60 секунд.", kind="input")
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    def _request(self, route: str, *, payload: dict[str, Any] | None = None, csv: bool = False) -> bytes:
        headers = {"Accept": "text/csv" if csv else "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
        req = request.Request(self.base_url + route, data=body, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                result = response.read(MAX_RESPONSE_BYTES + 1)
                content_type = response.headers.get("Content-Type", "").lower()
        except error.HTTPError as exc:
            detail = ""
            try:
                raw = exc.read(2048).decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    candidate = parsed.get("detail") or parsed.get("message") or parsed.get("error")
                    if isinstance(candidate, str):
                        detail = " " + " ".join(candidate.split())[:300]
            except (OSError, ValueError, UnicodeError):
                pass
            finally:
                exc.close()
            raise ApiError(f"API вернул ошибку HTTP {exc.code}.{detail}", kind="http", status_code=exc.code) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ApiError(f"API не ответил за {self.timeout:g} сек. Попробуйте обновить статус позже.", kind="timeout") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ApiError(f"API не ответил за {self.timeout:g} сек. Попробуйте обновить статус позже.", kind="timeout") from exc
            raise ApiError("Не удалось подключиться к API прогноза. Проверьте адрес и убедитесь, что сервис запущен.", kind="connection") from exc
        except (OSError, HTTPException) as exc:
            raise ApiError("Соединение с API прервалось при чтении ответа.", kind="connection") from exc
        if len(result) > MAX_RESPONSE_BYTES:
            raise ApiError("Ответ API превышает ограничение интерфейса в 10 MiB.", kind="response")
        if csv:
            if not result.strip():
                raise ApiError("API вернул пустой CSV-файл.", kind="empty")
            if "text/html" in content_type or "application/json" in content_type:
                raise ApiError("API вернул документ другого формата вместо CSV.", kind="response")
        return result

    def _json(self, route: str, payload: dict[str, Any] | None = None) -> Any:
        raw = self._request(route, payload=payload)
        try:
            return json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_nonfinite)
        except (ValueError, UnicodeError) as exc:
            raise ApiError("API вернул некорректный JSON.", kind="response") from exc

    @staticmethod
    def _object(value: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ApiError(f"Маршрут {endpoint} должен возвращать JSON-объект.", kind="response")
        return value

    @classmethod
    def _run(cls, value: Any, endpoint: str) -> dict[str, Any]:
        value = cls._object(value, endpoint)
        if not isinstance(value.get("run_id"), str) or not value["run_id"].strip():
            raise ApiError(f"В ответе {endpoint} отсутствует run_id.", kind="response")
        if not isinstance(value.get("status"), str) or value["status"] not in RUN_STATUSES:
            raise ApiError(f"Ответ {endpoint} содержит неизвестный статус расчёта.", kind="response")
        return value

    def health(self) -> dict[str, Any]:
        value = self._object(self._json("/health"), "/health")
        if not isinstance(value.get("status"), str) or not value["status"]:
            raise ApiError("В ответе /health отсутствует статус сервиса.", kind="response")
        return value

    def create_run(self, issue_time: str, horizon_hours: int, turbine_ids: list[str]) -> dict[str, Any]:
        payload = validate_run_input(issue_time, horizon_hours, turbine_ids)
        return self._run(self._json("/runs", payload), "/runs")

    def get_run(self, run_id: str) -> dict[str, Any]:
        value = self._run(self._json(f"/runs/{_quoted_run_id(run_id)}"), "/runs/{run_id}")
        if value["run_id"] != run_id:
            raise ApiError("API вернул статус другого расчёта: run_id не совпадает с запросом.", kind="response")
        return value

    def get_forecast(self, run_id: str) -> list[dict[str, Any]]:
        value = self._json(f"/runs/{_quoted_run_id(run_id)}/forecast")
        if isinstance(value, dict) and "records" in value:
            value = value["records"]
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ApiError("Прогноз должен быть массивом записей или объектом с массивом records.", kind="response")
        required = ("run_id", "issue_time", "turbine_id", "valid_time", "lead_hours", "prediction", "target_unit")
        for index, row in enumerate(value):
            missing = [key for key in required if key not in row]
            if missing:
                raise ApiError(f"В строке прогноза {index + 1} отсутствуют поля: {', '.join(missing)}.", kind="response")
            if row["run_id"] != run_id:
                raise ApiError(f"Строка прогноза {index + 1} относится к другому расчёту: run_id не совпадает с запросом.", kind="response")
            prediction = row["prediction"]
            if isinstance(prediction, bool) or not isinstance(prediction, (int, float)) or not math.isfinite(prediction):
                raise ApiError(f"Строка прогноза {index + 1} содержит некорректное числовое значение prediction.", kind="response")
        return value

    def get_forecast_csv(self, run_id: str) -> bytes:
        return self._request(f"/runs/{_quoted_run_id(run_id)}/forecast.csv", csv=True)

    def get_evaluation(self) -> dict[str, Any]:
        return self._object(self._json("/evaluation"), "/evaluation")
