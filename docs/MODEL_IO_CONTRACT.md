# Model input and output contract

Status: **proposed for participant 1 review; not yet agreed or implemented**.

Owners: participant 2 implements data, features, model and evaluation; participant 1 supplies archived weather, owns shared contracts and integrates the agent/API. This proposal makes section 5 of `IMPLEMENTATION_PLAN.md` concrete without changing participant 1's code.

## 1. Public interface

```python
# Proposed module: wind_agent.model.interface
train(history, weather_features, cutoff, config) -> ModelArtifact
predict(model_artifact, weather_frame, issue_time, horizon_hours) -> pandas.DataFrame
evaluate(predictions, observed, split_config) -> MetricsReport
```

For the first integration, `model_artifact` is the path to a model package directory containing `model.joblib` and `metadata.json`. The package contains both turbine models, or one model supporting both IDs. `train` returns that directory path; `evaluate` returns a JSON-serializable dictionary. These types are proposed, not existing classes/functions.

`issue_time` must be a timezone-aware UTC datetime on an exact hour. `horizon_hours` is 24 or 48. Participant 1 passes one or both turbines; participant 2 predicts for the unique turbine IDs present in the input. Turbine IDs are strings: `turbine_1`, `turbine_2`.

`predict` performs no network requests, does not train, and does not mutate its input frame. Participant 1 checks that all turbines requested by the user are present; participant 2 can validate only the turbines actually supplied in the frame.

## 2. Hour convention requiring agreement

**Proposed convention: `valid_time` is the END of the predicted one-hour interval.**

For `issue_time = 2026-01-31T18:00:00Z`:

- lead 1: interval `[18:00, 19:00)`, `valid_time = 19:00Z`;
- lead 24: interval ending at `2026-02-01T18:00:00Z`;
- lead 48: interval ending at `2026-02-02T18:00:00Z`.

For each turbine, input and output must contain exactly `issue_time + 1h, ..., issue_time + Hh`; no missing hours or duplicate `(turbine_id, valid_time)` keys. This predicts the next H complete hourly intervals. The API/UI must show or explain interval end labels; February coverage must be checked by target intervals in the agreed data timezone, not merely by date strings on end labels. In particular, an interval ending at February 1 midnight belongs to January 31, while February's final interval ends at March 1 midnight. Participant 1 must choose daily origins/replay boundary runs to cover the intended February intervals.

Participant 2 will convert SCADA interval labels to this convention only after establishing the original timezone and start/end meaning. Neither is stated in the CSV. If unresolved, assumptions must be explicit in configuration and model metadata; naive timestamps must not silently become UTC.

Weather features need a documented time meaning too: an instantaneous weather value at interval end is not automatically the mean over that hour. The adapter should retain source timing semantics and the model metadata should specify `weather_feature_time_basis` (for example `instant_at_end` or `interval_mean`). Do not shift API timestamps by one hour without a source-based reason. We will validate/calibrate with the same weather feature convention used at prediction time.

## 3. Input from participant 1

`weather_frame` is a pandas DataFrame. All timestamp columns are timezone-aware UTC; wind is m/s and temperature is Celsius.

| Column | Type | Meaning |
|---|---|---|
| `turbine_id` | string | `turbine_1` or `turbine_2` |
| `valid_time` | UTC timestamp | End of the target interval |
| `wind_speed_ms` | finite float | Weather forecast wind; source height must be declared |
| `temperature_c` | finite float | Weather forecast ambient temperature |
| `wind_height_m` | positive float | Forecast wind height; must match the model's expected height/conversion |
| `model_run_time` | UTC timestamp | Weather model initialization |
| `forecast_available_at` | UTC timestamp | Observed publication time, or explicitly policy-derived availability bound |
| `availability_basis` | string | `observed` or `assumed_delay`; assumptions must be documented |
| `provider` | string | Forecast source |
| `weather_model` | string | Explicit weather model identity |
| `raw_sha256` | string | Hash identifying the cached source response/file |

Participant 1 retains full coordinates, request parameters, `retrieved_at`, raw files and the availability policy in its provenance store. These are not numerical model features. `retrieved_at` today must not be treated as historical publication time. A policy-derived availability timestamp is not proof that the run was actually available then; this limitation must remain visible in metadata/UI.

For v1, participant 1 selects one allowed weather run per turbine/forecast request. Runs must provide all H target intervals. The model package declares expected provider/model, wind height and time basis; mismatches cause an explicit error instead of an unnoticed distribution change. Additional columns are permitted, but only the artifact's feature list enters the model. No future measured weather or power may be required.

## 4. Output from participant 2

A DataFrame sorted by `turbine_id`, then `valid_time`, with one output row per input target key:

| Column | Type | Meaning |
|---|---|---|
| `schema_version` | string | Proposed initial value `1.0` |
| `issue_time` | UTC timestamp | The historical prediction origin |
| `turbine_id` | string | Preserved turbine ID |
| `valid_time` | UTC timestamp | Preserved interval end |
| `lead_hours` | integer | `(valid_time - issue_time) / 1 hour`, 1 through H |
| `prediction` | finite float | Predicted hourly mean normalized active power |
| `target_unit` | string | `normalized_power` |
| `weather_model` | string | Copied from the selected input |
| `weather_run_time` | UTC timestamp | Input `model_run_time` |
| `model_version` | string | Version/hash of the model package |
| `training_cutoff` | UTC timestamp | Availability cutoff applied to all fitting/calibration data |
| `data_quality` | string | `ok` or `warning` for a successfully computed row |
| `warnings` | list of strings | Such as an explicit assumption about weather availability |

Participant 1 adds `run_id` and `revision` and owns JSON/CSV serialization, agent logs and stored result provenance. Existing result-contract names in the shared plan are preserved. No aggregate farm MW/MWh field is proposed because rated capacities and the normalization definition are unknown.

Prediction postprocessing, including any `[0, 1]` clipping, must be explicit in metadata and applied consistently during validation. The observed training range alone is not a confirmed physical definition. No confidence intervals are promised before calibration.

## 5. Model package metadata

At minimum: schema/model version, turbine IDs, feature names/order/dtypes/units, weather provider/model/height/time basis, target definition/unit, interval-end convention, original data timezone and timestamp assumption, training cutoff, target reporting-delay assumption, training source hashes, preprocessing and clipping policy, seed, package versions, validation periods and metric provenance.

All fitted components (including imputation, scaling and wind calibration) obey the same cutoff. For SCADA targets: `interval_end + reporting_delay <= cutoff <= issue_time`. A package trained using January 31 23:50 cannot serve a January 31 23:00 local origin.

Metadata also records the latest information cutoff used for model selection and calibration; these must not exceed the issue time either. The model file's present-day creation timestamp is not compared with the historical origin: replay is performed now, but its information must be limited to the simulated past.

## 6. Failure behavior

For v1, invalid input fails the entire request with a documented `ValueError` code; participant 1 catches it and sets the run state to `failed`. No silent zero filling or partial-success table.

Proposed codes: `INVALID_SCHEMA`, `NAIVE_TIMESTAMP`, `UNSUPPORTED_TURBINE`, `INVALID_HORIZON`, `DUPLICATE_TARGET`, `INCOMPLETE_HORIZON`, `NONFINITE_FEATURE`, `WEATHER_NOT_AVAILABLE`, `MODEL_TRAINED_AFTER_ISSUE`, `FEATURE_SEMANTICS_MISMATCH`, `NONFINITE_PREDICTION`.

Participant 2 validates `model_run_time <= forecast_available_at <= issue_time` and `training_cutoff <= issue_time`, as well as full horizon and feature semantics. These comparisons enforce recorded values; participant 1 remains responsible for whether its availability evidence/policy is valid. No comparison can turn an assumed timestamp into observed historical proof.

## 7. Acceptance example and handoff

- One turbine, 24 hourly intervals: 24 output rows.
- Both turbines, 48 hourly intervals each: 96 output rows.
- Same package and identical inputs: identical predictions.
- Weather published after origin: failure.
- A future-trained package: failure.
- Missing hour, unit/height mismatch or unknown turbine: failure with reason.
- Save/load in a new Python process: same predictions.

Example counts are contract checks, not a claim that these functions are implemented. Participant 1 first supplies a small archived-weather frame plus metadata; participant 2 supplies the baseline package and a model-only integration test. January validation weather must use the same provider/height/time semantics as February inference. February target values are not in the supplied CSVs, so no February accuracy is promised.

## 8. Participant 1 response requested

Please reply with agreement or exact changes for:

1. **Time:** interval-end labels and the daily origin/replay boundary policy; confirm how you want the API/UI to label target hours.
2. **Weather:** column names, provider/model, wind height, feature time basis, availability policy and a small sample input.
3. **Interface:** package directory path + DataFrame input/output, turbine IDs and typed error approach; indicate any already implemented shared contracts to reuse.
4. **Unknown source semantics:** whether timezone, SCADA interval labels and normalization have been clarified by the organizer. If not, agree explicit configuration assumptions before fitting.

Once participant 1 responds, participant 2 updates this document to **agreed**, records the response link and implements against that version. No agreement is inferred from silence.
