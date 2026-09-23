# Подключение модели участника №2

Платформа принимает **путь к каталогу пакета модели** и вызывает импортируемый Python-модуль. Это конкретизация [PR #2](https://github.com/BAITC-Hacks/hack-7ea5e17b-edubridge/pull/2); [ответ по четырём вопросам](pr-2-response.md) сохранён в репозитории. Модель, обучение, подготовку SCADA и оценку реализует участник №2. Синтетический `config/demo.json` не подтверждает качество реальной модели.

## Время и replay

Внутренняя договорённость — `timestamp_convention="hour_end"`: прогноз с `valid_time=t` относится к `[t-1h, t)`. При `issue_time=2026-01-31T18:00:00Z` lead 1 имеет `valid_time=19:00Z` и описывает `[18:00, 19:00)`. На турбину нужны H строк `issue_time+1h...+Hh`, H — 24 или 48. Все timestamps aware UTC; naive значения отвергаются.

GFS в `valid_time` — мгновенное поле на конце целевого интервала: `weather_feature_time_basis="instant_at_end"`. Это не средняя погода за час. Погодные timestamps не сдвигаются; подготовка признаков и validation используют ту же трактовку. Внутренняя договорённость **не подтверждает** исходные timezone или labels SCADA.

Replay содержит **29 origin**, ежедневно с 31 января по 28 февраля включительно, в 23:00 `Asia/Almaty` (18:00 UTC). Это командное допущение о расписании. Первый интервал заканчивается 1 февраля 00:00, но принадлежит январю. Последний февральский интервал заканчивается 1 марта 00:00. Отбор февраля:

```text
2026-02-01 00:00 Asia/Almaty < valid_time <= 2026-03-01 00:00 Asia/Almaty
```

Получаются 672 уникальных интервала на турбину. Полные горизонты всех origin сохраняются отдельно, включая интервалы вне февраля. При перекрытии итоговая таблица использует последний `issue_time`, без выбора по факту/ошибке. API/UI подписывает «конец интервала» либо показывает обе границы.

## Граница обучения

В `config/archive.json` поля `history_timezone` и `train_cutoff` намеренно равны `null`. Реальный расчёт мощности требует явные IANA timezone истории и границу разрешённых сведений в aware UTC. `weather-fetch` не требует модели.

Timezone, исходные labels SCADA, reporting delay и определение нормализации ещё не подтверждены организатором. Если участник №2 принимает допущения, он записывает их в конфигурации, metadata, model card и предупреждениях. Валидное имя IANA не является доказательством исходного timezone.

`ModelArtifact.training_cutoff` ограничивает **все** сведения, повлиявшие на fitting: preprocessing, imputation, scaling, calibration, подбор параметров и model selection. Для SCADA действует `interval_end + reporting_delay <= training_cutoff`. Сегодняшняя дата сохранения весов не заменяет информационный cutoff.

```text
artifact.training_cutoff <= settings.train_cutoff
artifact.training_cutoff <= request.issue_time
settings.train_cutoff <= начало 1 февраля 2026 в settings.history_timezone
```

Начало 1 февраля — конец последнего январского интервала. Но для первого origin 31 января 23:00 нужны данные, доступные уже к нему; пакет с более поздним cutoff не подходит. Отдельные selection/calibration cutoff не должны превышать общий `training_cutoff`. Сравнение дат не заменяет аудит fitting. Февральские наблюдения не участвуют в обучении или выборе модели.

## Каталог и точный JSON metadata

`settings.model_artifact_path` указывает на **каталог**, например:

```text
artifacts/model/baseline-v1/
    metadata.json
    model.joblib
```

`metadata.json` проходит строгий `wind_agent.contracts.ModelArtifact`. Обязательны `model_version`, `training_cutoff`, `target_unit`, `metadata`. Необязательный `artifact_path` указывает на веса; без него bridge использует `model.joblib` внутри пакета. Остальные сведения PR #2 помещаются во вложенный `metadata`: дополнительные поля верхнего уровня запрещены.

Пример структуры **не является metadata готовой модели**. Дата, признаки и значения `replace-...` иллюстративны. `Asia/Almaty` и reporting delay `0` здесь явно считаются примерами допущений, а не подтверждением SCADA; реальные значения заполняет владелец модели:

```json
{
  "model_version": "replace-with-model-version",
  "training_cutoff": "2026-01-30T00:00:00Z",
  "target_unit": "normalized_power",
  "metadata": {
    "schema_version": "1.0",
    "is_fixture": false,
    "turbine_ids": ["turbine_1", "turbine_2"],
    "feature_columns": ["wind_speed_ms", "temperature_c"],
    "feature_dtypes": {"wind_speed_ms": "float64", "temperature_c": "float64"},
    "feature_units": {"wind_speed_ms": "m/s", "temperature_c": "degC"},
    "weather_provider": "NOAA GFS via AWS Open Data",
    "weather_model": "gfs_0p25",
    "wind_height_m": 100.0,
    "weather_feature_time_basis": "instant_at_end",
    "timestamp_convention": "hour_end",
    "history_timezone": "Asia/Almaty",
    "source_timestamp_convention": "replace-with-explicit-source-convention",
    "source_semantics_status": "assumed",
    "target_definition": "hourly mean normalized active power; normalization definition unresolved",
    "target_reporting_delay_seconds": 0,
    "selection_cutoff": "2026-01-30T00:00:00Z",
    "calibration_cutoff": "2026-01-30T00:00:00Z",
    "training_data_sha256": "replace-with-source-data-hash",
    "preprocessing": "replace-with-fitted-preprocessing-description",
    "clipping_policy": "none",
    "serialization_format": "joblib",
    "seed": 42,
    "package_versions": {},
    "validation": {"status": "replace-with-real-periods-and-metric-provenance"},
    "assumptions": ["Example timezone and reporting delay are not organizer confirmations"]
  }
}
```

`metadata` гибкий; реальные признаки и остальные сведения фиксирует №2. Объявите ожидаемые provider/model/height/time basis; несовпадение входа отклоняется. Платформа проверяет предоставленные контрактные значения и selection/calibration cutoff. Нечисловые значения числовых полей и `NaN`/`Inf` запрещены.

`normalized_power` не означает MW/MWh или подтверждённый диапазон `[0,1]`. При доказанных границах можно добавить `metadata.target_bounds=[lower, upper]`; иначе границы не задаются. Clipping явно описывается и одинаково применяется в validation/inference. Без калибровки интервалы неопределённости не обещаются.

Путь к каталогу разрешается относительно рабочего каталога запуска, обычно корня репозитория. Для однозначности используйте стандартный `model.joblib` и не задавайте необязательный `artifact_path`.

## Python-интерфейс

Модуль участника №2 — `wind_agent.model.interface`, файл — `src/wind_agent/model/interface.py`:

```python
from datetime import datetime
from pathlib import Path

import pandas as pd


def predict(
    model_artifact: str | Path,
    weather_frame: pd.DataFrame,
    issue_time: datetime,
    horizon_hours: int,
) -> pd.DataFrame:
    ...  # Inference пакета по пути model_artifact.
```

Первый аргумент внешнего `predict` — **путь к каталогу**, не Pydantic-объект и не загруженные веса. Pydantic `ModelArtifact` проверяет metadata внутри платформы. Участник №2 загружает `model.joblib` сам. `train(...)` возвращает путь к пакету; `evaluate(...)` — JSON-совместимый словарь. Агент не вызывает обучение.

`predict` не делает сетевых запросов, не обучается и не изменяет входной DataFrame. Один пакет поддерживает `turbine_1` и `turbine_2`; прогнозирует только для ID, присутствующих во входе. Платформа дополнительно проверяет наличие всех запрошенных пользователем целей.

## Входная погода

[Реальный погодный пример](evidence/weather-input-sample.json) получен из сохранённых NOAA-байтов: **полный горизонт 24 часа для обеих турбин, 48 строк**. Загрузите `records` в DataFrame, разберите временные колонки как aware UTC и передайте `horizon_hours=24`. Для горизонта 48 часов нужны 96 строк; их можно получить командой `weather-fetch`.

`weather_frame` содержит поля `WeatherRecord`; provenance-колонки допустимы, но в модель входят только объявленные признаки:

| Поля | Смысл |
| --- | --- |
| `turbine_id` | `turbine_1` или `turbine_2` |
| `valid_time` | Конец целевого интервала, aware UTC; GFS мгновенно в этот момент |
| `wind_speed_ms`, `wind_height_m` | m/s; текущий GFS-ветер на 100 m над землёй |
| `temperature_c` | °C из GFS-поля на 2 m |
| `model_run_time`, `lead_hours` | Инициализация погоды и lead **от неё**, не от энергетического issue |
| `forecast_available_at`, `availability_basis`, `availability_evidence` | Время допустимости и его основание |
| `provider`, `weather_model` | `NOAA GFS via AWS Open Data`, `gfs_0p25` |
| `raw_sha256`, `source_request`, `retrieved_at`, `provenance_kind` | Аудит исходных байтов; retrieved_at сегодня не является исторической публикацией |
| `latitude`, `longitude` | Координаты цели; ячейка GFS отдельно указана в provenance |

`availability_basis` — описательная строка `s3_last_modified_all_required_singlepart_grib_and_index_objects`, **без enum-ограничения `observed|assumed_delay`**. Это максимум S3 LastModified всех необходимых одночастных GRIB/индексов; multipart ETag отклоняется. Основание — metadata архива. Точная исходная публикация NOAA и история ACL bucket не восстановлены. [Описание источника и ограничения](weather-source.md) сохраняются в evidence; эта строка не означает наблюдение исходной публикации NOAA.

Агент проверяет все пары `(turbine_id, valid_time)`, один допустимый run на турбину и `model_run_time <= forecast_available_at <= issue_time`. Модель проверяет свою семантику, единицы и высоту. Сортировка — `(turbine_id, valid_time)`, pandas index не является идентификатором. Допустимы только концы интервалов `issue_time+1h...+Hh`.

Будущие фактические мощность/погода не передаются. Если модели нужны лаги наблюдений, контракт расширяется совместно с №1; скрытое чтение февральских фактов запрещено.

## Выход модели и ошибки

Минимальный DataFrame для совместимости: `turbine_id`, `valid_time`, `prediction`. Предпочтительный ответ №2 — полные колонки PR #2:

```text
schema_version, issue_time, turbine_id, valid_time, lead_hours, prediction,
target_unit, weather_model, weather_run_time, model_version, training_cutoff,
data_quality, warnings
```

Платформа добавляет `run_id`, `revision`. Остальные переданные контрактные поля проверяются на согласованность с запросом, погодой и metadata; противоречия не перезаписываются молча. `warnings` модели сохраняются и объединяются с предупреждениями платформы; проверенный `data_quality` сохраняется. Нужен ровно один конечный прогноз на каждую входную пару. Неописанные выходы и интервалы неопределённости требуют отдельного расширения контракта.

Ошибки модели v1 — `ValueError` с читаемым кодом в начале: `INVALID_SCHEMA`, `NAIVE_TIMESTAMP`, `UNSUPPORTED_TURBINE`, `INVALID_HORIZON`, `DUPLICATE_TARGET`, `INCOMPLETE_HORIZON`, `NONFINITE_FEATURE`, `WEATHER_NOT_AVAILABLE`, `MODEL_TRAINED_AFTER_ISSUE`, `FEATURE_SEMANTICS_MISMATCH`, `NONFINITE_PREDICTION`. Исключение отклоняет весь запрос; агент сохраняет `failed` и объяснение. Частично успешных результатов и тихого заполнения нулями нет. Ошибки самой платформы могут иметь собственный текст.

## Подключение и приёмка

В `config/archive.json` заполняются `model_adapter_module="wind_agent.model.interface"`, `model_artifact_path` как путь к каталогу, `history_timezone`, `train_cutoff`. Реальные зависимости модели фиксируются в `pyproject.toml` совместно с №1.

```powershell
python -m wind_agent --config config/archive.json health
python -m wind_agent --config config/archive.json run --issue-time 2026-01-31T18:00:00Z --horizon-hours 24
python -m wind_agent --config config/archive.json replay --horizon-hours 48
```

`health` показывает конфигурационную готовность; загрузку пакета и прогноз проверяет `run`. Перед расчётом перечитывается metadata. Fingerprint включает metadata, SHA256 весов и погоду; прежние входы сохраняют ревизию, новые допустимые входы создают следующую. Старые пакеты сохраняйте для воспроизведения старых ревизий. Metadata заменяйте атомарно между расчётами. После изменения Python-кода адаптера увеличьте `model_version` и перезапустите сервис: импортированный модуль автоматически не перезагружается.

Для приёмки №2 передаёт пакет, model-only тест на реальной погоде и проверку save/load в новом процессе. Январская validation использует те же provider/height/time semantics. Пока февральский факт отсутствует, точность февраля не заявляется; реальные validation-метрики и их provenance передаются для отдельного подключения `/evaluation`.
