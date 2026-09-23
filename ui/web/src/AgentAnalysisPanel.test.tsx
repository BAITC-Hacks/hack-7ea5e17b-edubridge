// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import AgentAnalysisPanel from "./AgentAnalysisPanel";
import type { Run } from "./data";

const baseRun: Run = { run_id: "analysis-run", status: "completed", revision: 2 };
function report() {
  return {
    run_id: baseRun.run_id,
    revision: baseRun.revision,
    decision: "review_required",
    next_action: "review_inputs",
    is_demo: true,
    reasons: ["SYNTHETIC FIXTURE", "Check the input source"],
    per_turbine: {
      turbine_1: {
        target_unit: "fixture_dimensionless",
        prediction_min: 0.1,
        prediction_max: 0.7,
        prediction_mean: 0.4,
        largest_hourly_ramp: { absolute_delta: 0.2, to_valid_time: "2026-02-01T00:00:00Z" },
        flat_forecast: false,
        weather_wind_speed_ms: { min: 2, max: 12, mean: 7 },
      },
    },
    previous_comparison: {
      status: "compared",
      common_hours: 24,
      changed_count: 3,
      per_turbine: {
        turbine_1: { status: "compared", mean_absolute_delta: 0.02, max_absolute_delta: 0.08 },
      },
    },
  };
}
const event = (analysis: unknown) => ({ step: "analysing", message: "Analysis", details: { analysis } });
afterEach(cleanup);

describe("agent diagnostic display", () => {
  it("labels fixture units, keeps the advisory limits and shows actual report values", () => {
    render(<AgentAnalysisPanel run={{ ...baseRun, events: [event(report())] }} zone="Asia/Almaty" />);
    expect(screen.getByRole("heading", { name: "Анализ агента" })).toBeTruthy();
    expect(screen.getByText("Нужна проверка входов")).toBeTruthy();
    expect(screen.getByText(/Синтетический fixture: анализ не описывает реальный/)).toBeTruthy();
    expect(screen.getByText(/не оценка точности/)).toBeTruthy();
    expect(screen.getByText("Check the input source")).toBeTruthy();
    const turbine = within(screen.getByRole("region", { name: "Анализ: Турбина 01" }));
    expect(turbine.getByText("Демо-значение · fixture_dimensionless")).toBeTruthy();
    expect(turbine.getByText("0,1 / 0,7")).toBeTruthy();
    expect(turbine.getByText("0,4")).toBeTruthy();
    expect(turbine.getByText("0,2")).toBeTruthy();
    expect(turbine.getByText(/05:00/)).toBeTruthy();
    expect(turbine.getByText("2 / 12")).toBeTruthy();
    expect(turbine.getByText("0,02 / 0,08 · fixture_dimensionless")).toBeTruthy();
    expect(screen.getByText(/общих часов 24, изменено 3/)).toBeTruthy();
    expect(screen.getByText(/не ошибка относительно факта/)).toBeTruthy();
  });

  it("does not invent an analysis for old backends, unfinished runs or invalid reports", () => {
    const { container, rerender } = render(<AgentAnalysisPanel run={baseRun} />);
    expect(container.textContent).toBe("");
    for (const run of [
      { ...baseRun, events: [{ step: "completed", message: "Old backend" }] },
      { ...baseRun, status: "analysing" as const, events: [event(report())] },
      { ...baseRun, events: [event({ ...report(), per_turbine: null })] },
      { ...baseRun, events: [event({ ...report(), revision: 1 })] },
    ]) {
      rerender(<AgentAnalysisPanel run={run} />);
      expect(container.textContent).toBe("");
    }
  });

  it("uses the latest matching run revision and ignores stale or foreign diagnostics", () => {
    const current = {
      ...report(),
      decision: "monitor_updates",
      next_action: "monitor_updates",
      reasons: ["Current revision"],
      is_demo: false,
      per_turbine: {
        turbine_1: { ...report().per_turbine.turbine_1, target_unit: "normalized_power" },
      },
      previous_comparison: { status: "unavailable" },
    };
    render(<AgentAnalysisPanel run={{
      ...baseRun,
      events: [
        event({ ...current, reasons: ["Earlier event"] }),
        event(current),
        event({ ...current, revision: 1, reasons: ["Stale revision"] }),
        event({ ...current, run_id: "another-run", reasons: ["Foreign run"] }),
      ],
    }} />);
    expect(screen.getByText("Наблюдать обновления")).toBeTruthy();
    expect(screen.getByText("Current revision")).toBeTruthy();
    expect(screen.getByText("Нормализованная мощность · normalized_power")).toBeTruthy();
    for (const text of ["Earlier event", "Stale revision", "Foreign run", "Синтетический fixture"])
      expect(screen.queryByText(text)).toBeNull();
    expect(screen.getByText(/Сравнение с предыдущей ревизией недоступно/)).toBeTruthy();
  });
});
