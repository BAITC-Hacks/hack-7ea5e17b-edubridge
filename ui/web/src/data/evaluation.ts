import type {
  Evaluation,
  EvaluationMetricCard,
  EvaluationScore,
  ForecastRecord,
} from "./types";
import { timestamp } from "./time";
import { ForecastError } from "./validation";

function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new ForecastError(message);
}

function nonnegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function score(value: EvaluationScore) {
  requireValue(
    value && nonnegative(value.mae) && nonnegative(value.rmse),
    "Отчёт содержит некорректные MAE/RMSE.",
  );
  for (const count of [value.n_forecasts, value.n_unique_targets]) {
    requireValue(
      count === undefined || (Number.isInteger(count) && count > 0),
      "Отчёт содержит некорректное число наблюдений.",
    );
  }
}

function metricCard(value: EvaluationMetricCard) {
  requireValue(
    value &&
      value.target_unit === "normalized_power" &&
      nonnegative(value.equal_turbine_mean_mae),
    "Отчёт содержит неподдерживаемые единицы или некорректный MAE.",
  );
  score(value.overall);
  for (const entry of Object.values(value.by_turbine ?? {})) score(entry);
  for (const entry of Object.values(value.by_lead_group ?? {})) score(entry);
}

/** Check the display contract and package association, not statistical validity or authorship. */
export function validateEvaluation(
  report: Evaluation,
  rows: ForecastRecord[] = [],
): Evaluation {
  requireValue(
    report.reason == null || typeof report.reason === "string",
    "Причина состояния отчёта должна быть текстом.",
  );
  requireValue(
    report.warnings === undefined ||
      (Array.isArray(report.warnings) &&
        report.warnings.every((warning) => typeof warning === "string")),
    "Предупреждения отчёта должны быть списком текстовых сообщений.",
  );
  if (report.status !== "ready") return report;
  const metrics = report.metrics,
    baseline = report.baseline;
  requireValue(
    report.is_demo !== true && report.target_unit === "normalized_power",
    "Отчёт ready не может содержать синтетические или неподдерживаемые единицы.",
  );
  requireValue(
    typeof report.model_version === "string" &&
      report.model_version.length &&
      typeof report.training_cutoff === "string",
    "В отчёте отсутствует версия или cutoff production-пакета.",
  );
  requireValue(
    metrics?.tuning &&
      metrics.independent_holdout &&
      metrics.production_refit &&
      baseline?.comparison,
    "В отчёте не разделены tuning, holdout и production refit.",
  );
  requireValue(
    typeof metrics.tuning.selected_candidate === "string" &&
      metrics.tuning.selected_candidate.length &&
      metrics.tuning.selected_candidate ===
        metrics.independent_holdout.selected_candidate,
    "В отчёте некорректно указан выбранный кандидат.",
  );
  const productionTime = timestamp(report.training_cutoff);
  const holdoutTime = timestamp(metrics.independent_holdout.training_cutoff);
  const selectionTime = timestamp(metrics.tuning.selection_cutoff);
  requireValue(
    selectionTime <= holdoutTime &&
      holdoutTime <= productionTime &&
      timestamp(metrics.production_refit.training_cutoff) === productionTime,
    "Границы обучения и выбора в отчёте не согласованы.",
  );
  requireValue(
    metrics.independent_holdout.used_for_selection === false &&
      metrics.production_refit.independent_metrics === null,
    "Отчёт смешивает независимую оценку и production refit.",
  );
  requireValue(
    typeof metrics.production_refit.includes_holdout === "boolean",
    "Не указан состав production refit.",
  );
  requireValue(
    metrics.tuning.selection_metric === "equal_turbine_mean_mae" &&
      baseline.comparison.metric === "equal_turbine_mean_mae",
    "Неподдерживаемая метрика сравнения моделей.",
  );
  metricCard(metrics.tuning.scores);
  metricCard(metrics.independent_holdout.scores);
  for (const stage of [baseline.tuning, baseline.independent_holdout]) {
    requireValue(
      stage?.constant_mean && stage.wind_curve,
      "В отчёте отсутствует сопоставимый baseline.",
    );
    metricCard(stage.constant_mean);
    metricCard(stage.wind_curve);
  }
  const comparison = baseline.comparison;
  const modelMae = metrics.independent_holdout.scores.equal_turbine_mean_mae;
  const curveMae =
    baseline.independent_holdout.wind_curve.equal_turbine_mean_mae;
  requireValue(
    comparison.selected_mae === modelMae &&
      comparison.wind_curve_mae === curveMae &&
      comparison.wind_curve_better === curveMae < modelMae,
    "Сравнение baseline не совпадает с метриками holdout.",
  );
  requireValue(
    report.periods?.windows?.tune &&
      report.periods.windows.holdout &&
      typeof report.periods.history_timezone === "string",
    "В отчёте не указан период оценки и исходный часовой пояс.",
  );
  for (const phase of ["tune", "holdout"]) {
    const period = report.periods.windows[phase];
    requireValue(
      timestamp(period.target_start) < timestamp(period.target_end),
      "Некорректные границы периода оценки.",
    );
  }
  for (const row of rows) {
    requireValue(
      row.model_version === report.model_version &&
        row.target_unit === report.target_unit &&
        typeof row.training_cutoff === "string" &&
        timestamp(row.training_cutoff) === productionTime,
      "Отчёт оценки относится к другому production-пакету, чем показанный прогноз.",
    );
  }
  return report;
}
