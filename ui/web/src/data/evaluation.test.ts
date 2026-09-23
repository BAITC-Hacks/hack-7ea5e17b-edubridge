import { afterEach, describe, expect, it, vi } from "vitest";
import readyJson from "./__fixtures__/evaluation-ready.json";
import { ApiClient } from "./client";
import { validateEvaluation } from "./evaluation";
import type { Evaluation, ForecastRecord } from "./types";

// Captured from evaluation_report(load_settings('config/archive-model.json'))
// at main 6e4724e; values come from the bound wind-power-v1 package, not a UI estimate.
const ready = () => structuredClone(readyJson) as Evaluation;
const forecast: ForecastRecord = {
  run_id: "archive-example",
  revision: 1,
  issue_time: "2026-01-31T18:00:00Z",
  turbine_id: "turbine_1",
  valid_time: "2026-01-31T19:00:00Z",
  lead_hours: 1,
  prediction: 0.4,
  target_unit: "normalized_power",
  model_version: "wind-power-v1",
  training_cutoff: "2026-01-31T18:00:00Z",
  data_quality: "warning",
};

afterEach(() => vi.unstubAllGlobals());

describe("latest evaluation API contract", () => {
  it("preserves actual tuning, holdout, baseline and production scopes with different cutoffs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(readyJson)));
    const report = validateEvaluation(await new ApiClient().getEvaluation(), [
      forecast,
    ]);
    expect(report.status).toBe("ready");
    expect(report.metrics!.tuning.scores.equal_turbine_mean_mae).toBeCloseTo(
      0.232464,
      6,
    );
    expect(
      report.metrics!.independent_holdout.scores.equal_turbine_mean_mae,
    ).toBeCloseTo(0.226919, 6);
    expect(report.baseline!.comparison.wind_curve_mae).toBeCloseTo(0.20946, 6);
    expect(report.baseline!.comparison.wind_curve_better).toBe(true);
    expect(report.metrics!.independent_holdout.training_cutoff).not.toBe(
      report.training_cutoff,
    );
    expect(report.metrics!.production_refit.independent_metrics).toBeNull();
    expect(report.test_truth_available).toBe(false);
    expect(report.periods!.history_timezone).toBe("Etc/GMT-5");
    expect(report.provenance!.source_files_rechecked).toBe(false);
  });

  it("rejects a report for another configured package without comparing holdout cutoff to production rows", () => {
    expect(validateEvaluation(ready(), [forecast]).status).toBe("ready");
    expect(() =>
      validateEvaluation(ready(), [
        { ...forecast, model_version: "other-version" },
      ]),
    ).toThrow("другому production-пакету");
    expect(() =>
      validateEvaluation(ready(), [
        { ...forecast, training_cutoff: "2026-01-14T19:00:00Z" },
      ]),
    ).toThrow("другому production-пакету");
  });

  it("rejects fabricated units, nonfinite metrics and an inconsistent baseline claim", () => {
    const unit = ready();
    unit.target_unit = "MW";
    expect(() => validateEvaluation(unit)).toThrow("единицы");
    const nonfinite = ready();
    nonfinite.metrics!.independent_holdout.scores.overall.rmse = Infinity;
    expect(() => validateEvaluation(nonfinite)).toThrow("MAE/RMSE");
    const comparison = ready();
    comparison.baseline!.comparison.wind_curve_better = false;
    expect(() => validateEvaluation(comparison)).toThrow("baseline");
  });

  it("requires explicit period and scope instead of treating production as independently evaluated", () => {
    const missingPeriod = ready();
    missingPeriod.periods = null;
    expect(() => validateEvaluation(missingPeriod)).toThrow("период");
    const production = ready();
    production.metrics!.production_refit.independent_metrics = {
      mae: 0.226919,
    };
    expect(() => validateEvaluation(production)).toThrow("production refit");
    const selectedFromHoldout = ready();
    selectedFromHoldout.metrics!.independent_holdout.used_for_selection = true;
    expect(() => validateEvaluation(selectedFromHoldout)).toThrow(
      "независимую оценку",
    );
  });

  it("requires a valid timezone-aware selection boundary no later than the holdout model cutoff", () => {
    for (const selection_cutoff of [
      "invalid-date",
      "2026-02-30T00:00:00Z",
      "2026-01-14T19:00:00",
      "2026-02-01T00:00:00Z",
    ]) {
      const report = ready();
      report.metrics!.tuning.selection_cutoff = selection_cutoff;
      expect(() => validateEvaluation(report)).toThrow();
    }
    const report = ready();
    report.metrics!.tuning.selection_cutoff =
      report.metrics!.independent_holdout.training_cutoff;
    expect(validateEvaluation(report).status).toBe("ready");
  });

  it("preserves backend invalid/unavailable reasons without replacing null scores with zero", () => {
    for (const status of ["invalid", "unavailable"]) {
      const report: Evaluation = {
        status,
        reason: "Model weight SHA256 differs from package metadata",
        metrics: null,
        baseline: null,
        test_truth_available: false,
      };
      expect(validateEvaluation(report, [forecast])).toEqual(report);
      expect(validateEvaluation(report).metrics).toBeNull();
    }
  });

  it("rejects malformed display text before it can become an invalid React child", () => {
    for (const warnings of ["not-an-array", [{ message: "nested object" }]]) {
      const report = { ...ready(), warnings } as unknown as Evaluation;
      expect(() => validateEvaluation(report)).toThrow("Предупреждения");
    }
    const candidate = ready();
    candidate.metrics!.tuning.selected_candidate = {
      name: "nested",
    } as unknown as string;
    expect(() => validateEvaluation(candidate)).toThrow("кандидат");
    const invalid = {
      status: "invalid",
      reason: { detail: "nested object" },
    } as unknown as Evaluation;
    expect(() => validateEvaluation(invalid)).toThrow("текстом");
  });
});
