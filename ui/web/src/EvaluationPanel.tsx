import { BarChart3, Info, ShieldCheck, TriangleAlert } from "lucide-react";
import { useI18n } from "./i18n";
import type { EvaluationScore } from "./data";
import type { Result } from "./useForecast";
import "./evaluation.css";

function ScoreTable({
  title,
  scores,
}: {
  title: string;
  scores?: Record<string, EvaluationScore>;
}) {
  const { t, number, turbineName } = useI18n();
  if (!scores || !Object.keys(scores).length) return null;
  return (
    <div className="evaluation-score-table">
      <h3>{title}</h3>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t("Группа")}</th>
              <th>MAE</th>
              <th>RMSE</th>
              <th>{t("Прогнозов")}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(scores).map(([key, score]) => (
              <tr key={key}>
                <td>{turbineName(key)}</td>
                <td>{number(score.mae, 6)}</td>
                <td>{number(score.rmse, 6)}</td>
                <td>{number(score.n_forecasts, 0)}</td>
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
  const { t, number, time, message } = useI18n();
  const metric = (value: number) => number(value, 6);
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
          <h2>{t("Качество модели")}</h2>
          <p>
            {t("Временная валидация · ошибка в normalized_power · меньше — лучше.")}
          </p>
        </div>
        <ShieldCheck size={24} />
      </div>
      {ready && metrics && comparison ? (
        <>
          <div className="evaluation-package">
            <span className="tag">{report.model_version}</span>
            <span>
              {t("Выбранный кандидат:")}{" "}
              <strong>{metrics.tuning.selected_candidate}</strong>
            </span>
          </div>
          <div className="evaluation-cards">
            <article>
              <span className="label">{t("TUNING · ВЫБОР МОДЕЛИ")}</span>
              <strong>
                {metric(metrics.tuning.scores.equal_turbine_mean_mae)}
              </strong>
              <span>{t("MAE · равный вес турбин")}</span>
              <small>{t("Кандидат выбран по этому периоду.")}</small>
            </article>
            <article>
              <span className="label">{t("НЕЗАВИСИМЫЙ HOLDOUT")}</span>
              <strong>
                {metric(
                  metrics.independent_holdout.scores.equal_turbine_mean_mae,
                )}
              </strong>
              <span>{t("MAE · равный вес турбин")}</span>
              <small>
                RMSE: {metric(metrics.independent_holdout.scores.overall.rmse)}
              </small>
            </article>
            <article>
              <span className="label">{t("WIND CURVE · HOLDOUT")}</span>
              <strong>{metric(comparison.wind_curve_mae)}</strong>
              <span>{t("MAE · равный вес турбин")}</span>
              <small>{t("Простой baseline на том же периоде.")}</small>
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
                  ? t("На holdout простой baseline лучше выбранной модели")
                  : t("Сравнение на отдельном holdout-периоде")}
              </strong>
              <p>
                {comparison.wind_curve_better
                  ? t("Ошибка wind curve ниже. Выбор модели сохранён по заранее заданному tuning-критерию; holdout не использовался для нового выбора.")
                  : t("Сравнение относится к указанному периоду. Оно не устанавливает преимущество на будущих данных.")}
              </p>
            </div>
          </div>
          <div className="evaluation-periods">
            {(["tune", "holdout"] as const).map((phase) => {
              const period = report.periods!.windows[phase];
              return (
                <article key={phase}>
                  <h3>
                    {phase === "tune" ? t("Период выбора") : t("Независимая оценка")}
                  </h3>
                  <p>
                    {time(period.target_start, "UTC")}
                    <br />
                    {report.periods!.target_start_exclusive
                      ? t("после начала → ")
                      : t("от начала → ")}
                    {time(period.target_end, "UTC")}
                    {report.periods!.target_end_inclusive
                      ? t(" включительно")
                      : ""}
                  </p>
                  <small>
                    {phase === "tune" ? t("Граница выбора") : t("Cutoff модели")}:{" "}
                    {time(
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
            {t("Исходный часовой пояс отчёта:")}{" "}
            <strong>{report.periods!.history_timezone}</strong>.{" "}
            {t("Границы выше показаны в UTC. Перекрывающиеся прогнозы не являются независимыми наблюдениями.")}
          </p>
          <div className="evaluation-production">
            <Info size={18} />
            <div>
              <h3>{t("Production refit — отдельный пакет")}</h3>
              <p>
                {t("Обучение до {cutoff}.", {
                  cutoff: time(metrics.production_refit.training_cutoff, "UTC"),
                })}{" "}
                {metrics.production_refit.includes_holdout
                  ? t("В обучение включён holdout.")
                  : t("Holdout не включён в обучение.")}{" "}
                {t("Независимая оценка итогового production refit не предоставлена.")}
              </p>
              <p>
                {t("Показанные holdout-метрики относятся к модели, замороженной до holdout, с cutoff {cutoff}.", {
                  cutoff: time(metrics.independent_holdout.training_cutoff, "UTC"),
                })}
              </p>
            </div>
          </div>
          <div className="evaluation-tables">
            <ScoreTable
              title={t("Holdout по турбинам")}
              scores={metrics.independent_holdout.scores.by_turbine}
            />
            <ScoreTable
              title={t("Holdout по горизонту, ч")}
              scores={metrics.independent_holdout.scores.by_lead_group}
            />
          </div>
          {report.warnings?.length ? (
            <details className="evaluation-limitations">
              <summary>{t("Ограничения отчёта ({count})", { count: number(report.warnings.length, 0) })}</summary>
              <ul>
                {report.warnings.map((warning, index) => (
                  <li key={index}>{message(warning)}</li>
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
              ? t("Демо показывает интерфейс")
              : invalid
                ? t("Отчёт оценки не прошёл проверку")
                : t("Отчёт ещё не предоставлен")}
          </h3>
          <p>
            {synthetic || report?.is_demo
              ? t("Синтетические значения не измеряют точность модели. Реальные метрики появятся после подключения проверенного отчёта.")
              : (report?.reason && message(report.reason)) ||
                t("Для показа метрик требуется отчёт, связанный с production-пакетом текущего прогноза.")}
          </p>
          <span className="tag">{t("Метрики недоступны")}</span>
        </div>
      )}
      {result.evaluationError && (
        <p role="alert" className="error-inline">
          {message(result.evaluationError)}
        </p>
      )}
      <p className="muted fine-print">
        {report?.test_truth_available === true
          ? t("Доступность тестовых наблюдений указана сервисом; период и единицы сверяйте с отчётом.")
          : t("Фактическая выработка февраля 2026 не предоставлена. Эти метрики не являются февральской точностью.")}{" "}
        {t("MAE/RMSE не выражают процент точности или MW/MWh.")}
      </p>
      {report && (
        <details className="journal">
          <summary>{t("Полный отчёт и контрольные суммы")}</summary>
          <pre className="evaluation-json">
            {JSON.stringify(report, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
