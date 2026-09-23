import { Activity, TriangleAlert, Wind } from "lucide-react";
import type { Run } from "./data";
import { useI18n } from "./i18n";

type JsonObject = Record<string, unknown>;
const object = (value: unknown): JsonObject | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : null;
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

function currentAnalysis(run: Run): JsonObject | null {
  if (run.status !== "completed" || !Number.isInteger(run.revision)) return null;
  for (const event of [...(run.events || [])].reverse()) {
    const report = object(event.details?.analysis);
    if (
      report?.run_id !== run.run_id ||
      report?.revision !== run.revision
    ) continue;
    const turbines = object(report.per_turbine);
    if (
      (report.decision !== "review_required" && report.decision !== "monitor_updates") ||
      (report.next_action !== "review_inputs" && report.next_action !== "monitor_updates") ||
      typeof report.is_demo !== "boolean" ||
      !Array.isArray(report.reasons) ||
      !report.reasons.every((reason) => typeof reason === "string") ||
      ["model_warnings", "review_warnings", "methodology_warnings"].some((field) =>
        report[field] !== undefined && (!Array.isArray(report[field]) ||
          !(report[field] as unknown[]).every((warning) => typeof warning === "string"))) ||
      !turbines || !Object.keys(turbines).length ||
      Object.values(turbines).some((value) => {
        const turbine = object(value);
        return !turbine ||
          !["normalized_power", "fixture_dimensionless"].includes(String(turbine.target_unit));
      })
    ) return null;
    return report;
  }
  return null;
}

export default function AgentAnalysisPanel({ run, zone = "UTC" }: { run: Run; zone?: string }) {
  const { t, time, message, intlLocale, turbineName } = useI18n();
  const number = (value: unknown) =>
    typeof value === "number" && Number.isFinite(value)
      ? new Intl.NumberFormat(intlLocale, { maximumFractionDigits: 4 }).format(value)
      : t("Не предоставлено");
  const report = currentAnalysis(run);
  if (!report) return null;
  const turbines = object(report.per_turbine)!;
  const reasons = strings(report.reasons);
  const methodologyWarnings = [...new Set(strings(report.methodology_warnings))];
  // Older reports have only model_warnings; never silently discard an unclassified warning.
  const reviewWarnings = [...new Set([
    ...strings(report.review_warnings),
    ...strings(report.model_warnings).filter((warning) => !methodologyWarnings.includes(warning)),
  ])];
  const comparison = object(report.previous_comparison);
  const compared = comparison?.status === "compared";
  const fixture = report.is_demo === true ||
    Object.values(turbines).some((value) => object(value)?.target_unit === "fixture_dimensionless");

  const needsReview = report.decision === "review_required" || fixture || reviewWarnings.length > 0;

  return (
    <article className="panel table-card" aria-labelledby="agent-analysis-title">
      <div className="provenance-content">
        <div className="section-heading">
          <div>
            <h2 id="agent-analysis-title">{t("Анализ агента")}</h2>
            <p>{t("Диагностика прогноза · ревизия {revision} · не оценка точности", { revision: run.revision! })}</p>
          </div>
          <Activity size={23} />
        </div>
        <div className={needsReview ? "source-warnings" : "evaluation-production"}>
          {needsReview ? <TriangleAlert size={17} /> : <Activity size={17} />}
          <div>
            <strong>
              {needsReview ? t("Нужна проверка входов") : t("Наблюдать обновления")}
            </strong>
            {fixture && <p>{t("Синтетический fixture: анализ не описывает реальный эксплуатационный прогноз.")}</p>}
            {!needsReview && <p>{t("Дополнительных диагностических причин для проверки нет. Наблюдение обновлений не подтверждает точность прогноза и не снимает ограничений методики.")}</p>}
            {reasons.map((reason, index) => <p key={index}>{message(reason)}</p>)}
            {reviewWarnings.length > 0 && <>
              <p>{t("Предупреждения для проверки ({count})", { count: number(reviewWarnings.length) })}</p>
              <ul>{reviewWarnings.map((warning) => <li key={warning}>{message(warning)}</li>)}</ul>
            </>}
          </div>
        </div>
        {methodologyWarnings.length > 0 && (
          <details className="evaluation-limitations">
            <summary>{t("Постоянные ограничения методики ({count})", { count: number(methodologyWarnings.length) })}</summary>
            <p>{t("Эти ограничения сохраняются при любом решении агента. Условия времени, нормализация и неопределённость требуют отдельного подтверждения.")}</p>
            <ul>{methodologyWarnings.map((warning) => <li key={warning}>{message(warning)}</li>)}</ul>
          </details>
        )}
        <div className="provenance-grid">
          {Object.entries(turbines).map(([id, value]) => {
            const turbine = object(value)!;
            const ramp = object(turbine.largest_hourly_ramp);
            const wind = object(turbine.weather_wind_speed_ms);
            const unit = String(turbine.target_unit);
            const delta = object(object(comparison?.per_turbine)?.[id]);
            return (
              <section className="source-card" key={id} aria-label={t("Анализ: {turbine}", { turbine: turbineName(id) })}>
                <div className="source-title">
                  <Wind size={18} /><h3>{turbineName(id)}</h3>
                </div>
                <p className="fine-print muted">
                  {unit === "fixture_dimensionless" ? t("Демо-значение") : t("Нормализованная мощность")} · {unit}
                </p>
                {Object.entries({
                  "Минимум / максимум": `${number(turbine.prediction_min)} / ${number(turbine.prediction_max)}`,
                  "Среднее": number(turbine.prediction_mean),
                  "Макс. изменение за час": ramp ? number(ramp.absolute_delta) : t("Не предоставлено"),
                  "Конец часа изменения": typeof ramp?.to_valid_time === "string"
                    ? time(ramp.to_valid_time, zone) : t("Не предоставлено"),
                  "Постоянный прогноз ≥ 24 ч": turbine.flat_forecast === true ? t("Да — проверить входы")
                    : turbine.flat_forecast === false ? t("Не обнаружен") : t("Не предоставлено"),
                  "Ветер, мин. / макс., м/с": wind ? `${number(wind.min)} / ${number(wind.max)}` : t("Не предоставлено"),
                  "Средний ветер, м/с": number(wind?.mean),
                }).map(([label, value]) => (
                  <div className="metadata-row" key={label}><span>{t(label)}</span><strong>{value}</strong></div>
                ))}
                {compared && delta?.status === "compared" && (
                  <div className="metadata-row">
                    <span>{t("Изменение к прошлой ревизии, среднее / максимум")}</span>
                    <strong>{number(delta.mean_absolute_delta)} / {number(delta.max_absolute_delta)} · {unit}</strong>
                  </div>
                )}
              </section>
            );
          })}
        </div>
        <p className="muted fine-print">
          {compared
            ? t("Сравнение ревизий: общих часов {common}, изменено {changed}. Это разница прогнозов, не ошибка относительно факта.", {
              common: number(comparison.common_hours), changed: number(comparison.changed_count),
            })
            : t("Сравнение с предыдущей ревизией недоступно для сопоставимых часов.")}
        </p>
        <p className="muted fine-print">
          {t("Фактическая выработка здесь не используется. Это рекомендация проверить входы или наблюдать обновления; она не меняет прогноз. Для величины скачков порог тревоги не установлен.")}
        </p>
        <details className="journal">
          <summary>{t("Полный анализ и ограничения")}</summary>
          <pre>{JSON.stringify(report, null, 2)}</pre>
        </details>
      </div>
    </article>
  );
}
