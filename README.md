# Agentic AI для прогнозирования выработки ВЭС

HackAlem AI, трек «Энергетика», задача Самрук-Казына. Платформа участника №1: почасовые прогнозы двух турбин на 24/48 часов и ежедневный исторический replay февраля 2026.

**Реализовано:** строгие контракты; загрузка оперативных архивных GFS; raw cache и provenance; агент загрузка → проверка → модель → анализ → ревизия; FastAPI; CLI; календарь replay и контроль покрытия; тесты и CI.

**Продукт пока не собран полностью:** модель, история ВЭС, её временная семантика и метрики — зона участника №2; UI — участника №3. Реальный прогноз мощности требует их интеграции. Синтетический режим проверяет платформу, **не является прогнозом ВЭС или доказательством точности**. `/evaluation` возвращает `unavailable` до интеграции метрик.

## Воспроизводимый запуск

Python **3.11**, Git, интернет для установки. Команды выполняются из корня репозитория; ключи API и платная подписка не нужны.

```sh
git clone https://github.com/BAITC-Hacks/hack-7ea5e17b-edubridge.git
cd hack-7ea5e17b-edubridge
git switch feat/agent-platform
python -m venv .venv
```

Windows PowerShell без изменения execution policy:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,weather]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m wind_agent --config config/demo.json run --issue-time 2026-01-31T18:00:00Z --horizon-hours 24
.\.venv\Scripts\python.exe -m wind_agent --config config/demo.json replay --horizon-hours 48
.\.venv\Scripts\python.exe -m wind_agent --config config/demo.json serve
```

Linux/macOS: вместо `.\.venv\Scripts\python.exe` используйте `.venv/bin/python`. Для платформы без GRIB достаточно `pip install -e ".[dev]"`; extra `weather` устанавливает ecCodes. Прямые зависимости зафиксированы в `pyproject.toml`.

API: <http://127.0.0.1:8000/docs>, режим и готовность: <http://127.0.0.1:8000/health>. Остановка — Ctrl+C. **Один процесс/worker на каталог артефактов**; для CLI replay одновременно с API задайте другой `artifact_dir`. При перезапуске незавершённые задания помечаются `failed`, их можно отправить снова.

Demo возвращает постоянный тестовый результат `0.5`, единицу `fixture_dimensionless`, `data_quality=fixture` и предупреждение `SYNTHETIC FIXTURE`. Обучения и метрик качества здесь нет. Повтор с теми же входами сохраняет ID и ревизию.

## Реальная архивная погода без модели

```powershell
.\.venv\Scripts\python.exe -m wind_agent --config config/archive.json weather-fetch --issue-time 2026-01-31T18:00:00Z --horizon-hours 24 --output artifacts/weather-fetch.json
.\.venv\Scripts\python.exe scripts/probe_weather.py
```

Первая команда сохраняет **48 реальных строк**: 24 часа × 2 турбины, без мощности. Вторая проверяет метаданные начала/середины/конца периода; `--fetch` скачивает полные горизонты. Первый запрос требует десятков MB и может занять несколько минут. GRIB ranges, индексы и списки объектов сохраняются в `artifacts/weather-cache`; повтор использует cache с проверкой SHA256 и метаданных.

Источник — NOAA GFS 0.25° в AWS Open Data: U/V ветра на 100 м, температура на 2 м, нативный почасовой шаг, ближайшая ячейка. Координаты подтверждены перенаправлениями ссылок из ТЗ. Обе турбины попадают в ячейку `43.75, 78.5`, поэтому погода одинакова — ограничение разрешения модели.

Для **всех** нужных GRIB и `.idx` проверяется `max(S3 LastModified) <= issue_time`; допускаются только single-part ETag установленного формата. У multipart `LastModified` может означать начало загрузки: такие объекты исключаются. Политика устанавливает время доступности конкретных архивных объектов, **не точное время исходной публикации NOAA**. Сегодняшний `retrieved_at` не участвует в историческом допуске. [Аудит и первичные источники](docs/weather-source.md), [свидетельства запросов](docs/evidence/).

## Подключение модели участника №2

1. Подготовить модуль `predict(model_artifact, weather_frame, issue_time, horizon_hours) -> pandas.DataFrame`; первый аргумент — **путь каталога пакета** с `model.joblib` и `metadata.json` по схеме `ModelArtifact`. Обязательные колонки результата: `turbine_id`, `valid_time`, `prediction`; дополнительные поля контракта №2 проверяются, его предупреждения сохраняются.
2. В `config/archive.json` заполнить `model_adapter_module`, `model_artifact_path` (каталог пакета), **подтверждённые `history_timezone` и `train_cutoff`**, определив timezone и смысл timestamp истории. Граница не может быть позже конца января в зоне истории; фактический `ModelArtifact.training_cutoff` также должен быть не позже каждого `issue_time`.
3. Запустить команды ниже. Обучение не расширяется на февраль. Для первого выпуска 31 января в 23:00 местного времени нельзя использовать сведения, ставшие доступными позже, даже если они относятся к январю.

```powershell
.\.venv\Scripts\python.exe -m wind_agent --config config/archive.json health
.\.venv\Scripts\python.exe -m wind_agent --config config/archive.json run --issue-time 2026-01-31T18:00:00Z --horizon-hours 48
.\.venv\Scripts\python.exe -m wind_agent --config config/archive.json replay --horizon-hours 48
```

С исходным `config/archive.json` команды мощности **завершаются ошибкой** о cutoff/отсутствующей модели. Это ожидаемый статус интеграции. Платформа не подставляет fixture/нули вместо отсутствующей модели или погоды. `target_unit` берётся из модели; нормализованную мощность нельзя подписывать MW/MWh и суммировать без известного масштаба. [Точный интерфейс модели](docs/model-integration.md).

## Replay и ревизии

Внутреннее соглашение для интеграции с [PR №2](https://github.com/BAITC-Hacks/hack-7ea5e17b-edubridge/pull/2): выпуск в **23:00 `Asia/Almaty` (UTC+5)**, `valid_time` — **конец** интервала `[valid_time−1h, valid_time)`, первый lead `+1h`. Погода GFS — мгновенное значение на конец интервала (`instant_at_end`), а не среднее за час. Это не подтверждает семантику исходной истории SCADA. Внутренние timestamps — aware UTC; naive запрещены.

- 29 выпусков: 31 января–28 февраля. Выпуск 28 февраля необходим для последнего февральского интервала, заканчивающегося 1 марта в 00:00. Горизонт48 сохраняет перекрытия и мартовские интервалы отдельно.
- `lead_hours` результата отсчитывается от `issue_time`, погоды — от `model_run_time`.
- В отдельную февральскую таблицу берётся последний `issue_time` для каждого целевого часа. Факт и ошибка в выборе не участвуют.
- Фильтр end-labels: `1 февраля 00:00 < valid_time <= 1 марта 00:00` в зоне replay. Интервал, заканчивающийся 1 февраля в 00:00, относится к январю.
- Проверяются **672 уникальных часа на турбину**, всего 1344 строки. При смене часа выпуска покрытие может быть неполным; это не скрывается.
- Replay продолжает остальные дни после ошибки, возвращает `partial`/`failed` и ненулевой exit code.

```text
<artifact_dir>/<mode>/runs/<run_id>/run.json
<artifact_dir>/<mode>/runs/<run_id>/revisions/<N>/forecast.json
<artifact_dir>/<mode>/runs/<run_id>/revisions/<N>/weather.json
<artifact_dir>/<mode>/runs/<run_id>/revisions/<N>/inputs.json
<artifact_dir>/<mode>/replay/2026-02-48h/report.json
<artifact_dir>/<mode>/replay/2026-02-48h/february.json
```

Журнал сохраняет этапы, ошибки и причину пересчёта. Fingerprint учитывает значения, доступность, raw SHA и metadata/байты модели. Новое время скачивания или путь cache сами по себе ревизию не создают. Новые допустимые входы создают новую ревизию того же ID; старые сохраняются. Поздний погодный выпуск запрещён для ревизии старого `issue_time`: новый момент расчёта требует нового `issue_time`.

## API и проверки

[Контракт API](docs/api-contract.md): `GET /health`, `POST /runs` (202), `GET /runs/{id}`, `/forecast`, `/forecast.csv`, `GET /evaluation`. UI получает ID и опрашивает состояние. CSV содержит те же записи, что JSON. Калиброванные интервалы неопределённости отсутствуют и не имитируются.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src/wind_agent/agent src/wind_agent/weather src/wind_agent/api src/wind_agent/contracts.py src/wind_agent/config.py src/wind_agent/cli.py
.\.venv\Scripts\python.exe -m pip check
```

Тесты без сети: поздняя публикация, координаты, cutoff, полнота часов, retry/cache, multipart, ошибки провайдера/модели, идемпотентность, ревизии, границы февраля/марта, API/CLI. Реальные запросы отделены в probe.

**Проверено 23 сентября:** отдельный чистый клон, Python 3.11.9/Windows, новая изолированная venv, установка `.[dev,weather]`, **79 прошедших тестов**, `pip check`, импорт ecCodes, demo24 и replay48 (29 выдач, 1344 строки без пропусков). [Машиночитаемый отчёт](docs/evidence/platform-validation.json). Ruff также прошёл в рабочем checkout. Workflow настроен на тесты, lint и синтетический replay, однако GitHub Actions **не смог запустить ни одного шага из-за billing-блокировки аккаунта**; успешный CI или Linux-проверку не заявляем.

## Команда и происхождение

| Участник | Ответственность | Ветка |
| --- | --- | --- |
| №1 | Погода, агент, контракты, API/CLI, replay, интеграция | `feat/agent-platform` |
| №2 | История, признаки, ML, временная валидация | `feat/forecast-model` |
| №3 | UI, графики, экспорт, демо | `feat/dashboard-demo` |

Изменения в `main` идут через PR; прямой push в `main`, force-push и автоматический merge не используются. Этапы фиксируются содержательными commit/push. Дедлайн — 23 сентября 2026, 18:00 по Астане.

[План](IMPLEMENTATION_PLAN.md) · [ТЗ](https://docs.google.com/document/d/1Fn5IJoj87Fx7IAknG26zkfX8c0eq7feCujd0m66PCgY/preview?tab=t.0) · [Регламент](https://edu.astanahub.com/hackathons/df4743f5-c492-415c-b45a-1f13adb78e06?tab=regulations)

Сторонние компоненты: Python, Pydantic, FastAPI/Starlette, HTTPX, pandas/NumPy, Uvicorn, ECMWF ecCodes, NOAA GFS и AWS Open Data; pytest/Ruff для проверки. Код платформы разрабатывается в официальном репозитории при помощи Codex. Fixture не выдаётся за архивные данные, обученную модель или измеренную точность.
