"""Read NOAA's operational GFS archive, never analysis/reanalysis substitutes.

S3 LastModified is an availability bound for admitted single-part objects, not
the original NOAA publication timestamp. Every required GRIB/index must qualify.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlencode
import uuid
import xml.etree.ElementTree as ET

import httpx

from wind_agent.contracts import WeatherRecord

UTC = timezone.utc
BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
FIELDS = {"u100": ("UGRD", "100 m above ground"),
          "v100": ("VGRD", "100 m above ground"),
          "t2m": ("TMP", "2 m above ground")}
DECODE_LOCK = threading.Lock()
CACHE_WRITE_LOCK = threading.Lock()


class WeatherUnavailable(RuntimeError):
    """No complete admissible operational forecast could be obtained."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Weather times must include a timezone")
    return value.astimezone(UTC)


def _iso(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    # Identical selected fields can occur at several forecast hours; avoid
    # concurrent replace of the same content-addressed file on Windows.
    with CACHE_WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() == data:
            return
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    modified: datetime
    size: int
    etag: str


@dataclass(frozen=True)
class CachedResponse:
    body: bytes
    metadata: dict[str, Any]


class RawCache:
    """Content-addressed bytes plus exact requests, response headers and SHA256."""

    def __init__(self, path: Path, client: httpx.Client, retries: int, offline: bool):
        self.path, self.client, self.retries, self.offline = path, client, retries, offline

    def get(self, url: str, headers: dict[str, str] | None = None,
            *, refresh: bool = False, limit: int = 12_000_000) -> CachedResponse:
        headers = headers or {}
        request = {"method": "GET", "url": url, "headers": headers}
        key = _sha(_dump(request).encode())
        meta_path = self.path / "requests" / (key + ".json")
        if meta_path.exists() and (not refresh or self.offline):
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            body = (self.path / "raw" / (metadata["sha256"] + ".bin")).read_bytes()
            if _sha(body) != metadata["sha256"]:
                raise WeatherUnavailable(f"Corrupted raw cache: {meta_path}")
            if metadata["request"] != request:
                raise WeatherUnavailable(f"Cache request mismatch: {meta_path}")
            return CachedResponse(body, metadata)
        if self.offline:
            raise WeatherUnavailable(f"Offline cache miss: {url}")
        for attempt in range(self.retries + 1):
            try:
                with self.client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    expected = 206 if "Range" in headers else 200
                    if response.status_code != expected:
                        raise WeatherUnavailable(f"Expected HTTP {expected}, got {response.status_code}: {url}")
                    if int(response.headers.get("content-length", "0")) > limit:
                        raise WeatherUnavailable(f"Unexpectedly large response: {url}")
                    chunks, count = [], 0
                    for chunk in response.iter_bytes():
                        count += len(chunk)
                        if count > limit:
                            raise WeatherUnavailable(f"Response exceeds byte limit: {url}")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    digest = _sha(body)
                    metadata = {"request": request, "status": response.status_code,
                                "response_headers": dict(response.headers),
                                "retrieved_at": datetime.now(UTC).isoformat(),
                                "sha256": digest, "bytes": len(body)}
                _atomic(self.path / "raw" / (digest + ".bin"), body)
                _atomic(meta_path, _dump(metadata).encode())
                return CachedResponse(body, metadata)
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                transient = (isinstance(error, httpx.TransportError) or
                             error.response.status_code in (408, 429, 500, 502, 503, 504))
                if not transient or attempt >= self.retries:
                    raise WeatherUnavailable(f"Archive request failed: {url}: {error}") from error
                time.sleep(min(2 ** attempt, 4))
        raise AssertionError("Unreachable retry state")


def parse_index(raw: bytes, object_size: int, model_run: datetime,
                lead: int) -> dict[str, tuple[int, int]]:
    """Require exactly one instantaneous field per parameter/height/run/lead."""
    lines = [line.split(":") for line in raw.decode("ascii").splitlines() if line]
    result: dict[str, tuple[int, int]] = {}
    expected_run = "d=" + model_run.strftime("%Y%m%d%H")
    for i, parts in enumerate(lines):
        if len(parts) < 7:
            raise WeatherUnavailable("Malformed GRIB index")
        for name, (parameter, level) in FIELDS.items():
            if parts[3:5] != [parameter, level]:
                continue
            if parts[2] != expected_run or parts[5] != f"{lead} hour fcst":
                raise WeatherUnavailable(f"Index run/lead mismatch for {name}")
            if name in result:
                raise WeatherUnavailable(f"Duplicate GRIB index field {name}")
            start = int(parts[1])
            end = (int(lines[i + 1][1]) if i + 1 < len(lines) else object_size) - 1
            if not 0 <= start <= end < object_size:
                raise WeatherUnavailable("Invalid GRIB byte range")
            result[name] = (start, end)
    if set(result) != set(FIELDS):
        raise WeatherUnavailable(f"Missing fields in GRIB index: {set(FIELDS) - set(result)}")
    return result


def decode_point(raw: bytes, field: str, latitude: float, longitude: float,
                 model_run: datetime, lead: int) -> tuple[float, dict[str, float]]:
    try:
        import eccodes
    except ImportError as error:
        raise WeatherUnavailable("Install weather support: pip install -e '.[weather]'") from error
    with DECODE_LOCK:
        handle = eccodes.codes_new_from_message(raw)
        try:
            # ecCodes definitions may label GFS 100 m wind as u/v or 100u/100v.
            # Validate canonical GRIB2 identifiers and height as well as aliases.
            names, level, units, category, number = {
                "u100": ({"u", "100u"}, 100, "m s**-1", 2, 2),
                "v100": ({"v", "100v"}, 100, "m s**-1", 2, 3),
                "t2m": ({"2t"}, 2, "K", 0, 0),
            }[field]
            actual = tuple(eccodes.codes_get(handle, key) for key in ("shortName", "level", "units"))
            valid = model_run + timedelta(hours=lead)
            checks = {"dataDate": int(model_run.strftime("%Y%m%d")),
                      "dataTime": model_run.hour * 100,
                      "validityDate": int(valid.strftime("%Y%m%d")),
                      "validityTime": valid.hour * 100,
                      "forecastTime": lead, "typeOfLevel": "heightAboveGround",
                      "stepType": "instant", "edition": 2, "discipline": 0,
                      "parameterCategory": category, "parameterNumber": number}
            if (actual[0] not in names or actual[1:] != (level, units)
                    or any(eccodes.codes_get(handle, key) != value
                           for key, value in checks.items())):
                raise WeatherUnavailable(f"GRIB metadata mismatch: {field}, {actual}")
            point = eccodes.codes_grib_find_nearest(handle, latitude, longitude)[0]
            value = float(point["value"])
            if not math.isfinite(value) or value == eccodes.codes_get(handle, "missingValue"):
                raise WeatherUnavailable(f"Missing/nonfinite GRIB value: {field}")
            grid = {"latitude": float(point["lat"]), "longitude": float(point["lon"]),
                    "distance_km": float(point["distance"])}
            return value, grid
        finally:
            eccodes.codes_release(handle)


class GFSArchiveProvider:
    """Native hourly 0.25° GFS; nearest cell; 100m wind and 2m temperature.

    Targets are issue+1h through issue+horizon. The latest model initialization
    whose COMPLETE requested horizon was already in the archive is selected.
    """

    def __init__(self, turbines: Mapping[str, tuple[float, float]], cache_dir: str | Path,
                 timeout_seconds: float = 30, retries: int = 2, workers: int = 4,
                 offline: bool = False, client: httpx.Client | None = None):
        if retries < 0 or retries > 5 or workers < 1 or workers > 8:
            raise ValueError("retries must be 0..5 and workers 1..8")
        self.turbines = dict(turbines)
        for latitude, longitude in self.turbines.values():
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError("Invalid turbine coordinates")
        self.cache_dir = Path(cache_dir)
        self.workers = workers
        self.client = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False)
        self._owns_client = client is None
        self.cache = RawCache(self.cache_dir, self.client, retries, offline)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> GFSArchiveProvider:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @staticmethod
    def object_key(model_run: datetime, lead: int) -> str:
        return (f"gfs.{model_run:%Y%m%d}/{model_run:%H}/atmos/"
                f"gfs.t{model_run:%H}z.pgrb2.0p25.f{lead:03d}")

    def list_run(self, model_run: datetime) -> tuple[dict[str, ObjectInfo], str]:
        prefix = self.object_key(model_run, 0)[:-3]
        url = BASE_URL + "/?" + urlencode({"list-type": "2", "prefix": prefix, "max-keys": "1000"})
        response = self.cache.get(url, refresh=True)
        try:
            root = ET.fromstring(response.body)
            if root.findtext("s:IsTruncated", namespaces=NS) != "false":
                raise WeatherUnavailable("Unexpected truncated archive listing")
            objects = {}
            for entry in root.findall("s:Contents", NS):
                key = entry.findtext("s:Key", namespaces=NS)
                objects[key] = ObjectInfo(key, _iso(entry.findtext("s:LastModified", namespaces=NS)),
                                          int(entry.findtext("s:Size", namespaces=NS)),
                                          entry.findtext("s:ETag", namespaces=NS))
            return objects, response.metadata["sha256"]
        except (ET.ParseError, TypeError, ValueError) as error:
            raise WeatherUnavailable("Invalid S3 object listing") from error

    def select_run(self, issue_time: datetime, horizon_hours: int
                   ) -> tuple[datetime, datetime, dict[str, ObjectInfo], str]:
        issue_time = _utc(issue_time)
        run = issue_time.replace(hour=issue_time.hour // 6 * 6, minute=0, second=0, microsecond=0)
        for _ in range(9):  # bounded: at most 48h of previous initializations
            first = int((issue_time - run).total_seconds() // 3600) + 1
            last = first + horizon_hours - 1
            if last > 120:
                break
            objects, listing_sha = self.list_run(run)
            keys = [self.object_key(run, lead) + suffix
                    for lead in range(first, last + 1) for suffix in ("", ".idx")]
            required = [objects[key] for key in keys if key in objects]
            if len(required) == len(keys):
                available = max(item.modified for item in required)
                # Multipart LastModified can mean upload INITIATION, not
                # completion. AWS documents multipart ETags ending in -N.
                # Accept only the ordinary quoted 32-hex ETag form here.
                single_part = all(re.fullmatch(r'"[0-9a-f]{32}"', item.etag or "")
                                  for item in required)
                if single_part and run <= available <= issue_time:
                    return run, available, {item.key: item for item in required}, listing_sha
            run -= timedelta(hours=6)
        raise WeatherUnavailable("No complete GFS run available at historical issue_time within 48h")

    @staticmethod
    def _check_object(response: CachedResponse, info: ObjectInfo,
                      byte_range: tuple[int, int] | None = None) -> None:
        headers = response.metadata["response_headers"]
        if headers.get("etag") != info.etag or "last-modified" not in headers:
            raise WeatherUnavailable(f"Archive object changed or lacks evidence: {info.key}")
        modified = parsedate_to_datetime(headers["last-modified"]).astimezone(UTC)
        if modified != info.modified:
            raise WeatherUnavailable(f"Archive object LastModified changed: {info.key}")
        if byte_range:
            start, end = byte_range
            if (headers.get("content-range") != f"bytes {start}-{end}/{info.size}" or
                    len(response.body) != end - start + 1):
                raise WeatherUnavailable(f"Incorrect HTTP byte range: {info.key}")
        elif len(response.body) != info.size:
            raise WeatherUnavailable(f"Incorrect archive object size: {info.key}")

    def fetch(self, issue_time: datetime, horizon_hours: int,
              turbine_ids: list[str]) -> list[WeatherRecord]:
        issue_time = _utc(issue_time)
        if issue_time.minute or issue_time.second or issue_time.microsecond:
            raise ValueError("issue_time must be aligned to a whole hour")
        if horizon_hours not in (24, 48):
            raise ValueError("horizon_hours must be 24 or 48")
        if not turbine_ids or len(turbine_ids) != len(set(turbine_ids)):
            raise ValueError("turbine_ids must be nonempty and unique")
        if set(turbine_ids) - self.turbines.keys():
            raise ValueError("Unknown turbine_ids")
        run, available, objects, listing_sha = self.select_run(issue_time, horizon_hours)
        first_lead = int((issue_time - run).total_seconds() // 3600) + 1

        def one_hour(lead: int) -> list[WeatherRecord]:
            key = self.object_key(run, lead)
            info, index_info = objects[key], objects[key + ".idx"]
            index = self.cache.get(BASE_URL + "/" + index_info.key, {"If-Match": index_info.etag})
            self._check_object(index, index_info)
            ranges = parse_index(index.body, info.size, run, lead)
            responses = {}
            for field, (start, end) in ranges.items():
                response = self.cache.get(BASE_URL + "/" + key,
                                          {"Range": f"bytes={start}-{end}", "If-Match": info.etag})
                self._check_object(response, info, (start, end))
                responses[field] = response
            # Exact selected GRIB messages, in stable parameter order, are also retained together.
            bundle = b"".join(responses[field].body for field in sorted(responses))
            bundle_sha = _sha(bundle)
            _atomic(self.cache_dir / "raw" / (bundle_sha + ".bin"), bundle)
            retrieved = max(_iso(response.metadata["retrieved_at"]) for response in responses.values())
            provenance = {"listing_sha256": listing_sha, "index_sha256": index.metadata["sha256"],
                          "grib_etag": info.etag, "grib_last_modified": info.modified.isoformat(),
                          "index_last_modified": index_info.modified.isoformat(),
                          "fields": {name: response.metadata for name, response in responses.items()}}
            manifest_bytes = _dump(provenance).encode()
            manifest_name = _sha(manifest_bytes) + ".json"
            _atomic(self.cache_dir / "extractions" / manifest_name, manifest_bytes)
            records = []
            for turbine_id in turbine_ids:
                latitude, longitude = self.turbines[turbine_id]
                values, grids = {}, {}
                for field, response in responses.items():
                    values[field], grids[field] = decode_point(response.body, field, latitude, longitude, run, lead)
                request = {"url": BASE_URL + "/" + key,
                           "ranges": {field: list(bounds) for field, bounds in ranges.items()},
                           "feature_time_basis": "instant_at_end",
                           "spatial_method": "nearest_grid_point", "grid": grids["u100"],
                           "raw_bundle_order": sorted(responses), "extraction_manifest": manifest_name}
                records.append(WeatherRecord(
                    turbine_id=turbine_id, latitude=latitude, longitude=longitude,
                    provider="NOAA GFS via AWS Open Data", weather_model="gfs_0p25",
                    model_run_time=run, forecast_available_at=available,
                    availability_basis="s3_last_modified_all_required_singlepart_grib_and_index_objects",
                    availability_evidence="S3 LastModified with single-part ETags; archive-object availability bound, not original NOAA publication time",
                    provenance_kind="operational_archive", retrieved_at=retrieved,
                    valid_time=run + timedelta(hours=lead), lead_hours=lead,
                    wind_speed_ms=math.hypot(values["u100"], values["v100"]), wind_height_m=100,
                    temperature_c=values["t2m"] - 273.15,
                    source_request=_dump(request), raw_sha256=bundle_sha))
            return records

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            records = [record for batch in executor.map(one_hour, range(first_lead, first_lead + horizon_hours))
                       for record in batch]
        return sorted(records, key=lambda record: (record.turbine_id, record.valid_time))
