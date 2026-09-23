// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import EvaluationPanel from "./EvaluationPanel";
import { I18nProvider, LOCALE_STORAGE_KEY } from "./i18n";
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
beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  localStorage.clear();
});

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

  it.each([
    {
      locale: "kk",
      heading: "Модель сапасы",
      baseline: "Holdout кезеңінде қарапайым базалық модель таңдалған модельден жақсы",
      separator: ",",
      scope: "Соңғы production refit моделінің тәуелсіз бағалауы берілмеген.",
      february: "Бұл метрикалар ақпандағы дәлдікті көрсетпейді.",
      units: "MAE/RMSE дәлдік пайызын немесе MW/MWh шамасын білдірмейді.",
    },
    {
      locale: "en",
      heading: "Model quality",
      baseline: "The simple baseline outperforms the selected model on holdout",
      separator: ".",
      scope: "No independent evaluation of the final production refit is available.",
      february: "These metrics do not measure February accuracy.",
      units: "MAE/RMSE do not represent percentage accuracy or MW/MWh.",
    },
  ])("localizes a real evaluation in $locale without changing its scientific claims", ({
    locale, heading, baseline, separator, scope, february, units,
  }) => {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    const { container } = render(
      <I18nProvider>
        <EvaluationPanel
          result={result(readyJson as Evaluation)}
          synthetic={false}
        />
      </I18nProvider>,
    );
    expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
    expect(screen.getByText(baseline)).toBeTruthy();
    for (const fraction of ["232464", "226919", "209460"]) {
      expect(screen.getByText(`0${separator}${fraction}`)).toBeTruthy();
    }
    // The raw JSON report intentionally retains the backend's identifiers and text.
    const interfaceContent = container.cloneNode(true) as HTMLElement;
    interfaceContent.querySelectorAll("pre").forEach((node) => node.remove());
    const interfaceText = interfaceContent.textContent || "";
    expect(interfaceText).toContain(scope);
    expect(interfaceText).toContain(february);
    expect(interfaceText).toContain(units);
    expect(interfaceText).toContain("normalized_power");
    expect(interfaceText).not.toMatch(
      /Качество модели|На holdout простой baseline|Независимая оценка|Метрики недоступны|Фактическая выработка|Полный отчёт|Группа|Прогнозов/,
    );
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
      screen.getByText("SHA256 весов модели не совпадает с метаданными пакета"),
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
