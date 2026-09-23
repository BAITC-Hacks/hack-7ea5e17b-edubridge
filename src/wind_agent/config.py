"""JSON configuration. Relative paths are relative to the working directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from wind_agent.contracts import Contract, FiniteFloat, NonEmptyString, UTCDateTime


class TurbineConfig(Contract):
    turbine_id: NonEmptyString
    latitude: Annotated[FiniteFloat, Field(ge=-90, le=90)]
    longitude: Annotated[FiniteFloat, Field(ge=-180, le=180)]
    coordinates_source: NonEmptyString | None = None
    is_synthetic: StrictBool = False


class Settings(Contract):
    mode: Literal["archive", "demo"] = "archive"
    weather_provider: NonEmptyString = "gfs_s3"
    model_adapter_module: NonEmptyString | None = None
    model_artifact_path: Path | None = None
    cache_dir: Path = Path("artifacts/weather-cache")
    artifact_dir: Path = Path("artifacts/runs")
    turbines: list[TurbineConfig] = Field(default_factory=list)
    replay_timezone: Literal["Asia/Almaty"] = "Asia/Almaty"
    replay_issue_hour: Annotated[StrictInt, Field(ge=0, le=23)] = 23
    timestamp_convention: Literal["hour_end"] = "hour_end"
    replay_assumption: NonEmptyString = (
        "Team assumption: issue at 23:00 Asia/Almaty, first forecast hour at +1h; "
        "valid_time denotes the end of the predicted hour; weather is instant_at_end. "
        "This is not a confirmed "
        "timestamp convention of the source turbine dataset."
    )
    history_timezone: NonEmptyString | None = None
    train_cutoff: UTCDateTime | None = None
    default_horizon_hours: Literal[24, 48] = 48
    api_host: NonEmptyString = "127.0.0.1"
    api_port: Annotated[StrictInt, Field(ge=1, le=65535)] = 8000

    @field_validator("replay_timezone")
    @classmethod
    def timezone_available(cls, value: str) -> str:
        # On Windows the tzdata package supplies the IANA database.
        ZoneInfo(value)
        return value

    @field_validator("history_timezone")
    @classmethod
    def history_timezone_available(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("history_timezone must be an installed IANA timezone name") from error
        return value

    @model_validator(mode="after")
    def check_mode(self) -> "Settings":
        ids = [turbine.turbine_id for turbine in self.turbines]
        if len(ids) != len(set(ids)):
            raise ValueError("configured turbine IDs must be unique")
        if self.mode == "archive" and self.weather_provider == "fixture":
            raise ValueError("fixture weather is allowed only in explicitly labelled demo mode")
        if self.mode == "archive" and any(item.is_synthetic for item in self.turbines):
            raise ValueError("synthetic coordinates are allowed only in demo mode")
        return self

    def turbine(self, turbine_id: str) -> TurbineConfig:
        for turbine in self.turbines:
            if turbine.turbine_id == turbine_id:
                return turbine
        raise ValueError(f"unknown turbine_id: {turbine_id}")


def load_settings(path: str | Path | None = None) -> Settings:
    """Load explicit settings; defaults intentionally do not configure a model."""
    if path is None:
        return Settings()
    return Settings.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
