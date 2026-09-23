"""GRIB metadata validation across ecCodes naming conventions."""
from datetime import datetime, timezone
import sys
from types import SimpleNamespace

import pytest

from wind_agent.weather.gfs import WeatherUnavailable, decode_point

RUN = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)


def install_decoder(monkeypatch, field, short_name, overrides=None):
    level, units, category, number = {
        "u100": (100, "m s**-1", 2, 2),
        "v100": (100, "m s**-1", 2, 3),
        "t2m": (2, "K", 0, 0),
    }[field]
    metadata = dict(shortName=short_name, level=level, units=units,
                    dataDate=20260131, dataTime=1200, validityDate=20260131,
                    validityTime=1900, forecastTime=7, typeOfLevel="heightAboveGround",
                    stepType="instant", edition=2, discipline=0,
                    parameterCategory=category, parameterNumber=number, missingValue=9999.)
    metadata.update(overrides or {})
    released = []
    monkeypatch.setitem(sys.modules, "eccodes", SimpleNamespace(
        codes_new_from_message=lambda raw: metadata,
        codes_get=lambda handle, key: handle[key],
        codes_grib_find_nearest=lambda *args: [dict(value=3., lat=43.75, lon=78.5, distance=12.)],
        codes_release=released.append,
    ))
    return released


@pytest.mark.parametrize("field,short_name", [
    ("u100", "u"), ("u100", "100u"), ("v100", "v"), ("v100", "100v"), ("t2m", "2t"),
])
def test_accepts_equivalent_ec_codes_names(monkeypatch, field, short_name):
    released = install_decoder(monkeypatch, field, short_name)
    value, grid = decode_point(b"message", field, 43.64, 78.53, RUN, 7)
    assert value == 3. and grid["longitude"] == 78.5
    assert len(released) == 1


@pytest.mark.parametrize("override", [
    {"parameterNumber": 3}, {"parameterCategory": 0}, {"discipline": 1},
    {"edition": 1}, {"level": 10}, {"units": "K"}, {"shortName": "10u"},
    {"forecastTime": 8}, {"validityTime": 2000}, {"dataTime": 600},
    {"typeOfLevel": "isobaricInhPa"}, {"stepType": "avg"},
])
def test_alias_does_not_relax_parameter_height_or_time(monkeypatch, override):
    released = install_decoder(monkeypatch, "u100", "u", override)
    with pytest.raises(WeatherUnavailable, match="metadata mismatch"):
        decode_point(b"message", "u100", 43.64, 78.53, RUN, 7)
    assert len(released) == 1
