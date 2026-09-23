import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  CalendarDays,
  Check,
  CheckCheck,
  ChevronDown,
  Clock3,
  CloudSun,
  Database,
  FileCheck2,
  Info,
  LayoutGrid,
  LoaderCircle,
  Radio,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Wind,
  X,
} from "lucide-react";
import { formatTime, isSynthetic, weatherProvenance } from "./data";
import type { Run, RunRequest } from "./data";
import { ACTIVE, useForecast } from "./useForecast";
import EvaluationPanel from "./EvaluationPanel";
import type { Mode, Result } from "./useForecast";

const STATUS: Record<string, string> = {
  queued: "В очереди",
  fetching_weather: "Получение погоды",
  validating: "Проверка данных",
  forecasting: "Прогнозирование",
  analysing: "Анализ результата",
  completed: "Расчёт завершён",
  failed: "Ошибка расчёта",
};
const STAGES = ["fetching_weather", "validating", "forecasting", "analysing"];
const turbineName = (id: string) =>
  ({ turbine_1: "Турбина 01", turbine_2: "Турбина 02" })[id] || id;
const number = (value: number | undefined, digits = 3) =>
  value === undefined
    ? "—"
    : new Intl.NumberFormat("ru-RU", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      }).format(value);
const asText = (value: unknown) =>
  value === null || value === undefined || value === ""
    ? "Не предоставлено"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
const ForecastChart = lazy(() => import("./ForecastChart"));
const warningList = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(asText) : value ? [asText(value)] : [];
type Tab = "forecast" | "sources" | "evaluation";

function Logo() {
  return (
    <a className="brand" href="#overview" aria-label="JelAI — обзор">
      <img src="/assets/jelai-mark.svg" alt="" />
      <span>
        Jel<span className="brand-ai">AI</span>
        <small>WIND INTELLIGENCE</small>
      </span>
    </a>
  );
}

function AgentSteps({ run, busy }: { run?: Run; busy: boolean }) {
  const events = run?.events || [];
  const current = run?.status;
  return (
    <div className="agent-steps">
      {STAGES.map((stage, index) => {
        const done =
          current === "completed" ||
          (events.some((event) => event.step === stage) &&
            STAGES.indexOf(current || "") > index);
        const active = current === stage;
        return (
          <div
            className={`agent-step ${done ? "done" : ""} ${active ? "current" : ""}`}
            key={stage}
          >
            <span className="step-mark">
              {done ? (
                <Check size={12} strokeWidth={3} />
              ) : active && busy ? (
                <LoaderCircle size={12} className="spin" />
              ) : (
                String(index + 1).padStart(2, "0")
              )}
            </span>
            <span>{STATUS[stage]}</span>
            <span className="step-end">
              {done ? "Готово" : active ? "Сейчас" : "—"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Provenance({ result }: { result: Result }) {
  const metadata = weatherProvenance(result.run, result.rows);
  const warnings = [
    ...new Set([
      ...warningList(result.run.warnings),
      ...result.rows.flatMap((row) => warningList(row.warnings)),
    ]),
  ];
  return (
    <div className="provenance-content">
      <div className="section-heading">
        <div>
          <h2>Откуда берётся прогноз</h2>
          <p>Версия модели, погодный выпуск и границы доступных данных.</p>
        </div>
        <Database size={23} />
      </div>
      <div className="provenance-grid">
        {metadata.map((row, i) => (
          <article className="source-card" key={i}>
            <div className="source-title">
              <Wind size={18} />
              <h3>{turbineName(String(row.turbine_id))}</h3>
            </div>
            {Object.entries({
              "Источник погоды": row.provider,
              "Погодная модель": row.weather_model,
              "Выпуск погоды": row.weather_run_time,
              "Доступен с": row.forecast_available_at,
              "Основание доступности": row.availability_basis,
              "Модель прогноза": row.model_version,
              "Обучение до": row.training_cutoff,
            }).map(([key, value]) => (
              <div className="metadata-row" key={key}>
                <span>{key}</span>
                <strong>{asText(value)}</strong>
              </div>
            ))}
            <details>
              <summary>Контрольная сумма и метаданные</summary>
              <pre>{JSON.stringify(row, null, 2)}</pre>
            </details>
          </article>
        ))}
      </div>
      {warnings.length > 0 && (
        <div className="source-warnings">
          <TriangleAlert size={16} />
          <div>
            {warnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </div>
        </div>
      )}
      <p className="muted fine-print">
        Историческая доступность проверяется по исходным артефактам сервиса.
        Наличие метаданных на экране само по себе её не подтверждает.
      </p>
      <details className="journal">
        <summary>
          <Activity size={15} /> Полный журнал агента <ChevronDown size={15} />
        </summary>
        <pre>{JSON.stringify(result.run.events || result.run, null, 2)}</pre>
      </details>
    </div>
  );
}

export default function App() {
  const [mode, setMode] = useState<Mode>("demo");
  const [apiUrl, setApiUrl] = useState("/api");
  const [apiDraft, setApiDraft] = useState("/api");
  const [settings, setSettings] = useState(false);
  const [tab, setTab] = useState<Tab>("forecast");
  const [date, setDate] = useState("2026-02-01");
  const [hour, setHour] = useState("00");
  const [horizon, setHorizon] = useState<24 | 48>(48);
  const [selection, setSelection] = useState("both");
  const [zone, setZone] = useState("UTC");
  const [formError, setFormError] = useState("");
  const analysis = useRef<HTMLElement>(null);
  const tabButtons = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  const connectionDialog = useRef<HTMLDialogElement>(null);
  const state = useForecast(mode, apiUrl);
  const { result, busy, error, health } = state;
  const rows = result?.rows || [];
  const synthetic =
    mode === "demo" || (!!result && isSynthetic(rows, result.run));
  const fixtureUnit = rows[0]?.target_unit === "fixture_dimensionless";
  const quantity = fixtureUnit ? "Демо-значение" : "Нормализованная мощность";
  const active = busy || (!!result && !error && ACTIVE.has(result.run.status));
  const turbineIds = result?.request.turbine_ids || ["turbine_1", "turbine_2"];
  const mean = (id: string) => {
    const values = rows.filter((row) => row.turbine_id === id);
    return values.length
      ? values.reduce((sum, row) => sum + row.prediction, 0) / values.length
      : undefined;
  };
  const expected = result
    ? result.request.horizon_hours * result.request.turbine_ids.length
    : 0;
  const coverage =
    expected && rows.length ? Math.round((rows.length / expected) * 100) : 0;
  const available =
    result?.run.status === "completed" && rows.length > 0 && !error;
  const status = error
    ? "Нужна проверка"
    : result
      ? STATUS[result.run.status] || result.run.status
      : mode === "api"
        ? "Ожидание выпуска"
        : "Подготовка демо";
  const runWarnings =
    result?.run.status === "failed" ? asText(result.run.error) : null;
  const apiSynthetic = health?.is_demo === true || health?.mode === "demo";

  useEffect(() => {
    if (settings) connectionDialog.current?.showModal();
    else connectionDialog.current?.close();
  }, [settings]);

  function navigate(next: Tab) {
    setTab(next);
    analysis.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    setFormError("");
    if (
      date < "2026-01-31" ||
      date > "2026-02-28" ||
      !/^\d{4}-\d{2}-\d{2}$/.test(date)
    ) {
      setFormError(
        "Выберите исторический выпуск с 31 января по 28 февраля 2026.",
      );
      return;
    }
    const request: RunRequest = {
      issue_time: `${date}T${hour}:00:00Z`,
      horizon_hours: horizon,
      turbine_ids:
        selection === "both" ? ["turbine_1", "turbine_2"] : [selection],
    };
    void state.start(request);
  }
  function downloadCsv() {
    if (!result?.csv || !available) return;
    const blob = new Blob([result.csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = `${synthetic ? "SYNTHETIC_" : ""}jelai_${result.run.run_id}_r${result.run.revision ?? 0}.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#analysis">
        К данным прогноза
      </a>
      <aside className="sidebar" aria-label="Навигация">
        <a className="rail-logo" href="#overview" aria-label="JelAI — начало">
          <img src="/assets/jelai-mark.svg" alt="JelAI" />
        </a>
        <div className="rail-nav">
          <button
            className={
              tab === "forecast" ? "rail-button selected" : "rail-button"
            }
            onClick={() => {
              setTab("forecast");
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
            title="Обзор"
            aria-label="Обзор"
          >
            <LayoutGrid />
          </button>
          <button
            className="rail-button"
            onClick={() => navigate("forecast")}
            title="Почасовой прогноз"
            aria-label="Почасовой прогноз"
          >
            <BarChart3 />
          </button>
          <button
            className={
              tab === "sources" ? "rail-button selected" : "rail-button"
            }
            onClick={() => navigate("sources")}
            title="Источник и качество"
            aria-label="Источник и качество"
          >
            <Database />
          </button>
          <button
            className={
              tab === "evaluation" ? "rail-button selected" : "rail-button"
            }
            onClick={() => navigate("evaluation")}
            title="Оценка модели"
            aria-label="Оценка модели"
          >
            <ShieldCheck />
          </button>
        </div>
        <div className="rail-bottom">
          <button
            className="rail-button"
            onClick={() => setSettings(true)}
            title="Подключение и бренд"
            aria-label="Подключение и бренд"
          >
            <Settings2 />
          </button>
          <span className="team-avatar" title="HackAlem AI · Энергетика">
            HA
          </span>
        </div>
      </aside>
      <main id="overview">
        <header className="topbar">
          <Logo />
          <div className="topbar-center">
            <span className="breadcrumb">Рабочее пространство</span>
            <span className="breadcrumb-divider">/</span>
            <span>Ветропарк</span>
          </div>
          <div className="topbar-actions">
            <div
              className="mode-switch"
              role="group"
              aria-label="Источник данных"
            >
              <button
                aria-pressed={mode === "demo"}
                className={mode === "demo" ? "chosen" : ""}
                onClick={() => setMode("demo")}
              >
                Демо
              </button>
              <button
                aria-pressed={mode === "api"}
                className={mode === "api" ? "chosen" : ""}
                onClick={() => setMode("api")}
              >
                API
              </button>
            </div>
            <button
              className="icon-button settings-button"
              title="Настройки подключения"
              aria-label="Настройки подключения"
              onClick={() => setSettings(true)}
            >
              <Settings2 size={18} />
            </button>
          </div>
        </header>

        <div className="workspace">
          <div className="page-heading">
            <div>
              <div className="eyebrow">ЭНЕРГЕТИКА · HACKALEM AI</div>
              <h1>
                Обзор ветропарка<span className="heading-dot">.</span>
              </h1>
            </div>
            <span
              className={`connection-chip ${synthetic || apiSynthetic ? "demo" : ""}`}
            >
              <span className="status-dot" />
              {mode === "demo"
                ? "Демонстрационный режим"
                : synthetic || apiSynthetic
                  ? "API · синтетическое демо"
                  : health
                    ? "API подключён"
                    : "Режим API"}
            </span>
          </div>

          <section className="hero" aria-label="Обзор выпуска">
            <div className="hero-shade" />
            <div className="hero-content">
              <span className="hero-kicker">
                <Wind size={16} /> JELAI / ПРОГНОЗ ВЕТРОЭНЕРГИИ
              </span>
              <h2>
                Энергия ветра.
                <br />
                Ясность на 48 часов.
              </h2>
              <p className="hero-description">
                От погодного выпуска
                <br />к почасовому прогнозу.
              </p>
              <div className="hero-metrics">
                {turbineIds.map((id, index) => (
                  <div className="hero-metric" key={id}>
                    <span>
                      <span className={`legend-dot turbine-${index}`} />
                      {turbineName(id)}
                      <ArrowUpRight size={13} />
                    </span>
                    <strong>{number(mean(id))}</strong>
                    <small>
                      {fixtureUnit
                        ? "Условное значение · среднее"
                        : "Норм. мощность · среднее"}
                    </small>
                  </div>
                ))}
              </div>
              <div className="hero-disclosure">
                <Info size={13} />
                <span>
                  {synthetic
                    ? "Синтетические данные · не реальный прогноз"
                    : "Исторический расчёт · не live-телеметрия"}
                </span>
              </div>
            </div>
            <div className="agent-card">
              <div className="card-kicker">
                <span>
                  <Sparkles size={15} /> AI-агент
                </span>
                <span className={`agent-state ${active ? "running" : ""}`}>
                  <span />
                  {active ? "В работе" : available ? "Готово" : "Ожидание"}
                </span>
              </div>
              <h3>{status}</h3>
              <AgentSteps run={result?.run} busy={active} />
              <div className="agent-bottom">
                <span>Горизонт выпуска</span>
                <strong>
                  {result ? `${result.request.horizon_hours} ч` : "—"}
                </strong>
              </div>
              <button
                className="text-button"
                disabled={!result || busy}
                onClick={() => void state.refresh()}
              >
                <RefreshCw size={13} className={busy ? "spin" : ""} />
                Обновить статус
                <ArrowRight size={13} />
              </button>
            </div>
            <span className="scene-caption">Иллюстрация ветропарка</span>
          </section>

          <form
            className="controls-card"
            onSubmit={submit}
            aria-label="Параметры выпуска"
          >
            <label className="date-field">
              <span>
                <CalendarDays size={13} /> Дата выпуска
              </span>
              <input
                type="date"
                aria-label="Дата выпуска"
                min="2026-01-31"
                max="2026-02-28"
                required
                value={date}
                onInput={(event) => setDate(event.currentTarget.value)}
              />
            </label>
            <label className="hour-field">
              <span>Время, UTC</span>
              <select
                value={hour}
                aria-label="Время выпуска UTC"
                onChange={(event) => setHour(event.target.value)}
              >
                {Array.from({ length: 24 }, (_, h) =>
                  String(h).padStart(2, "0"),
                ).map((h) => (
                  <option key={h} value={h}>
                    {h}:00
                  </option>
                ))}
              </select>
            </label>
            <label className="turbine-field">
              <span>Турбины</span>
              <select
                aria-label="Турбины"
                value={selection}
                onChange={(event) => setSelection(event.target.value)}
              >
                <option value="both">Обе турбины</option>
                <option value="turbine_1">Турбина 01</option>
                <option value="turbine_2">Турбина 02</option>
              </select>
            </label>
            <div className="horizon-field">
              <span>Горизонт</span>
              <div
                className="segmented"
                role="group"
                aria-label="Горизонт прогноза"
              >
                {([24, 48] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={horizon === value}
                    className={horizon === value ? "chosen" : ""}
                    onClick={() => setHorizon(value)}
                  >
                    {value} ч
                  </button>
                ))}
              </div>
            </div>
            <button className="primary-button" type="submit" disabled={active}>
              {active ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Sparkles size={16} />
              )}
              {active ? "Расчёт…" : "Рассчитать прогноз"}
              {!active && <ArrowRight size={16} />}
            </button>
          </form>
          {(formError || error || runWarnings) && (
            <div className="error-banner" role="alert">
              <TriangleAlert size={18} />
              <div>
                <strong>
                  {formError
                    ? "Проверьте параметры"
                    : "Результат не подтверждён"}
                </strong>
                <p>{formError || error || runWarnings}</p>
                {error && (
                  <small>
                    Автоматический опрос приостановлен. Проверьте подключение и
                    обновите статус.
                  </small>
                )}
              </div>
              {result && !formError && (
                <button
                  className="secondary-button"
                  onClick={() => void state.refresh()}
                  disabled={busy}
                >
                  Повторить запрос
                </button>
              )}
            </div>
          )}

          <section id="analysis" className="analysis-section" ref={analysis}>
            <div className="analysis-toolbar">
              <div className="tabs" role="tablist" aria-label="Данные прогноза">
                {(
                  [
                    ["forecast", "Прогноз"],
                    ["sources", "Источник и качество"],
                    ["evaluation", "Оценка модели"],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    id={`tab-${key}`}
                    key={key}
                    role="tab"
                    ref={(element) => {
                      tabButtons.current[key] = element;
                    }}
                    tabIndex={tab === key ? 0 : -1}
                    onKeyDown={(event) => {
                      const keys: Tab[] = ["forecast", "sources", "evaluation"];
                      const index = keys.indexOf(key);
                      const next =
                        event.key === "ArrowRight"
                          ? keys[(index + 1) % keys.length]
                          : event.key === "ArrowLeft"
                            ? keys[(index + keys.length - 1) % keys.length]
                            : event.key === "Home"
                              ? keys[0]
                              : event.key === "End"
                                ? keys[keys.length - 1]
                                : undefined;
                      if (next) {
                        event.preventDefault();
                        setTab(next);
                        tabButtons.current[next]?.focus();
                      }
                    }}
                    aria-selected={tab === key}
                    aria-controls={`panel-${key}`}
                    className={tab === key ? "active" : ""}
                    onClick={() => setTab(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <label className="timezone">
                <Clock3 size={13} />
                <select
                  aria-label="Часовой пояс отображения"
                  value={zone}
                  onChange={(event) => setZone(event.target.value)}
                >
                  <option value="UTC">UTC</option>
                  <option value="Asia/Almaty">Алматы · UTC+5</option>
                </select>
              </label>
            </div>
            {available && result ? (
              <>
                <div className="run-caption">
                  <span>
                    <span className="tiny-dot" />
                    Выпуск {formatTime(result.request.issue_time, zone)} ·{" "}
                    {result.request.horizon_hours} ч ·{" "}
                    {result.request.turbine_ids.map(turbineName).join(", ")}
                  </span>
                  <span className="run-id" title={result.run.run_id}>
                    ID {result.run.run_id} · рев. {result.run.revision ?? "—"}
                  </span>
                </div>
                <div
                  role="tabpanel"
                  tabIndex={0}
                  id={`panel-${tab}`}
                  aria-labelledby={`tab-${tab}`}
                >
                  {tab === "forecast" && (
                    <>
                      <div className="analytics-grid">
                        <article className="chart-card panel">
                          <div className="section-heading">
                            <div>
                              <h2>Почасовой прогноз</h2>
                              <p>{quantity} · среднее за час</p>
                            </div>
                            <div className="chart-legend">
                              {turbineIds.map((id, i) => (
                                <span key={id}>
                                  <i className={`legend-dot turbine-${i}`} />
                                  {turbineName(id)}
                                </span>
                              ))}
                            </div>
                          </div>
                          <Suspense
                            fallback={
                              <div className="chart chart-loading">
                                <LoaderCircle size={22} className="spin" />
                                <span>Подготовка графика</span>
                              </div>
                            }
                          >
                            <ForecastChart rows={rows} zone={zone} />
                          </Suspense>
                          <div className="chart-note">
                            <Info size={12} />
                            Время обозначает конец часа. Фактические наблюдения
                            не представлены.
                          </div>
                        </article>
                        <article className="coverage-card panel">
                          <div className="card-kicker">
                            <span>
                              <FileCheck2 size={16} /> Полнота прогноза
                            </span>
                            <CheckCheck size={17} />
                          </div>
                          <div className="coverage-ring">
                            <svg viewBox="0 0 180 150" aria-hidden="true">
                              <path
                                className="ring-track"
                                d="M30 125A74 74 0 1 1 150 125"
                                pathLength="100"
                              />
                              <path
                                className="ring-value"
                                d="M30 125A74 74 0 1 1 150 125"
                                pathLength="100"
                                strokeDasharray={`${coverage} 100`}
                              />
                            </svg>
                            <div>
                              <strong>
                                {coverage}
                                <span>%</span>
                              </strong>
                              <small>часовой сетки</small>
                            </div>
                          </div>
                          <div className="coverage-bottom">
                            <span>
                              Получено значений
                              <strong>
                                {rows.length} <small>/ {expected}</small>
                              </strong>
                            </span>
                            <span>
                              Пропущено часов
                              <strong>{expected - rows.length}</strong>
                            </span>
                          </div>
                          <p>Полнота данных, не точность модели.</p>
                        </article>
                      </div>
                      <div className="bottom-grid">
                        <article className="source-summary panel">
                          <span className="summary-icon">
                            <CloudSun size={22} />
                          </span>
                          <div>
                            <span className="label">ПОГОДНЫЙ ИСТОЧНИК</span>
                            <h3>{asText(rows[0]?.weather_model)}</h3>
                            <p>
                              Выпуск:{" "}
                              {rows[0]?.weather_run_time
                                ? formatTime(
                                    String(rows[0].weather_run_time),
                                    zone,
                                  )
                                : "не указан"}
                            </p>
                          </div>
                          <button
                            className="circle-button"
                            title="Подробнее об источнике"
                            aria-label="Подробнее об источнике"
                            onClick={() => setTab("sources")}
                          >
                            <ArrowUpRight size={17} />
                          </button>
                        </article>
                        <article className="source-summary panel">
                          <span className="summary-icon lilac">
                            <ShieldCheck size={21} />
                          </span>
                          <div>
                            <span className="label">ПРОВЕРЯЕМОСТЬ</span>
                            <h3>
                              {synthetic
                                ? "Синтетический пример"
                                : "Метаданные выпуска"}
                            </h3>
                            <p>
                              {synthetic
                                ? "Точность модели не измеряется"
                                : "Источник, версия модели и cutoff"}
                            </p>
                          </div>
                          <button
                            className="circle-button"
                            title="Открыть оценку модели"
                            aria-label="Открыть оценку модели"
                            onClick={() => setTab("evaluation")}
                          >
                            <ArrowUpRight size={17} />
                          </button>
                        </article>
                      </div>
                      <article className="table-card panel">
                        <div className="section-heading">
                          <div>
                            <h2>
                              Почасовые значения{" "}
                              <span className="count-badge">{rows.length}</span>
                            </h2>
                            <p>
                              {synthetic
                                ? "Синтетический пример"
                                : "Результат API"}{" "}
                              · {quantity.toLowerCase()} · {zone}
                            </p>
                          </div>
                          <button
                            className="secondary-button"
                            onClick={downloadCsv}
                            disabled={!result.csv}
                          >
                            <ArrowDownToLine size={15} /> Скачать CSV
                          </button>
                        </div>
                        {result.csvError && (
                          <p role="alert" className="error-inline">
                            CSV недоступен: {result.csvError}
                          </p>
                        )}
                        <div className="table-scroll">
                          <table>
                            <thead>
                              <tr>
                                <th>Конец часового интервала</th>
                                <th>Турбина</th>
                                <th>Горизонт</th>
                                <th>{quantity}</th>
                                <th>Источник</th>
                              </tr>
                            </thead>
                            <tbody>
                              {rows.map((row) => (
                                <tr key={`${row.turbine_id}-${row.valid_time}`}>
                                  <td>{formatTime(row.valid_time, zone)}</td>
                                  <td>
                                    <span
                                      className={`legend-dot turbine-${turbineIds.indexOf(row.turbine_id)}`}
                                    />
                                    {turbineName(row.turbine_id)}
                                  </td>
                                  <td>+{row.lead_hours} ч</td>
                                  <td className="numeric">
                                    {number(row.prediction, 4)}
                                  </td>
                                  <td>
                                    <span
                                      className={`table-tag ${synthetic ? "synthetic" : ""}`}
                                    >
                                      {synthetic ? "Синтетический" : "API"}
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div className="table-footer">
                          <span>
                            Значения турбин не суммируются и не переводятся в
                            МВт без подтверждённого масштаба.
                          </span>
                          <Check size={13} /> CSV сверяется с результатом
                        </div>
                      </article>
                    </>
                  )}
                  {tab === "sources" && (
                    <article className="panel">
                      <Provenance result={result} />
                    </article>
                  )}
                  {tab === "evaluation" && (
                    <article className="panel">
                      <EvaluationPanel result={result} synthetic={synthetic} />
                    </article>
                  )}
                </div>
              </>
            ) : (
              <div className="empty-state panel">
                <span className="empty-icon">
                  {active ? (
                    <LoaderCircle className="spin" size={26} />
                  ) : (
                    <Wind size={28} />
                  )}
                </span>
                <h2>
                  {active
                    ? status
                    : error || runWarnings
                      ? "Прогноз пока недоступен"
                      : "Начните с исторического выпуска"}
                </h2>
                <p>
                  {active
                    ? "Агент проверяет входные данные и готовит результат. Состояние обновляется автоматически."
                    : "Выберите дату, турбины и горизонт, затем нажмите «Рассчитать прогноз»."}
                </p>
                {mode === "api" && (
                  <button
                    className="secondary-button"
                    onClick={() => setSettings(true)}
                  >
                    <Settings2 size={15} />
                    Проверить подключение
                  </button>
                )}
                {result?.run.events && (
                  <details className="journal">
                    <summary>Журнал агента</summary>
                    <pre>{JSON.stringify(result.run.events, null, 2)}</pre>
                  </details>
                )}
              </div>
            )}
            {state.history.length > 1 && (
              <label className="history-select">
                История запусков
                <select
                  aria-label="История запусков"
                  value={result?.run.run_id || ""}
                  onChange={(event) => state.selectRun(event.target.value)}
                >
                  {!available && <option value="">Текущий расчёт</option>}
                  {state.history.map((item) => (
                    <option key={item.run.run_id} value={item.run.run_id}>
                      {formatTime(item.request.issue_time, zone)} ·{" "}
                      {item.request.horizon_hours} ч ·{" "}
                      {item.request.turbine_ids.map(turbineName).join(", ")} ·{" "}
                      {item.run.run_id}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </section>
          <footer>
            <span>
              <img src="/assets/jelai-mark.svg" alt="" />
              JelAI <i /> Энергия данных. Сила ветра.
            </span>
            <span>HackAlem AI 2026 · Трек «Энергетика»</span>
          </footer>
        </div>
      </main>
      <dialog
        className="settings-dialog"
        ref={connectionDialog}
        onCancel={() => setSettings(false)}
        onClose={() => setSettings(false)}
        aria-labelledby="connection-title"
      >
        <div className="dialog-heading">
          <h2 id="connection-title">Подключение и бренд</h2>
          <button
            className="icon-button"
            aria-label="Закрыть настройки"
            onClick={() => setSettings(false)}
          >
            <X size={20} />
          </button>
        </div>
        <p>
          Режим API использует погодный сервис команды. Демо работает
          самостоятельно.
        </p>
        <label>
          Адрес API
          <input
            value={apiDraft}
            aria-label="Адрес API"
            onChange={(event) => setApiDraft(event.target.value)}
            placeholder="/api"
          />
        </label>
        <p className="fine-print muted">
          Локальный путь /api подключён к серверу команды. Другой адрес должен
          разрешать запросы из браузера.
        </p>
        <div className="dialog-actions">
          <button
            className="primary-button"
            onClick={() => {
              setApiUrl(apiDraft.trim());
              setMode("api");
            }}
          >
            Применить адрес
            <Check size={15} />
          </button>
          <button
            className="secondary-button"
            disabled={mode !== "api" || state.healthBusy}
            onClick={() => void state.checkHealth()}
          >
            {state.healthBusy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Radio size={15} />
            )}
            Проверить
          </button>
        </div>
        {state.healthError && (
          <p className="error-inline" role="alert">
            {state.healthError}
          </p>
        )}
        {health && (
          <div className="health-result">
            <strong>
              {apiSynthetic
                ? "API отвечает · синтетическое демо"
                : "API отвечает"}
            </strong>
            <details>
              <summary>Готовность сервиса</summary>
              <pre>{JSON.stringify(health, null, 2)}</pre>
            </details>
          </div>
        )}
        <div className="brand-download">
          <img src="/assets/jelai-logo.svg" alt="JelAI" />
          <p>«Жел» + AI. Три потока вокруг центра — ветер, данные и прогноз.</p>
          <a href="/assets/jelai-logo.svg" download="jelai-logo.svg">
            <ArrowDownToLine size={15} />
            Скачать логотип SVG
          </a>
          <a href="/assets/jelai-mark.svg" download="jelai-mark.svg">
            Скачать знак
          </a>
        </div>
      </dialog>
    </div>
  );
}
