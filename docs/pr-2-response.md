# Ответ участника №1 по контракту PR #2

Рассмотрен [MODEL_IO_CONTRACT.md из PR #2](https://github.com/BAITC-Hacks/hack-7ea5e17b-edubridge/pull/2). Ниже — конкретные решения интеграции в `feat/agent-platform`. Ответ сохранён в репозитории; комментарий в PR автоматически не опубликован, PR не слит. Участнику №2 нужно отразить эти решения в своей спецификации и реализации.

## 1. Время

Принимаем **конец интервала**: `valid_time=t` означает `[t-1h, t)`, lead — `1..H` от `issue_time`, aware UTC. API/UI подписывает «конец интервала» либо отображает начало и конец. Исходный смысл SCADA timestamp этим не подтверждается.

Ежедневный origin — 23:00 `Asia/Almaty`, явное командное допущение. Replay содержит **29 выпусков, 31 января — 28 февраля включительно**. Февраль отбирается по `Feb 1 00:00 < valid_time <= Mar 1 00:00` в этой зоне: 672 интервала на турбину. Для перекрывающихся прогнозов выбирается последний допустимый origin; фактическая ошибка не участвует. Полные горизонты и ревизии сохраняются, включая интервалы вне февраля.

## 2. Погода

Названия колонок PR #2 принимаем; общий `WeatherRecord` добавляет coordinates, source request и остальные provenance-поля. Реальный источник — `provider="NOAA GFS via AWS Open Data"`, `weather_model="gfs_0p25"`; ветер в m/s на **100 m**, температура в °C из поля на **2 m**. Ближайшая ячейка для обеих турбин — 43.75°N, 78.50°E. Временной смысл — **`instant_at_end`**, не среднее за час; погодные timestamps не сдвигаются.

Точное изменение к предложению: `availability_basis` остаётся описательной строкой, **без ограничения `observed|assumed_delay`**. Текущее значение — `s3_last_modified_all_required_singlepart_grib_and_index_objects`; `forecast_available_at` — максимум S3 LastModified всех необходимых GRIB/индексов, multipart исключён. Это metadata архива; точная исходная публикация NOAA и история ACL не восстановлены. `retrieved_at` не используется как историческая доступность. [Политика и evidence](weather-source.md).

[Реальный погодный пример](evidence/weather-input-sample.json) подготовлен из сохранённых NOAA-байтов: **24 часа для обеих турбин, 48 строк**, с полным provenance. После загрузки `records` в DataFrame и разбора временных колонок как aware UTC он подходит для `predict(..., horizon_hours=24)`. Другие горизонты получаются командой `weather-fetch`. Участник №2 проверяет provider/model/height/time basis и использует такую же семантику при январской validation.

## 3. Интерфейс

Принимаем `wind_agent.model.interface.predict(package_directory, weather_frame, issue_time, horizon_hours) -> pandas.DataFrame`. Каталог содержит **`model.joblib` + `metadata.json`**. `train` возвращает путь к каталогу; `evaluate` — JSON-совместимый словарь. ID: `turbine_1`, `turbine_2`. Inference не обучается, не делает сетевых запросов и не меняет входной DataFrame.

Изменение к metadata: используем существующую строгую оболочку `wind_agent.contracts.ModelArtifact`: обязательные верхние поля `model_version`, `training_cutoff`, `target_unit`, `metadata`; необязательный `artifact_path` по умолчанию определяется bridge как `model.joblib`. Все остальные поля спецификации помещаются **внутрь `metadata`**, включая schema version, семантику, feature list и validation provenance. Первый аргумент внешнего `predict` при этом остаётся **путём**, не Pydantic-объектом. [Точный JSON и инструкция](model-integration.md).

Полные выходные колонки PR #2 поддерживаются. Минимум для совместимости — `turbine_id`, `valid_time`, `prediction`; остальные контрактные поля при наличии проверяются, противоречия не затираются. `warnings` и `data_quality` модели сохраняются; агент добавляет свои предупреждения, `run_id`, `revision`. `target_unit="normalized_power"` принимается без объявления MW/MWh или подтверждённого `[0,1]`.

Принимаем перечисленные в PR коды `ValueError`; ошибка отклоняет весь запрос. Общие Pydantic-схемы уже доступны в `wind_agent.contracts`; модель может их переиспользовать. Интервалы неопределённости добавляются только после отдельного согласования и калибровки.

## 4. Неизвестная семантика источника

Подтверждения организатора о timezone SCADA, исходных labels, задержке публикации цели и определении нормализации **пока нет**. Не выдаём внутренние `hour_end`/`instant_at_end` за такое подтверждение. Перед fitting участник №2 фиксирует установленную семантику либо конкретные явные допущения в metadata/model card и предупреждениях.

Реальный расчёт требует явные `settings.history_timezone` (IANA) и `settings.train_cutoff`; оба остаются `null` в поставляемом archive config. Cutoff не позже начала 1 февраля в timezone истории. Артефакт дополнительно удовлетворяет `training_cutoff <= issue_time`, в том числе для первого origin 31 января 23:00. Все fitting, imputation, scaling, calibration и model selection подчинены общей информационной границе; для целевых наблюдений учитывается reporting delay. Февральские факты не используются при обучении/выборе модели.

Следующий предметный handoff от №2: пакет, model-only тест на реальной погоде, проверка save/load, объявленные допущения и validation-метрики. После этого выполняется интеграционный расчёт; февральская точность без test truth не обещается.
