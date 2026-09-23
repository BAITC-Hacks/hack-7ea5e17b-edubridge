# SCADA source and hourly preparation — participant 2

Implemented: validate the original CSVs, preserve observations, produce quality
reports and prepare complete hourly targets under explicit source-clock assumptions.
The [model contract](MODEL_IO_CONTRACT.md) describes the separate forecast interface.
Model fitting and validation are the next stages.

## Install and run

Use Python 3.11 and the repository's existing dependencies:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Download the two supplied CSV files unchanged from [turbine 1](https://drive.google.com/file/d/1hubNF3tgc7DbgXxHLpIF6zIBHtvMyzLX/view)
and [turbine 2](https://drive.google.com/file/d/1_WTrYhZ3-71A9IpkBb9RHPN7ncVaupBk/view).
The CLI verifies their original SHA256 values from `wind_agent.data.sources` before
parsing. Re-saving in Excel or changing line endings changes those hashes; use the
original download. A Google Drive HTML login/error page will fail the hash check.

With inputs saved as `artifacts/raw/turbine_1.csv` and `artifacts/raw/turbine_2.csv`:

```sh
.venv/bin/python -m wind_agent.data --turbine-1 artifacts/raw/turbine_1.csv --turbine-2 artifacts/raw/turbine_2.csv --output-dir artifacts/data
.venv/bin/python -m pytest tests/model/test_data_loader.py -q
```

On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.
`artifacts/` is ignored by Git. Both source files are validated before output is
written, and source paths cannot be used as output files.

Outputs:

- `history.csv`: one row per original observation, ordered by turbine and source
  timestamp. No new rows, interpolation, clipping or missing-value filling.
- `quality-report.json`: source URLs/hashes/bytes, time coverage, missing spans,
  actual zero-power counts, observed ranges and unresolved semantics.

The small report for the supplied originals is committed as
[history-quality.json](evidence/history-quality.json). Raw and transformed datasets
are kept outside Git; the command above recreates the transformed file.

Verified on Python 3.11.16/macOS with the repository's pinned dependencies: 163
project tests passed (84 data tests), Ruff passed, and the official-source CLI
produced a byte-identical report on repeat. Original source hashes remained
unchanged. Turbine 1 has 142,360 observations and 9,992 missing slots; turbine 2 has
149,499 observations and 2,853 missing slots within their observed bounds. The
existing Starlette test client emits an AnyIO deprecation warning; tests pass.

## Python boundary

```python
from wind_agent.data import load_history

loaded = load_history("artifacts/raw/turbine_1.csv", "turbine_1")
frame = loaded.frame
report = loaded.report
```

`load_history(path, turbine_id, expected_sha256=...)` optionally pins a source hash;
the CLI always pins both official inputs. Turbine IDs are explicit. CSV `ID` is
retained as a string row counter, never used to join turbines. The canonical columns
are:

| Column | Meaning |
| --- | --- |
| `turbine_id` | `turbine_1` or `turbine_2` |
| `source_row_id` | Original row counter; not a time or model feature |
| `source_timestamp` | Parsed naive source label; timezone still unknown |
| `observed_wind_speed_ms` | Historical measured wind, m/s |
| `normalized_power` | Supplied normalized active power; normalization unknown |
| `observed_temperature_c` | Historical measured ambient temperature, °C |

Observed weather columns deliberately differ from forecast input column names.
Future measured weather must not be substituted for archived weather forecasts.

Wrong headers/row widths, invalid timestamps, duplicate timestamps, timestamps off
the ten-minute grid, invalid/nonfinite numbers and negative wind speed raise
`DataValidationError`, a `ValueError` with a readable code. Input order is recorded;
out-of-order rows are sorted with their values/IDs kept together. Repeated source
IDs are reported and do not merge observations. Power outside `[0, 1]` is reported
and preserved: the physical normalization definition is not confirmed.

Missing-slot coverage uses the inclusive observed minimum/maximum in each file.
It does not prove that periods before/after those bounds are complete. Reports are
descriptive source audits, not fitted preprocessing statistics or forecast scores.

## Implemented hourly targets

`wind_agent.data.hourly.prepare_hourly` requires `HistorySemantics` and an aware
`training_cutoff`. The CLI requires every semantic setting explicitly with `--hourly`.
There are no implicit timezone, interval-label or reporting-delay defaults.

Provisional settings for the delivered preparation (all **unconfirmed**):

| Setting | Assumption |
| --- | --- |
| History timezone | `Etc/GMT-5`: fixed UTC+5 for all source labels |
| Source labels | Start of each ten-minute interval |
| Source values | Mean over that ten-minute interval |
| Reporting delay | 0 seconds; historical publication was not observed |
| First forecast cutoff | `2026-01-31T18:00:00Z`, 23:00 at UTC+5 |
| Output labels | End of the one-hour interval, aware UTC |

The fixed offset is a provisional dataset-clock assumption, **not** the historical
civil-time rule for Kazakhstan. Civil time was moved back one hour in the relevant
regions on March 1, 2024 ([official decree](https://primeminister.kz/ru/decisions/19012024-20)).
`Asia/Almaty` represents the earlier UTC+6 offset and an ambiguous local hour on
February 29, 2024. This does not establish how the SCADA clock was recorded. If its
labels followed civil time, the fixed-offset interpretation before March 1 is off
by one hour. The original source labels/IDs remain in `history.csv`; change the
assumption and rerun when confirmed. `Asia/Almaty` can be selected, but ambiguous
or nonexistent local labels cause an error instead of inference, shifting or dropping.
For future archive-weather validation, prefer post-transition periods while this
specific historical ambiguity is unresolved; even their source timezone is assumed.

Reproduce the delivered provisional preparation:

```sh
.venv/bin/python -m wind_agent.data --turbine-1 artifacts/raw/turbine_1.csv --turbine-2 artifacts/raw/turbine_2.csv --output-dir artifacts/data --hourly --history-timezone Etc/GMT-5 --source-timestamp-convention interval_start --reporting-delay-seconds 0 --training-cutoff 2026-01-31T18:00:00Z --assumption 'Unconfirmed: all source labels use fixed UTC+5 and mark the start of a ten-minute mean; reporting delay is zero and normalization remains unresolved.'
```

In addition to the original audit files, this writes:

- `hourly-history.csv`: the UTC hourly grid within each turbine's observed extent.
  `sample_count=6` means complete; 1–5 means partial; 0 means missing. Incomplete
  targets and observed weather are null, never zero-filled. `data_quality` and
  `is_complete` distinguish the cases. Actual recorded zeros remain zeros.
- `hourly-training.csv`: complete hours satisfying
  `valid_time + reporting_delay <= training_cutoff`. This is eligible data, not a
  fitted model or a validation split. Regenerate with an earlier cutoff for each
  training/validation experiment; do not train on January holdout targets.
- `hourly-report.json`: explicit assumptions, source hashes, runtime versions,
  cutoff, complete/partial/missing counts and limits. `available_at` in the CSV is
  policy-derived, not proof of the original SCADA publication time.

Every complete hourly value is the mean of six disjoint ten-minute observations;
no power sum or conversion to MW/MWh occurs. Source `interval_end` is also supported
by locating each ten-minute interval before its label. The hourly output always
uses `[valid_time - 1h, valid_time)`. Cutoff cannot exceed February 1 00:00 in the
declared history timezone. The full audit table may contain later observations;
fit from `training_frame` / `hourly-training.csv`, with the appropriate experiment cutoff.

| Provisional result | Turbine 1 | Turbine 2 |
| --- | ---: | ---: |
| Complete hours in source | 23,667 | 24,785 |
| Partial hours | 96 | 219 |
| Entirely missing hours | 1,629 | 388 |
| Eligible by first forecast cutoff | 23,666 | 24,784 |

The last January hour `[23:00, 00:00)` is excluded for each turbine at the first
23:00 origin. Independent aggregation matched all complete-hour means. The compact
report is [hourly-quality.json](evidence/hourly-quality.json). February truth and
trained-model metrics are not available from this preparation.

Қазақша: алты толық өлшемнің орташа мәні бір сағаттық қуат болады. Жазбасы толық
емес сағаттар белгіленеді және оқытуға кірмейді. Алғашқы болжам сәтінде әлі
аяқталмаған соңғы сағат та алынып тасталады. Уақыт жорамалдары есепте сақталған.
