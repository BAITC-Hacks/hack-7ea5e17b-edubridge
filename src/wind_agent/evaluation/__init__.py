"""Chronological split assignment and auditable normalized-power error reports."""

from .metrics import evaluate
from .splits import (
    EvaluationError,
    SplitAssignment,
    SplitConfig,
    SplitWindow,
    assign_splits,
    ensure_model_cutoff,
)

__all__ = [
    "EvaluationError", "SplitAssignment", "SplitConfig", "SplitWindow",
    "assign_splits", "ensure_model_cutoff", "evaluate",
]
