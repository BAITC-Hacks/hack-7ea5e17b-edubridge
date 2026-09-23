from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx
import pytest

from wind_agent.weather.gfs import (
    GFSArchiveProvider, ObjectInfo, RawCache, WeatherUnavailable, parse_index,
)

UTC = timezone.utc
ISSUE = datetime(2026, 2, 1, 18, tzinfo=UTC)
RUN = ISSUE - timedelta(hours=6)
TURBINES = {"turbine_1": (43.645150, 78.535604), "turbine_2": (43.643198, 78.538828)}


def listing(run, horizon=48):
    first = int((ISSUE - run).total_seconds() / 3600) + 1
    return {key: ObjectInfo(key, run + timedelta(hours=4), 300, '"' + 'e' * 32 + '"')
            for lead in range(first, first + horizon)
            for key in (GFSArchiveProvider.object_key(run, lead), GFSArchiveProvider.object_key(run, lead) + ".idx")}


@pytest.mark.parametrize("horizon", [24, 48])
def test_selects_complete_available_run_not_latest_initialization(tmp_path, horizon):
    with GFSArchiveProvider(TURBINES, tmp_path) as provider:
        provider.list_run = lambda run: (listing(run, horizon), "a" * 64)
        selected, available, objects, _ = provider.select_run(ISSUE, horizon)
    assert selected == RUN
    assert available == RUN + timedelta(hours=4)
    assert len(objects) == horizon * 2


@pytest.mark.parametrize("suffix", ["", ".idx"])
def test_one_late_object_disqualifies_whole_run(tmp_path, suffix):
    def list_run(run):
        objects = listing(run)
        if run == RUN:
            key = GFSArchiveProvider.object_key(run, 30) + suffix
            objects[key] = ObjectInfo(key, ISSUE + timedelta(minutes=1), 300, '"' + 'e' * 32 + '"')
        return objects, "a" * 64
    with GFSArchiveProvider(TURBINES, tmp_path) as provider:
        provider.list_run = list_run
        selected, *_ = provider.select_run(ISSUE, 48)
    assert selected == RUN - timedelta(hours=6)


def test_missing_horizon_does_not_silently_fill(tmp_path):
    with GFSArchiveProvider(TURBINES, tmp_path) as provider:
        provider.list_run = lambda run: ({}, "a" * 64)
        with pytest.raises(WeatherUnavailable, match="No complete"):
            provider.fetch(ISSUE, 48, list(TURBINES))


def test_multipart_etag_cannot_prove_upload_completion(tmp_path):
    def list_run(run):
        objects = listing(run)
        key = next(iter(objects))
        item = objects[key]
        objects[key] = ObjectInfo(key, item.modified, item.size, '"' + 'e' * 32 + '-5"')
        return objects, "a" * 64
    with GFSArchiveProvider(TURBINES, tmp_path) as provider:
        provider.list_run = list_run
        with pytest.raises(WeatherUnavailable, match="No complete"):
            provider.select_run(ISSUE, 48)


def test_index_rejects_wrong_run_and_accumulated_fields():
    good = b"1:0:d=2026020112:UGRD:100 m above ground:24 hour fcst:\n2:100:d=2026020112:VGRD:100 m above ground:24 hour fcst:\n3:200:d=2026020112:TMP:2 m above ground:24 hour fcst:\n"
    assert parse_index(good, 300, RUN, 24) == {"u100": (0, 99), "v100": (100, 199), "t2m": (200, 299)}
    for bad in (good.replace(b"d=2026020112", b"d=2026020212"), good.replace(b"24 hour fcst", b"0-24 hour ave fcst"), good.splitlines()[0]):
        with pytest.raises(WeatherUnavailable):
            parse_index(bad, 300, RUN, 24)


def test_retry_is_bounded_and_permanent_errors_are_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("wind_agent.weather.gfs.time.sleep", lambda _: None)
    for status, expected in ((503, 3), (404, 1)):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(status)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            cache = RawCache(tmp_path, client, retries=2, offline=False)
            with pytest.raises(WeatherUnavailable):
                cache.get("https://example.invalid/archive")
        assert len(calls) == expected


def test_cache_integrity_and_offline_reuse(tmp_path):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=b"real response bytes")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        cache = RawCache(tmp_path, client, 0, False)
        first = cache.get("https://example.invalid/archive")
        cache.offline = True
        assert cache.get("https://example.invalid/archive").body == first.body
        assert len(calls) == 1
        raw_path = tmp_path / "raw" / (first.metadata["sha256"] + ".bin")
        raw_path.write_bytes(b"changed")
        with pytest.raises(WeatherUnavailable, match="Corrupted"):
            cache.get("https://example.invalid/archive")


def test_range_response_cannot_download_entire_world_file(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x"))) as client:
        cache = RawCache(tmp_path, client, 0, False)
        with pytest.raises(WeatherUnavailable, match="Expected HTTP 206"):
            cache.get("https://example.invalid/archive", {"Range": "bytes=0-1"})


@pytest.mark.parametrize("horizon", [24, 48])
def test_complete_hourly_fetch_and_raw_provenance(tmp_path, monkeypatch, horizon):
    modified = "Sun, 01 Feb 2026 16:00:00 GMT"
    def respond(request):
        lead = int(request.url.path.split(".f")[-1].split(".")[0])
        headers = {"Last-Modified": modified, "ETag": '"' + 'e' * 32 + '"'}
        if request.url.path.endswith(".idx"):
            lines = [f"{i + 1}:{i * 100}:d=2026020112:{p}:{height}:{lead} hour fcst:"
                     for i, (p, height) in enumerate((("UGRD", "100 m above ground"), ("VGRD", "100 m above ground"), ("TMP", "2 m above ground")))]
            return httpx.Response(200, headers=headers, content=("\n".join(lines) + "\n").encode())
        start, end = map(int, request.headers["range"].split("=")[1].split("-"))
        headers["Content-Range"] = f"bytes {start}-{end}/300"
        return httpx.Response(206, headers=headers, content=bytes([start // 100]) * 100)
    def list_run(run):
        objects = listing(run, horizon)
        for key, info in list(objects.items()):
            if key.endswith(".idx"):
                req = httpx.Request("GET", "https://example.invalid/" + key)
                objects[key] = ObjectInfo(key, info.modified, len(respond(req).content), info.etag)
        return objects, "a" * 64
    def decode(raw, field, latitude, longitude, run, lead):
        return {"u100": 3., "v100": 4., "t2m": 273.15}[field], {"latitude": 43.75, "longitude": 78.5, "distance_km": 12.}
    monkeypatch.setattr("wind_agent.weather.gfs.decode_point", decode)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with GFSArchiveProvider(TURBINES, tmp_path, client=client) as provider:
            provider.list_run = list_run
            records = provider.fetch(ISSUE, horizon, list(TURBINES))
            second = provider.fetch(ISSUE, horizon, list(TURBINES))
    assert records == second
    assert len(records) == horizon * len(TURBINES)
    for turbine in TURBINES:
        rows = [row for row in records if row.turbine_id == turbine]
        assert [row.valid_time for row in rows] == [ISSUE + timedelta(hours=h) for h in range(1, horizon + 1)]
    for record in records:
        assert record.forecast_available_at <= ISSUE
        assert record.wind_speed_ms == 5 and record.temperature_c == 0
        assert record.model_run_time == RUN
        raw = (tmp_path / "raw" / (record.raw_sha256 + ".bin")).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record.raw_sha256
        manifest = json.loads((tmp_path / "extractions" / json.loads(record.source_request)["extraction_manifest"]).read_text())
        assert set(manifest["fields"]) == {"u100", "v100", "t2m"}
