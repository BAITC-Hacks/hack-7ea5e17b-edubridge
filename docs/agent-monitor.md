# Автономное обновление прогнозов

`AgentMonitor` самостоятельно вызывает существующий цикл агента через заданный интервал, без HTTP-запросов и повторного нажатия кнопки. Он заново проверяет доступный погодный выпуск и пакет модели. При том же fingerprint сохраняется ревизия; изменившиеся допустимые входы вызывают пересчёт. Логи фиксируют проверку, решение, ревизию и причину ошибки.

## Запуск

После установки проекта с `.[dev,weather,model]`, из корня репозитория:

```sh
python -m wind_agent --config config/monitor-model.json watch --interval-seconds 60
```

Watch работает до `Ctrl+C`. Для ограниченной демонстрации используйте `--max-cycles 3`. Текущие события выводятся в stderr, итоговый JSON-отчёт — в stdout. Это режим с реальным архивом GFS и переданным пакетом модели; сохраняются предупреждения о непроверенных SCADA timezone, метках интервалов, задержке публикации и нормализации. Текущий календарный прогноз на основании январского пакета не является доказательством его качества вне исторического теста.

Историческая демонстрация с явными часами, без ожидания суток:

```sh
python -m wind_agent --config config/monitor-model.json watch --start-issue 2026-01-31T18:00:00Z --end-issue 2026-02-01T18:00:00Z --step-hours 24 --horizon-hours 48
```

Система последовательно сама запустит оба выпуска с исходными историческими cutoff. `--step-hours` задаёт шаг виртуального времени, `--end-issue` включителен. Даты должны быть timezone-aware, не идти назад и не превышать текущее время. Сеть нужна только для отсутствующих погодных данных и проверки актуальных метаданных; полный февральский replay повторять для этой демонстрации не требуется.

Эта команда проверена 23 сентября 2026 с настоящими GFS и моделью: два завершённых выпуска по 96 строк, без ошибок, lock освобождён. [Протокол CLI и локальных проверок](evidence/agent-platform-checks.json). Отдельный [протокол monitor](evidence/agent-monitor-integration.json) включает повтор неизменных входов и сверку API/CSV. Это проверка выполнения, а не точности прогноза или длительной работы по реальным часам.

Для тестового режима без сети существует отдельный `config/monitor-demo.json`. Его погода и прогнозы **синтетические**; такой запуск подтверждает только работу управления. Доказательство реального февральского расчёта находится в [replay-validation.md](replay-validation.md).

## Временная корректность

На каждом тике `issue_time = floor_hour(now_UTC)`. Например, тики `18:05Z` и `18:45Z` проверяют один выпуск `18:00Z`. Новая погода с доступностью `18:30Z` не допускается задним числом в него. На тике `19:00Z` создаётся **новый** выпуск с новым cutoff, и такой погодный источник уже может пройти обычные проверки агента. Старые ревизии и горизонты сохраняются.

Поэтому рост времени и обновление входов различаются:

- тот же час и те же входы: `action=unchanged`, прежние ID и revision;
- тот же час и новые допустимые входы: `action=recalculated`, новая revision того же ID;
- следующий UTC-час: `action=new_issue`, новый ID и новый полный горизонт;
- более поздний погодный выпуск не становится допустимым только из-за нового `retrieved_at`.

Состояние последнего `as_of` сохраняется на диске. После перезапуска нельзя вернуться к более раннему часу. Для другой исторической последовательности или перехода с live-времени обратно в февраль используйте отдельный `artifact_dir`. Это не подтверждает исходную временную семантику SCADA: фиксированный UTC+5 и другие условия пакета модели остаются явными предположениями.

## Ошибки, владение и хранение

На временной ошибке monitor назначает повтор через 5, затем 10 секунд; задержка ограничена 300 секундами и не меньше выбранного интервала опроса. Максимум три последовательные неудачные попытки текущего часа заканчивают сессию статусом `halted`. Успех обнуляет счётчик. Ранний тик до `next_retry_at` не вызывает провайдера. Новый UTC-час начинает новую ограниченную последовательность попыток, сохраняя прежний исторический cutoff. HTTP retry самого погодного адаптера также ограничен.

CLI **всегда добавляет `monitor-jobs`** к `settings.artifact_dir` и берёт owner lock **до** создания `AgentService`. Так повторно использованная API-конфигурация не заставит watch объявить активную задачу API прерванной. Для `config/monitor-model.json` фактические пути:

```text
artifacts/agent-monitor/monitor-jobs/archive/runs/<run_id>/...
artifacts/agent-monitor/monitor-jobs/archive/monitor/status.json
artifacts/agent-monitor/monitor-jobs/archive/monitor/events.jsonl
artifacts/agent-monitor/monitor-jobs/archive/monitor/events.previous.jsonl
artifacts/agent-monitor/monitor-jobs/archive/monitor/last_session.json
artifacts/agent-monitor/monitor-jobs/archive/monitor/owner.lock
```

Один каталог обслуживает один процесс-владелец. Не направляйте API непосредственно в `monitor-jobs` и не запускайте второй watch в нём. Lock защищает monitor, а не произвольный API-процесс. Если процесс аварийно завершился и lock остался, сначала подтвердите, что его PID больше не работает; автоматического удаления такого lock нет. Штатная остановка освобождает его.

Monitor хранит только последнее состояние, обрабатывает исторические тики лениво и ротирует event-log: текущий файл до примерно 5 MB плюс один предыдущий. История прогнозов и служебные записи самого `AgentService` сохраняются по существующей политике платформы; они не выдаются за бесконечно ограниченное хранилище. Интервал опроса обязан быть конечным и не меньше секунды; ожидание прерывается stop-event, активного бесконечного цикла без ожидания нет.

`status.json` содержит `as_of`, `issue_time`, `status`, `action`, `run_id`, `revision`, `input_fingerprint`, `consecutive_failures`, `next_retry_at`, `error`, `executed` и `issue_advanced`. Если анализ доступен, `analysis` добавляет `decision`, `next_action` и `reasons`. Эти рекомендации не меняют числовой прогноз или временные ограничения. Ошибка progress-callback сохраняется отдельно в `callback-error.json` и не превращает успешный прогноз в неуспешный.

## Python-интерфейс

```python
from threading import Event
from wind_agent.agent import AgentService
from wind_agent.agent.monitor import AgentMonitor, acquire_monitor_owner
from wind_agent.config import load_settings

settings = load_settings("config/monitor-model.json")
# Прямой Python-интерфейс не добавляет CLI-подкаталог monitor-jobs.
# Этот artifact_dir должен быть отдельным от API.
with acquire_monitor_owner(settings.artifact_dir / settings.mode):
    service = AgentService(settings)
    monitor = AgentMonitor(service, on_tick=print)
    try:
        report = monitor.run(60, Event(), max_cycles=3, owner_lock=False)
    finally:
        if service.weather is not None:
            service.weather.close()
```

Низкоуровневый `.tick(aware_datetime)` выполняет одну проверку и возвращает JSON-совместимый state. `.run(interval_seconds, stop_event, max_cycles=None, clock=utcnow, wait=None)` автоматически повторяет её; clock и wait подменяются в тестах. `.run_history(iterable_of_aware_ticks, stop_event=None, clock=utcnow)` последовательно обрабатывает явные прошедшие моменты без задержки. Обе сессии возвращают `status`, `tick_count`, `executed_runs`, `completed_runs`, `failed_runs`, `deferred_ticks`, `last_state`; live `.run` также возвращает `cycles`. `failed_runs` считает неудачные попытки, даже если последующая попытка восстановилась.

Готовый GFS-адаптер уже обновляет S3 listing при каждом `fetch`; cache индекс/GRIB привязан к URL, диапазону и ETag, содержимое проверяется по SHA256. Поэтому monitor не отключает cache и не заменяет погодный провайдер. Подробности происхождения и ограничения публикационного времени остаются в [weather-source.md](weather-source.md).

Проверки без сети:

```sh
python -m pytest tests/agent/test_monitor.py -q
```

Тест цикла `.run` автоматически получает revision `1 → 1 → 2` при обновлении тестового источника. Отдельный тест получает `1 → 2` после изменения модели при неизменной погоде. Проверяются новый час, отсутствие утечки от поздней погоды, backoff/halt, восстановление состояния, прекращение по stop-event, owner lock и ротация журналов. Тестовые изменения источника не выдаются за доказательство нового реального погодного выпуска.
