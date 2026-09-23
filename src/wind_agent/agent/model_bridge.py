"""Participant #2 supplies the model module and immutable artifact metadata."""

import importlib
from pathlib import Path

from wind_agent.contracts import ModelArtifact


class ModelBridge:
    def __init__(self, module_name, metadata_path):
        if not module_name or not metadata_path:
            raise ValueError("Model unavailable: configure model_adapter_module and model_artifact_path")
        self.module = importlib.import_module(module_name)
        if not callable(getattr(self.module, "predict", None)):
            raise ValueError("Model adapter must export predict(artifact, weather_frame, issue_time, horizon_hours)")
        path = Path(metadata_path)
        self.package_dir = path if path.is_dir() else path.parent
        self.metadata_path = path / "metadata.json" if path.is_dir() else path
        self.reload()

    def reload(self):
        self.artifact = ModelArtifact.model_validate_json(self.metadata_path.read_text("utf-8"))
        if self.artifact.artifact_path is None:
            self.artifact.artifact_path = str(self.package_dir / "model.joblib")

    def predict(self, artifact, weather_frame, issue_time, horizon_hours):
        return self.module.predict(self.package_dir, weather_frame, issue_time, horizon_hours)
