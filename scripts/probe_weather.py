"""Reproducible NOAA archive probe. Metadata-only by default; --fetch downloads."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wind_agent.weather import GFSArchiveProvider
from wind_agent.weather.gfs import BASE_URL, decode_point, parse_index

TURBINES = {"turbine_1": (43.645150, 78.535604), "turbine_2": (43.643198, 78.538828)}


def sample_fields(provider, run, objects, lead=24):
    """A small real GRIB check for source qualification, not a full forecast."""
    key = provider.object_key(run, lead)
    if key not in objects:
        # Stay within the same complete horizon already admitted by select_run.
        key = min(key for key in objects if not key.endswith(".idx"))
        lead = int(key[-3:])
    info, index_info = objects[key], objects[key + ".idx"]
    index = provider.cache.get(BASE_URL + "/" + index_info.key, {"If-Match": index_info.etag})
    provider._check_object(index, index_info)
    fields = {}
    for field, (start, end) in parse_index(index.body, info.size, run, lead).items():
        response = provider.cache.get(BASE_URL + "/" + key,
                                      {"Range": f"bytes={start}-{end}", "If-Match": info.etag})
        provider._check_object(response, info, (start, end))
        points = {}
        for turbine_id, (latitude, longitude) in TURBINES.items():
            value, grid = decode_point(response.body, field, latitude, longitude, run, lead)
            points[turbine_id] = {"value": value, "grid": grid}
        fields[field] = {"http": response.metadata, "points": points}
    return {"model_run_time": run.isoformat(), "lead_hours": lead,
            "index": index.metadata, "fields": fields}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", nargs="+", default=["2026-01-31", "2026-02-01", "2026-02-14", "2026-02-28"])
    parser.add_argument("--hour-utc", type=int, default=18)
    parser.add_argument("--horizon", type=int, choices=(24, 48), default=48)
    parser.add_argument("--february", action="store_true", help="Audit 29 daily issues Jan 31 through Feb 28, 2026 (hour-end coverage)")
    parser.add_argument("--sample-fields", action="store_true", help="Decode three fields at lead24 of each selected run")
    parser.add_argument("--fetch", action="store_true", help="Download and decode the entire hourly horizon for BOTH turbines")
    parser.add_argument("--cache", type=Path, default=Path("artifacts/weather-cache"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/weather-probe.json"))
    args = parser.parse_args()
    if args.february:
        args.dates = [(datetime(2026, 1, 31) + timedelta(days=day)).date().isoformat() for day in range(29)]
    report = {"retrieved_at": datetime.now(timezone.utc).isoformat(),
              "provider": "NOAA operational GFS, AWS Open Data, 0.25 degree",
              "availability_basis": "max S3 LastModified of every required single-part GRIB and index object; multipart ETags rejected; not original NOAA publication timestamp",
              "feature_time_basis": "instant_at_end",
              "turbines": TURBINES, "checks": []}
    with GFSArchiveProvider(TURBINES, args.cache) as provider:
        def check_date(date):
            issue = datetime.fromisoformat(date).replace(hour=args.hour_utc, tzinfo=timezone.utc)
            run, available, objects, listing_sha = provider.select_run(issue, args.horizon)
            check = {"issue_time": issue.isoformat(), "model_run_time": run.isoformat(),
                     "forecast_available_at": available.isoformat(), "horizon_hours": args.horizon,
                     "object_count": len(objects), "listing_sha256": listing_sha,
                     "objects": [{"key": item.key, "last_modified": item.modified.isoformat(),
                                  "size": item.size, "etag": item.etag} for item in objects.values()]}
            if args.sample_fields:
                check["sample_fields"] = sample_fields(provider, run, objects)
            if args.fetch:
                records = provider.fetch(issue, args.horizon, list(TURBINES))
                check["records"] = [record.model_dump(mode="json") for record in records]
                check["record_count"] = len(records)
            print(f"{date}: run={run.isoformat()} available={available.isoformat()} objects={len(objects)}"
                  + (f" rows={check['record_count']}" if args.fetch else ""), flush=True)
            return check
        # Avoid nested parallel bulk downloads; metadata audits use four workers.
        with ThreadPoolExecutor(max_workers=1 if args.fetch else 4) as executor:
            report["checks"] = list(executor.map(check_date, args.dates))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Evidence: {args.output}")


if __name__ == "__main__":
    main()
