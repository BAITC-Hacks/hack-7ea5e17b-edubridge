import { describe, expect, it } from "vitest";
import type { ForecastRecord, Run } from "./types";
import { validateCsv } from "./validation";

const run: Run = { run_id: "archive-run", revision: 1, status: "completed" };

function record(): ForecastRecord {
  return {
    schema_version: "1.0",
    run_id: run.run_id,
    revision: run.revision,
    issue_time: "2026-01-31T18:00:00Z",
    turbine_id: "turbine_1",
    valid_time: "2026-01-31T19:00:00Z",
    lead_hours: 1,
    prediction: 0.1234567890123456,
    target_unit: "normalized_power",
    weather_model: "gfs",
    weather_run_time: "2026-01-31T12:00:00Z",
    model_version: "wind-power-v1",
    training_cutoff: "2026-01-31T17:00:00Z",
    data_quality: "warning",
    warnings: ["Unconfirmed source timezone", "Normalization is unconfirmed"],
    forecast_available_at: "2026-01-31T15:40:00Z",
    provider: "NOAA archive",
    raw_sha256: "verified-object-sha256",
  };
}

function csv(rows: Record<string, unknown>[], fields = Object.keys(rows[0])) {
  const cell = (value: unknown) => {
    const text = value == null
      ? ""
      : typeof value === "object" ? JSON.stringify(value) : String(value);
    return '"' + text.replace(/"/g, '""') + '"';
  };
  return [
    fields.map(cell).join(","),
    ...rows.map((row) => fields.map((field) => cell(row[field])).join(",")),
  ].join("\r\n") + "\r\n";
}

describe("CSV preserves the displayed JSON contract", () => {
  it.each([
    ["removed warnings", { warnings: [] }],
    ["removed one warning", { warnings: ["Unconfirmed source timezone"] }],
    ["replaced warnings", { warnings: ["Everything is verified"] }],
    ["warning changed to ok", { data_quality: "ok" }],
    ["both disclosures removed", { data_quality: "ok", warnings: [] }],
    ["changed schema", { schema_version: "2.0" }],
    ["changed publication time", { forecast_available_at: "2026-01-31T16:00:00Z" }],
    ["changed provider", { provider: "actual weather" }],
    ["changed raw object", { raw_sha256: "different-object-sha256" }],
  ])("rejects %s even when predictions are unchanged", (_name, change) => {
    const source = record();
    expect(() => validateCsv(csv([{ ...source, ...change }]), [source], run))
      .toThrow("CSV");
  });

  it("rejects missing disclosure columns and unverified extra columns", () => {
    const source = record();
    for (const field of ["warnings", "data_quality", "schema_version", "raw_sha256"])
      expect(() => validateCsv(
        csv([source], Object.keys(source).filter((key) => key !== field)),
        [source],
        run,
      )).toThrow("CSV");
    expect(() => validateCsv(csv([{ ...source, verified: true }]), [source], run))
      .toThrow("CSV");
  });

  it("accepts equivalent JSON, timestamps and numeric notation in quoted CSV", () => {
    const source = record();
    source.data_quality = {
      status: "warning",
      detail: { reason: 'Comma, "quotes"\nand a newline', confirmed: false },
    };
    source.warnings = ['Comma, "quotes"\nand a newline', "Source timezone"];
    const equivalent = {
      ...source,
      issue_time: "2026-01-31T23:00:00+05:00",
      valid_time: "2026-02-01T00:00:00+05:00",
      weather_run_time: "2026-01-31T17:00:00+05:00",
      training_cutoff: "2026-01-31T22:00:00+05:00",
      forecast_available_at: "2026-01-31T20:40:00+05:00",
      prediction: source.prediction.toExponential(),
      revision: "1.0",
      lead_hours: "1e0",
      data_quality: JSON.stringify({
        detail: { confirmed: false, reason: 'Comma, "quotes"\nand a newline' },
        status: "warning",
      }, null, 2),
      warnings: JSON.stringify(source.warnings, null, 2),
    };
    const content = "\uFEFF" + csv([equivalent], Object.keys(source).reverse());
    expect(validateCsv(content, [source], run)).toBe(content);
  });

  it("rejects changes inside structured quality disclosures and malformed JSON", () => {
    const source = record();
    source.data_quality = { status: "warning", detail: { confirmed: false } };
    for (const data_quality of [
      { status: "warning", detail: { confirmed: true } },
      { status: "warning" },
      { status: "warning", detail: { confirmed: "false" } },
      "{malformed}",
      "[]",
      "null",
    ])
      expect(() => validateCsv(csv([{ ...source, data_quality }]), [source], run))
        .toThrow("CSV");
  });

  it("keeps null, empty strings and empty warning arrays distinct", () => {
    const source = record();
    source.training_cutoff = null;
    source.raw_sha256 = null;
    source.provider = "";
    source.warnings = [];
    expect(validateCsv(csv([source]), [source], run)).toContain("[]");
    expect(() => validateCsv(
      csv([{ ...source, training_cutoff: "null", raw_sha256: "null" }]),
      [source],
      run,
    )).not.toThrow();
    for (const change of [
      { warnings: "" },
      { warnings: "null" },
      { warnings: {} },
      { provider: "null" },
      { raw_sha256: "fabricated-hash" },
      { training_cutoff: "2026-01-31T17:00:00Z" },
    ])
      expect(() => validateCsv(csv([{ ...source, ...change }]), [source], run))
        .toThrow("CSV");
  });

  it("checks optional fields for each turbine instead of reusing the first row", () => {
    const first = record();
    const second = { ...record(), turbine_id: "turbine_2", provider: "Second source" };
    delete first.provider;
    const fields = [...new Set([...Object.keys(first), ...Object.keys(second)])];
    expect(() => validateCsv(csv([second, first], fields), [first, second], run))
      .not.toThrow();
    expect(() => validateCsv(
      csv([{ ...second, provider: "NOAA archive" }, first], fields),
      [first, second],
      run,
    )).toThrow("CSV");
  });

  it("does not silently allow even small prediction changes", () => {
    const source = record();
    expect(() => validateCsv(
      csv([{ ...source, prediction: source.prediction + 1e-10 }]),
      [source],
      run,
    )).toThrow("CSV");
  });
});
