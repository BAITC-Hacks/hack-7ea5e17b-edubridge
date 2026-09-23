import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, validateRunRequest } from "./client";
import { DemoClient } from "./demo";
import {
  formatTime,
  isSynthetic,
  validateCsv,
  validateForecast,
  weatherProvenance,
} from "./validation";
import type { ForecastRecord, Run, RunRequest } from "./types";

const request: RunRequest = {
  issue_time: "2026-02-01T00:00:00Z",
  horizon_hours: 24,
  turbine_ids: ["turbine_1", "turbine_2"],
};

async function completed(params = request) {
  const client = new DemoClient();
  let run = await client.createRun(params);
  for (let poll = 0; poll < 5; poll++) run = await client.getRun(run.run_id);
  return { client, run, rows: await client.getForecast(run.run_id) };
}

function csv(rows: ForecastRecord[]): string {
  const fields = Object.keys(rows[0]);
  const field = (value: unknown) => {
    const text =
      value == null
        ? ""
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);
    return '"' + text.replace(/"/g, '""') + '"';
  };
  return [
    fields.join(","),
    ...rows.map((row) => fields.map((name) => field(row[name])).join(",")),
  ].join("\n");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("deterministic, explicit demo", () => {
  it("uses five status polls, a complete hourly grid and independent turbine curves", async () => {
    const { client, run, rows } = await completed();
    expect(run.status).toBe("completed");
    expect(run.events?.map((event) => event.step)).toEqual([
      "queued",
      "fetching_weather",
      "validating",
      "forecasting",
      "analysing",
      "completed",
    ]);
    expect(validateForecast(rows, request, run)).toHaveLength(48);
    expect(
      rows
        .filter((row) => row.turbine_id === "turbine_1")
        .map((row) => row.prediction),
    ).not.toEqual(
      rows
        .filter((row) => row.turbine_id === "turbine_2")
        .map((row) => row.prediction),
    );
    expect(isSynthetic(rows, run)).toBe(true);
    expect(
      rows.every((row) => !("actual" in row) && !("confidence" in row)),
    ).toBe(true);
    expect(await client.getEvaluation()).toMatchObject({
      metrics: null,
      baseline: null,
      test_truth_available: false,
    });
    expect(
      validateCsv(await client.getForecastCsv(run.run_id), rows, run),
    ).toContain("synthetic");
  });

  it("keeps values deterministic on a new run and labels its revision as simulated", async () => {
    const { client, run, rows } = await completed();
    let repeat = await client.createRun(request);
    expect(repeat.run_id).not.toBe(run.run_id);
    expect(repeat.revision).toBe(2);
    await expect(client.getForecast(repeat.run_id)).rejects.toMatchObject({
      statusCode: 409,
    });
    for (let poll = 0; poll < 5; poll++)
      repeat = await client.getRun(repeat.run_id);
    expect(
      (await client.getForecast(repeat.run_id)).map((row) => row.prediction),
    ).toEqual(rows.map((row) => row.prediction));
    expect(repeat.recalculation_reason).toContain("синтетический");
    await expect(client.getRun("missing")).rejects.toMatchObject({
      statusCode: 404,
    });
  });

  it("supports a 48-hour single-turbine forecast and protects in-memory records", async () => {
    const params: RunRequest = {
      ...request,
      horizon_hours: 48,
      turbine_ids: ["turbine_2"],
    };
    const { client, run, rows } = await completed(params);
    run.request!.turbine_ids.length = 0;
    rows[0].prediction = -999;
    const fresh = await client.getForecast(run.run_id);
    expect(validateForecast(fresh, params, run)).toHaveLength(48);
    expect(fresh[0].prediction).not.toBe(-999);
  });
});

describe("forecast integrity", () => {
  it("accepts backend fixture units only when every row is explicitly a fixture", async () => {
    const { run, rows } = await completed();
    const fixture = rows.map((row) => ({
      ...row,
      target_unit: "fixture_dimensionless",
      data_quality: "fixture",
    }));
    expect(validateForecast(fixture, request, run)).toHaveLength(48);
    expect(isSynthetic(fixture)).toBe(true);
    fixture[0].data_quality = "ok";
    expect(() => validateForecast(fixture, request, run)).toThrow("единицы");
    expect(() =>
      validateForecast(
        [{ ...rows[0], target_unit: "MW" }, ...rows.slice(1)],
        request,
        run,
      ),
    ).toThrow("единицы");
  });

  it("rejects duplicate, missing, shifted and mixed-unit hourly results", async () => {
    const { run, rows } = await completed();
    expect(() => validateForecast(rows.slice(1), request, run)).toThrow(
      "Неполный",
    );
    expect(() =>
      validateForecast([rows[0], rows[0], ...rows.slice(2)], request, run),
    ).toThrow("повторяется");
    expect(() =>
      validateForecast(
        [{ ...rows[0], valid_time: request.issue_time }, ...rows.slice(1)],
        request,
        run,
      ),
    ).toThrow("сетка");
    expect(() =>
      validateForecast(
        [
          {
            ...rows[0],
            target_unit: "fixture_dimensionless",
            data_quality: "fixture",
          },
          ...rows.slice(1),
        ],
        request,
        run,
      ),
    ).toThrow("разные единицы");
  });

  it.each(["training_cutoff", "weather_run_time", "forecast_available_at"])(
    "rejects future %s",
    async (field) => {
      const { run, rows } = await completed();
      rows[0][field] = "2026-02-01T01:00:00Z";
      expect(() => validateForecast(rows, request, run)).toThrow();
    },
  );

  it("requires numeric predictions, explicit timezones and a matching run/revision", async () => {
    const { run, rows } = await completed();
    for (const value of [Infinity, NaN, true, "0.5"])
      expect(() =>
        validateForecast(
          [{ ...rows[0], prediction: value as number }, ...rows.slice(1)],
          request,
          run,
        ),
      ).toThrow();
    expect(() =>
      validateForecast(
        [{ ...rows[0], valid_time: "2026-02-01T01:00:00" }, ...rows.slice(1)],
        request,
        run,
      ),
    ).toThrow("пояс");
    expect(() =>
      validateForecast(rows, request, { ...run, revision: 2 }),
    ).toThrow("Ревизия");
    expect(() =>
      validateForecast(rows, request, { ...run, run_id: "different" }),
    ).toThrow("другого запуска");
  });

  it("does not clip unusual but finite model values to a invented 0–1 boundary", async () => {
    const { run, rows } = await completed();
    rows[0].prediction = 1.2;
    expect(validateForecast(rows, request, run)[0].prediction).toBe(1.2);
  });

  it("rejects a non-hourly or naive request, duplicates and impossible calendar dates", () => {
    for (const issue_time of [
      "2026-02-01T00:01:00Z",
      "2026-02-01T00:00:00",
      "2026-02-30T00:00:00Z",
      "2026-02-01T00:00:00.0001Z",
    ])
      expect(() => validateRunRequest({ ...request, issue_time })).toThrow();
    expect(() => validateRunRequest({ ...request, turbine_ids: [] })).toThrow(
      "турбину",
    );
    expect(() =>
      validateRunRequest({ ...request, turbine_ids: ["x", "x"] }),
    ).toThrow("один раз");
    expect(
      validateRunRequest({
        ...request,
        issue_time: "2026-02-01T05:00:00+05:00",
      }).issue_time,
    ).toBe("2026-02-01T00:00:00.000Z");
  });
});

describe("CSV and provenance", () => {
  it("accepts quoted fields with commas, newlines, BOM and full precision", async () => {
    const { run, rows } = await completed();
    rows[0].provider = 'Example, with "quotes"\nand a newline';
    expect(validateCsv("\uFEFF" + csv(rows), rows, run)).toContain("newline");
  });

  it("rejects rounded values, stale revisions, changed models and removed synthetic labels", async () => {
    const { run, rows } = await completed();
    for (const changed of [
      { ...rows[0], prediction: rows[0].prediction + 0.001 },
      { ...rows[0], revision: 999 },
      { ...rows[0], model_version: "different-model" },
      { ...rows[0], source_mode: "live" },
    ])
      expect(() =>
        validateCsv(csv([changed, ...rows.slice(1)]), rows, run),
      ).toThrow("CSV");
    expect(() => validateCsv(csv(rows.slice(1)), rows, run)).toThrow("CSV");
    expect(() => validateCsv(csv([rows[0], ...rows]), rows, run)).toThrow(
      "CSV",
    );
    expect(() => validateCsv(csv(rows) + '\n"unclosed', rows, run)).toThrow(
      "CSV",
    );
  });

  it("retains backend fixture disclosure in CSV even without source_mode", async () => {
    const { run, rows } = await completed();
    const fixture = rows.map((row) => {
      const value = {
        ...row,
        target_unit: "fixture_dimensionless",
        data_quality: "fixture",
      };
      delete value.source_mode;
      return value;
    });
    expect(validateCsv(csv(fixture), fixture, run)).toContain(
      "fixture_dimensionless",
    );
    expect(() =>
      validateCsv(
        csv([{ ...fixture[0], data_quality: "good" }, ...fixture.slice(1)]),
        fixture,
        run,
      ),
    ).toThrow("CSV");
  });

  it("matches weather events by turbine, model and exact release time without filling explicit nulls", async () => {
    const { run, rows } = await completed();
    delete rows[0].provider;
    const metadata = {
      weather_model: rows[0].weather_model,
      model_run_time: rows[0].weather_run_time,
      provider: "archive-object",
      forecast_available_at: "2026-01-31T20:00:00Z",
      raw_sha256_example: "abc",
    };
    run.events = [
      {
        step: "fetching_weather",
        message: "Archive",
        details: { weather_provenance: { turbine_1: metadata } },
      },
      {
        step: "fetching_weather",
        message: "Wrong release",
        details: {
          weather_provenance: {
            turbine_1: {
              ...metadata,
              model_run_time: "2026-01-01T00:00:00Z",
              provider: "wrong",
            },
          },
        },
      },
    ];
    const result = weatherProvenance(run, rows);
    expect(result).toHaveLength(2);
    expect(result[0].provider).toBe("archive-object");
    expect(result[0].forecast_available_at).toBeNull();
    expect(result[0].raw_sha256).toBeNull();
    expect(result[1].provider).toBe("synthetic fixture");
  });

  it("converts display timezone while keeping a complete explicit date", () => {
    expect(formatTime(request.issue_time, "UTC")).toContain("00:00");
    expect(formatTime(request.issue_time, "Asia/Almaty")).toContain("05:00");
    expect(formatTime("2026-02-01T00:00:00", "UTC")).toBe("Некорректное время");
    expect(formatTime(null)).toBe("Не предоставлено");
  });
});

describe("API adapter without automatic demo fallback", () => {
  it("uses all six shared routes and passes the request body unchanged in meaning", async () => {
    const { run, rows } = await completed();
    const body = csv(rows);
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ status: "ok", is_demo: true }))
      .mockResolvedValueOnce(
        Response.json(
          { run_id: run.run_id, status: "queued" },
          { status: 202 },
        ),
      )
      .mockResolvedValueOnce(Response.json(run))
      .mockResolvedValueOnce(Response.json({ records: rows }))
      .mockResolvedValueOnce(
        new Response(body, { headers: { "Content-Type": "text/csv" } }),
      )
      .mockResolvedValueOnce(
        Response.json({ metrics: null, test_truth_available: false }),
      );
    vi.stubGlobal("fetch", fetcher);
    const client = new ApiClient();
    expect(await client.health()).toMatchObject({ status: "ok" });
    expect(await client.createRun(request)).toMatchObject({ status: "queued" });
    expect(await client.getRun(run.run_id)).toEqual(run);
    expect(await client.getForecast(run.run_id)).toEqual(rows);
    expect(await client.getForecastCsv(run.run_id)).toBe(body);
    expect(await client.getEvaluation()).toMatchObject({ metrics: null });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/health",
      "/api/runs",
      `/api/runs/${run.run_id}`,
      `/api/runs/${run.run_id}/forecast`,
      `/api/runs/${run.run_id}/forecast.csv`,
      "/api/evaluation",
    ]);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(
      validateRunRequest(request),
    );
  });

  it("rejects wrong run identities, malformed statuses and non-finite predictions", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ run_id: "other", status: "completed" }),
      )
      .mockResolvedValueOnce(
        Response.json({ run_id: "same", status: "unknown" }),
      )
      .mockResolvedValueOnce(Response.json([{ run_id: "other" }]))
      .mockResolvedValueOnce(new Response('{"status": NaN}'));
    vi.stubGlobal("fetch", fetcher);
    const client = new ApiClient("/api");
    await expect(client.getRun("same")).rejects.toThrow("другому расчёту");
    await expect(client.getRun("same")).rejects.toThrow("статус");
    await expect(client.getForecast("same")).rejects.toThrow("поля");
    await expect(client.health()).rejects.toThrow("JSON");
  });

  it("accepts backend revision zero before an artifact exists, including an initial failure", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const client = new ApiClient("/api");
    for (const status of [
      "queued",
      "fetching_weather",
      "validating",
      "forecasting",
      "analysing",
      "failed",
    ]) {
      fetcher.mockResolvedValueOnce(
        Response.json({ run_id: "new-run", status, revision: 0 }),
      );
      await expect(client.getRun("new-run")).resolves.toMatchObject({
        status,
        revision: 0,
      });
    }
    fetcher.mockResolvedValueOnce(
      Response.json({ run_id: "new-run", status: "completed", revision: 0 }),
    );
    await expect(client.getRun("new-run")).rejects.toThrow("ревизию");
    fetcher.mockResolvedValueOnce(
      Response.json({ run_id: "new-run", status: "completed", revision: 1 }),
    );
    await expect(client.getRun("new-run")).resolves.toMatchObject({
      status: "completed",
      revision: 1,
    });
    for (const revision of [-1, 0.5, "0"]) {
      fetcher.mockResolvedValueOnce(
        Response.json({ run_id: "new-run", status: "queued", revision }),
      );
      await expect(client.getRun("new-run")).rejects.toThrow("ревизию");
    }
  });

  it("exposes connection and HTTP errors instead of producing synthetic results", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("Network failed"))
      .mockResolvedValueOnce(
        Response.json(
          { detail: "Weather archive unavailable" },
          { status: 503 },
        ),
      );
    vi.stubGlobal("fetch", fetcher);
    const client = new ApiClient("/api");
    await expect(client.createRun(request)).rejects.toMatchObject({
      kind: "connection",
    });
    await expect(client.getRun("real-run")).rejects.toMatchObject({
      kind: "http",
      statusCode: 503,
      message: expect.stringContaining("Weather archive unavailable"),
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("bounds timeouts even when fetch ignores abort", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(
      (_url: RequestInfo | URL, _init?: RequestInit) =>
        new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", fetcher);
    const result = new ApiClient("/api", 100).health();
    const rejection = expect(result).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(101);
    await rejection;
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it("rejects oversized responses and JSON masquerading as a CSV download", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          new Response("{}", {
            headers: { "content-length": String(11 * 1024 * 1024) },
          }),
        )
        .mockResolvedValueOnce(Response.json({ error: "not a CSV" })),
    );
    const client = new ApiClient("/api");
    await expect(client.health()).rejects.toThrow("10 MiB");
    await expect(client.getForecastCsv("id")).rejects.toThrow("вместо CSV");
  });

  it("rejects unsafe or malformed endpoints before network access", () => {
    for (const base of [
      "ftp://example.test",
      "//example.test",
      "https://u:p@example.test",
      "https://example.test?x=1",
      "http://example.test#x",
      "javascript:alert(1)",
      "api",
      "http://localhost:0",
    ])
      expect(() => new ApiClient(base)).toThrow(ApiError);
    for (const timeout of [0, -1, Infinity, 60_001])
      expect(() => new ApiClient("/api", timeout)).toThrow(ApiError);
    expect(new ApiClient("https://example.test/prefix/").baseUrl).toBe(
      "https://example.test/prefix",
    );
  });
});
