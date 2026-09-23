"""Shared API and model contracts; timestamps are always aware UTC.

The model integration boundary remains the DataFrame interface documented in
IMPLEMENTATION_PLAN.md. These records validate rows at its input and output.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _timestamp_input(value: Any) -> Any:
    if not isinstance(value, (str, datetime)):
        raise ValueError("timestamp must be an aware datetime or ISO 8601 string with timezone")
    return value


UTCDateTime = Annotated[
    AwareDatetime, BeforeValidator(_timestamp_input), AfterValidator(_as_utc)
]
NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def _finite_metadata(value: Any) -> Any:
    """Reject non-finite numbers even inside unstructured metadata."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("metadata numbers must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _finite_metadata(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_metadata(item)
    return value


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )


class WeatherRecord(Contract):
    """Weather at valid_time; GFS values are instantaneous at the target hour end."""

    turbine_id: NonEmptyString
    latitude: Annotated[FiniteFloat, Field(ge=-90, le=90)]
    longitude: Annotated[FiniteFloat, Field(ge=-180, le=180)]
    provider: NonEmptyString
    weather_model: NonEmptyString
    model_run_time: UTCDateTime
    forecast_available_at: UTCDateTime
    availability_basis: NonEmptyString
    retrieved_at: UTCDateTime
    valid_time: UTCDateTime
    lead_hours: Annotated[StrictInt, Field(ge=0)]
    wind_speed_ms: Annotated[FiniteFloat, Field(ge=0)]
    wind_height_m: Annotated[FiniteFloat, Field(gt=0)]
    temperature_c: FiniteFloat
    source_request: NonEmptyString
    raw_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    provenance_kind: Literal["operational_archive", "fixture", "hindcast"] | None = None
    availability_evidence: NonEmptyString | None = None

    @model_validator(mode="after")
    def check_times(self) -> "WeatherRecord":
        if (self.valid_time - self.model_run_time).total_seconds() != self.lead_hours * 3600:
            raise ValueError("weather lead_hours must equal valid_time - model_run_time")
        if self.forecast_available_at < self.model_run_time:
            raise ValueError("forecast_available_at cannot precede model_run_time")
        return self


class ForecastRecord(Contract):
    """Prediction for [valid_time - 1 hour, valid_time), labelled by its end."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: NonEmptyString
    revision: Annotated[StrictInt, Field(ge=1)]
    issue_time: UTCDateTime
    turbine_id: NonEmptyString
    valid_time: UTCDateTime
    lead_hours: Annotated[StrictInt, Field(ge=1)]
    prediction: FiniteFloat
    target_unit: NonEmptyString
    weather_model: NonEmptyString
    weather_run_time: UTCDateTime
    model_version: NonEmptyString
    training_cutoff: UTCDateTime
    data_quality: NonEmptyString = "validated"
    warnings: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_times(self) -> "ForecastRecord":
        if (self.valid_time - self.issue_time).total_seconds() != self.lead_hours * 3600:
            raise ValueError("forecast lead_hours must equal valid_time - issue_time")
        if self.weather_run_time > self.issue_time:
            raise ValueError("weather_run_time cannot be later than issue_time")
        if self.training_cutoff > self.issue_time:
            raise ValueError("training_cutoff cannot be later than issue_time")
        return self


class RunRequest(Contract):
    issue_time: UTCDateTime
    horizon_hours: Literal[24, 48] = 24
    turbine_ids: Annotated[list[NonEmptyString], Field(min_length=1)]

    @field_validator("horizon_hours", mode="before")
    @classmethod
    def integer_horizon(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("horizon_hours must be the integer 24 or 48")
        return value

    @field_validator("issue_time")
    @classmethod
    def hourly_issue(cls, value: datetime) -> datetime:
        if value.minute or value.second or value.microsecond:
            raise ValueError("issue_time must fall on an exact hour")
        return value

    @field_validator("turbine_ids")
    @classmethod
    def unique_turbines(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("turbine_ids must be unique")
        return value


class RunStatus(str, Enum):
    queued = "queued"
    fetching_weather = "fetching_weather"
    validating = "validating"
    forecasting = "forecasting"
    analysing = "analysing"
    completed = "completed"
    failed = "failed"


class AgentEvent(Contract):
    at: UTCDateTime
    step: RunStatus
    message: NonEmptyString
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def finite_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _finite_metadata(value)


class RunRecord(Contract):
    run_id: NonEmptyString
    request: RunRequest
    status: RunStatus = RunStatus.queued
    revision: Annotated[StrictInt, Field(ge=0)] = 0
    created_at: UTCDateTime
    updated_at: UTCDateTime
    events: list[AgentEvent] = Field(default_factory=list)
    warnings: list[StrictStr] = Field(default_factory=list)
    error: StrictStr | None = None
    input_fingerprint: StrictStr | None = None
    recalculation_reason: StrictStr | None = None


class ModelArtifact(Contract):
    """Validated metadata.json envelope beside participant 2's model.joblib.

    ``metadata`` should document feature names/order, missing-value handling,
    units, hour-end convention, serialization version, and confirmed bounds of
    the target. The model bridge passes the package directory to predict(); it
    infers artifact_path from that directory when this optional field is absent.
    """

    model_version: NonEmptyString
    training_cutoff: UTCDateTime
    target_unit: NonEmptyString
    metadata: dict[str, Any]
    artifact_path: NonEmptyString | None = None

    @field_validator("metadata")
    @classmethod
    def finite_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _finite_metadata(value)
