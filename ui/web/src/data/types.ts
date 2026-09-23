export type RunStatus =
  | "queued"
  | "fetching_weather"
  | "validating"
  | "forecasting"
  | "analysing"
  | "completed"
  | "failed";

export interface RunRequest {
  issue_time: string;
  horizon_hours: 24 | 48;
  turbine_ids: string[];
}

export interface RunEvent {
  at?: string;
  step: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface Run {
  run_id: string;
  status: RunStatus;
  revision?: number;
  events?: RunEvent[];
  steps?: {
    name: string;
    status: "pending" | "running" | "completed";
    message: string;
  }[];
  warnings?: string[] | string;
  error?: string | Record<string, unknown> | null;
  request?: RunRequest;
  source_mode?: string;
  recalculation_reason?: string;
  [key: string]: unknown;
}

export interface ForecastRecord {
  run_id: string;
  revision?: number;
  issue_time: string;
  turbine_id: string;
  valid_time: string;
  lead_hours: number;
  prediction: number;
  target_unit: string;
  weather_model?: string;
  weather_run_time?: string | null;
  model_version?: string;
  training_cutoff?: string | null;
  forecast_available_at?: string | null;
  data_quality?: string | Record<string, unknown>;
  warnings?: string[] | string;
  source_mode?: string;
  [key: string]: unknown;
}

export interface Health {
  status: string;
  mode?: string;
  source_mode?: string;
  is_demo?: boolean;
  model_ready?: boolean;
  weather_ready?: boolean;
  data_ready?: boolean;
  turbine_ids?: string[];
  warnings?: string[];
  [key: string]: unknown;
}

export interface EvaluationScore {
  mae: number;
  rmse: number;
  n_forecasts?: number;
  n_unique_targets?: number;
  [key: string]: unknown;
}

export interface EvaluationMetricCard {
  target_unit: string;
  equal_turbine_mean_mae: number;
  overall: EvaluationScore;
  by_turbine?: Record<string, EvaluationScore>;
  by_lead_group?: Record<string, EvaluationScore>;
  [key: string]: unknown;
}

export interface EvaluationMetrics {
  tuning: {
    selected_candidate: string;
    scores: EvaluationMetricCard;
    selection_metric: string;
    selection_cutoff: string;
  };
  independent_holdout: {
    selected_candidate: string;
    scores: EvaluationMetricCard;
    training_cutoff: string;
    used_for_selection: boolean;
    applies_to: string;
  };
  production_refit: {
    training_cutoff: string;
    includes_holdout: boolean;
    independent_metrics: Record<string, unknown> | null;
    reason: string;
  };
}

export interface EvaluationBaseline {
  tuning: Record<string, EvaluationMetricCard>;
  independent_holdout: Record<string, EvaluationMetricCard>;
  comparison: {
    metric: string;
    selected_mae: number;
    wind_curve_mae: number;
    wind_curve_better: boolean;
  };
}

export interface EvaluationPeriods {
  history_timezone: string;
  target_start_exclusive: boolean;
  target_end_inclusive: boolean;
  windows: Record<
    string,
    {
      target_start: string;
      target_end: string;
      issue_start?: string;
      issue_end?: string;
      [key: string]: unknown;
    }
  >;
  [key: string]: unknown;
}

export interface Evaluation {
  status?: string;
  reason?: string | null;
  source_mode?: string;
  is_demo?: boolean;
  test_truth_available?: boolean;
  model_version?: string | null;
  training_cutoff?: string | null;
  target_unit?: string;
  metrics?: EvaluationMetrics | null;
  baseline?: EvaluationBaseline | null;
  periods?: EvaluationPeriods | null;
  provenance?: Record<string, unknown>;
  warnings?: string[];
  [key: string]: unknown;
}

export interface ForecastClient {
  health(): Promise<Health>;
  createRun(request: RunRequest): Promise<Run>;
  getRun(id: string): Promise<Run>;
  getForecast(id: string): Promise<ForecastRecord[]>;
  getForecastCsv(id: string): Promise<string>;
  getEvaluation(): Promise<Evaluation>;
}
