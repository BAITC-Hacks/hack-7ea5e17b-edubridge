# SCADA source ingestion — participant 2

Implemented step: read and validate the two original turbine CSVs, preserve their
observations and produce a reproducible quality report. This is not model training
or hourly target preparation. The [model contract](MODEL_IO_CONTRACT.md) describes
the separate forecast interface.

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

Verified on Python 3.11.16/macOS with the repository's pinned dependencies: 125
project tests passed (46 data tests), Ruff passed, and the official-source CLI
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

## Next step: hourly targets

Before hourly preparation/fitting, establish or explicitly record assumptions for
source timezone, original ten-minute interval labels and reporting delay. The
agreed UTC `hour_end` output convention does not establish these source semantics.
Then aggregate complete groups of six measurements into hourly mean power, retain
coverage flags, and apply `interval_end + reporting_delay <= training_cutoff`.
No conversion to MW/MWh is justified by the supplied data.

Қазақша: бұл модуль деректі оқып, жарамдылығын тексереді. Нақты `0` сақталады;
жоқ жазба `0` болып толтырылмайды. Келесі қадамда уақыт жорамалдарын ашық бекітіп,
10 минуттық алты өлшемнен бір сағаттың орташа қуатын есептейміз.
