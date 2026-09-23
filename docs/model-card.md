# Wind power v1

**Measured result:** `hgb_medium` was selected using tuning MAE **0.232464**.
Its later holdout MAE is **0.226919**; the wind-curve baseline scores **0.209460**
on that same holdout. The more complex model did not improve on the wind curve
in this independent period. The selection remains the predeclared tuning choice;
these holdout results were not used to change its parameters or choose a different
winner. Additional independent periods are needed before claiming consistent
benefit over the simpler model.

The delivered package has passed real archived-weather integration through
participant 1's agent for both turbines and both 24/48-hour horizons. Reloading
in a fresh Python process reproduces the same predictions. See
[integration evidence](evidence/model-integration.json).

This package estimates hourly **normalized active power** for `turbine_1` and
`turbine_2`, at leads 1–24 or 1–48 hours from an explicit issue time. It uses
archived NOAA GFS wind speed at 100 m and temperature at 2 m, sampled at the end
of each target hour. Each turbine has a separate fitted estimator. Prediction
loads saved weights and makes no network, training or LLM calls.

The model's source-clock assumptions are provisional. Outputs carry
`data_quality="warning"` and describe those assumptions. The normalization
formula and turbine ratings are unknown, so values cannot be converted to MW
or MWh or summed as station MW.

## Historical labels and weather

The audited official files contain 142,360 and 149,499 ten-minute records from
March 2023 through January 2026. A complete hourly label is the mean of all six
ten-minute values; partial and missing hours do not become training labels.
Source SHA256s and completeness counts are in
[hourly-quality.json](evidence/hourly-quality.json).

Explicit working assumptions: fixed UTC+5 (`Etc/GMT-5`), source timestamps mark
the start of ten-minute mean intervals, zero reporting delay. Model target
timestamps are aware UTC interval ends. The training weather window is recent
December 2025–January 2026; it does not straddle Kazakhstan's 2024 clock change.
These choices still need source-owner confirmation.

Archived forecasts are selected using all required single-part S3 GRIB/index
objects' availability by issue time. The maximum `LastModified` is the recorded
availability basis. This establishes the declared archive-object policy; the
original NOAA publication time and historical access-control state are not
independently established. Retrieval time today is not used as historical
availability. Source bytes and their hashes remain in the local archive cache.

Both turbines use the same GFS cell, approximately 43.75°N, 78.5°E. Wind is the
norm of the two 100 m wind components; temperature is converted from kelvin to
Celsius. Weather values are instantaneous at target-hour end; the target is an
hourly mean. Measured SCADA wind/temperature never replace forecast inputs.

## Selection and information boundaries

All 60 weather origins are declared before fitting: 30 train, 14 tune, 15
holdout, one integration. Each origin requests both turbines for 48 hours.
Rows are then restricted to nonoverlapping target intervals and complete,
available labels. Forecast vintages can share a target within one split;
target hours cannot cross splits. Reports count both forecast rows and unique
targets; overlapping forecasts are not independent statistical samples.

| Stage | Target interval in fixed UTC+5, labelled by end | Information cutoff UTC |
|---|---|---|
| Train | Dec 1 00:00 < end ≤ Dec 31 00:00 | Dec 30, 2025 19:00 |
| Tune | Jan 1 00:00 < end ≤ Jan 15 00:00 | Jan 14, 2026 19:00 |
| Holdout | Jan 16 00:00 < end ≤ Jan 31 23:00 | Jan 31, 2026 18:00 |

The candidates are constant mean, a learned binned wind curve, and three
histogram gradient boosting configurations. The curve uses 1 m/s bins, linear
interpolation between populated bin centers and constant endpoint extension.
Boosting uses wind speed and temperature, a fixed random seed and no internal
early stopping. There is no clipping, imputation or scaling.

Selection minimizes the equal-turbine mean MAE on the tuning split, breaking
exact ties in declared candidate order. The choice is saved before computing
holdout errors. The selected algorithm and two baselines are refitted on
train+tune for holdout evaluation. The final production model is subsequently
refitted on every complete available pair among the 59 preproduction weather
origins, including the holdout history and eligible buffer hours.

The production cutoff is **2026-01-31 18:00 UTC**, the first forecast issue. No
later January hour or February observation enters it. January holdout scores
apply to the model frozen before holdout, not to the final production refit.

## Use and limitations

- Predictive accuracy is measured in normalized-power MAE/RMSE, not an accuracy
  percentage. Low error on one winter period does not establish year-round skill.
- No February ground truth was supplied. Integration forecasts cannot establish
  February accuracy.
- No calibrated uncertainty intervals are supplied. Temperature at 2 m, wind at
  100 m and a coarse grid cannot fully describe local turbine conditions.
- The model does not separately identify maintenance, curtailment or shutdowns.
  Valid zero-power observations are retained.
- Only packages generated by this repository or otherwise trusted should be
  loaded with joblib. Hash/metadata checks establish consistency, not the safety
  of arbitrary third-party serialized code.

See [reproduction commands](MODEL_REPRODUCIBILITY.md),
[Kazakh team handoff](MODEL_HANDOFF_KK.md), the
[fixed candidate configuration](../config/model-experiment.json) and the
[weather collection manifest](evidence/weather-training-manifest.json).

## Measured validation results

All values below are errors in `normalized_power`; lower is better. Tuning MAE
uses equal weight for the two turbines. Holdout rows have equal turbine counts.

| Candidate | Tuning MAE | Holdout MAE | Holdout RMSE |
|---|---:|---:|---:|
| constant_mean | 0.327375 | 0.315663 | 0.356472 |
| wind_curve | 0.248695 | 0.209460 | 0.262259 |
| hgb_small | 0.239597 | Not evaluated | Not evaluated |
| hgb_medium | 0.232464 | 0.226919 | 0.305229 |
| hgb_wide | 0.236334 | Not evaluated | Not evaluated |

Only the selected HGB configuration and the two predeclared baselines were
evaluated on holdout. Unselected HGB configurations were not compared on holdout.

| Selected model, holdout | MAE | RMSE | Forecast rows |
|---|---:|---:|---:|
| turbine_1 | 0.222126 | 0.296741 | 719 |
| turbine_2 | 0.231712 | 0.313487 | 719 |
| Lead 1-24 h | 0.203781 | 0.276473 | 718 |
| Lead 25-48 h | 0.249992 | 0.331430 | 720 |

| Split | Forecast rows | Unique turbine/hour targets |
|---|---:|---:|
| train | 2804 | 1426 |
| tune | 1296 | 672 |
| holdout | 1438 | 766 |

Of 5,664 preproduction weather rows, split evaluation excludes 98 rows outside
the declared target windows and 28 rows with missing truth. The final production
refit uses 2,824 pairs for turbine 1 and 2,812 for turbine 2 (5,636 total),
including eligible buffer hours. No missing label is filled with zero.

Model SHA256: `8f54f869b02318db534e956d84e3abc0339f574089cefb75da9db7ad3782ede4`.
Full machine-readable results: [validation.json](../models/wind-power-v1/validation.json).
Weights and bound metadata: [model package](../models/wind-power-v1/).
