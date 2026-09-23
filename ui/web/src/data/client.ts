import type {
  Evaluation,
  ForecastClient,
  ForecastRecord,
  Health,
  Run,
  RunRequest,
  RunStatus,
} from "./types";
import { timestamp } from "./time";

const MAX_BYTES = 10 * 1024 * 1024;
const STATUSES: RunStatus[] = [
  "queued",
  "fetching_weather",
  "validating",
  "forecasting",
  "analysing",
  "completed",
  "failed",
];

export class ApiError extends Error {
  readonly kind: string;
  readonly statusCode?: number;
  constructor(message: string, kind = "response", statusCode?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.statusCode = statusCode;
  }
}

export function validateRunRequest(request: RunRequest): RunRequest {
  let issue: number;
  try {
    issue = timestamp(request.issue_time);
  } catch {
    throw new ApiError(
      "Укажите корректное время выпуска с часовым поясом.",
      "input",
    );
  }
  if (issue % 3_600_000 !== 0 || /\.(\d*[1-9]\d*)/.test(request.issue_time))
    throw new ApiError(
      "Время выпуска должно приходиться на начало часа по UTC.",
      "input",
    );
  if (request.horizon_hours !== 24 && request.horizon_hours !== 48)
    throw new ApiError("Горизонт должен составлять 24 или 48 часов.", "input");
  if (!Array.isArray(request.turbine_ids) || !request.turbine_ids.length)
    throw new ApiError("Выберите хотя бы одну турбину.", "input");
  if (
    request.turbine_ids.some(
      (id) =>
        typeof id !== "string" ||
        !id.trim() ||
        id.length > 128 ||
        /[\x00-\x1f\x7f]/.test(id),
    )
  )
    throw new ApiError("Некорректный идентификатор турбины.", "input");
  if (new Set(request.turbine_ids).size !== request.turbine_ids.length)
    throw new ApiError(
      "Каждую турбину можно выбрать только один раз.",
      "input",
    );
  return {
    issue_time: new Date(issue).toISOString(),
    horizon_hours: request.horizon_hours,
    turbine_ids: [...request.turbine_ids],
  };
}

function quoteId(id: string): string {
  if (
    typeof id !== "string" ||
    !id.trim() ||
    id === "." ||
    id === ".." ||
    id.length > 256 ||
    /[\x00-\x1f\x7f]/.test(id)
  )
    throw new ApiError("Некорректный идентификатор расчёта.", "input");
  return encodeURIComponent(id);
}

function object(value: unknown, route: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new ApiError(`Маршрут ${route} должен возвращать JSON-объект.`);
  return value as Record<string, unknown>;
}

function run(value: unknown): Run {
  const result = object(value, "/runs");
  if (typeof result.run_id !== "string" || !result.run_id.trim())
    throw new ApiError("В ответе сервиса отсутствует run_id.");
  if (!STATUSES.includes(result.status as RunStatus))
    throw new ApiError("Сервис вернул неизвестный статус расчёта.");
  // A new backend run has revision 0 until its first forecast is committed.
  // Failed initial runs can retain 0; completed output must have a real revision.
  const minimumRevision = result.status === "completed" ? 1 : 0;
  if (
    result.revision !== undefined &&
    (!Number.isInteger(result.revision) ||
      Number(result.revision) < minimumRevision)
  )
    throw new ApiError("Сервис вернул некорректную ревизию расчёта.");
  return result as unknown as Run;
}

export class ApiClient implements ForecastClient {
  readonly baseUrl: string;
  readonly timeoutMs: number;
  constructor(baseUrl = "/api", timeoutMs = 10_000) {
    if (
      typeof baseUrl !== "string" ||
      !baseUrl.trim() ||
      /\s|[\x00-\x1f\x7f]/.test(baseUrl)
    )
      throw new ApiError("Укажите корректный адрес API.", "input");
    let parsed: URL;
    try {
      parsed = new URL(baseUrl, "http://relative.invalid");
    } catch {
      throw new ApiError("Укажите корректный HTTP(S) адрес API.", "input");
    }
    const relative = baseUrl.startsWith("/") && !baseUrl.startsWith("//");
    if (
      (!relative && !/^https?:\/\//i.test(baseUrl)) ||
      !["http:", "https:"].includes(parsed.protocol) ||
      parsed.username ||
      parsed.password ||
      parsed.search ||
      parsed.hash ||
      parsed.port === "0" ||
      baseUrl.includes("\\")
    )
      throw new ApiError(
        "Адрес API должен быть HTTP(S) URL или путём без логина, пароля и параметров.",
        "input",
      );
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 60_000)
      throw new ApiError(
        "Тайм-аут должен быть больше 0 и не больше 60 секунд.",
        "input",
      );
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeoutMs = timeoutMs;
  }

  private async request(
    route: string,
    payload?: RunRequest,
    csv = false,
  ): Promise<string> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(
          new ApiError(
            `API не ответил за ${this.timeoutMs / 1000} сек. Обновите статус позже.`,
            "timeout",
          ),
        );
      }, this.timeoutMs);
    });
    const operation = async () => {
      const response = await fetch(this.baseUrl + route, {
        method: payload ? "POST" : "GET",
        signal: controller.signal,
        headers: {
          Accept: csv ? "text/csv" : "application/json",
          ...(payload ? { "Content-Type": "application/json" } : {}),
        },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
      });
      if (Number(response.headers.get("content-length")) > MAX_BYTES) {
        controller.abort();
        throw new ApiError(
          "Ответ API превышает ограничение интерфейса в 10 MiB.",
        );
      }
      const reader = response.body?.getReader();
      const decoder = new TextDecoder("utf-8", { fatal: true });
      let content = "";
      let size = 0;
      if (reader) {
        try {
          while (true) {
            const chunk = await reader.read();
            if (chunk.done) break;
            size += chunk.value.byteLength;
            if (size > MAX_BYTES) {
              await reader.cancel();
              throw new ApiError(
                "Ответ API превышает ограничение интерфейса в 10 MiB.",
              );
            }
            content += decoder.decode(chunk.value, { stream: true });
          }
          content += decoder.decode();
        } finally {
          reader.releaseLock();
        }
      }
      if (!response.ok) {
        let detail = "";
        try {
          const parsed = JSON.parse(content);
          const candidate = parsed?.detail ?? parsed?.message ?? parsed?.error;
          if (typeof candidate === "string")
            detail = " " + candidate.replace(/\s+/g, " ").slice(0, 300);
        } catch {
          /* A proxy may return HTML. Its text is not a useful API error. */
        }
        throw new ApiError(
          `API вернул ошибку HTTP ${response.status}.${detail}`,
          "http",
          response.status,
        );
      }
      if (csv) {
        if (!content.trim())
          throw new ApiError("API вернул пустой CSV-файл.", "empty");
        if (
          /text\/html|application\/json/i.test(
            response.headers.get("content-type") ?? "",
          )
        )
          throw new ApiError("API вернул другой формат вместо CSV.");
      }
      return content;
    };
    try {
      return await Promise.race([operation(), timeout]);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (controller.signal.aborted)
        throw new ApiError(
          "Время ожидания API истекло. Обновите статус позже.",
          "timeout",
        );
      throw new ApiError(
        "Не удалось получить ответ API. Проверьте адрес, подключение и запуск сервиса.",
        "connection",
      );
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  private async json(route: string, payload?: RunRequest): Promise<unknown> {
    const text = await this.request(route, payload);
    try {
      return JSON.parse(text.replace(/^\uFEFF/, ""));
    } catch {
      throw new ApiError("API вернул некорректный JSON.");
    }
  }

  async health(): Promise<Health> {
    const value = object(await this.json("/health"), "/health");
    if (typeof value.status !== "string" || !value.status)
      throw new ApiError("В ответе /health отсутствует статус сервиса.");
    return value as Health;
  }
  async createRun(request: RunRequest): Promise<Run> {
    return run(await this.json("/runs", validateRunRequest(request)));
  }
  async getRun(id: string): Promise<Run> {
    const value = run(await this.json(`/runs/${quoteId(id)}`));
    if (value.run_id !== id)
      throw new ApiError(
        "Ответ относится к другому расчёту: run_id не совпадает.",
      );
    return value;
  }
  async getForecast(id: string): Promise<ForecastRecord[]> {
    let value = await this.json(`/runs/${quoteId(id)}/forecast`);
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      "records" in value
    )
      value = value.records;
    if (!Array.isArray(value))
      throw new ApiError("Прогноз должен содержать массив записей.");
    return value.map((item) => {
      const row = object(item, "/forecast");
      if (
        [
          "run_id",
          "issue_time",
          "turbine_id",
          "valid_time",
          "lead_hours",
          "prediction",
          "target_unit",
        ].some((key) => !(key in row))
      )
        throw new ApiError("В прогнозе отсутствуют обязательные поля.");
      if (row.run_id !== id)
        throw new ApiError(
          "Прогноз относится к другому расчёту: run_id не совпадает.",
        );
      if (
        typeof row.prediction !== "number" ||
        !Number.isFinite(row.prediction)
      )
        throw new ApiError("Прогноз содержит некорректное числовое значение.");
      return row as unknown as ForecastRecord;
    });
  }
  async getForecastCsv(id: string): Promise<string> {
    return this.request(`/runs/${quoteId(id)}/forecast.csv`, undefined, true);
  }
  async getEvaluation(): Promise<Evaluation> {
    return object(await this.json("/evaluation"), "/evaluation") as Evaluation;
  }
}
