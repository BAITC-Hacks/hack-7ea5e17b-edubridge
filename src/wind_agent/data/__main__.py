"""Audit both official inputs: python -m wind_agent.data --help."""

import argparse
import json
from pathlib import Path

import pandas as pd

from .loader import load_history
from .sources import SOURCES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turbine-1", type=Path, required=True)
    parser.add_argument("--turbine-2", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/data"))
    args = parser.parse_args()
    paths = {"turbine_1": args.turbine_1, "turbine_2": args.turbine_2}
    if args.turbine_1.resolve() == args.turbine_2.resolve():
        parser.error("turbines must have distinct source files")
    destination = args.output_dir
    outputs = (destination / "history.csv", destination / "quality-report.json")
    if {path.resolve() for path in paths.values()} & {path.resolve() for path in outputs}:
        parser.error("output paths must not overwrite source files")
    if any(output.exists() and output.samefile(path) for output in outputs for path in paths.values()):
        parser.error("output files must not be hard links to source files")
    if outputs[0].exists() and outputs[1].exists() and outputs[0].samefile(outputs[1]):
        parser.error("history and quality report must be distinct files")

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
    destination.mkdir(parents=True, exist_ok=True)
    history = pd.concat([item.frame for item in loaded.values()], ignore_index=True)
    history.to_csv(outputs[0], index=False, date_format="%Y-%m-%d %H:%M:%S")
    outputs[1].write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "history": str(outputs[0]), "quality_report": str(outputs[1]),
        "rows": len(history), "timestamp_timezone": "unknown",
    }))


if __name__ == "__main__":
    main()
