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
import { isSynthetic, weatherProvenance } from "./data";
import { useI18n } from "./i18n";
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
  const { t } = useI18n();
  return (
    <a className="brand" href="#overview" aria-label={t("JelAI — обзор")}>
      <img src="/assets/jelai-mark.svg" alt="" />
      <span>
        Jel<span className="brand-ai">AI</span>
        <small>WIND INTELLIGENCE</small>
      </span>
    </a>
  );
}

function AgentSteps({ run, busy }: { run?: Run; busy: boolean }) {
  const { t } = useI18n();
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
            <span>{t(STATUS[stage])}</span>
            <span className="step-end">
              {done ? t("Готово") : active ? t("Сейчас") : "—"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Provenance({ result }: { result: Result }) {
  const { t, time, turbineName, message } = useI18n();
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
          <h2>{t("Откуда берётся прогноз")}</h2>
          <p>
            {t("Версия модели, погодный выпуск и границы доступных данных.")}
          </p>
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
                <span>{t(key)}</span>
                <strong>
                  {["Выпуск погоды", "Доступен с", "Обучение до"].includes(
                    key,
                  ) &&
                  typeof value === "string" &&
                  value
                    ? time(value, "UTC")
                    : message(asText(value))}
                </strong>
              </div>
            ))}
            <details>
              <summary>{t("Контрольная сумма и метаданные")}</summary>
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
              <p key={warning}>{message(warning)}</p>
            ))}
          </div>
        </div>
      )}
      <p className="muted fine-print">
        {t(
          "Историческая доступность проверяется по исходным артефактам сервиса. Наличие метаданных на экране само по себе её не подтверждает.",
        )}
      </p>
      <details className="journal">
        <summary>
          <Activity size={15} />
          {t("Полный журнал агента")}
          <ChevronDown size={15} />
        </summary>
        <pre>{JSON.stringify(result.run.events || result.run, null, 2)}</pre>
      </details>
    </div>
  );
}

export default function App() {
  const { locale, setLocale, t, number, time, turbineName, message } =
    useI18n();
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
  const quantity = fixtureUnit
    ? t("Демо-значение")
    : t("Нормализованная мощность");
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
    ? t("Нужна проверка")
    : result
      ? STATUS[result.run.status] || result.run.status
      : mode === "api"
        ? t("Ожидание выпуска")
        : t("Подготовка демо");
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
        {t("К данным прогноза")}
      </a>
      <aside className="sidebar" aria-label={t("Навигация")}>
        <a
          className="rail-logo"
          href="#overview"
          aria-label={t("JelAI — начало")}
        >
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
            title={t("Обзор")}
            aria-label={t("Обзор")}
          >
            <LayoutGrid />
          </button>
          <button
            className="rail-button"
            onClick={() => navigate("forecast")}
            title={t("Почасовой прогноз")}
            aria-label={t("Почасовой прогноз")}
          >
            <BarChart3 />
          </button>
          <button
            className={
              tab === "sources" ? "rail-button selected" : "rail-button"
            }
            onClick={() => navigate("sources")}
            title={t("Источник и качество")}
            aria-label={t("Источник и качество")}
          >
            <Database />
          </button>
          <button
            className={
              tab === "evaluation" ? "rail-button selected" : "rail-button"
            }
            onClick={() => navigate("evaluation")}
            title={t("Оценка модели")}
            aria-label={t("Оценка модели")}
          >
            <ShieldCheck />
          </button>
        </div>
        <div className="rail-bottom">
          <button
            className="rail-button"
            onClick={() => setSettings(true)}
            title={t("Подключение и бренд")}
            aria-label={t("Подключение и бренд")}
          >
            <Settings2 />
          </button>
          <span className="team-avatar" title={t("HackAlem AI · Энергетика")}>
            HA
          </span>
        </div>
      </aside>
      <main id="overview">
        <header className="topbar">
          <Logo />
          <div className="topbar-center">
            <span className="breadcrumb">{t("Рабочее пространство")}</span>
            <span className="breadcrumb-divider">/</span>
            <span>{t("Ветропарк")}</span>
          </div>
          <div className="topbar-actions">
            <select
              className="language-select"
              aria-label={t("Язык интерфейса")}
              value={locale}
              onChange={(event) =>
                setLocale(event.target.value as "ru" | "kk" | "en")
              }
            >
              <option value="ru" lang="ru">
                Русский
              </option>
              <option value="kk" lang="kk">
                Қазақша
              </option>
              <option value="en" lang="en">
                English
              </option>
            </select>
            <div
              className="mode-switch"
              role="group"
              aria-label={t("Источник данных")}
            >
              <button
                aria-pressed={mode === "demo"}
                className={mode === "demo" ? "chosen" : ""}
                onClick={() => setMode("demo")}
              >
                {t("Демо")}
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
              title={t("Настройки подключения")}
              aria-label={t("Настройки подключения")}
              onClick={() => setSettings(true)}
            >
              <Settings2 size={18} />
            </button>
          </div>
        </header>

        <div className="workspace">
          <div className="page-heading">
            <div>
              <div className="eyebrow">{t("ЭНЕРГЕТИКА · HACKALEM AI")}</div>
              <h1>
                {t("Обзор ветропарка")}
                <span className="heading-dot">.</span>
              </h1>
            </div>
            <span
              className={`connection-chip ${synthetic || apiSynthetic ? "demo" : ""}`}
            >
              <span className="status-dot" />
              {mode === "demo"
                ? t("Демонстрационный режим")
                : synthetic || apiSynthetic
                  ? t("API · синтетическое демо")
                  : health
                    ? t("API подключён")
                    : t("Режим API")}
            </span>
          </div>

          <section className="hero" aria-label={t("Обзор выпуска")}>
            <div className="hero-shade" />
            <div className="hero-content">
              <span className="hero-kicker">
                <Wind size={16} />
                {t("JELAI / ПРОГНОЗ ВЕТРОЭНЕРГИИ")}
              </span>
              <h2>
                {t("Энергия ветра.")}
                <br />
                {t("Ясность на 48 часов.")}
              </h2>
              <p className="hero-description">
                {t("От погодного выпуска к почасовому прогнозу.")}
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
                        ? t("Условное значение · среднее")
                        : t("Норм. мощность · среднее")}
                    </small>
                  </div>
                ))}
              </div>
              <div className="hero-disclosure">
                <Info size={13} />
                <span>
                  {synthetic
                    ? t("Синтетические данные · не реальный прогноз")
                    : t("Исторический расчёт · не live-телеметрия")}
                </span>
              </div>
            </div>
            <div className="agent-card">
              <div className="card-kicker">
                <span>
                  <Sparkles size={15} />
                  {t("AI-агент")}
                </span>
                <span className={`agent-state ${active ? "running" : ""}`}>
                  <span />
                  {active
                    ? t("В работе")
                    : available
                      ? t("Готово")
                      : t("Ожидание")}
                </span>
              </div>
              <h3>{t(status)}</h3>
              <AgentSteps run={result?.run} busy={active} />
              <div className="agent-bottom">
                <span>{t("Горизонт выпуска")}</span>
                <strong>
                  {result
                    ? t("{hours} ч", {
                        hours: number(result.request.horizon_hours, 0),
                      })
                    : "—"}
                </strong>
              </div>
              <button
                className="text-button"
                disabled={!result || busy}
                onClick={() => void state.refresh()}
              >
                <RefreshCw size={13} className={busy ? "spin" : ""} />
                {t("Обновить статус")}
                <ArrowRight size={13} />
              </button>
            </div>
            <span className="scene-caption">{t("Иллюстрация ветропарка")}</span>
          </section>

          <form
            className="controls-card"
            onSubmit={submit}
            aria-label={t("Параметры выпуска")}
          >
            <label className="date-field">
              <span>
                <CalendarDays size={13} />
                {t("Дата выпуска")}
              </span>
              <input
                type="date"
                aria-label={t("Дата выпуска")}
                min="2026-01-31"
                max="2026-02-28"
                required
                value={date}
                onInput={(event) => setDate(event.currentTarget.value)}
              />
            </label>
            <label className="hour-field">
              <span>{t("Время, UTC")}</span>
              <select
                value={hour}
                aria-label={t("Время выпуска UTC")}
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
              <span>{t("Турбины")}</span>
              <select
                aria-label={t("Турбины")}
                value={selection}
                onChange={(event) => setSelection(event.target.value)}
              >
                <option value="both">{t("Обе турбины")}</option>
                <option value="turbine_1">{t("Турбина 01")}</option>
                <option value="turbine_2">{t("Турбина 02")}</option>
              </select>
            </label>
            <div className="horizon-field">
              <span>{t("Горизонт")}</span>
              <div
                className="segmented"
                role="group"
                aria-label={t("Горизонт прогноза")}
              >
                {([24, 48] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={horizon === value}
                    className={horizon === value ? "chosen" : ""}
                    onClick={() => setHorizon(value)}
                  >
                    {t("{hours} ч", { hours: number(value, 0) })}
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
              {active ? t("Расчёт…") : t("Рассчитать прогноз")}
              {!active && <ArrowRight size={16} />}
            </button>
          </form>
          {(formError || error || runWarnings) && (
            <div className="error-banner" role="alert">
              <TriangleAlert size={18} />
              <div>
                <strong>
                  {formError
                    ? t("Проверьте параметры")
                    : t("Результат не подтверждён")}
                </strong>
                <p>{message(formError || error || runWarnings || "")}</p>
                {error && (
                  <small>
                    {t(
                      "Автоматический опрос приостановлен. Проверьте подключение и обновите статус.",
                    )}
                  </small>
                )}
              </div>
              {result && !formError && (
                <button
                  className="secondary-button"
                  onClick={() => void state.refresh()}
                  disabled={busy}
                >
                  {t("Повторить запрос")}
                </button>
              )}
            </div>
          )}

          <section id="analysis" className="analysis-section" ref={analysis}>
            <div className="analysis-toolbar">
              <div
                className="tabs"
                role="tablist"
                aria-label={t("Данные прогноза")}
              >
                {(
                  [
                    ["forecast", t("Прогноз")],
                    ["sources", t("Источник и качество")],
                    ["evaluation", t("Оценка модели")],
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
                  aria-label={t("Часовой пояс отображения")}
                  value={zone}
                  onChange={(event) => setZone(event.target.value)}
                >
                  <option value="UTC">UTC</option>
                  <option value="Asia/Almaty">{t("Алматы · UTC+5")}</option>
                </select>
              </label>
            </div>
            {available && result ? (
              <>
                <div className="run-caption">
                  <span>
                    <span className="tiny-dot" />
                    {t("Выпуск {date}", {
                      date: time(result.request.issue_time, zone),
                    })}{" "}
                    ·{" "}
                    {t("{hours} ч", {
                      hours: number(result.request.horizon_hours, 0),
                    })}{" "}
                    · {result.request.turbine_ids.map(turbineName).join(", ")}
                  </span>
                  <span className="run-id" title={result.run.run_id}>
                    {t("ID {id} · рев. {revision}", {
                      id: result.run.run_id,
                      revision:
                        result.run.revision === undefined
                          ? "—"
                          : number(result.run.revision, 0),
                    })}
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
                              <h2>{t("Почасовой прогноз")}</h2>
                              <p>
                                {t("{quantity} · среднее за час", { quantity })}
                              </p>
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
                                <span>{t("Подготовка графика")}</span>
                              </div>
                            }
                          >
                            <ForecastChart rows={rows} zone={zone} />
                          </Suspense>
                          <div className="chart-note">
                            <Info size={12} />
                            {t(
                              "Время обозначает конец часа. Фактические наблюдения не представлены.",
                            )}
                          </div>
                        </article>
                        <article className="coverage-card panel">
                          <div className="card-kicker">
                            <span>
                              <FileCheck2 size={16} />
                              {t("Полнота прогноза")}
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
                                {number(coverage, 0)}
                                <span>%</span>
                              </strong>
                              <small>{t("часовой сетки")}</small>
                            </div>
                          </div>
                          <div className="coverage-bottom">
                            <span>
                              {t("Получено значений")}
                              <strong>
                                {number(rows.length, 0)}{" "}
                                <small>/ {number(expected, 0)}</small>
                              </strong>
                            </span>
                            <span>
                              {t("Пропущено часов")}
                              <strong>
                                {number(expected - rows.length, 0)}
                              </strong>
                            </span>
                          </div>
                          <p>{t("Полнота данных, не точность модели.")}</p>
                        </article>
                      </div>
                      <div className="bottom-grid">
                        <article className="source-summary panel">
                          <span className="summary-icon">
                            <CloudSun size={22} />
                          </span>
                          <div>
                            <span className="label">
                              {t("ПОГОДНЫЙ ИСТОЧНИК")}
                            </span>
                            <h3>{message(asText(rows[0]?.weather_model))}</h3>
                            <p>
                              {t("Выпуск:")}{" "}
                              {rows[0]?.weather_run_time
                                ? time(String(rows[0].weather_run_time), zone)
                                : t("не указан")}
                            </p>
                          </div>
                          <button
                            className="circle-button"
                            title={t("Подробнее об источнике")}
                            aria-label={t("Подробнее об источнике")}
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
                            <span className="label">{t("ПРОВЕРЯЕМОСТЬ")}</span>
                            <h3>
                              {synthetic
                                ? t("Синтетический пример")
                                : t("Метаданные выпуска")}
                            </h3>
                            <p>
                              {synthetic
                                ? t("Точность модели не измеряется")
                                : t("Источник, версия модели и cutoff")}
                            </p>
                          </div>
                          <button
                            className="circle-button"
                            title={t("Открыть оценку модели")}
                            aria-label={t("Открыть оценку модели")}
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
                              {t("Почасовые значения")}{" "}
                              <span className="count-badge">
                                {number(rows.length, 0)}
                              </span>
                            </h2>
                            <p>
                              {synthetic
                                ? t("Синтетический пример")
                                : t("Результат API")}{" "}
                              · {quantity.toLowerCase()} · {zone}
                            </p>
                          </div>
                          <button
                            className="secondary-button"
                            onClick={downloadCsv}
                            disabled={!result.csv}
                          >
                            <ArrowDownToLine size={15} />
                            {t("Скачать CSV")}
                          </button>
                        </div>
                        {result.csvError && (
                          <p role="alert" className="error-inline">
                            {t("CSV недоступен: {error}", {
                              error: message(result.csvError),
                            })}
                          </p>
                        )}
                        <div className="table-scroll">
                          <table>
                            <thead>
                              <tr>
                                <th>{t("Конец часового интервала")}</th>
                                <th>{t("Турбина")}</th>
                                <th>{t("Горизонт")}</th>
                                <th>{quantity}</th>
                                <th>{t("Источник")}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {rows.map((row) => (
                                <tr key={`${row.turbine_id}-${row.valid_time}`}>
                                  <td>{time(row.valid_time, zone)}</td>
                                  <td>
                                    <span
                                      className={`legend-dot turbine-${turbineIds.indexOf(row.turbine_id)}`}
                                    />
                                    {turbineName(row.turbine_id)}
                                  </td>
                                  <td>
                                    {t("+{hours} ч", {
                                      hours: number(row.lead_hours, 0),
                                    })}
                                  </td>
                                  <td className="numeric">
                                    {number(row.prediction, 4)}
                                  </td>
                                  <td>
                                    <span
                                      className={`table-tag ${synthetic ? "synthetic" : ""}`}
                                    >
                                      {synthetic ? t("Синтетический") : "API"}
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div className="table-footer">
                          <span>
                            {t(
                              "Значения турбин не суммируются и не переводятся в МВт без подтверждённого масштаба.",
                            )}
                          </span>
                          <Check size={13} />
                          {t("CSV сверяется с результатом")}
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
                    ? t(status)
                    : error || runWarnings
                      ? t("Прогноз пока недоступен")
                      : t("Начните с исторического выпуска")}
                </h2>
                <p>
                  {active
                    ? t(
                        "Агент проверяет входные данные и готовит результат. Состояние обновляется автоматически.",
                      )
                    : t(
                        "Выберите дату, турбины и горизонт, затем нажмите «Рассчитать прогноз».",
                      )}
                </p>
                {mode === "api" && (
                  <button
                    className="secondary-button"
                    onClick={() => setSettings(true)}
                  >
                    <Settings2 size={15} />
                    {t("Проверить подключение")}
                  </button>
                )}
                {result?.run.events && (
                  <details className="journal">
                    <summary>{t("Журнал агента")}</summary>
                    <pre>{JSON.stringify(result.run.events, null, 2)}</pre>
                  </details>
                )}
              </div>
            )}
            {state.history.length > 1 && (
              <label className="history-select">
                {t("История запусков")}
                <select
                  aria-label={t("История запусков")}
                  value={result?.run.run_id || ""}
                  onChange={(event) => state.selectRun(event.target.value)}
                >
                  {!available && (
                    <option value="">{t("Текущий расчёт")}</option>
                  )}
                  {state.history.map((item) => (
                    <option key={item.run.run_id} value={item.run.run_id}>
                      {time(item.request.issue_time, zone)} ·{" "}
                      {t("{hours} ч", {
                        hours: number(item.request.horizon_hours, 0),
                      })}{" "}
                      · {item.request.turbine_ids.map(turbineName).join(", ")} ·{" "}
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
              JelAI <i />
              {t("Энергия данных. Сила ветра.")}
            </span>
            <span>{t("HackAlem AI 2026 · Трек «Энергетика»")}</span>
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
          <h2 id="connection-title">{t("Подключение и бренд")}</h2>
          <button
            className="icon-button"
            aria-label={t("Закрыть настройки")}
            onClick={() => setSettings(false)}
          >
            <X size={20} />
          </button>
        </div>
        <p>
          {t(
            "Режим API использует погодный сервис команды. Демо работает самостоятельно.",
          )}
        </p>
        <label>
          {t("Адрес API")}
          <input
            value={apiDraft}
            aria-label={t("Адрес API")}
            onChange={(event) => setApiDraft(event.target.value)}
            placeholder="/api"
          />
        </label>
        <p className="fine-print muted">
          {t(
            "Локальный путь /api подключён к серверу команды. Другой адрес должен разрешать запросы из браузера.",
          )}
        </p>
        <div className="dialog-actions">
          <button
            className="primary-button"
            onClick={() => {
              setApiUrl(apiDraft.trim());
              setMode("api");
            }}
          >
            {t("Применить адрес")}
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
            {t("Проверить")}
          </button>
        </div>
        {state.healthError && (
          <p className="error-inline" role="alert">
            {message(state.healthError)}
          </p>
        )}
        {health && (
          <div className="health-result">
            <strong>
              {apiSynthetic
                ? t("API отвечает · синтетическое демо")
                : t("API отвечает")}
            </strong>
            <details>
              <summary>{t("Готовность сервиса")}</summary>
              <pre>{JSON.stringify(health, null, 2)}</pre>
            </details>
          </div>
        )}
        <div className="brand-download">
          <img src="/assets/jelai-logo.svg" alt="JelAI" />
          <p>
            {t(
              "«Жел» + AI. Три потока вокруг центра — ветер, данные и прогноз.",
            )}
          </p>
          <a href="/assets/jelai-logo.svg" download="jelai-logo.svg">
            <ArrowDownToLine size={15} />
            {t("Скачать логотип SVG")}
          </a>
          <a href="/assets/jelai-mark.svg" download="jelai-mark.svg">
            {t("Скачать знак")}
          </a>
        </div>
      </dialog>
    </div>
  );
}
