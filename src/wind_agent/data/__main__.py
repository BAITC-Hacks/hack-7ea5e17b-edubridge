"""Audit both official inputs: python -m wind_agent.data --help."""

import argparse
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

from .loader import load_history
from .hourly import HistorySemantics, prepare_hourly
from .sources import SOURCES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turbine-1", type=Path, required=True)
    parser.add_argument("--turbine-2", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/data"))
    parser.add_argument("--hourly", action="store_true", help="also prepare complete hourly targets")
    parser.add_argument("--history-timezone", help="explicit IANA timezone assumption")
    parser.add_argument("--source-timestamp-convention", choices=("interval_start", "interval_end"))
    parser.add_argument("--reporting-delay-seconds", type=int)
    parser.add_argument("--training-cutoff", help="aware timestamp; no later than February 1")
    parser.add_argument("--assumption", action="append", help="assumption statement; repeatable")
    args = parser.parse_args()
    hourly_arguments = (
        args.history_timezone, args.source_timestamp_convention,
        args.reporting_delay_seconds, args.training_cutoff, args.assumption,
    )
    if args.hourly and any(value is None for value in hourly_arguments):
        parser.error("--hourly requires timezone, interval convention, delay, cutoff and assumption")
    if not args.hourly and any(value is not None for value in hourly_arguments):
        parser.error("source-clock and cutoff arguments require --hourly")
    paths = {"turbine_1": args.turbine_1, "turbine_2": args.turbine_2}
    if args.turbine_1.resolve() == args.turbine_2.resolve():
        parser.error("turbines must have distinct source files")
    destination = args.output_dir
    outputs = [destination / "history.csv", destination / "quality-report.json"]
    if args.hourly:
        outputs.extend(destination / name for name in (
            "hourly-history.csv", "hourly-training.csv", "hourly-report.json",
        ))
    if {path.resolve() for path in paths.values()} & {path.resolve() for path in outputs}:
        parser.error("output paths must not overwrite source files")
    if any(output.exists() and output.samefile(path) for output in outputs for path in paths.values()):
        parser.error("output files must not be hard links to source files")
    for first, second in combinations(outputs, 2):
        if first.resolve() == second.resolve() or (
            first.exists() and second.exists() and first.samefile(second)
        ):
            parser.error("all output files must be distinct")

    # Verify both files completely before writing any output.
    loaded = {
        turbine: load_history(path, turbine, expected_sha256=SOURCES[turbine]["sha256"])
        for turbine, path in paths.items()
    }
    report = {
        "schema_version": "1.0",
        "sources": SOURCES,
        "files": [item.report for item in loaded.values()],
        "transforms": "Column names and row order only; naive times, no filling or fitting.",
    }
    history = pd.concat([item.frame for item in loaded.values()], ignore_index=True)
    hourly = None
    if args.hourly:
        semantics = HistorySemantics(
            history_timezone=args.history_timezone,
            source_timestamp_convention=args.source_timestamp_convention,
            target_reporting_delay_seconds=args.reporting_delay_seconds,
            assumptions=tuple(args.assumption),
        )
        hourly = prepare_hourly(history, semantics=semantics, training_cutoff=args.training_cutoff)
        hourly.report["source_sha256"] = {
            turbine: item.report["source_sha256"] for turbine, item in loaded.items()
        }
    destination.mkdir(parents=True, exist_ok=True)
    history.to_csv(outputs[0], index=False, date_format="%Y-%m-%d %H:%M:%S")
    outputs[1].write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    summary = {
        "history": str(outputs[0]), "quality_report": str(outputs[1]),
        "rows": len(history), "timestamp_timezone": "unknown",
    }
    if hourly is not None:
        hourly.frame.to_csv(outputs[2], index=False)
        hourly.training_frame.to_csv(outputs[3], index=False)
        outputs[4].write_text(
            json.dumps(hourly.report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        summary.update({
            "hourly_rows": len(hourly.frame), "training_hours": len(hourly.training_frame),
            "hourly_report": str(outputs[4]),
        })
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
