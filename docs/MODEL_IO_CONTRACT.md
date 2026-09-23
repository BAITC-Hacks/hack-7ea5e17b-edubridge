# Model input and output contract

Status: **agreed for integration on 2026-09-23; participant 2's model is not yet implemented**.

Participant 1 explicitly accepted the interface and recorded two adaptations in [their response](pr-2-response.md), merged into `main` via [PR #3](https://github.com/BAITC-Hacks/hack-7ea5e17b-edubridge/pull/3) at commit `903828a`. Participant 2 accepts those adaptations below: descriptive `availability_basis` and the existing `ModelArtifact` metadata envelope. [Integration instructions](model-integration.md) contain the matching platform contract. This agreement does not imply organizer confirmation of source-data semantics or approval/merge of PR #2.

Owners: participant 2 implements data, features, model and evaluation; participant 1 supplies archived weather, owns shared contracts and integrates the agent/API. This contract makes section 5 of `IMPLEMENTATION_PLAN.md` concrete without changing participant 1's code.

## 1. Public interface

```python
# Agreed module: wind_agent.model.interface
train(history, weather_features, cutoff, config) -> pathlib.Path
predict(model_artifact, weather_frame, issue_time, horizon_hours) -> pandas.DataFrame
evaluate(predictions, observed, split_config) -> dict
```

For the first integration, `model_artifact` is the path to a model package directory containing `model.joblib` and `metadata.json`. The package contains both turbine models, or one model supporting both IDs. `train` returns that directory path; `evaluate` returns a JSON-serializable dictionary. The external `predict` receives a `str | pathlib.Path`, not the platform's Pydantic `ModelArtifact` object. Participant 2 still needs to implement these functions.

`issue_time` must be a timezone-aware UTC datetime on an exact hour. `horizon_hours` is 24 or 48. Participant 1 passes one or both turbines; participant 2 predicts for the unique turbine IDs present in the input. Turbine IDs are strings: `turbine_1`, `turbine_2`.

`predict` performs no network requests, does not train, and does not mutate its input frame. Participant 1 checks that all turbines requested by the user are present; participant 2 can validate only the turbines actually supplied in the frame.

## 2. Agreed hour convention

**Agreed convention: `valid_time` is the END of the predicted one-hour interval (`hour_end`).**

For `issue_time = 2026-01-31T18:00:00Z`:

- lead 1: interval `[18:00, 19:00)`, `valid_time = 19:00Z`;
- lead 24: interval ending at `2026-02-01T18:00:00Z`;
- lead 48: interval ending at `2026-02-02T18:00:00Z`.

For each turbine, input and output must contain exactly `issue_time + 1h, ..., issue_time + Hh`; no missing hours or duplicate `(turbine_id, valid_time)` keys. This predicts the next H complete hourly intervals. The API/UI must show or explain interval end labels. An interval ending at February 1 midnight belongs to January 31, while February's final interval ends at March 1 midnight.

Daily origins are 23:00 `Asia/Almaty` (18:00 UTC), an explicit team assumption. Replay uses 29 origins from January 31 through February 28 inclusive. February coverage is selected in that timezone by `2026-02-01 00:00 < valid_time <= 2026-03-01 00:00`: 672 intervals per turbine. The latest eligible origin wins overlaps without using observed error. Full horizons and revisions, including intervals outside February, are retained. This replay timezone does not establish the original SCADA timezone.

Participant 2 will convert SCADA interval labels to this convention only after establishing the original timezone and start/end meaning. Neither is stated in the CSV. If unresolved, assumptions must be explicit in configuration and model metadata; naive timestamps must not silently become UTC.

The agreed GFS feature convention is `weather_feature_time_basis="instant_at_end"`: instantaneous weather at the end of the target interval, not an hourly mean. Weather timestamps are not shifted. Model metadata, validation and calibration must use this same convention.

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
| `forecast_available_at` | UTC timestamp | Archive-object availability bound under the documented source policy |
| `availability_basis` | nonempty string | Descriptive evidence/policy name; not an enum |
| `provider` | string | Forecast source |
| `weather_model` | string | Explicit weather model identity |
| `raw_sha256` | string | Hash identifying the cached source response/file |

Participant 1 retains full coordinates, request parameters, `retrieved_at`, raw files and the availability policy in its provenance store. These are not numerical model features. `retrieved_at` today must not be treated as historical publication time. A policy-derived availability timestamp is not proof that the run was actually available then; this limitation must remain visible in metadata/UI.

The delivered source is `provider="NOAA GFS via AWS Open Data"`, `weather_model="gfs_0p25"`, wind at **100 m** in m/s and temperature at **2 m** in Celsius. Both turbines use the nearest grid cell, 43.75°N, 78.50°E. `availability_basis="s3_last_modified_all_required_singlepart_grib_and_index_objects"` uses the maximum S3 LastModified across required single-part GRIB and index objects. Multipart objects are excluded. This is archive metadata; original NOAA publication and historical bucket ACLs are not reconstructed. See [weather-source.md](weather-source.md).

The full input also contains `WeatherRecord` provenance fields. Its `lead_hours`, if present, is measured from the **weather model's run time**. Output `lead_hours` is measured from the **power forecast's issue time** and must be computed independently. In the supplied sample the first weather lead is 7, while the first power lead is 1.

For v1, participant 1 selects one allowed weather run per turbine/forecast request. Runs must provide all H target intervals. The model package declares expected provider/model, wind height and time basis; mismatches cause an explicit error instead of an unnoticed distribution change. Additional columns are permitted, but only the artifact's feature list enters the model. No future measured weather or power may be required.

## 4. Output from participant 2

A DataFrame sorted by `turbine_id`, then `valid_time`, with one output row per input target key:

| Column | Type | Meaning |
|---|---|---|
| `schema_version` | string | Agreed value `1.0` |
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

The platform accepts a minimum of `turbine_id`, `valid_time`, `prediction`; participant 2 will return the full table above. Supplied contract fields are checked, not silently overwritten. Model warnings and data-quality values are retained, and platform warnings are added.

Prediction postprocessing, including any `[0, 1]` clipping, must be explicit in metadata and applied consistently during validation. The observed training range alone is not a confirmed physical definition. No confidence intervals are promised before calibration.

## 5. Model package metadata

`metadata.json` must validate against `wind_agent.contracts.ModelArtifact`. Only `model_version`, `training_cutoff`, `target_unit`, `metadata` and optional `artifact_path` are allowed at the top level. **All remaining fields listed below belong inside `metadata`**, including `schema_version`. Omit `artifact_path` for the standard package; the bridge resolves `model.joblib` inside its directory. See the exact JSON example in [model-integration.md](model-integration.md#каталог-и-точный-json-metadata).

At minimum: schema/model version, turbine IDs, feature names/order/dtypes/units, weather provider/model/height/time basis, target definition/unit, interval-end convention, original data timezone and timestamp assumption, training cutoff, target reporting-delay assumption, training source hashes, preprocessing and clipping policy, seed, package versions, validation periods and metric provenance.

All fitted components (including imputation, scaling and wind calibration) obey the same cutoff. For SCADA targets: `interval_end + reporting_delay <= cutoff <= issue_time`. A package trained using January 31 23:50 cannot serve a January 31 23:00 local origin.

Metadata also records the latest information cutoff used for model selection and calibration; these must not exceed the overall `training_cutoff`, which must not exceed the issue time. The model file's present-day creation timestamp is not compared with the historical origin: replay is performed now, but its information must be limited to the simulated past.

Before real inference, `settings.history_timezone` and `settings.train_cutoff` must be explicit; both are currently `null` in `config/archive.json`. Require `artifact.training_cutoff <= settings.train_cutoff`, `artifact.training_cutoff <= issue_time`, and `settings.train_cutoff <= February 1 00:00` in the declared history timezone. Unknown SCADA timezone, labels, reporting delay and normalization must be resolved or recorded as explicit assumptions before fitting; source confirmation is still outstanding.

## 6. Failure behavior

For v1, invalid input fails the entire request with a documented `ValueError` code; participant 1 catches it and sets the run state to `failed`. No silent zero filling or partial-success table.

Agreed model codes: `INVALID_SCHEMA`, `NAIVE_TIMESTAMP`, `UNSUPPORTED_TURBINE`, `INVALID_HORIZON`, `DUPLICATE_TARGET`, `INCOMPLETE_HORIZON`, `NONFINITE_FEATURE`, `WEATHER_NOT_AVAILABLE`, `MODEL_TRAINED_AFTER_ISSUE`, `FEATURE_SEMANTICS_MISMATCH`, `NONFINITE_PREDICTION`.

Participant 2 validates `model_run_time <= forecast_available_at <= issue_time` and `training_cutoff <= issue_time`, as well as full horizon and feature semantics. These comparisons enforce recorded values; participant 1 remains responsible for whether its availability evidence/policy is valid. No comparison can turn an assumed timestamp into observed historical proof.

## 7. Acceptance example and handoff

- One turbine, 24 hourly intervals: 24 output rows.
- Both turbines, 48 hourly intervals each: 96 output rows.
- Same package and identical inputs: identical predictions.
- Weather published after origin: failure.
- A future-trained package: failure.
- Missing hour, unit/height mismatch or unknown turbine: failure with reason.
- Save/load in a new Python process: same predictions.

Example counts are contract checks, not a claim that these functions are implemented. Participant 1 has supplied [weather-input-sample.json](evidence/weather-input-sample.json): 48 rows, both turbines, 24 hours, `issue_time=2026-01-31T18:00:00Z`. Read its `records` into a DataFrame and parse timestamp columns as aware UTC. Participant 2 next supplies the baseline package and a model-only integration test. January validation weather must use the same provider/height/time semantics as February inference. February target values are not in the supplied CSVs, so no February accuracy is promised.

## 8. Agreement and remaining handoff

Participant 1's [committed response](https://github.com/BAITC-Hacks/hack-7ea5e17b-edubridge/blob/903828a/docs/pr-2-response.md) explicitly confirms:

1. **Time:** interval-end labels, UTC interface, daily 23:00 Almaty origins and the February coverage policy.
2. **Weather:** accepted columns, NOAA GFS, 100 m wind, `instant_at_end`, the documented S3 availability evidence and a real 24-hour sample for both turbines.
3. **Interface:** package directory path + DataFrame input/output, turbine IDs and `ValueError` approach, with the existing shared `ModelArtifact` envelope.
4. **Unknown source semantics:** no organizer confirmation yet; participant 2 must document concrete assumptions or established semantics before fitting.

Participant 2 accepts the two requested adaptations in this revision. The remaining handoff is an implemented model package, a model-only test on real weather, save/load verification, explicit assumptions and validation metrics. Model implementation, platform integration with that model, and PR #2 review/merge remain separate completion steps.
