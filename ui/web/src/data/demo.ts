import { ApiError, validateRunRequest } from "./client";
import type {
  Evaluation,
  ForecastClient,
  ForecastRecord,
  Health,
  Run,
  RunRequest,
  RunStatus,
} from "./types";

export const DEFAULT_TURBINE_IDS = ["turbine_1", "turbine_2"];
export const DEMO_WARNING =
  "Синтетическая демонстрация. Реальная погода не загружалась, модель не обучалась, точность не оценивалась.";
const UNIT_WARNING =
  "Условная нормализованная шкала 0–1. Значения не выражают МВт или МВт·ч.";
const STATES: RunStatus[] = [
  "queued",
  "fetching_weather",
  "validating",
  "forecasting",
  "analysing",
  "completed",
];
const MESSAGES = [
  "Демонстрационный расчёт поставлен в очередь.",
  "Имитация выбора архивного прогноза. Запрос к провайдеру не выполняется.",
  "Проверка структуры примера и почасовой сетки.",
  "Построение воспроизводимых демонстрационных кривых без обученной модели.",
  "Проверка результата примера. Фактические наблюдения отсутствуют.",
  "Синтетический пример готов к просмотру.",
];

function seedOf(value: string): number {
  let seed = 2166136261;
  for (const char of value) {
    seed ^= char.charCodeAt(0);
    seed = Math.imul(seed, 16777619);
  }
  return seed >>> 0;
}

function csvValue(value: unknown): string {
  const text =
    value === null || value === undefined
      ? ""
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** In-memory fixtures are explicit; ApiClient never falls back to this implementation. */
export class DemoClient implements ForecastClient {
  private readonly runs = new Map<string, Run & { request: RunRequest }>();
  private readonly revisions = new Map<string, number>();

  async health(): Promise<Health> {
    return {
      status: "ok",
      mode: "demo",
      is_demo: true,
      source_mode: "synthetic",
      model_ready: false,
      weather_ready: false,
      data_ready: false,
      turbine_ids: [...DEFAULT_TURBINE_IDS],
      warnings: [DEMO_WARNING],
    };
  }

  async createRun(request: RunRequest): Promise<Run> {
    const params = validateRunRequest(request);
    const key = JSON.stringify([
      params.issue_time,
      params.horizon_hours,
      [...params.turbine_ids].sort(),
    ]);
    const revision = (this.revisions.get(key) ?? 0) + 1;
    this.revisions.set(key, revision);
    const id = `demo-${String(this.runs.size + 1).padStart(4, "0")}`;
    this.runs.set(id, {
      run_id: id,
      revision,
      status: "queued",
      request: params,
      source_mode: "synthetic",
      error: null,
      warnings: [
        DEMO_WARNING,
        UNIT_WARNING,
        "Фактическая выработка и калиброванные интервалы прогноза не предоставлены.",
      ],
      recalculation_reason:
        revision > 1
          ? "Повторный синтетический запуск. Ревизия примера обновлена, входные данные не изменились."
          : "Первый запуск синтетической демонстрации.",
      events: [
        {
          step: "queued",
          message: MESSAGES[0],
          at: new Date().toISOString(),
          details: { source_mode: "synthetic" },
        },
      ],
    });
    return this.snapshot(id);
  }

  private known(id: string): Run & { request: RunRequest } {
    const value = this.runs.get(id);
    if (!value)
      throw new ApiError(
        "Демонстрационный расчёт не найден в этой сессии.",
        "http",
        404,
      );
    return value;
  }

  private snapshot(id: string): Run {
    const value = this.known(id);
    const position = STATES.indexOf(value.status);
    return structuredClone({
      ...value,
      steps: STATES.slice(1, -1).map((name, index) => ({
        name,
        status:
          position > index + 1
            ? "completed"
            : position === index + 1
              ? "running"
              : "pending",
        message: MESSAGES[index + 1],
      })),
    });
  }

  async getRun(id: string): Promise<Run> {
    const value = this.known(id);
    const position = STATES.indexOf(value.status);
    const next = Math.min(position + 1, STATES.length - 1);
    if (next !== position) {
      value.status = STATES[next];
      value.events?.push({
        step: value.status,
        message: MESSAGES[next],
        at: new Date().toISOString(),
        details: { source_mode: "synthetic" },
      });
    }
    return this.snapshot(id);
  }

  async getForecast(id: string): Promise<ForecastRecord[]> {
    const run = this.known(id);
    if (run.status !== "completed")
      throw new ApiError(
        "Синтетический расчёт ещё выполняется. Обновите статус.",
        "http",
        409,
      );
    const issue = Date.parse(run.request.issue_time);
    const rows: ForecastRecord[] = [];
    for (const turbine of run.request.turbine_ids) {
      const seed = seedOf(turbine);
      const phase = ((seed % 360) * Math.PI) / 180;
      const scale = 0.86 + (seed % 11) / 100;
      for (let lead = 1; lead <= run.request.horizon_hours; lead++) {
        const valid = issue + lead * 3_600_000;
        const hour = valid / 3_600_000;
        const wind =
          8.4 +
          2.7 * Math.sin((hour * Math.PI) / 18 + phase) +
          1.4 * Math.cos((hour * Math.PI) / 7 + phase / 2);
        const value =
          scale *
          Math.min(1, Math.max(0, (wind ** 3 - 3 ** 3) / (12 ** 3 - 3 ** 3)));
        rows.push({
          schema_version: "1.0",
          run_id: id,
          revision: run.revision,
          issue_time: run.request.issue_time,
          turbine_id: turbine,
          valid_time: new Date(valid).toISOString(),
          lead_hours: lead,
          prediction: Number(value.toFixed(6)),
          target_unit: "normalized_power",
          time_basis: "interval_end",
          weather_model: "synthetic-demo",
          weather_run_time: new Date(issue - 6 * 3_600_000).toISOString(),
          model_version: "synthetic-fixture-v1",
          training_cutoff: null,
          data_quality: {
            status: "synthetic",
            reason:
              "Демонстрационные данные; реальные наблюдения не использовались.",
          },
          warnings: [DEMO_WARNING, UNIT_WARNING],
          source_mode: "synthetic",
          provider: "synthetic fixture",
          forecast_available_at: null,
          availability_basis: "not_applicable_synthetic",
          raw_sha256: null,
        });
      }
    }
    return rows;
  }

  async getForecastCsv(id: string): Promise<string> {
    const rows = await this.getForecast(id);
    const fields = Object.keys(rows[0]);
    return (
      [
        fields.map(csvValue).join(","),
        ...rows.map((row) =>
          fields.map((field) => csvValue(row[field])).join(","),
        ),
      ].join("\r\n") + "\r\n"
    );
  }

  async getEvaluation(): Promise<Evaluation> {
    return {
      status: "unavailable",
      source_mode: "synthetic",
      is_demo: true,
      reason:
        "Синтетический пример не позволяет оценить точность. Фактическая выработка и отчёт валидации не подключены.",
      test_truth_available: false,
      metrics: null,
      baseline: null,
      warnings: [DEMO_WARNING],
    };
  }
}
