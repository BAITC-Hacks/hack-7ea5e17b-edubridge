# Agentic AI для прогнозирования выработки ВЭС

HackAlem AI, трек «Энергетика», задача Самрук-Казына. Почасовые прогнозы двух турбин на 24/48 часов: архивная погода NOAA GFS → проверка доступности → обученная модель → анализ и ревизии → API и интерфейс.

**Реализовано:** строгие контракты; загрузка оперативных архивных GFS; raw cache и provenance; агент загрузка → проверка → модель → анализ → ревизия; FastAPI; CLI; календарь replay и контроль покрытия; тесты и CI.

**Интеграция:** пакет участника №2 `models/wind-power-v1` подключён к агенту и интерфейсу участника №3. `/evaluation` читает валидацию этого пакета с проверкой SHA256 весов и связи отчёта с metadata. Реальные HTTP-запросы проверены для всех шести сочетаний турбин и горизонтов, включая график и CSV. Исходные временные условия SCADA остаются явными предположениями.

Выбранная по tuning модель HGB имеет holdout MAE **0.226919**, простая wind-curve baseline — **0.209460**: на независимом периоде простая модель лучше. Выбор не менялся после просмотра holdout. Это ошибки нормализованной мощности, не проценты точности и не оценка февраля. [Model card](docs/model-card.md), [запуск и воспроизведение](docs/MODEL_REPRODUCIBILITY.md), [передача №1/№3 на казахском](docs/MODEL_HANDOFF_KK.md).

## Воспроизводимый запуск

Python **3.11**, Git, интернет для установки. Команды выполняются из корня репозитория; ключи API и платная подписка не нужны.

```sh
git clone https://github.com/BAITC-Hacks/hack-7ea5e17b-edubridge.git
cd hack-7ea5e17b-edubridge
python -m venv .venv
```

Windows PowerShell без изменения execution policy:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,weather,model]" -r ui/requirements.txt
.\.venv\Scripts\python.exe scripts/run_app.py
```

Linux/macOS: вместо `.\.venv\Scripts\python.exe` используйте `.venv/bin/python`. Extra `weather` устанавливает ecCodes, `model` — зависимости обученной модели. Прямые зависимости зафиксированы в `pyproject.toml` и `ui/requirements.txt`.

Откройте интерфейс <http://127.0.0.1:8501>, выберите **API**, нажмите «Проверить подключение». Для первого реального примера задайте **31.01.2026, 18:00 UTC**, обе турбины, **48 ч**, затем «Рассчитать прогноз». Дождитесь завершения; доступны график, источник и качество, оценка модели и «Скачать CSV». Интерфейс по умолчанию открывается в отдельном режиме «Демо»: для реального расчёта нужен переключатель **API**.

Swagger: <http://127.0.0.1:8000/docs>, состояние: <http://127.0.0.1:8000/health>. Запускатель использует `config/archive-model.json`, запускает оба сервиса на localhost и останавливает их по Ctrl+C. Другие порты: `python scripts/run_app.py --api-port 8010 --ui-port 8510`. Первый реальный расчёт скачивает архивные погодные данные и может занять несколько минут; ключи и исходная SCADA для применения сохранённой модели не нужны.

**Один процесс/worker на каталог артефактов.** API пишет в `artifacts/live`. Для параллельного replay используйте готовый `config/replay-february.json` с отдельным каталогом. Обычный CLI `run` с `config/archive-model.json` запускайте при остановленном API. При перезапуске незавершённые задания помечаются `failed`, их можно отправить снова.

Расчёт и полный replay без интерфейса:

```powershell
.\.venv\Scripts\python.exe -m wind_agent --config config/archive-model.json run --issue-time 2026-01-31T18:00:00Z --horizon-hours 48
.\.venv\Scripts\python.exe -m wind_agent --config config/replay-february.json replay --horizon-hours 48
```

Синтетическая проверка без погодной сети: `python scripts/run_app.py --config config/demo.json` или `python -m wind_agent --config config/demo.json replay --horizon-hours 48`. Это проверка платформы, а не прогноз ВЭС или доказательство точности.

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

Готовый пакет уже подключён в `config/archive-model.json`: модуль `wind_agent.model.interface`, пакет `models/wind-power-v1`, cutoff `2026-01-31T18:00Z`, явная предполагаемая timezone `Etc/GMT-5`. Следующие пункты описывают контракт для замены пакета; шаблон `config/archive.json` сохраняет незаполненные поля.

1. Подготовить модуль `predict(model_artifact, weather_frame, issue_time, horizon_hours) -> pandas.DataFrame`; первый аргумент — **путь каталога пакета** с `model.joblib` и `metadata.json` по схеме `ModelArtifact`. Обязательные колонки результата: `turbine_id`, `valid_time`, `prediction`; дополнительные поля контракта №2 проверяются, его предупреждения сохраняются.
2. В `config/archive.json` заполнить `model_adapter_module`, `model_artifact_path` (каталог пакета), **подтверждённые `history_timezone` и `train_cutoff`**, определив timezone и смысл timestamp истории. Граница не может быть позже конца января в зоне истории; фактический `ModelArtifact.training_cutoff` также должен быть не позже каждого `issue_time`.
3. Запустить команды ниже. Обучение не расширяется на февраль. Для первого выпуска 31 января в 23:00 местного времени нельзя использовать сведения, ставшие доступными позже, даже если они относятся к январю.

```powershell
.\.venv\Scripts\python.exe -m wind_agent --config config/archive.json health
.\.venv\Scripts\python.exe -m wind_agent --config config/archive.json run --issue-time 2026-01-31T18:00:00Z --horizon-hours 48
.\.venv\Scripts\python.exe -m wind_agent --config config/archive.json replay --horizon-hours 48
```

С незаполненным шаблоном `config/archive.json` команды мощности **завершаются ошибкой** о cutoff/отсутствующей модели. Готовый пакет запускается через `config/archive-model.json`. Платформа не подставляет fixture/нули вместо отсутствующей модели или погоды. `target_unit` берётся из модели; нормализованную мощность нельзя подписывать MW/MWh и суммировать без известного масштаба. [Точный интерфейс модели](docs/model-integration.md).

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

`/evaluation` отдельно показывает tuning, независимый holdout и окончательный production refit. Holdout относится к замороженной модели до финального дообучения; независимых метрик финального пакета и факта февраля нет. Порог готовности отчёта и поля описаны в [evaluation-api.md](docs/evaluation-api.md). На holdout wind-curve baseline лучше выбранного HGB; API сохраняет это предупреждение.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src/wind_agent tests/model tests/weather tests/agent scripts/run_app.py scripts/verify_app_integration.py scripts/verify_february_replay.py
.\.venv\Scripts\python.exe -m pip check
```

Тесты без сети: поздняя публикация, координаты, cutoff, полнота часов, retry/cache, multipart, ошибки провайдера/модели, идемпотентность, ревизии, границы февраля/марта, API/CLI. Реальные запросы отделены в probe.

**Проверено 23 сентября на Windows/Python 3.11.9:** **340 тестов проходят**. Реальная интеграционная проверка `python scripts/verify_app_integration.py --app-test` запускает отдельный HTTP API, проверяет шесть вариантов запроса, повторяемость ревизий, полное совпадение JSON/CSV, источник погоды и Streamlit с настоящим API. [Отчёт интеграции](docs/evidence/app-integration.json). Полный месячный расчёт и аудит доступны через `python scripts/verify_february_replay.py --horizon 48`; требования и границы проверки — в [replay-validation.md](docs/replay-validation.md).

Ранее отдельно проверялась чистая установка платформы: [исторический отчёт](docs/evidence/platform-validation.json). Workflow настроен на тесты, lint и синтетический replay, однако GitHub Actions **не смог запустить ни одного шага из-за billing-блокировки аккаунта**; успешный CI или Linux-проверку не заявляем.

## Команда и происхождение

| Участник | Ответственность | Ветка |
| --- | --- | --- |
| №1 | Погода, агент, контракты, API/CLI, replay, интеграция | `feat/agent-platform` |
| №2 | История, признаки, ML, временная валидация | `feat/forecast-model` |
| №3 | UI, графики, экспорт, демо | `feat/dashboard-demo` |

Изменения в `main` идут через PR; прямой push в `main`, force-push и автоматический merge не используются. Этапы фиксируются содержательными commit/push. Дедлайн — 23 сентября 2026, 18:00 по Астане.

[План](IMPLEMENTATION_PLAN.md) · [ТЗ](https://docs.google.com/document/d/1Fn5IJoj87Fx7IAknG26zkfX8c0eq7feCujd0m66PCgY/preview?tab=t.0) · [Регламент](https://edu.astanahub.com/hackathons/df4743f5-c492-415c-b45a-1f13adb78e06?tab=regulations)

Сторонние компоненты: Python, Pydantic, FastAPI/Starlette, HTTPX, pandas/NumPy, Uvicorn, ECMWF ecCodes, NOAA GFS и AWS Open Data; pytest/Ruff для проверки. Код платформы разрабатывается в официальном репозитории при помощи Codex. Fixture не выдаётся за архивные данные, обученную модель или измеренную точность.
