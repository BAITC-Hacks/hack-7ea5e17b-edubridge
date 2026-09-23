# Реальная validation через `/evaluation`

`wind_agent.agent.evaluation_report.evaluation_report(settings) -> dict` читает `metadata.json`, `validation.json` и SHA256 `model.joblib` из настроенного пакета. Функция не обучает модель, не загружает сериализованные веса и не делает сетевых запросов. `AgentService` предоставляет этот словарь через существующий `GET /evaluation`; generic JSON-отображение UI не требует отдельного формата графиков.

Состояния:

- `ready`: отчёт разобран, связан с metadata/весами и прошёл проверки семантики.
- `unavailable`: пакет не настроен, нужный файл отсутствует либо включён demo.
- `invalid`: JSON, метрики или их связь с пакетом некорректны; `reason` объясняет отказ. Поля `metrics` и `baseline` остаются `null`, ошибки не превращаются в нулевые метрики.

Demo всегда возвращает `unavailable`, `is_demo=true` и не читает метрики реального пакета, даже если его путь случайно задан. Для archive `is_demo=false`. `test_truth_available=false`: февральский факт не предоставлен; январские метрики не выдаются за февральскую точность.

## Разделение этапов

| Поле ответа | Что описывает |
| --- | --- |
| `metrics.tuning` | Оценку выбранного кандидата на периоде выбора, selection metric и cutoff |
| `metrics.independent_holdout` | Отдельный последующий период; модель после refit train+tune была заморожена до holdout |
| `metrics.production_refit` | Итоговое обучение выбранного алгоритма, включающее holdout; `independent_metrics=null` |
| `baseline.tuning` | `constant_mean` и `wind_curve` на tuning |
| `baseline.independent_holdout` | Те же baseline на независимом holdout |
| `baseline.comparison` | Сравнение equal-turbine MAE выбранного кандидата и wind curve на holdout |
| `periods` | Исходная split-конфигурация: timezone, исключённая нижняя/включённая верхняя границы target, origin и cutoff |
| `provenance` | SHA256 metadata, validation, весов и объявленные хеши исходных данных/эксперимента |

Отчёт текущего `wind-power-v1` сохраняет выбор `hgb_medium` по tuning MAE **0.232464**. На независимом holdout его MAE **0.226919**, а у `wind_curve` — **0.209460**: baseline лучше. Ответ явно сообщает это предупреждением и `wind_curve_better=true`. Этот факт не запускает новый выбор или переобучение. MAE/RMSE измеряются в `normalized_power`, не в процентах точности или MW/MWh.

Holdout относится к модели с cutoff `2026-01-14T19:00:00Z`. Финальный production-пакет переобучен до `2026-01-31T18:00:00Z`; его независимой оценки не предоставлено. Верхнеуровневые `model_version` и `training_cutoff` относятся к **настроенному production-пакету**, а cutoff оценённой holdout-модели расположен отдельно в `metrics.independent_holdout.training_cutoff`.

## Проверка связи и ограничения

В данном формате участника №2 `metadata.metadata.validation` содержит копию полного отчёта. Читатель требует её точное равенство `validation.json`, совпадение model version, production cutoff, выбранного алгоритма/параметров и source hashes. SHA256 реальных байтов `model.joblib` сравнивается с `metadata.model_sha256`. Проверяются конечные неотрицательные метрики, согласованные числа строк, turbine IDs, временные границы и отсутствие использования holdout для выбора.

Это контроль целостности и связи **локальных файлов**, не цифровая подпись автора и не повторная статистическая валидация. Исходные SCADA и weather-файлы заново не читаются (`source_files_rechecked=false`); их хеши отражаются как сведения исходного отчёта. Изменение всех согласованных файлов требует отдельного review и не становится доказанным экспериментом только благодаря совпадению хешей.

В `warnings` сохраняются ограничения из отчёта и metadata: один зимний период, зависимость перекрывающихся origins, общая ячейка GFS, неподтверждённые свойства SCADA и отсутствие февральского факта. Сведения о timezone берутся из пакета (`Etc/GMT-5`), а не заменяются на исторический `Asia/Almaty`.

Проверки находятся в `tests/agent/test_evaluation_report.py`: реальный пакет, разделение этапов, более сильный baseline, demo, отсутствующие/повреждённые файлы, подмена метрик или весов, чужая версия модели, несовпадающие source hashes и перекрывающиеся периоды.
