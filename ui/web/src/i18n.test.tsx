// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { DemoClient } from "./data";
import { I18nProvider, LOCALE_STORAGE_KEY, messages, translate, translateMessage, useI18n } from "./i18n";
import type { Locale } from "./i18n";

vi.mock("./ForecastChart", () => ({ default: () => <div data-testid="forecast-chart" /> }));

function Probe() {
  const { locale, setLocale, number, time } = useI18n();
  return <>
    <select aria-label="locale" value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
      <option value="ru">ru</option><option value="kk">kk</option><option value="en">en</option>
    </select>
    <output data-testid="number">{number(0.125)}</output>
    <output data-testid="time">{time("2026-01-31T18:00:00Z", "Asia/Almaty")}</output>
  </>;
}

beforeEach(() => {
  localStorage.clear();
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: vi.fn() });
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: vi.fn() });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

describe("language selection", () => {
  it("persists the language across mounts and updates document metadata", () => {
    const view = render(<I18nProvider><Probe /></I18nProvider>);
    fireEvent.change(screen.getByLabelText("locale"), { target: { value: "kk" } });
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("kk");
    expect(document.documentElement.lang).toBe("kk");
    expect(document.title).toContain("жел энергиясы");
    view.unmount();
    render(<I18nProvider><Probe /></I18nProvider>);
    expect((screen.getByLabelText("locale") as HTMLSelectElement).value).toBe("kk");
  });

  it("uses Russian for an unsupported saved locale", () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, "unsupported");
    render(<I18nProvider><Probe /></I18nProvider>);
    expect(document.documentElement.lang).toBe("ru");
  });

  it("still switches if browser storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<I18nProvider><Probe /></I18nProvider>);
    fireEvent.change(screen.getByLabelText("locale"), { target: { value: "en" } });
    expect(document.documentElement.lang).toBe("en");
    expect(screen.getByTestId("number").textContent).toBe("0.125");
  });

  it("formats dates and numbers by language while retaining the requested timezone", () => {
    render(<I18nProvider><Probe /></I18nProvider>);
    expect(screen.getByTestId("number").textContent).toBe("0,125");
    fireEvent.change(screen.getByLabelText("locale"), { target: { value: "en" } });
    expect(screen.getByTestId("number").textContent).toBe("0.125");
    expect(screen.getByTestId("time").textContent).toContain("23:00");
    expect(screen.getByTestId("time").textContent).toContain("31/01/2026");
  });

  it("changes the complete dashboard language without recalculating or losing its result", async () => {
    const createRun = vi.spyOn(DemoClient.prototype, "createRun");
    render(<I18nProvider><App /></I18nProvider>);
    await screen.findByRole("heading", { name: "Расчёт завершён" });
    const initialCalls = createRun.mock.calls.length;
    for (const locale of ["kk", "en", "ru"] as const) {
      const selector = screen.getByRole("combobox", { name: translate("Язык интерфейса", document.documentElement.lang as Locale) });
      fireEvent.change(selector, { target: { value: locale } });
      await waitFor(() => expect(document.documentElement.lang).toBe(locale));
      expect(screen.getByRole("button", { name: translate("Рассчитать прогноз", locale) })).toBeTruthy();
      expect(screen.getByRole("heading", { name: translate("Расчёт завершён", locale) })).toBeTruthy();
      expect(screen.getByRole("button", { name: translate("Скачать CSV", locale) })).toBeTruthy();
      expect(screen.getAllByRole("row")).toHaveLength(97);
      expect(createRun.mock.calls.length).toBe(initialCalls);
    }
  });
});

describe("translated runtime messages", () => {
  it("translates errors and retains interpolated diagnostics", () => {
    expect(translateMessage("Горизонт должен составлять 24 или 48 часов.", "en")).not.toMatch(/[А-Яа-я]/);
    const error = translateMessage("API вернул ошибку HTTP 503. Service unavailable", "kk");
    expect(error).toContain("503");
    expect(error).toContain("Service unavailable");
    expect(error).not.toContain("вернул ошибку");
    expect(translateMessage("custom_backend_code: detail", "kk")).toBe("custom_backend_code: detail");
  });

  it("provides nonempty Kazakh and English entries with matching interpolation parameters", () => {
    const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
    for (const [source, entry] of Object.entries(messages)) {
      for (const locale of ["kk", "en"] as const) {
        expect(entry[locale].trim(), `${locale}: ${source}`).not.toBe("");
        expect(placeholders(entry[locale]), `${locale}: ${source}`).toEqual(placeholders(source));
      }
    }
  });
});
