import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiClient,
  DemoClient,
  validateForecast,
  validateCsv,
  validateEvaluation,
} from "./data";
import type {
  ForecastClient,
  ForecastRecord,
  Run,
  RunRequest,
  Health,
  Evaluation,
} from "./data";

export type Mode = "demo" | "api";
export type Result = {
  run: Run;
  request: RunRequest;
  rows: ForecastRecord[];
  csv: string | null;
  evaluation: Evaluation | null;
  csvError: string | null;
  evaluationError: string | null;
};
export const ACTIVE = new Set([
  "queued",
  "fetching_weather",
  "validating",
  "forecasting",
  "analysing",
]);
const message = (error: unknown) =>
  error instanceof Error ? error.message : "Не удалось получить результат.";
const initialRequest: RunRequest = {
  issue_time: "2026-02-01T00:00:00Z",
  horizon_hours: 48,
  turbine_ids: ["turbine_1", "turbine_2"],
};

export function useForecast(mode: Mode, baseUrl: string) {
  const [result, setResult] = useState<Result | null>(null);
  const [history, setHistory] = useState<Result[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [healthBusy, setHealthBusy] = useState(false);
  const client = useRef<ForecastClient | null>(null);
  const epoch = useRef(0);
  const operation = useRef(0);

  const save = useCallback((value: Result) => {
    setResult(value);
    setHistory((previous) =>
      value.run.status === "completed"
        ? [
            value,
            ...previous.filter((item) => item.run.run_id !== value.run.run_id),
          ].slice(0, 15)
        : previous.filter((item) => item.run.run_id !== value.run.run_id),
    );
  }, []);

  const load = useCallback(
    async (
      api: ForecastClient,
      id: string,
      request: RunRequest,
    ): Promise<Result> => {
      const run = await api.getRun(id);
      const next: Result = {
        run,
        request,
        rows: [],
        csv: null,
        evaluation: null,
        csvError: null,
        evaluationError: null,
      };
      if (run.status === "completed") {
        next.rows = validateForecast(await api.getForecast(id), request, run);
        const [csv, evaluation] = await Promise.allSettled([
          api.getForecastCsv(id),
          api.getEvaluation(),
        ]);
        try {
          if (csv.status === "rejected") throw csv.reason;
          next.csv = validateCsv(csv.value, next.rows, run);
        } catch (reason) {
          next.csvError = message(reason);
        }
        try {
          if (evaluation.status === "rejected") throw evaluation.reason;
          next.evaluation = validateEvaluation(evaluation.value, next.rows);
        } catch (reason) {
          next.evaluationError = message(reason);
        }
      }
      return next;
    },
    [],
  );

  useEffect(() => {
    const current = ++epoch.current;
    operation.current++;
    setResult(null);
    setHistory([]);
    setError(null);
    setHealth(null);
    setHealthError(null);
    setHealthBusy(false);
    setBusy(mode === "demo");
    try {
      client.current =
        mode === "demo" ? new DemoClient() : new ApiClient(baseUrl);
    } catch (reason) {
      client.current = null;
      setError(message(reason));
      setBusy(false);
      return;
    }
    const api = client.current;
    if (mode === "demo") {
      void (async () => {
        try {
          const run = await api.createRun(initialRequest);
          let next = await load(api, run.run_id, initialRequest);
          for (let i = 0; i < 8 && ACTIVE.has(next.run.status); i++)
            next = await load(api, run.run_id, initialRequest);
          if (current === epoch.current) save(next);
        } catch (reason) {
          if (current === epoch.current) setError(message(reason));
        } finally {
          if (current === epoch.current) setBusy(false);
        }
      })();
    }
    return () => {
      epoch.current++;
    };
  }, [mode, baseUrl, load, save]);

  const start = useCallback(
    async (request: RunRequest) => {
      const api = client.current;
      if (!api) {
        setError("Проверьте адрес API в настройках подключения.");
        return;
      }
      const current = epoch.current,
        token = ++operation.current;
      setBusy(true);
      setError(null);
      setResult(null);
      try {
        const run = await api.createRun(request);
        if (current === epoch.current && token === operation.current) {
          setHistory((previous) =>
            previous.filter((item) => item.run.run_id !== run.run_id),
          );
          setResult({
            run,
            request,
            rows: [],
            csv: null,
            evaluation: null,
            csvError: null,
            evaluationError: null,
          });
        }
        const next = await load(api, run.run_id, request);
        if (current === epoch.current && token === operation.current)
          save(next);
      } catch (reason) {
        if (current === epoch.current && token === operation.current)
          setError(message(reason));
      } finally {
        if (current === epoch.current && token === operation.current)
          setBusy(false);
      }
    },
    [load, save],
  );

  const refresh = useCallback(async () => {
    const api = client.current;
    if (!result || !api || busy) return;
    const current = epoch.current,
      token = ++operation.current;
    setBusy(true);
    setError(null);
    try {
      const next = await load(api, result.run.run_id, result.request);
      if (current === epoch.current && token === operation.current) save(next);
    } catch (reason) {
      if (current === epoch.current && token === operation.current) {
        setResult((previous) =>
          previous
            ? { ...previous, rows: [], csv: null, evaluation: null }
            : null,
        );
        setHistory((previous) =>
          previous.filter((item) => item.run.run_id !== result.run.run_id),
        );
        setError(message(reason));
      }
    } finally {
      if (current === epoch.current && token === operation.current)
        setBusy(false);
    }
  }, [result, busy, load, save]);

  useEffect(() => {
    if (!result || busy || error || !ACTIVE.has(result.run.status)) return;
    const timer = window.setTimeout(() => {
      void refresh();
    }, 1100);
    return () => window.clearTimeout(timer);
  }, [result, busy, error, refresh]);

  const checkHealth = useCallback(async () => {
    if (!client.current) return;
    const current = epoch.current;
    setHealthBusy(true);
    setHealthError(null);
    setHealth(null);
    try {
      const next = await client.current.health();
      if (current === epoch.current) setHealth(next);
    } catch (reason) {
      if (current === epoch.current) setHealthError(message(reason));
    } finally {
      if (current === epoch.current) setHealthBusy(false);
    }
  }, []);

  function selectRun(id: string) {
    const found = history.find((item) => item.run.run_id === id);
    if (found) {
      operation.current++;
      setResult(found);
      setError(null);
      setBusy(false);
    }
  }
  return {
    result,
    history,
    busy,
    error,
    health,
    healthError,
    healthBusy,
    start,
    refresh,
    checkHealth,
    selectRun,
  };
}
