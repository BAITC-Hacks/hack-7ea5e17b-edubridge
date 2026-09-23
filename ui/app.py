"""Role 3 dashboard. Run from repository root: python ui/run.py."""
from __future__ import annotations

import html
import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import plotly.graph_objects as go
import streamlit as st

from ui.client import ApiClient, ApiError
from ui.fixtures import DemoClient
from ui.presentation import (ForecastError, collect_warnings, display_time,
                             observed_points, turbine_label, validate_forecast,
                             validate_run_identity, validate_csv, weather_provenance)

st.set_page_config(page_title="Ветропрогноз · HackAlem AI", page_icon="🌬️", layout="wide")

STATUS = {
    "queued": "В очереди", "fetching_weather": "Получение погоды", "validating": "Проверка данных",
    "forecasting": "Прогнозирование", "analysing": "Анализ результата", "completed": "Расчёт завершён",
    "failed": "Расчёт не выполнен",
}
ACTIVE = set(STATUS) - {"completed", "failed"}
COLORS = ["#12856F", "#527EBE", "#A57A43", "#9D67B0"]

st.markdown("""<style>
    .stApp {background:#F5F7F8;color:#182F38}
    [data-testid="stHeader"] {background:rgba(245,247,248,.92)}
    [data-testid="stSidebar"] {background:#FFFFFF;border-right:1px solid #E2E8E9}
    [data-testid="stSidebar"] .stMarkdown p {color:#657981}
    .block-container {max-width:1480px;padding-top:2.3rem;padding-bottom:2rem}
    h1,h2,h3 {color:#17343C;letter-spacing:-.035em}
    h1 {font-size:2.5rem!important;font-weight:650!important;line-height:1.13!important}
    h3 {font-size:1.15rem!important}
    [data-testid="stMetric"] {background:#FFF;border:1px solid #E1E8E8;border-radius:14px;padding:18px 20px}
    [data-testid="stMetricValue"] {color:#173F43;font-variant-numeric:tabular-nums;font-size:2rem}
    [data-testid="stVerticalBlockBorderWrapper"]>div {border-radius:14px}
    [data-testid="stForm"] {border:0;padding:0}
    button[kind="primary"] {background:#137C68;border:1px solid #137C68;border-radius:8px}
    button[kind="primary"]:hover {background:#0D6756;border-color:#0D6756}
    .brand {display:flex;gap:12px;align-items:center;margin:8px 0 28px}
    .brand-icon {width:40px;height:40px;border-radius:11px;background:#137C68;color:white;display:flex;align-items:center;justify-content:center;font-size:25px}
    .brand-title {font-size:17px;font-weight:700;letter-spacing:-.02em;color:#173F43}
    .eyebrow {font-size:11px;letter-spacing:.14em;font-weight:700;color:#698289;margin-bottom:12px}
    .subtle {color:#6B7E84;font-size:13px;line-height:1.7}
    .demo-banner {border:1px solid #EAD9B6;background:#FFF9EE;color:#72552A;border-radius:10px;padding:12px 16px;font-size:13px;margin:5px 0 21px}
    .step {padding:9px 0;border-bottom:1px solid #EEF1F1;display:flex;gap:10px;align-items:center;font-size:13px}
    .step-dot {width:21px;height:21px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;background:#ECF1F2;color:#7B8E93;font-size:11px;flex-shrink:0}
    .step-dot.done {background:#E1F3EB;color:#14765E}
    .step-dot.running {background:#DAEBFD;color:#326CA8}
    .empty {padding:42px 32px;border:1px dashed #CCDADD;background:#FFF;border-radius:16px;text-align:center;margin-top:18px}
    .empty strong {font-size:23px;font-weight:600;display:block;margin-bottom:12px}
    .footer {font-size:11px;color:#87989D;border-top:1px solid #DFE7E8;margin-top:26px;padding-top:14px}
    @media(max-width:700px){h1{font-size:1.9rem!important}.block-container{padding-top:1.5rem}}
</style>""", unsafe_allow_html=True)


def initialize_context(mode: str, url: str, timeout: float) -> None:
    key = (mode, url if mode == "API" else "demo", timeout)
    if st.session_state.get("context") == key:
        return
    # Commit context only after validation succeeds: an invalid API URL must
    # never reuse the previous demo client on a subsequent rerender.
    client = DemoClient() if mode == "Демо" else ApiClient(url, timeout)
    st.session_state.context = key
    st.session_state.client = client
    st.session_state.history = {}
    st.session_state.current_id = None
    st.session_state.health_result = None
    st.session_state.connection_error = None
    st.session_state.launch_error = None
    if mode == "Демо":
        request = {"issue_time": "2026-02-01T00:00:00Z", "horizon_hours": 48,
                   "turbine_ids": ["turbine_1", "turbine_2"]}
        begin_run(request)
        for _ in range(6):
            refresh_run()


def begin_run(request: dict) -> None:
    run = st.session_state.client.create_run(**request)
    run_id = run["run_id"]
    # Repeated real requests may be idempotent; preserve the backend's run/revision.
    st.session_state.history[run_id] = {"run": run, "request": request, "rows": None,
                                        "error": None, "csv": None, "csv_error": None,
                                        "evaluation": None, "evaluation_error": None}
    st.session_state.current_id = run_id
    st.session_state.launch_error = None


def refresh_run(force: bool = False) -> None:
    entry = current_entry()
    if entry is None:
        return
    client = st.session_state.client
    run_id = st.session_state.current_id
    try:
        if force or entry["run"].get("status") in ACTIVE:
            latest = client.get_run(run_id)
            if (latest.get("revision") != entry["run"].get("revision")
                    or latest.get("status") != "completed"):
                entry.update(rows=None, csv=None, evaluation=None)
            entry["run"] = latest
        status = entry["run"].get("status")
        if status not in STATUS:
            raise ForecastError(f"Неизвестное состояние расчёта: {status}.")
        if status == "completed" and (entry["rows"] is None or force):
            entry["rows"] = validate_forecast(client.get_forecast(run_id), entry["request"])
            validate_run_identity(entry["rows"], entry["run"])
            entry["csv"] = None
            try:
                entry["csv"] = validate_csv(client.get_forecast_csv(run_id), entry["rows"], entry["run"])
                entry["csv_error"] = None
            except (ApiError, ForecastError) as exc:
                entry["csv_error"] = str(exc)
            entry["evaluation"] = None
            try:
                entry["evaluation"] = client.get_evaluation()
                entry["evaluation_error"] = None
            except ApiError as exc:
                entry["evaluation_error"] = str(exc)
        entry["error"] = None
    except (ApiError, ForecastError) as exc:
        entry["error"] = str(exc)
        # A stale valid result must not survive a failed refresh as current data.
        if isinstance(exc, ForecastError):
            entry["rows"] = None
            entry["csv"] = None


def current_entry() -> dict | None:
    return st.session_state.history.get(st.session_state.current_id)


def is_synthetic(entry: dict) -> bool:
    return (st.session_state.context[0] == "Демо"
            or entry["run"].get("source_mode") == "synthetic"
            or any(row.get("source_mode") == "synthetic" or row.get("data_quality") == "fixture"
                   or row.get("target_unit") == "fixture_dimensionless" for row in entry["rows"] or []))


def value_label(rows: list[dict]) -> str:
    return "Демонстрационное значение" if rows and rows[0]["target_unit"] == "fixture_dimensionless" else "Нормализованная мощность"


def render_steps(run: dict) -> None:
    steps = run.get("steps") or []
    events = run.get("events") or []
    if not steps and isinstance(events, list):
        steps = [{"name": event.get("step", "Шаг"),
                  "status": ("failed" if event.get("step") == "failed" else
                             "running" if event.get("step") == run.get("status") and run.get("status") in ACTIVE else "completed"),
                  "message": event.get("message", ""), "at": event.get("at")}
                 for event in events[-6:] if isinstance(event, dict)]
    if not isinstance(steps, list) or not steps:
        st.caption("Сервис не предоставил подробный журнал шагов.")
        return
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            st.text(str(step))
            continue
        state = step.get("status", "pending")
        done = state in {"completed", "success", "done"}
        css = "done" if done else "running" if state == "running" else ""
        symbol = "✓" if done else "!" if state == "failed" else str(index + 1)
        name = str(step.get("name", step.get("step", "Шаг")))
        label = STATUS.get(name, name)
        st.markdown(f'<div class="step"><span class="step-dot {css}">{symbol}</span>'
                    f'<span>{html.escape(label)}</span></div>', unsafe_allow_html=True)
    with st.expander("Подробный журнал"):
        st.json(events or steps, expanded=False)


def forecast_figure(rows: list[dict], zone: str, synthetic: bool) -> go.Figure:
    fig = go.Figure()
    actuals = observed_points(rows, synthetic)
    for index, turbine in enumerate(dict.fromkeys(row["turbine_id"] for row in rows)):
        series = [row for row in rows if row["turbine_id"] == turbine]
        color = COLORS[index % len(COLORS)]
        # Plotly uses local strings intentionally: browser timezone must not shift labels.
        x = [row["_valid_utc"].astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M") for row in series]
        fig.add_trace(go.Scatter(x=x, y=[row["prediction"] for row in series],
                                name=turbine_label(turbine), mode="lines", line={"color": color, "width": 3},
                                hovertemplate="%{x}<br>" + value_label(rows) + ": %{y:.3f}<extra>%{fullData.name}</extra>"))
        observed = [row for row in actuals if row["turbine_id"] == turbine]
        if observed:
            fig.add_trace(go.Scatter(
                x=[row["_valid_utc"].astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M") for row in observed],
                y=[row["actual"] for row in observed], name=f"{turbine_label(turbine)} · факт",
                mode="lines", line={"color": color, "width": 2, "dash": "dot"}))
    fig.update_layout(height=345, margin={"l": 5, "r": 16, "t": 15, "b": 15},
                      font={"family": "Arial, sans-serif", "color": "#60757D", "size": 12},
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend={"orientation": "h", "y": 1.16, "x": 0}, hovermode="x unified",
                      xaxis={"type": "date", "tickformat": "%d %b\n%H:%M", "showgrid": False,
                             "title": f"Время прогноза · {zone}", "zeroline": False},
                      yaxis={"title": value_label(rows), "gridcolor": "#E9EEEF", "zeroline": False})
    return fig


def render_provenance(entry: dict, zone: str) -> None:
    rows = entry["rows"] or []
    run = entry["run"]
    if is_synthetic(entry):
        st.info("Показан синтетический пример. Погодные значения и версия модели условные; модель не обучалась.")
    else:
        st.caption("Время скачивания сегодня не доказывает доступность погодного выпуска в прошлом.")
    provenance = weather_provenance(run, rows)
    st.dataframe(provenance, width="stretch", hide_index=True)
    st.caption("Метаданные получены из прогноза и журнала соответствующего погодного выпуска. Для проверки исторической доступности нужны исходные артефакты сервиса.")
    if rows and not is_synthetic(entry) and any(row.get("forecast_available_at") in (None, "", "Не предоставлено") for row in provenance):
        st.warning("Время исторической доступности погоды не предоставлено. Соответствие этому ограничению здесь не подтверждено.")
    warnings = collect_warnings(run, rows)
    if warnings:
        st.markdown("**Предупреждения источника**")
        for warning in warnings:
            st.warning(warning)
    quality = [{"Турбина": turbine_label(row["turbine_id"]), "Время": display_time(row["valid_time"], zone),
                "Качество": str(row.get("data_quality", "Не предоставлено"))} for row in rows]
    with st.expander("Качество по часам"):
        st.dataframe(quality, width="stretch", hide_index=True)
    with st.expander("Ответ сервиса о расчёте"):
        st.json(run, expanded=False)


def render_evaluation(entry: dict) -> None:
    evaluation = entry["evaluation"]
    if is_synthetic(entry):
        st.info("Точность демо не измеряется. Здесь нет обученной модели и фактической выработки.")
    elif entry["evaluation_error"]:
        st.warning("Отчёт оценки недоступен: " + entry["evaluation_error"])
    elif not evaluation or evaluation.get("status") == "unavailable":
        st.info("Сервис пока не предоставил отчёт валидации модели.")
    else:
        st.markdown("**Отчёт валидации от сервиса**")
        st.caption("MAE/RMSE нужно читать вместе с единицами, периодом, горизонтом и baseline. Это не автоматически точность за февраль.")
        st.json(evaluation, expanded=True)
    if not evaluation or evaluation.get("test_truth_available") is not True:
        st.caption("Факт за февраль 2026 не подтверждён. Метрики тестового февраля не показаны.")


with st.sidebar:
    st.markdown('<div class="brand"><div class="brand-icon">≋</div><div><div class="brand-title">Ветропрогноз</div>'
                '<div class="subtle">HackAlem AI · Энергетика</div></div></div>', unsafe_allow_html=True)
    mode = st.segmented_control("Режим", ["Демо", "API"], default="Демо", key="mode") or "Демо"
    api_url = os.getenv("WIND_API_BASE_URL", "http://127.0.0.1:8000")
    try:
        timeout = float(os.getenv("WIND_API_TIMEOUT", "10"))
    except ValueError:
        timeout = 10.0
    if mode == "API":
        with st.expander("Подключение API", expanded=True):
            api_url = st.text_input("Адрес сервиса", value=api_url)
            timeout = st.number_input("Тайм-аут, секунд", min_value=1.0, max_value=60.0,
                                      value=min(60.0, max(1.0, timeout)), step=1.0)
    try:
        initialize_context(mode, api_url, timeout)
    except ApiError as exc:
        st.error(str(exc))
        st.stop()
    if mode == "API":
        if st.button("Проверить подключение", width="stretch"):
            try:
                st.session_state.health_result = st.session_state.client.health()
                st.session_state.connection_error = None
            except ApiError as exc:
                st.session_state.connection_error = str(exc)
                st.session_state.health_result = None
        if st.session_state.connection_error:
            st.error(st.session_state.connection_error)
        if st.session_state.health_result:
            health = st.session_state.health_result
            if health.get("is_demo") is True or health.get("mode") == "demo":
                st.warning("API работает в синтетическом демо-режиме.")
            elif health.get("model_ready") is True and (health.get("data_ready") is True or health.get("weather_ready") is True):
                st.success("Сервис, модель и данные готовы")
            else:
                st.info("Сервис отвечает. Готовность модели и данных проверьте в ответе.")
            with st.expander("Ответ /health"):
                st.json(health, expanded=False)
    st.divider()
    st.markdown("**Параметры выпуска**")
    with st.form("forecast_controls"):
        issue_date = st.date_input("Дата исторического выпуска", value=date(2026, 2, 1),
                                    min_value=date(2026, 1, 31), max_value=date(2026, 2, 28))
        issue_hour = st.selectbox("Время выпуска (UTC)", list(range(24)), format_func=lambda hour: f"{hour:02}:00")
        ids = ["turbine_1", "turbine_2"]
        health = st.session_state.health_result or {}
        if isinstance(health.get("turbine_ids"), list) and health["turbine_ids"]:
            ids = [str(value) for value in health["turbine_ids"]]
        turbines = st.multiselect("Турбины", ids, default=ids, format_func=turbine_label)
        horizon = st.radio("Горизонт", [24, 48], index=1, horizontal=True, format_func=lambda value: f"{value} ч")
        submitted = st.form_submit_button("Рассчитать прогноз", type="primary", width="stretch")
    if submitted:
        request = {"issue_time": datetime.combine(issue_date, time(issue_hour), tzinfo=timezone.utc).isoformat(),
                   "horizon_hours": horizon, "turbine_ids": turbines}
        if not turbines:
            st.session_state.launch_error = "Выберите хотя бы одну турбину."
        else:
            try:
                begin_run(request)
            except ApiError as exc:
                st.session_state.launch_error = str(exc)
    display_zone = st.selectbox("Часовой пояс графика", ["UTC", "Asia/Almaty"], key="display_zone")
    st.caption("Момент выпуска вводится в UTC. Часовой пояс графика меняет только отображение.")
    history = st.session_state.history
    if len(history) > 1:
        selected = st.selectbox("История запусков", list(history), index=list(history).index(st.session_state.current_id),
                                format_func=lambda rid: f"{rid} · рев. {history[rid]['run'].get('revision', '—')}")
        st.session_state.current_id = selected
    st.divider()
    st.caption("Нормализованную мощность нельзя переводить в МВт без номинала турбин. Условные значения API-демо подписываются отдельно.")

st.markdown('<div class="eyebrow">ПЛАНИРОВАНИЕ ВЫРАБОТКИ</div>', unsafe_allow_html=True)
st.title("Почасовой прогноз ветроэнергии")
st.caption("Два источника энергии. Один обзор погоды, расчёта и качества данных.")
if mode == "Демо":
    st.markdown('<div class="demo-banner"><strong>Демонстрационный режим.</strong> Синтетические данные для проверки интерфейса. Это не реальный прогноз.</div>', unsafe_allow_html=True)
if st.session_state.launch_error:
    st.error("Не удалось запустить новый расчёт: " + st.session_state.launch_error)

entry = current_entry()
polling = bool(entry and entry["run"].get("status") in ACTIVE and not entry["error"])


@st.fragment(run_every="2s" if polling else None)
def render_result() -> None:
    entry = current_entry()
    if entry is None:
        st.markdown('<div class="empty"><strong>Выберите исторический выпуск</strong>'
                    '<span class="subtle">Проверьте подключение к API, задайте турбины и горизонт, затем запустите расчёт.</span></div>', unsafe_allow_html=True)
        return
    if entry["run"].get("status") == "completed" and entry["rows"] is None and not entry["error"]:
        refresh_run()
    was_active = entry["run"].get("status") in ACTIVE
    if was_active and not entry["error"]:
        refresh_run()
        if entry["run"].get("status") not in ACTIVE or entry["error"]:
            st.rerun()
    run = entry["run"]
    rows = entry["rows"] or []
    synthetic = is_synthetic(entry)
    quantity = value_label(rows)
    left, right = st.columns([3, 2], vertical_alignment="center")
    with left:
        st.markdown(f"**Выпуск {display_time(entry['request']['issue_time'], display_zone)}**")
        st.caption(f"{run.get('run_id', st.session_state.current_id)} · Ревизия {run.get('revision', 'не указана')}")
    with right:
        a, b = st.columns(2)
        if a.button("Обновить статус", width="stretch"):
            refresh_run(force=True)
            st.rerun()
        if b.button("Повторить расчёт", width="stretch", disabled=run.get("status") in ACTIVE):
            try:
                begin_run(entry["request"])
            except ApiError as exc:
                st.session_state.launch_error = str(exc)
            st.rerun()
    if synthetic and mode == "API":
        st.warning("API вернул синтетические данные. Этот результат не является реальным прогнозом.")
    if entry["error"]:
        st.error("Результат не подтверждён: " + entry["error"])
        st.caption("Автоматический опрос приостановлен. Проверьте сервис и нажмите «Обновить статус».")
        return
    if run.get("status") == "failed":
        st.error("Расчёт не выполнен: " + str(run.get("error") or run.get("message") or "Причина не передана сервисом"))
        render_steps(run)
        return
    if not rows:
        st.info(STATUS.get(run.get("status"), "Ожидание результата"))
        render_steps(run)
        st.caption("Статус обновляется автоматически каждые 2 секунды. Интерфейс остаётся доступным.")
        return
    metrics = st.columns(len(entry["request"]["turbine_ids"]) + 2)
    for index, turbine in enumerate(entry["request"]["turbine_ids"]):
        values = [row["prediction"] for row in rows if row["turbine_id"] == turbine]
        metrics[index].metric(turbine_label(turbine), f"{sum(values) / len(values):.3f}",
                              help=f"{quantity}: среднее за выбранный горизонт. Не МВт/МВт·ч.")
    metrics[-2].metric("Горизонт", f"{entry['request']['horizon_hours']} ч")
    metrics[-1].metric("Почасовых значений", str(len(rows)))
    st.caption(f"{quantity}: на карточках показано среднее за горизонт. Значения турбин не суммируются.")
    forecast_tab, source_tab, evaluation_tab = st.tabs(["Прогноз", "Источник и качество", "Оценка модели"])
    with forecast_tab:
        chart, process = st.columns([3.3, 1.25], gap="large")
        with chart:
            with st.container(border=True):
                st.subheader("Выработка по часам")
                st.plotly_chart(forecast_figure(rows, display_zone, synthetic), width="stretch", config={"displayModeBar": False})
                if synthetic:
                    st.caption("В примере valid_time обозначает конец часового интервала. Интервалы неопределённости не рассчитывались.")
                elif not observed_points(rows, synthetic):
                    st.caption("Фактические наблюдения с подтверждённым источником не переданы. Показан только прогноз.")
        with process:
            with st.container(border=True):
                st.subheader("Цикл агента")
                st.caption("Симуляция шагов" if mode == "Демо" else STATUS.get(run.get("status"), "Статус не указан"))
                render_steps(run)
                if run.get("recalculation_reason"):
                    st.caption("Причина запуска: " + str(run["recalculation_reason"]))
            st.caption("Погода: " + str(rows[0].get("weather_model", "Не предоставлено")))
            st.caption("Выпуск погоды: " + display_time(rows[0].get("weather_run_time"), display_zone))
        table_header, download = st.columns([3, 1])
        table_header.subheader("Почасовые значения")
        if entry["csv"]:
            download.download_button("Скачать CSV", data=entry["csv"],
                                     file_name=f"{'SYNTHETIC_' if synthetic else ''}forecast_{st.session_state.current_id}.csv",
                                     mime="text/csv", width="stretch")
        elif entry["csv_error"]:
            st.warning("Экспорт CSV недоступен: " + entry["csv_error"])
        table = [{"Время (" + display_zone + ")": display_time(row["valid_time"], display_zone),
                  "Турбина": turbine_label(row["turbine_id"]), "Горизонт, ч": row["lead_hours"],
                  quantity: row["prediction"],
                  "Источник": "Синтетический пример" if synthetic else "API",
                  "run_id": row["run_id"], "revision": row.get("revision")} for row in rows]
        st.dataframe(table, width="stretch", hide_index=True, height=250,
                     column_config={quantity: st.column_config.NumberColumn(format="%.4f")})
    with source_tab:
        render_provenance(entry, display_zone)
    with evaluation_tab:
        render_evaluation(entry)


render_result()
st.markdown('<div class="footer">HackAlem AI 2026 · Трек «Энергетика» · Интерфейс команды</div>', unsafe_allow_html=True)
