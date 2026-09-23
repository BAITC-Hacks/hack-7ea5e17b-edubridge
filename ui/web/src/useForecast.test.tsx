// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, DemoClient } from "./data";
import type { Evaluation, ForecastRecord, Run, RunRequest } from "./data";
import { useForecast } from "./useForecast";
import evaluationReadyJson from "./data/__fixtures__/evaluation-ready.json";
import type { Mode } from "./useForecast";

const request: RunRequest = {
  issue_time: "2026-02-01T00:00:00Z",
  horizon_hours: 24,
  turbine_ids: ["turbine_1"],
};
const evaluation: Evaluation = {
  status: "unavailable",
  metrics: null,
  baseline: null,
  test_truth_available: false,
};

async function fixture() {
  const demo = new DemoClient();
  let run = await demo.createRun(request);
  for (let index = 0; index < 5; index++) run = await demo.getRun(run.run_id);
  return {
    run,
    rows: await demo.getForecast(run.run_id),
    csv: await demo.getForecastCsv(run.run_id),
  };
}

function serialize(rows: ForecastRecord[]): string {
  const fields = Object.keys(rows[0]);
  const quote = (value: unknown) =>
    '"' +
    (value == null
      ? ""
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value)
    ).replace(/"/g, '""') +
    '"';
  return [
    fields.join(","),
    ...rows.map((row) => fields.map((field) => quote(row[field])).join(",")),
  ].join("\n");
}

function mockCompleted(value: Awaited<ReturnType<typeof fixture>>) {
  return {
    create: vi
      .spyOn(ApiClient.prototype, "createRun")
      .mockResolvedValue(value.run),
    getRun: vi
      .spyOn(ApiClient.prototype, "getRun")
      .mockResolvedValue(value.run),
    getForecast: vi
      .spyOn(ApiClient.prototype, "getForecast")
      .mockResolvedValue(value.rows),
    getCsv: vi
      .spyOn(ApiClient.prototype, "getForecastCsv")
      .mockResolvedValue(value.csv),
    getEvaluation: vi
      .spyOn(ApiClient.prototype, "getEvaluation")
      .mockResolvedValue(evaluation),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("forecast state and source transitions", () => {
  it("keeps forecast artifacts when the global evaluation belongs to another model package", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    mocks.getEvaluation.mockResolvedValue(evaluationReadyJson as Evaluation);
    const { result } = renderHook(() => useForecast("api", "/api"));
    await act(async () => {
      await result.current.start(request);
    });
    expect(result.current.result?.rows).toHaveLength(24);
    expect(result.current.result?.csv).toBe(value.csv);
    expect(result.current.result?.evaluation).toBeNull();
    expect(result.current.result?.evaluationError).toContain(
      "другому production-пакету",
    );
    expect(result.current.error).toBeNull();
  });
  it("clears demo artifacts and history when API is selected; an API failure never restores demo", async () => {
    const create = vi
      .spyOn(ApiClient.prototype, "createRun")
      .mockRejectedValue(new Error("API недоступен"));
    const { result, rerender } = renderHook(
      ({ mode }) => useForecast(mode, "/api"),
      { initialProps: { mode: "demo" as Mode } },
    );
    await waitFor(() =>
      expect(result.current.result?.run.status).toBe("completed"),
    );
    expect(result.current.result?.rows).toHaveLength(96);
    expect(result.current.history).toHaveLength(1);
    rerender({ mode: "api" });
    expect(result.current.result).toBeNull();
    expect(result.current.history).toEqual([]);
    await act(async () => {
      await result.current.start(request);
    });
    expect(result.current.error).toBe("API недоступен");
    expect(result.current.result).toBeNull();
    expect(result.current.history).toEqual([]);
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("invalid endpoint construction drops the previous client even after repeated attempts", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const { result, rerender } = renderHook(
      ({ mode, base }) => useForecast(mode, base),
      { initialProps: { mode: "demo" as Mode, base: "/api" } },
    );
    await waitFor(() =>
      expect(result.current.result?.run.status).toBe("completed"),
    );
    rerender({ mode: "api", base: "javascript:alert(1)" });
    expect(result.current.error).toContain("Адрес API");
    await act(async () => {
      await result.current.start(request);
      await result.current.start(request);
    });
    expect(result.current.result).toBeNull();
    expect(result.current.error).toContain("адрес API");
    expect(result.current.busy).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("polls each active state and fetches the hourly artifacts only after completion", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    mocks.create.mockResolvedValue({
      run_id: value.run.run_id,
      status: "queued",
    });
    const states = [
      "fetching_weather",
      "validating",
      "forecasting",
      "analysing",
      "completed",
    ] as const;
    let poll = 0;
    mocks.getRun.mockImplementation(async () => ({
      ...value.run,
      status: states[Math.min(poll++, states.length - 1)],
    }));
    vi.useFakeTimers();
    const { result } = renderHook(() => useForecast("api", "/api"));
    await act(async () => {
      await result.current.start(request);
    });
    expect(result.current.result?.run.status).toBe("fetching_weather");
    expect(result.current.result?.rows).toEqual([]);
    expect(mocks.getForecast).not.toHaveBeenCalled();
    for (const status of states.slice(1)) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
      });
      expect(result.current.result?.run.status).toBe(status);
    }
    expect(result.current.result?.rows).toHaveLength(24);
    expect(mocks.getForecast).toHaveBeenCalledTimes(1);
    expect(mocks.getRun).toHaveBeenCalledTimes(5);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(mocks.getRun).toHaveBeenCalledTimes(5);
  });

  it("drops stale artifacts after a failed refresh, including the history entry that could restore them", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    const { result } = renderHook(() => useForecast("api", "/api"));
    await act(async () => {
      await result.current.start(request);
    });
    expect(result.current.result?.csv).toBe(value.csv);
    mocks.getRun.mockRejectedValueOnce(new Error("Соединение прервано"));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.error).toBe("Соединение прервано");
    expect(result.current.result).toMatchObject({
      rows: [],
      csv: null,
      evaluation: null,
    });
    expect(
      result.current.history.some(
        (item) => item.run.run_id === value.run.run_id,
      ),
    ).toBe(false);
    act(() => {
      result.current.selectRun(value.run.run_id);
    });
    expect(result.current.result?.csv).toBeNull();
    expect(result.current.error).toBe("Соединение прервано");
  });

  it("ignores a pending request from the old mode after the user switches to demo", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    let resolveOld!: (run: Run) => void;
    mocks.create.mockReturnValue(
      new Promise<Run>((resolve) => {
        resolveOld = resolve;
      }),
    );
    const { result, rerender } = renderHook(
      ({ mode }) => useForecast(mode, "/api"),
      { initialProps: { mode: "api" as Mode } },
    );
    let oldStart!: Promise<void>;
    act(() => {
      oldStart = result.current.start(request);
    });
    expect(result.current.busy).toBe(true);
    rerender({ mode: "demo" });
    await waitFor(() =>
      expect(result.current.result?.request.horizon_hours).toBe(48),
    );
    const currentCsv = result.current.result?.csv;
    await act(async () => {
      resolveOld(value.run);
      await oldStart;
    });
    expect(result.current.result?.request.horizon_hours).toBe(48);
    expect(result.current.result?.rows).toHaveLength(96);
    expect(result.current.result?.csv).toBe(currentCsv);
    expect(result.current.history).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it("fetches new rows and CSV for the same run ID when its revision changes", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    const { result } = renderHook(() => useForecast("api", "/api"));
    await act(async () => {
      await result.current.start(request);
    });
    const nextRun = { ...value.run, revision: 2 };
    const nextRows = value.rows.map((row) => ({
      ...row,
      revision: 2,
      prediction: row.prediction + 0.01,
    }));
    const nextCsv = serialize(nextRows);
    mocks.getRun.mockResolvedValue(nextRun);
    mocks.getForecast.mockResolvedValue(nextRows);
    mocks.getCsv.mockResolvedValue(nextCsv);
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.result?.run.revision).toBe(2);
    expect(result.current.result?.rows).toEqual(nextRows);
    expect(result.current.result?.csv).toBe(nextCsv);
    expect(result.current.history).toHaveLength(1);
    expect(result.current.history[0].run.revision).toBe(2);
    expect(mocks.getCsv).toHaveBeenCalledTimes(2);
  });

  it("does not reuse a previous CSV when the current download disagrees with valid JSON rows", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    const { result } = renderHook(() => useForecast("api", "/api"));
    await act(async () => {
      await result.current.start(request);
    });
    mocks.getCsv.mockResolvedValue(
      serialize([
        { ...value.rows[0], prediction: value.rows[0].prediction + 0.1 },
        ...value.rows.slice(1),
      ]),
    );
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.result?.rows).toHaveLength(24);
    expect(result.current.result?.csv).toBeNull();
    expect(result.current.result?.csvError).toContain("CSV");
    expect(result.current.error).toBeNull();
    expect(result.current.history[0].csv).toBeNull();
  });

  it("invalidates an idempotent run in history before loading a new POST result", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    const { result } = renderHook(() => useForecast("api", "/api"));
    await act(async () => {
      await result.current.start(request);
    });
    expect(result.current.history).toHaveLength(1);
    mocks.getRun.mockRejectedValueOnce(
      new Error("Повторный расчёт недоступен"),
    );
    await act(async () => {
      await result.current.start(request);
    });
    expect(result.current.error).toBe("Повторный расчёт недоступен");
    expect(
      result.current.history.some(
        (item) => item.run.run_id === value.run.run_id,
      ),
    ).toBe(false);
    act(() => {
      result.current.selectRun(value.run.run_id);
    });
    expect(result.current.result).toMatchObject({
      run: { run_id: value.run.run_id },
      request,
      rows: [],
      csv: null,
      evaluation: null,
    });
    expect(result.current.error).toBe("Повторный расчёт недоступен");
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.result?.run.run_id).toBe(value.run.run_id);
    expect(result.current.result?.rows).toHaveLength(24);
    expect(result.current.result?.csv).toBe(value.csv);
    expect(result.current.error).toBeNull();
  });

  it("removes the old completion from history when the same revision becomes active again", async () => {
    const value = await fixture();
    const mocks = mockCompleted(value);
    const { result } = renderHook(() => useForecast("api", "/api"));
    await act(async () => {
      await result.current.start(request);
    });
    mocks.getRun.mockResolvedValue({
      ...value.run,
      status: "fetching_weather",
    });
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.result).toMatchObject({
      run: { status: "fetching_weather" },
      rows: [],
      csv: null,
      evaluation: null,
    });
    expect(
      result.current.history.some(
        (item) => item.run.run_id === value.run.run_id,
      ),
    ).toBe(false);
    act(() => {
      result.current.selectRun(value.run.run_id);
    });
    expect(result.current.result?.run.status).toBe("fetching_weather");
    expect(result.current.result?.csv).toBeNull();
  });
});
