import type { ForecastRecord, Run, RunRequest } from "./types";
import { timestamp } from "./time";
import { validateRunRequest } from "./client";

export class ForecastError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ForecastError";
  }
}

function time(value: unknown): number {
  try {
    return timestamp(value);
  } catch (error) {
    throw new ForecastError(
      error instanceof Error ? error.message : "Некорректная дата.",
    );
  }
}

export function validateForecast(
  rows: ForecastRecord[],
  request: RunRequest,
  run?: Run,
): ForecastRecord[] {
  validateRunRequest(request);
  if (!Array.isArray(rows) || !rows.length)
    throw new ForecastError(
      "Сервис завершил расчёт, но вернул пустой прогноз.",
    );
  const origin = time(request.issue_time);
  const turbines = new Set(request.turbine_ids);
  const seen = new Set<string>();
  const units = new Set<string>();
  let firstRun: string | undefined;
  let firstRevision: number | undefined;
  const validated = rows.map((row) => {
    if (!row || typeof row !== "object" || Array.isArray(row))
      throw new ForecastError("Прогноз должен содержать записи JSON.");
    if (typeof row.prediction !== "number" || !Number.isFinite(row.prediction))
      throw new ForecastError(
        "Прогноз содержит нечисловое или бесконечное значение.",
      );
    if (
      row.target_unit !== "normalized_power" &&
      !(
        row.target_unit === "fixture_dimensionless" &&
        row.data_quality === "fixture"
      )
    )
      throw new ForecastError(
        "Неподдерживаемые единицы. Ожидается normalized_power или явно помеченный fixture.",
      );
    units.add(row.target_unit);
    if (units.size > 1)
      throw new ForecastError(
        "Нельзя отображать разные единицы на одной шкале прогноза.",
      );
    if (!turbines.has(row.turbine_id) || time(row.issue_time) !== origin)
      throw new ForecastError(
        "Прогноз относится к другому выпуску или набору турбин.",
      );
    if (
      !Number.isInteger(row.lead_hours) ||
      row.lead_hours < 1 ||
      row.lead_hours > request.horizon_hours
    )
      throw new ForecastError("Некорректный горизонт строки прогноза.");
    if (time(row.valid_time) !== origin + row.lead_hours * 3_600_000)
      throw new ForecastError(
        "Временная сетка не соответствует часовому горизонту выпуска.",
      );
    const key = JSON.stringify([row.turbine_id, row.lead_hours]);
    if (seen.has(key))
      throw new ForecastError("В прогнозе повторяется час одной турбины.");
    seen.add(key);
    if (typeof row.run_id !== "string" || !row.run_id.trim())
      throw new ForecastError("В прогнозе отсутствует идентификатор расчёта.");
    if (firstRun === undefined) {
      firstRun = row.run_id;
      firstRevision = row.revision;
    }
    if (row.run_id !== firstRun || row.revision !== firstRevision)
      throw new ForecastError(
        "Прогноз содержит несколько запусков или ревизий.",
      );
    if (
      row.revision !== undefined &&
      (!Number.isInteger(row.revision) || row.revision < 1)
    )
      throw new ForecastError("Некорректная ревизия прогноза.");
    if (run && row.run_id !== run.run_id)
      throw new ForecastError("Ответ содержит прогноз другого запуска.");
    if (run?.revision !== undefined && row.revision !== run.revision)
      throw new ForecastError(
        "Ревизия прогноза не совпадает с ревизией расчёта.",
      );
    for (const field of [
      "training_cutoff",
      "weather_run_time",
      "forecast_available_at",
    ] as const) {
      if (
        row[field] !== null &&
        row[field] !== undefined &&
        time(row[field]) > origin
      ) {
        const messages = {
          training_cutoff: "Модель обучена на данных после момента выпуска.",
          weather_run_time:
            "Погодный выпуск находится в будущем относительно расчёта.",
          forecast_available_at: "Погода стала доступна после момента расчёта.",
        };
        throw new ForecastError(messages[field]);
      }
    }
    return { ...row };
  });
  const count = turbines.size * request.horizon_hours;
  if (seen.size !== count)
    throw new ForecastError(
      `Неполный прогноз: получено ${seen.size} из ${count} часов по турбинам.`,
    );
  return validated.sort(
    (a, b) =>
      a.turbine_id.localeCompare(b.turbine_id) || a.lead_hours - b.lead_hours,
  );
}

/** RFC 4180 subset with embedded commas/newlines, quotes and CRLF; malformed input is rejected. */
function parseCsv(content: string): Record<string, string>[] {
  const text = content.replace(/^\uFEFF/, "");
  const records: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let afterQuote = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index++;
        } else {
          quoted = false;
          afterQuote = true;
        }
      } else field += char;
      continue;
    }
    if (char === '"') {
      if (field || afterQuote) throw new Error("Unexpected CSV quote");
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
      afterQuote = false;
    } else if (char === "\r" || char === "\n") {
      if (char === "\r" && text[index + 1] === "\n") index++;
      row.push(field);
      if (row.some((value) => value.length)) records.push(row);
      row = [];
      field = "";
      afterQuote = false;
    } else {
      if (afterQuote) throw new Error("Unexpected content after quote");
      field += char;
    }
  }
  if (quoted) throw new Error("Unclosed CSV quote");
  if (field.length || row.length || afterQuote) {
    row.push(field);
    records.push(row);
  }
  const headers = records.shift();
  if (
    !headers?.length ||
    headers.some((header) => !header) ||
    new Set(headers).size !== headers.length
  )
    throw new Error("Invalid CSV header");
  return records.map((values) => {
    if (values.length !== headers.length)
      throw new Error("Invalid CSV column count");
    return Object.fromEntries(
      headers.map((key, index) => [key, values[index]]),
    );
  });
}

function sameJsonValue(actual: unknown, expected: unknown): boolean {
  if (actual === expected) return true;
  if (Array.isArray(expected))
    return (
      Array.isArray(actual) &&
      actual.length === expected.length &&
      expected.every((value, index) => sameJsonValue(actual[index], value))
    );
  if (
    expected !== null &&
    typeof expected === "object" &&
    actual !== null &&
    typeof actual === "object" &&
    !Array.isArray(actual)
  ) {
    const expectedObject = expected as Record<string, unknown>;
    const actualObject = actual as Record<string, unknown>;
    const keys = Object.keys(expectedObject);
    return (
      Object.keys(actualObject).length === keys.length &&
      keys.every(
        (key) =>
          Object.hasOwn(actualObject, key) &&
          sameJsonValue(actualObject[key], expectedObject[key]),
      )
    );
  }
  return false;
}

const csvTimeFields = new Set([
  "issue_time",
  "valid_time",
  "weather_run_time",
  "training_cutoff",
  "forecast_available_at",
]);

function csvValueMatches(
  field: string,
  actual: string,
  expected: unknown,
): boolean {
  // CSV has no native null: the API writes an empty cell for JSON null.
  // Empty arrays and objects still require their JSON representation.
  if (expected === null) return actual === "" || actual.trim() === "null";
  if (expected === undefined) return actual === "";
  if (typeof expected === "string")
    return csvTimeFields.has(field)
      ? time(actual) === time(expected)
      : actual === expected;
  if (typeof expected === "number")
    return (
      /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/.test(actual.trim()) &&
      Number.isFinite(expected) &&
      Number(actual) === expected
    );
  if (typeof expected === "boolean")
    return actual.trim().toLowerCase() === String(expected);
  return sameJsonValue(JSON.parse(actual), expected);
}

export function validateCsv(
  content: string,
  rows: ForecastRecord[],
  run: Run,
): string {
  try {
    if (!rows.length || !content.trim()) throw new Error("Empty export");
    const expected = new Map(
      rows.map((row) => [
        JSON.stringify([row.turbine_id, time(row.valid_time)]),
        row,
      ]),
    );
    if (expected.size !== rows.length) throw new Error("Duplicate source data");
    const fields = new Set(rows.flatMap((row) => Object.keys(row)));
    const seen = new Set<string>();
    for (const row of parseCsv(content)) {
      if (
        Object.keys(row).length !== fields.size ||
        Object.keys(row).some((field) => !fields.has(field))
      )
        throw new Error("Different export fields");
      const key = JSON.stringify([row.turbine_id, time(row.valid_time)]);
      const source = expected.get(key);
      if (
        !source ||
        seen.has(key) ||
        row.run_id !== run.run_id ||
        row.run_id !== source.run_id
      )
        throw new Error("Wrong or duplicate run");
      const revision = run.revision ?? source.revision;
      if (
        revision !== undefined &&
        (!Object.hasOwn(row, "revision") ||
          !csvValueMatches("revision", row.revision, revision))
      )
        throw new Error("Different revision");
      for (const field of fields)
        if (!csvValueMatches(field, row[field], source[field]))
          throw new Error("Different values or metadata");
      seen.add(key);
    }
    if (seen.size !== expected.size) throw new Error("Incomplete export");
    return content;
  } catch {
    throw new ForecastError(
      "CSV не соответствует показанному прогнозу или не содержит обязательные метаданные.",
    );
  }
}

export function isSynthetic(rows: ForecastRecord[], run?: Run): boolean {
  return (
    run?.source_mode === "synthetic" ||
    rows.some(
      (row) =>
        row.source_mode === "synthetic" ||
        row.data_quality === "fixture" ||
        row.target_unit === "fixture_dimensionless" ||
        (typeof row.data_quality === "object" &&
          row.data_quality?.status === "synthetic"),
    )
  );
}

export function weatherProvenance(
  run: Run,
  rows: ForecastRecord[],
): Record<string, unknown>[] {
  const fields = [
    "turbine_id",
    "weather_model",
    "weather_run_time",
    "model_version",
    "training_cutoff",
    "forecast_available_at",
    "availability_basis",
    "provider",
    "raw_sha256",
    "source_mode",
    "time_basis",
  ];
  const events = Array.isArray(run.events) ? run.events : [];
  const seen = new Set<string>();
  return rows
    .filter((row) => {
      if (seen.has(row.turbine_id)) return false;
      seen.add(row.turbine_id);
      return true;
    })
    .map((row) => {
      let matching: Record<string, unknown> = {};
      for (const event of [...events].reverse()) {
        const provenance = event?.details?.weather_provenance;
        if (
          !provenance ||
          typeof provenance !== "object" ||
          Array.isArray(provenance)
        )
          continue;
        const candidate = (provenance as Record<string, unknown>)[
          row.turbine_id
        ];
        if (
          !candidate ||
          typeof candidate !== "object" ||
          Array.isArray(candidate)
        )
          continue;
        const meta = candidate as Record<string, unknown>;
        if (!row.weather_model || meta.weather_model !== row.weather_model)
          continue;
        try {
          if (time(meta.model_run_time) !== time(row.weather_run_time))
            continue;
        } catch {
          continue;
        }
        matching = Object.fromEntries(
          Object.entries(meta).filter(
            ([, value]) => typeof value === "string" || value === null,
          ),
        );
        if (!("raw_sha256" in matching))
          matching.raw_sha256 = matching.raw_sha256_example ?? null;
        if (!("time_basis" in matching))
          matching.time_basis = matching.weather_feature_time_basis ?? null;
        break;
      }
      return Object.fromEntries(
        fields.map((field) => [
          field,
          field in row ? row[field] : (matching[field] ?? null),
        ]),
      );
    });
}

export function formatTime(
  iso: string | null | undefined,
  zone = "UTC",
): string {
  if (!iso) return "Не предоставлено";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone: zone,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZoneName: "short",
    }).format(new Date(time(iso)));
  } catch {
    return "Некорректное время";
  }
}
