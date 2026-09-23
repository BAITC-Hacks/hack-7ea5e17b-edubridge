"""Small, single-process API shared with the dashboard participant."""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response

from wind_agent.agent import AgentService
from wind_agent.config import Settings, load_settings
from wind_agent.contracts import ForecastRecord, RunRecord, RunRequest


def create_app(
    settings: Settings | None = None, service: AgentService | None = None
) -> FastAPI:
    """Create an app; inject a service to isolate storage in tests."""
    agent = service if service is not None else AgentService(
        settings if settings is not None else load_settings(os.environ.get("WIND_AGENT_CONFIG"))
    )
    application = FastAPI(
        title="Wind Forecast Agent",
        version="0.1.0",
        description=(
            "Historical hourly wind forecasts. Inspect /health and run warnings: "
            "demo mode uses synthetic inputs and is not a historical forecast. "
            "Run this MVP with one worker."
        ),
    )
    application.state.agent = agent

    def get_run(run_id: str) -> RunRecord:
        try:
            return agent.get(run_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="Run not found") from None

    def get_forecast(run_id: str) -> list[ForecastRecord]:
        get_run(run_id)
        try:
            return agent.forecast(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/health")
    def health() -> dict[str, Any]:
        return agent.health()

    @application.post("/runs", status_code=202)
    def submit_run(request: RunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        try:
            record = agent.submit(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Capture the response before execution changes the stored record.
        response = {"run_id": record.run_id, "status": record.status}
        background_tasks.add_task(agent.execute, record.run_id)
        return response

    @application.get("/runs/{run_id}", response_model=RunRecord)
    def read_run(run_id: str) -> RunRecord:
        return get_run(run_id)

    @application.get("/runs/{run_id}/forecast", response_model=list[ForecastRecord])
    def forecast(run_id: str) -> list[ForecastRecord]:
        return get_forecast(run_id)

    @application.get("/runs/{run_id}/forecast.csv")
    def forecast_csv(run_id: str) -> Response:
        rows = jsonable_encoder(get_forecast(run_id))
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(ForecastRecord.model_fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })
        return Response(
            content=stream.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="forecast.csv"'},
        )

    @application.get("/evaluation")
    def evaluation() -> dict[str, Any]:
        return agent.evaluation()

    return application


def default_app() -> FastAPI:
    """Uvicorn factory: configuration is read on startup, never at import."""
    return create_app()
