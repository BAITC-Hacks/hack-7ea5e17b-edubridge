# Reproduce participant 2's model

Use Python 3.11 and run commands from the repository root. Windows users can
replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,weather,model]'
```

The OpenAI API is not involved in numerical training or prediction. The source
data, archived NOAA weather and local Python dependencies are sufficient.

## Prepare the official SCADA sources

Download the two CSVs referenced in [DATA_PREPARATION.md](DATA_PREPARATION.md),
then substitute their paths below. The loader verifies the official SHA256s.
The command makes the unresolved source-clock assumptions explicit; it does not
claim that the organizer confirmed them.

```sh
.venv/bin/python -m wind_agent.data \
  --turbine-1 ../sources/turbine_1.csv \
  --turbine-2 ../sources/turbine_2.csv \
  --hourly \
  --history-timezone Etc/GMT-5 \
  --source-timestamp-convention interval_start \
  --reporting-delay-seconds 0 \
  --training-cutoff 2026-01-31T18:00:00Z \
  --assumption 'Unconfirmed: all source labels use fixed UTC+5 and mark the start of a ten-minute mean; reporting delay is zero and normalization remains unresolved.'
```

Only hours containing all six ten-minute samples supply labels. Gaps remain
missing. `hourly-history.csv` contains the full grid for coverage reporting;
training filters complete labels by their availability cutoff.

## Collect genuine archived forecasts

```sh
.venv/bin/python -m wind_agent.evaluation.weather_dataset
```

The fixed plan contains 60 daily issues at 18:00 UTC: 30 training, 14 tuning,
15 holdout and one January 31 integration issue, each with a complete 48-hour
horizon for both turbines. Downloads require several GB and may take tens of
minutes. The command resumes completed origins and cached requests; failures
remain explicit in `docs/evidence/weather-training-manifest.json`.

It retains original GRIB field bytes, HTTP headers, S3 listings and extraction
manifests. It removes redundant concatenated bundles only after reconstructing
and verifying their SHA256 from retained originals. The provider can reconstruct
these bundles offline. Operational weather is used for fitting and forecasting;
observed SCADA wind and temperature are not model inputs.

The selection of weather runs uses the maximum S3 `LastModified` of all required
single-part GRIB/index objects. This is an explicit archive-availability policy,
not a reconstruction of NOAA's original publication time. Both turbines map to
the same 0.25-degree GFS grid cell.

## Train, select and package

```sh
.venv/bin/python -m wind_agent.model.experiment \
  --output artifacts/model-experiment-reproduced \
  --package models/wind-power-reproduced
```

Both output directories must be new or empty: an existing model is not replaced.
The declared candidates and tie policy are in
[`config/model-experiment.json`](../config/model-experiment.json). Selection uses
equal weight for the two turbines' tuning MAE. Every candidate sees identical
training and tuning keys; no random time split or hidden early stopping is used.

The runner saves the design before computing errors and saves `selection.json`
before looking at holdout metrics. The selected model and two baselines are
refitted on train+tune, then evaluated on the later holdout period. The selected
algorithm is finally refitted using complete archived-weather/target pairs
available by January 31, 18:00 UTC. The final model includes the holdout history;
its January holdout report describes the earlier frozen model, not an independent
test of this final refit.

Outputs include individual candidate prediction CSVs and reports under the
experiment directory, plus `model.joblib`, `metadata.json` and `validation.json`
under the model package. Raw source hashes, weather hashes, assumptions,
cutoffs, software versions and model hash are recorded. Model metadata is bound
into the weights; do not edit it after fitting. A rerun records new generation
timestamps, so artifact byte hashes may change even when predictions agree.

## Run the delivered model

[`config/archive-model.json`](../config/archive-model.json) points at the delivered
`models/wind-power-v1` package. For the reproduced package above, copy the config
and change `model_artifact_path` to `models/wind-power-reproduced`.

```sh
.venv/bin/python -m wind_agent --config config/archive-model.json health
.venv/bin/python -m wind_agent --config config/archive-model.json run --issue-time 2026-01-31T18:00:00Z --horizon-hours 24
.venv/bin/python -m wind_agent --config config/archive-model.json run --issue-time 2026-01-31T18:00:00Z --horizon-hours 48
.venv/bin/python scripts/validate_model_package.py
```

The last command requires the January 31 archive cache and performs no network
requests. It verifies the actual `AgentService`/`ModelBridge` path, a fresh Python
process, both horizons and both turbines, plus stable revisions for unchanged
inputs. This verifies integration; it does not measure February accuracy.

## Checks

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src/wind_agent tests/model tests/weather scripts/validate_model_package.py
.venv/bin/python -m pip check
```

To include the Streamlit application tests, also install `-r ui/requirements.txt`.
Use `python -m pytest` so repository-root UI imports resolve. Unit tests are
network-free and identify synthetic fixtures explicitly. The real model's
separate integration evidence is in `docs/evidence/model-integration.json`.

The full February archive replay and UI/metrics integration are complete; see
`docs/evidence/february-replay.json`, `docs/evaluation-api.md` and `ui/web/README.md`.
The 29 origins produced 2,784 full forecast records and 1,344 selected February
records. Final submission is a separate action on the hackathon platform.
No February ground truth is supplied; never label normalized power as MW/MWh
or January MAE as February accuracy. Autonomous updates and advisory diagnostics
are described in `docs/agent-monitor.md` and `docs/forecast-analysis.md`.
