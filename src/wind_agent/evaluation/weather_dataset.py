"""Collect a resumable, genuinely archived NOAA weather training dataset.

Each origin keeps complete WeatherRecord provenance. Original field GRIB bytes,
HTTP metadata, listings and extraction manifests remain in the provider cache.
Only a concatenated duplicate bundle may be removed, after its reconstruction
and SHA256 have been verified against the record and existing bundle.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any, Iterable
import uuid

from wind_agent.contracts import WeatherRecord
from wind_agent.weather.gfs import GFSArchiveProvider

UTC = timezone.utc
TURBINES = {"turbine_1": (43.645150, 78.535604),
            "turbine_2": (43.643198, 78.538828)}
HORIZON = 48
SCHEMA = "1.0"
POLICY = ("Keep original field GRIB bytes, HTTP responses, S3 listings and extraction "
          "manifests. Remove only redundant concatenated bundle .bin after verifying "
          "reconstruction from the retained original fields and the bundle SHA256.")


@dataclass(frozen=True)
class WeatherOrigin:
    issue_time: datetime
    split: str

    @property
    def filename(self) -> str:
        return self.issue_time.strftime("%Y%m%dT%H%M%SZ_48h.json")


def planned_origins() -> list[WeatherOrigin]:
    """60 origins: 30 training, 14 tuning, 15 holdout and one integration."""
    result = []
    for split, start, end in (
        ("train", "2025-11-30", "2025-12-29"),
        ("tune", "2025-12-31", "2026-01-13"),
        ("holdout", "2026-01-15", "2026-01-29"),
        ("integration", "2026-01-31", "2026-01-31"),
    ):
        day = datetime.fromisoformat(start).replace(hour=18, tzinfo=UTC)
        last = datetime.fromisoformat(end).replace(hour=18, tzinfo=UTC)
        while day <= last:
            result.append(WeatherOrigin(day, split))
            day += timedelta(days=1)
    return result


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return _sha(raw)


def validate_origin(payload: dict[str, Any], origin: WeatherOrigin) -> list[WeatherRecord]:
    """Reject incomplete horizons, fixtures, later weather and incorrect origins."""
    if (payload.get("schema_version") != SCHEMA or
            payload.get("issue_time") != origin.issue_time.isoformat() or
            payload.get("horizon_hours") != HORIZON or payload.get("split") != origin.split):
        raise ValueError("Weather origin envelope differs from the requested plan")
    records = [WeatherRecord.model_validate(row) for row in payload["records"]]
    expected = {(turbine, origin.issue_time + timedelta(hours=lead))
                for turbine in TURBINES for lead in range(1, HORIZON + 1)}
    if len(records) != len(expected) or {(r.turbine_id, r.valid_time) for r in records} != expected:
        raise ValueError("Weather origin must contain both complete 48-hour turbine horizons")
    if len({r.model_run_time for r in records}) != 1:
        raise ValueError("An origin must use one frozen operational forecast run")
    for row in records:
        if (row.provenance_kind != "operational_archive" or
                row.weather_model != "gfs_0p25" or
                row.forecast_available_at > origin.issue_time or
                row.model_run_time > origin.issue_time or row.wind_height_m != 100):
            raise ValueError("Weather origin lacks admissible operational as-of provenance")
        if (row.latitude, row.longitude) != TURBINES[row.turbine_id]:
            raise ValueError("Weather record coordinates do not match the real turbines")
        request = json.loads(row.source_request)
        if request.get("feature_time_basis") != "instant_at_end":
            raise ValueError("Weather features must be instantaneous at the hour end")
    return records


def compact_redundant_bundles(cache_dir: Path, records: Iterable[WeatherRecord]) -> dict[str, int]:
    """Verify retained originals before deleting this collection's duplicate bundles."""
    removed, removed_bytes, checked = 0, 0, set()
    raw_dir = cache_dir / "raw"
    for record in records:
        if record.raw_sha256 in checked:
            continue
        checked.add(record.raw_sha256)
        request = json.loads(record.source_request)
        manifest_name = request["extraction_manifest"]
        if Path(manifest_name).name != manifest_name:
            raise ValueError("Unsafe extraction manifest filename")
        manifest_bytes = (cache_dir / "extractions" / manifest_name).read_bytes()
        if _sha(manifest_bytes) + ".json" != manifest_name:
            raise ValueError("Extraction manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        order = request["raw_bundle_order"]
        if order != ["t2m", "u100", "v100"] or set(manifest["fields"]) != set(order):
            raise ValueError("Unexpected bundle fields or order")
        digest = hashlib.sha256()
        originals = set()
        total_bytes = 0
        for name in order:
            field_sha = manifest["fields"][name]["sha256"]
            if len(field_sha) != 64 or any(c not in "0123456789abcdef" for c in field_sha):
                raise ValueError("Invalid original field hash")
            originals.add(field_sha)
            raw = (raw_dir / (field_sha + ".bin")).read_bytes()
            if _sha(raw) != field_sha:
                raise ValueError("Original field bytes fail SHA256 verification")
            digest.update(raw)
            total_bytes += len(raw)
        if digest.hexdigest() != record.raw_sha256:
            raise ValueError("Reconstructed bundle fails WeatherRecord SHA256 verification")
        bundle_path = raw_dir / (record.raw_sha256 + ".bin")
        if bundle_path.exists():
            if record.raw_sha256 in originals:
                raise ValueError("Refusing to delete an original field")
            bundle = bundle_path.read_bytes()
            if len(bundle) != total_bytes or _sha(bundle) != record.raw_sha256:
                raise ValueError("Existing bundle fails SHA256 verification")
            bundle_path.unlink()
            removed += 1
            removed_bytes += len(bundle)
    return {"verified_bundle_count": len(checked), "removed_bundle_count": removed,
            "removed_redundant_bytes": removed_bytes}


def load_weather_dataset(output_dir: str | Path,
                         splits: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Return validated rows with issue_time, split and forecast_horizon_hours.

    Datetimes remain ISO8601 strings; downstream code must parse them as UTC.
    Collection can still be running: only atomically completed origin files load.
    Each (issue_time, turbine_id, valid_time) is a distinct forecast example.
    """
    selected = set(splits) if splits is not None else None
    manifest_path = Path(output_dir) / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("Weather dataset has no completion and integrity manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    completed = {entry["issue_time"]: entry for entry in manifest["origins"]
                 if entry["status"] == "completed"}
    rows = []
    for origin in planned_origins():
        path = Path(output_dir) / "origins" / origin.filename
        entry = completed.get(origin.issue_time.isoformat())
        if (selected is not None and origin.split not in selected) or entry is None:
            continue
        raw = path.read_bytes()
        if _sha(raw) != entry["sha256"]:
            raise ValueError("Weather origin file fails manifest SHA256 verification")
        payload = json.loads(raw)
        for record in validate_origin(payload, origin):
            rows.append({**record.model_dump(mode="json"),
                         "issue_time": origin.issue_time.isoformat(),
                         "forecast_horizon_hours": HORIZON, "split": origin.split})
    return rows


def collect_weather_dataset(cache_dir: str | Path, output_dir: str | Path,
                            manifest_path: str | Path,
                            origins: Iterable[WeatherOrigin] | None = None,
                            origin_workers: int = 2, hour_workers: int = 4,
                            offline: bool = False) -> dict[str, Any]:
    """Resume successful origins; retain explicit failed and pending plan entries."""
    if not 1 <= origin_workers <= 2 or not 1 <= hour_workers <= 4:
        raise ValueError("At most two origins and four per-origin workers are permitted")
    cache_dir, output_dir, manifest_path = Path(cache_dir), Path(output_dir), Path(manifest_path)
    selected = list(origins) if origins is not None else planned_origins()
    plan = {o.issue_time.isoformat(): o for o in planned_origins()}
    if any(plan.get(o.issue_time.isoformat()) != o for o in selected):
        raise ValueError("Requested origins are outside the declared temporal plan")
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    entries = {key: {"issue_time": key, "split": o.split, "status": "pending"}
               for key, o in plan.items()}
    for row in old.get("origins", []):
        if row["issue_time"] in entries:
            entries[row["issue_time"]] = row
    started = datetime.now(UTC).isoformat()

    def save() -> dict[str, Any]:
        statuses = [row["status"] for row in entries.values()]
        report = {"schema_version": SCHEMA, "started_at": old.get("started_at", started),
                  "updated_at": datetime.now(UTC).isoformat(),
                  "provider": "NOAA operational GFS via AWS Open Data",
                  "weather_model": "gfs_0p25", "feature_time_basis": "instant_at_end",
                  "wind_height_m": 100, "temperature_height_m": 2,
                  "horizon_hours": HORIZON, "turbines": TURBINES,
                  "cache_dir": str(cache_dir), "output_dir": str(output_dir),
                  "cache_retention_policy": POLICY,
                  "availability_basis": "max S3 LastModified of all required single-part GRIB and index objects; not original NOAA publication time",
                  "planned_origins": len(entries),
                  "completed_origins": statuses.count("completed"),
                  "failed_origins": statuses.count("failed"),
                  "pending_origins": statuses.count("pending") + statuses.count("running"),
                  "origins": [entries[key] for key in sorted(entries)]}
        _write_json(manifest_path, report)
        _write_json(output_dir / "manifest.json", report)
        return report

    def one(origin: WeatherOrigin) -> dict[str, Any]:
        tick = time.monotonic()
        path = output_dir / "origins" / origin.filename
        cache_dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raw = path.read_bytes()
            previous_hash = entries[origin.issue_time.isoformat()].get("sha256")
            if previous_hash is not None and _sha(raw) != previous_hash:
                raise ValueError("Completed origin changed since the preceding manifest")
            payload = json.loads(raw)
            records = validate_origin(payload, origin)
            digest, resumed = _sha(raw), True
        else:
            if shutil.disk_usage(cache_dir).free < 750_000_000:
                raise RuntimeError("Insufficient free disk for two archived-weather origins")
            with GFSArchiveProvider(TURBINES, cache_dir, workers=hour_workers,
                                    retries=3, timeout_seconds=45, offline=offline) as provider:
                records = provider.fetch(origin.issue_time, HORIZON, list(TURBINES))
            payload = {"schema_version": SCHEMA, "issue_time": origin.issue_time.isoformat(),
                       "horizon_hours": HORIZON, "split": origin.split,
                       "records": [record.model_dump(mode="json") for record in records]}
            validate_origin(payload, origin)
            # Compact before publishing: every completed dataset has retained originals.
            compaction = compact_redundant_bundles(cache_dir, records)
            digest = _write_json(path, payload)
            resumed = False
        if resumed:
            compaction = compact_redundant_bundles(cache_dir, records)
        return {"issue_time": origin.issue_time.isoformat(), "split": origin.split,
                "status": "completed", "path": str(path), "sha256": digest,
                "record_count": len(records), "resumed": resumed,
                "model_run_times": sorted({r.model_run_time.isoformat() for r in records}),
                "forecast_available_at": max(r.forecast_available_at for r in records).isoformat(),
                "valid_time_min": min(r.valid_time for r in records).isoformat(),
                "valid_time_max": max(r.valid_time for r in records).isoformat(),
                "all_weather_available_by_issue": True,
                "elapsed_seconds": round(time.monotonic() - tick, 3),
                "compaction": compaction}

    for origin in selected:
        entry = entries[origin.issue_time.isoformat()]
        if entry["status"] != "completed":
            entry["status"] = "running"
    save()
    with ThreadPoolExecutor(max_workers=origin_workers) as executor:
        futures = {executor.submit(one, origin): origin for origin in selected}
        for future in as_completed(futures):
            origin = futures[future]
            try:
                entry = future.result()
            except Exception as error:
                entry = {"issue_time": origin.issue_time.isoformat(), "split": origin.split,
                         "status": "failed", "error_type": type(error).__name__,
                         "error": str(error), "failed_at": datetime.now(UTC).isoformat()}
            entries[origin.issue_time.isoformat()] = entry
            report = save()
            print(json.dumps({"origin": origin.issue_time.isoformat(), **entry,
                              "progress": f"{report['completed_origins']}/{report['planned_origins']}"}),
                  flush=True)
    return save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("artifacts/weather-training/cache"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/weather-training"))
    parser.add_argument("--manifest", type=Path,
                        default=Path("docs/evidence/weather-training-manifest.json"))
    parser.add_argument("--only-date", action="append", help="UTC origin date; repeatable")
    parser.add_argument("--splits", nargs="+", choices=("train", "tune", "holdout", "integration"))
    parser.add_argument("--origin-workers", type=int, default=2)
    parser.add_argument("--hour-workers", type=int, default=4)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    origins = [o for o in planned_origins()
               if (not args.only_date or o.issue_time.date().isoformat() in args.only_date)
               and (not args.splits or o.split in args.splits)]
    if not origins:
        parser.error("No planned origins match the selection")
    report = collect_weather_dataset(args.cache, args.output, args.manifest, origins,
                                     args.origin_workers, args.hour_workers, args.offline)
    return int(report["failed_origins"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
