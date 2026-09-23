import { Activity, TriangleAlert, Wind } from "lucide-react";
import { formatTime } from "./data";
import type { Run } from "./data";

type JsonObject = Record<string, unknown>;
const object = (value: unknown): JsonObject | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : null;
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
const number = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 4 }).format(value)
    : "не предоставлено";
const turbineName = (id: string) =>
  ({ turbine_1: "Турбина 01", turbine_2: "Турбина 02" })[id] || id;

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
  const report = currentAnalysis(run);
  if (!report) return null;
  const turbines = object(report.per_turbine)!;
  const reasons = strings(report.reasons);
  const comparison = object(report.previous_comparison);
  const compared = comparison?.status === "compared";
  const fixture = report.is_demo === true ||
    Object.values(turbines).some((value) => object(value)?.target_unit === "fixture_dimensionless");

  return (
    <article className="panel table-card" aria-labelledby="agent-analysis-title">
      <div className="provenance-content">
        <div className="section-heading">
          <div>
            <h2 id="agent-analysis-title">Анализ агента</h2>
            <p>Диагностика прогноза · ревизия {run.revision} · не оценка точности</p>
          </div>
          <Activity size={23} />
        </div>
        <div className="source-warnings">
          {report.decision === "review_required" ? <TriangleAlert size={17} /> : <Activity size={17} />}
          <div>
            <strong>
              {report.next_action === "review_inputs" ? "Нужна проверка входов" : "Наблюдать обновления"}
            </strong>
            {fixture && <p>Синтетический fixture: анализ не описывает реальный эксплуатационный прогноз.</p>}
            {reasons.map((reason, index) => <p key={index}>{reason}</p>)}
          </div>
        </div>
        <div className="provenance-grid">
          {Object.entries(turbines).map(([id, value]) => {
            const turbine = object(value)!;
            const ramp = object(turbine.largest_hourly_ramp);
            const wind = object(turbine.weather_wind_speed_ms);
            const unit = String(turbine.target_unit);
            const delta = object(object(comparison?.per_turbine)?.[id]);
            return (
              <section className="source-card" key={id} aria-label={`Анализ: ${turbineName(id)}`}>
                <div className="source-title">
                  <Wind size={18} /><h3>{turbineName(id)}</h3>
                </div>
                <p className="fine-print muted">
                  {unit === "fixture_dimensionless" ? "Демо-значение" : "Нормализованная мощность"} · {unit}
                </p>
                {Object.entries({
                  "Минимум / максимум": `${number(turbine.prediction_min)} / ${number(turbine.prediction_max)}`,
                  "Среднее": number(turbine.prediction_mean),
                  "Макс. изменение за час": ramp ? number(ramp.absolute_delta) : "не предоставлено",
                  "Конец часа изменения": typeof ramp?.to_valid_time === "string"
                    ? formatTime(ramp.to_valid_time, zone) : "не предоставлено",
                  "Постоянный прогноз ≥ 24 ч": turbine.flat_forecast === true ? "Да — проверить входы"
                    : turbine.flat_forecast === false ? "Не обнаружен" : "не предоставлено",
                  "Ветер, мин. / макс., м/с": wind ? `${number(wind.min)} / ${number(wind.max)}` : "не предоставлено",
                  "Средний ветер, м/с": number(wind?.mean),
                }).map(([label, value]) => (
                  <div className="metadata-row" key={label}><span>{label}</span><strong>{value}</strong></div>
                ))}
                {compared && delta?.status === "compared" && (
                  <div className="metadata-row">
                    <span>Изменение к прошлой ревизии, среднее / максимум</span>
                    <strong>{number(delta.mean_absolute_delta)} / {number(delta.max_absolute_delta)} · {unit}</strong>
                  </div>
                )}
              </section>
            );
          })}
        </div>
        <p className="muted fine-print">
          {compared
            ? `Сравнение ревизий: общих часов ${number(comparison.common_hours)}, изменено ${number(comparison.changed_count)}. Это разница прогнозов, не ошибка относительно факта.`
            : "Сравнение с предыдущей ревизией недоступно для сопоставимых часов."}
        </p>
        <p className="muted fine-print">
          Фактическая выработка здесь не используется. Это рекомендация проверить входы или наблюдать обновления;
          она не меняет прогноз. Для величины скачков порог тревоги не установлен.
        </p>
        <details className="journal">
          <summary>Полный анализ и ограничения</summary>
          <pre>{JSON.stringify(report, null, 2)}</pre>
        </details>
      </div>
    </article>
  );
}
