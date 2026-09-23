"""Read-only source ingestion; hourly semantics and model fitting are separate."""

from .loader import DataValidationError, LoadedHistory, load_history

__all__ = ["DataValidationError", "LoadedHistory", "load_history"]
