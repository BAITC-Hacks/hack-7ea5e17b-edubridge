"""Collection integrity tests use temporary local bytes, never training artifacts."""

from collections import Counter
from datetime import timedelta
import hashlib
import json

import pytest

from wind_agent.contracts import WeatherRecord
from wind_agent.evaluation.weather_dataset import (
    TURBINES, compact_redundant_bundles, load_weather_dataset,
    planned_origins, validate_origin,
)


def payload_for(origin):
    records = []
    for turbine, (latitude, longitude) in TURBINES.items():
        for lead in range(1, 49):
            records.append(WeatherRecord(
                turbine_id=turbine, latitude=latitude, longitude=longitude,
                provider="NOAA GFS via AWS Open Data", weather_model="gfs_0p25",
                model_run_time=origin.issue_time - timedelta(hours=6),
                forecast_available_at=origin.issue_time - timedelta(hours=2),
                availability_basis="test-only metadata", retrieved_at=origin.issue_time,
                valid_time=origin.issue_time + timedelta(hours=lead), lead_hours=lead + 6,
                wind_speed_ms=5.0, wind_height_m=100.0, temperature_c=2.0,
                source_request=json.dumps({"feature_time_basis": "instant_at_end"}),
                raw_sha256="a" * 64, provenance_kind="operational_archive",
            ).model_dump(mode="json"))
    return {"schema_version": "1.0", "issue_time": origin.issue_time.isoformat(),
            "horizon_hours": 48, "split": origin.split, "records": records}


def test_temporal_plan_has_declared_origin_counts_and_purge_gaps():
    origins = planned_origins()
    assert Counter(o.split for o in origins) == {
        "train": 30, "tune": 14, "holdout": 15, "integration": 1}
    assert all(o.issue_time.hour == 18 and o.issue_time.utcoffset() == timedelta(0)
               for o in origins)
    assert not {"2025-12-30", "2026-01-14", "2026-01-30"} & {
        o.issue_time.date().isoformat() for o in origins}


@pytest.mark.parametrize("mutation", ["late", "fixture", "missing", "duplicate", "mean"])
def test_origin_rejects_unavailable_synthetic_incomplete_and_wrong_time_basis(mutation):
    origin = planned_origins()[-1]
    payload = payload_for(origin)
    if mutation == "late":
        payload["records"][0]["forecast_available_at"] = (
            origin.issue_time + timedelta(seconds=1)).isoformat()
    elif mutation == "fixture":
        payload["records"][0]["provenance_kind"] = "fixture"
    elif mutation == "missing":
        payload["records"].pop()
    elif mutation == "duplicate":
        payload["records"][-1] = payload["records"][0]
    else:
        payload["records"][0]["source_request"] = json.dumps({"feature_time_basis": "mean"})
    with pytest.raises(ValueError):
        validate_origin(payload, origin)


def cached_record(tmp_path):
    record = validate_origin(payload_for(planned_origins()[-1]), planned_origins()[-1])[0]
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    fields = {}
    bundle = b""
    for name in ["t2m", "u100", "v100"]:
        raw = ("test-only field bytes:" + name).encode()
        digest = hashlib.sha256(raw).hexdigest()
        fields[name] = {"sha256": digest}
        (raw_dir / (digest + ".bin")).write_bytes(raw)
        bundle += raw
    manifest_bytes = json.dumps({"fields": fields}).encode()
    manifest_name = hashlib.sha256(manifest_bytes).hexdigest() + ".json"
    (tmp_path / "extractions").mkdir()
    (tmp_path / "extractions" / manifest_name).write_bytes(manifest_bytes)
    bundle_sha = hashlib.sha256(bundle).hexdigest()
    (raw_dir / (bundle_sha + ".bin")).write_bytes(bundle)
    return record.model_copy(update={"raw_sha256": bundle_sha, "source_request": json.dumps({
        "raw_bundle_order": ["t2m", "u100", "v100"], "extraction_manifest": manifest_name})}), fields


def test_compaction_preserves_originals_and_verifiable_reconstruction(tmp_path):
    record, fields = cached_record(tmp_path)
    result = compact_redundant_bundles(tmp_path, [record, record])
    assert result["removed_bundle_count"] == 1
    assert not (tmp_path / "raw" / (record.raw_sha256 + ".bin")).exists()
    assert all((tmp_path / "raw" / (f["sha256"] + ".bin")).exists() for f in fields.values())
    assert compact_redundant_bundles(tmp_path, [record])["removed_bundle_count"] == 0


def test_compaction_never_deletes_bundle_when_original_is_corrupt(tmp_path):
    record, fields = cached_record(tmp_path)
    (tmp_path / "raw" / (fields["u100"]["sha256"] + ".bin")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Original field"):
        compact_redundant_bundles(tmp_path, [record])
    assert (tmp_path / "raw" / (record.raw_sha256 + ".bin")).exists()


def test_loader_includes_issue_provenance_and_only_requested_splits(tmp_path):
    origin = planned_origins()[-1]
    (tmp_path / "origins").mkdir()
    raw = json.dumps(payload_for(origin)).encode()
    (tmp_path / "origins" / origin.filename).write_bytes(raw)
    (tmp_path / "manifest.json").write_text(json.dumps({"origins": [{
        "issue_time": origin.issue_time.isoformat(), "status": "completed",
        "sha256": hashlib.sha256(raw).hexdigest()}]}))
    records = load_weather_dataset(tmp_path, ["integration"])
    assert len(records) == 96
    assert {r["issue_time"] for r in records} == {origin.issue_time.isoformat()}
    assert {r["forecast_horizon_hours"] for r in records} == {48}
    assert load_weather_dataset(tmp_path, ["train"]) == []
    (tmp_path / "origins" / origin.filename).write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="SHA256"):
        load_weather_dataset(tmp_path, ["integration"])
