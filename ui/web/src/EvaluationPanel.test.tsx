// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import EvaluationPanel from "./EvaluationPanel";
import readyJson from "./data/__fixtures__/evaluation-ready.json";
import type { Evaluation } from "./data";
import type { Result } from "./useForecast";

function result(evaluation: Evaluation): Result {
  return {
    run: { run_id: "archive-run", status: "completed", revision: 1 },
    request: {
      issue_time: "2026-01-31T18:00:00Z",
      horizon_hours: 48,
      turbine_ids: ["turbine_1", "turbine_2"],
    },
    rows: [],
    csv: null,
    csvError: null,
    evaluation,
    evaluationError: null,
  };
}
afterEach(cleanup);

describe("model evaluation display", () => {
  it("shows the real baseline advantage and distinguishes production from independent holdout", () => {
    render(
      <EvaluationPanel
        result={result(readyJson as Evaluation)}
        synthetic={false}
      />,
    );
    expect(
      screen.getByText("На holdout простой baseline лучше выбранной модели"),
    ).toBeTruthy();
    expect(screen.getByText("0,232464")).toBeTruthy();
    expect(screen.getByText("0,226919")).toBeTruthy();
    expect(screen.getByText("0,209460")).toBeTruthy();
    expect(
      screen.getByText(
        /Независимая оценка итогового production refit не предоставлена/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/Эти метрики не являются февральской точностью/),
    ).toBeTruthy();
    expect(screen.getByText("Etc/GMT-5")).toBeTruthy();
    expect(screen.getByText("Полный отчёт и контрольные суммы")).toBeTruthy();
  });

  it("shows a refused report reason and never renders metric cards for invalid status", () => {
    render(
      <EvaluationPanel
        result={result({
          status: "invalid",
          reason: "Model weight SHA256 differs from package metadata",
          metrics: null,
          baseline: null,
        })}
        synthetic={false}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Отчёт оценки не прошёл проверку" }),
    ).toBeTruthy();
    expect(
      screen.getByText("Model weight SHA256 differs from package metadata"),
    ).toBeTruthy();
    expect(screen.queryByText("НЕЗАВИСИМЫЙ HOLDOUT")).toBeNull();
  });

  it("keeps demo separate even if a real report is accidentally supplied", () => {
    const { container } = render(
      <EvaluationPanel result={result(readyJson as Evaluation)} synthetic />,
    );
    expect(
      screen.getByRole("heading", { name: "Демо показывает интерфейс" }),
    ).toBeTruthy();
    expect(within(container).queryByText("0,226919")).toBeNull();
    expect(container.querySelector(".evaluation-cards")).toBeNull();
  });
});
