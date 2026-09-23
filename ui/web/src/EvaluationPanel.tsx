import { BarChart3, Info, ShieldCheck, TriangleAlert } from "lucide-react";
import { formatTime } from "./data";
import type { EvaluationScore } from "./data";
import type { Result } from "./useForecast";
import "./evaluation.css";

const metric = (value: number) =>
  new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 6,
    maximumFractionDigits: 6,
  }).format(value);
const label = (key: string) =>
  ({ turbine_1: "Турбина 01", turbine_2: "Турбина 02" })[key] || key;

function ScoreTable({
  title,
  scores,
}: {
  title: string;
  scores?: Record<string, EvaluationScore>;
}) {
  if (!scores || !Object.keys(scores).length) return null;
  return (
    <div className="evaluation-score-table">
      <h3>{title}</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Группа</th>
              <th>MAE</th>
              <th>RMSE</th>
              <th>Прогнозов</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(scores).map(([key, score]) => (
              <tr key={key}>
                <td>{label(key)}</td>
                <td>{metric(score.mae)}</td>
                <td>{metric(score.rmse)}</td>
                <td>{score.n_forecasts ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function EvaluationPanel({
  result,
  synthetic,
}: {
  result: Result;
  synthetic: boolean;
}) {
  const report = result.evaluation;
  const invalid = report?.status === "invalid";
  const ready =
    !synthetic &&
    report?.is_demo !== true &&
    report?.status === "ready" &&
    report.metrics &&
    report.baseline &&
    report.periods;
  const metrics = ready ? report.metrics! : null;
  const comparison = ready ? report.baseline!.comparison : null;
  return (
    <div className="evaluation-content">
      <div className="section-heading">
        <div>
          <h2>Качество модели</h2>
          <p>
            Временная валидация · ошибка в normalized_power · меньше — лучше.
          </p>
        </div>
        <ShieldCheck size={24} />
      </div>
      {ready && metrics && comparison ? (
        <>
          <div className="evaluation-package">
            <span className="tag">{report.model_version}</span>
            <span>
              Выбранный кандидат:{" "}
              <strong>{metrics.tuning.selected_candidate}</strong>
            </span>
          </div>
          <div className="evaluation-cards">
            <article>
              <span className="label">TUNING · ВЫБОР МОДЕЛИ</span>
              <strong>
                {metric(metrics.tuning.scores.equal_turbine_mean_mae)}
              </strong>
              <span>MAE · равный вес турбин</span>
              <small>Кандидат выбран по этому периоду.</small>
            </article>
            <article>
              <span className="label">НЕЗАВИСИМЫЙ HOLDOUT</span>
              <strong>
                {metric(
                  metrics.independent_holdout.scores.equal_turbine_mean_mae,
                )}
              </strong>
              <span>MAE · равный вес турбин</span>
              <small>
                RMSE: {metric(metrics.independent_holdout.scores.overall.rmse)}
              </small>
            </article>
            <article>
              <span className="label">WIND CURVE · HOLDOUT</span>
              <strong>{metric(comparison.wind_curve_mae)}</strong>
              <span>MAE · равный вес турбин</span>
              <small>Простой baseline на том же периоде.</small>
            </article>
          </div>
          <div
            className={`evaluation-comparison ${comparison.wind_curve_better ? "baseline-better" : ""}`}
          >
            {comparison.wind_curve_better ? (
              <TriangleAlert size={19} />
            ) : (
              <Info size={19} />
            )}
            <div>
              <strong>
                {comparison.wind_curve_better
                  ? "На holdout простой baseline лучше выбранной модели"
                  : "Сравнение на отдельном holdout-периоде"}
              </strong>
              <p>
                {comparison.wind_curve_better
                  ? "Ошибка wind curve ниже. Выбор модели сохранён по заранее заданному tuning-критерию; holdout не использовался для нового выбора."
                  : "Сравнение относится к указанному периоду. Оно не устанавливает преимущество на будущих данных."}
              </p>
            </div>
          </div>
          <div className="evaluation-periods">
            {(["tune", "holdout"] as const).map((phase) => {
              const period = report.periods!.windows[phase];
              return (
                <article key={phase}>
                  <h3>
                    {phase === "tune" ? "Период выбора" : "Независимая оценка"}
                  </h3>
                  <p>
                    {formatTime(period.target_start, "UTC")}
                    <br />
                    {report.periods!.target_start_exclusive
                      ? "после начала → "
                      : "от начала → "}
                    {formatTime(period.target_end, "UTC")}
                    {report.periods!.target_end_inclusive
                      ? " включительно"
                      : ""}
                  </p>
                  <small>
                    {phase === "tune" ? "Граница выбора" : "Cutoff модели"}:{" "}
                    {formatTime(
                      phase === "tune"
                        ? metrics.tuning.selection_cutoff
                        : metrics.independent_holdout.training_cutoff,
                      "UTC",
                    )}
                  </small>
                </article>
              );
            })}
          </div>
          <p className="muted fine-print">
            Исходный часовой пояс отчёта:{" "}
            <strong>{report.periods!.history_timezone}</strong>. Границы выше
            показаны в UTC. Перекрывающиеся прогнозы не являются независимыми
            наблюдениями.
          </p>
          <div className="evaluation-production">
            <Info size={18} />
            <div>
              <h3>Production refit — отдельный пакет</h3>
              <p>
                Обучение до{" "}
                {formatTime(metrics.production_refit.training_cutoff, "UTC")}.{" "}
                {metrics.production_refit.includes_holdout
                  ? "В обучение включён holdout."
                  : "Holdout не включён в обучение."}{" "}
                Независимая оценка итогового production refit не предоставлена.
              </p>
              <p>
                Показанные holdout-метрики относятся к модели, замороженной до
                holdout, с cutoff{" "}
                {formatTime(metrics.independent_holdout.training_cutoff, "UTC")}
                .
              </p>
            </div>
          </div>
          <div className="evaluation-tables">
            <ScoreTable
              title="Holdout по турбинам"
              scores={metrics.independent_holdout.scores.by_turbine}
            />
            <ScoreTable
              title="Holdout по горизонту, ч"
              scores={metrics.independent_holdout.scores.by_lead_group}
            />
          </div>
          {report.warnings?.length ? (
            <details className="evaluation-limitations">
              <summary>Ограничения отчёта ({report.warnings.length})</summary>
              <ul>
                {report.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : (
        <div className="empty-evaluation">
          <div className="empty-icon">
            {invalid ? <TriangleAlert size={27} /> : <BarChart3 size={27} />}
          </div>
          <h3>
            {synthetic || report?.is_demo
              ? "Демо показывает интерфейс"
              : invalid
                ? "Отчёт оценки не прошёл проверку"
                : "Отчёт ещё не предоставлен"}
          </h3>
          <p>
            {synthetic || report?.is_demo
              ? "Синтетические значения не измеряют точность модели. Реальные метрики появятся после подключения проверенного отчёта."
              : report?.reason ||
                "Для показа метрик требуется отчёт, связанный с production-пакетом текущего прогноза."}
          </p>
          <span className="tag">Метрики недоступны</span>
        </div>
      )}
      {result.evaluationError && (
        <p role="alert" className="error-inline">
          {result.evaluationError}
        </p>
      )}
      <p className="muted fine-print">
        {report?.test_truth_available === true
          ? "Доступность тестовых наблюдений указана сервисом; период и единицы сверяйте с отчётом."
          : "Фактическая выработка февраля 2026 не предоставлена. Эти метрики не являются февральской точностью."}{" "}
        MAE/RMSE не выражают процент точности или MW/MWh.
      </p>
      {report && (
        <details className="journal">
          <summary>Полный отчёт и контрольные суммы</summary>
          <pre className="evaluation-json">
            {JSON.stringify(report, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
