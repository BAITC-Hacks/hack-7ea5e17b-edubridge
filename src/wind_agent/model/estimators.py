"""Small deterministic models; all numerical learning uses supplied training pairs."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor


FEATURES = ("wind_speed_ms", "temperature_c")
TURBINES = ("turbine_1", "turbine_2")


def numeric_values(frame, columns, *, code="NONFINITE_FEATURE"):
    if not isinstance(frame, pd.DataFrame) or not set(columns).issubset(frame.columns):
        raise ValueError("INVALID_SCHEMA: missing numerical columns")
    for column in columns:
        values = frame[column]
        if (
            not pd.api.types.is_numeric_dtype(values)
            or pd.api.types.is_bool_dtype(values)
            or pd.api.types.is_complex_dtype(values)
            or values.isna().any()
        ):
            raise ValueError(f"{code}: {column} must contain finite real numbers")
    values = frame[list(columns)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"{code}: numerical values must be finite")
    return values


@dataclass
class WindCurve:
    """Binned observed means interpolated at bin centers; constant beyond endpoints."""

    bin_width: float = 1.0

    def fit(self, frame, target):
        wind = numeric_values(frame, ["wind_speed_ms"])[:, 0]
        groups = pd.DataFrame({
            "bin": np.floor(wind / self.bin_width), "target": np.asarray(target),
        }).groupby("bin", sort=True)["target"].mean()
        self.centers_ = (groups.index.to_numpy(dtype=float) + 0.5) * self.bin_width
        self.values_ = groups.to_numpy(dtype=float)
        if not np.isfinite(self.centers_).all() or not np.isfinite(self.values_).all():
            raise ValueError("NONFINITE_PREDICTION: wind curve fit overflowed")
        return self

    def predict(self, frame):
        wind = numeric_values(frame, ["wind_speed_ms"])[:, 0]
        return np.interp(wind, self.centers_, self.values_)


@dataclass
class ConstantMean:
    def fit(self, frame, target):
        self.mean_ = float(np.mean(target))
        if not np.isfinite(self.mean_):
            raise ValueError("NONFINITE_PREDICTION: target mean overflowed")
        return self

    def predict(self, frame):
        return np.full(len(frame), self.mean_, dtype=float)


def fit_estimators(joined_frame, algorithm, hyperparameters=None):
    """Fit one estimator per represented turbine, with no clipping or preprocessing."""
    if (
        not isinstance(joined_frame, pd.DataFrame) or joined_frame.empty
        or not joined_frame.columns.is_unique or "turbine_id" not in joined_frame
    ):
        raise ValueError("INVALID_SCHEMA: nonempty training pairs are required")
    if not joined_frame["turbine_id"].isin(TURBINES).all():
        raise ValueError("UNSUPPORTED_TURBINE: unknown training turbine")
    numeric_values(joined_frame, [*FEATURES, "normalized_power"])
    if (joined_frame.wind_speed_ms < 0).any():
        raise ValueError("NONFINITE_FEATURE: wind speed must be nonnegative")
    parameters = dict(hyperparameters or {})
    models = {}
    for turbine, rows in joined_frame.groupby("turbine_id", sort=True):
        if algorithm == "wind_curve":
            if set(parameters) - {"bin_width"}:
                raise ValueError("INVALID_SCHEMA: wind_curve supports only bin_width")
            width = parameters.get("bin_width", 1.0)
            if isinstance(width, bool) or not np.isfinite(width) or width <= 0:
                raise ValueError("INVALID_SCHEMA: bin_width must be finite and positive")
            model = WindCurve(float(width))
        elif algorithm == "constant_mean":
            if parameters:
                raise ValueError("INVALID_SCHEMA: constant_mean has no hyperparameters")
            model = ConstantMean()
        elif algorithm == "hist_gradient_boosting":
            allowed = {"learning_rate", "max_iter", "max_leaf_nodes", "max_depth",
                       "min_samples_leaf", "l2_regularization", "max_bins"}
            if set(parameters) - allowed:
                raise ValueError("INVALID_SCHEMA: unsupported HGB hyperparameter")
            defaults = {"max_iter": 120, "max_leaf_nodes": 15, "min_samples_leaf": 20,
                        "learning_rate": 0.08, "l2_regularization": 1.0}
            model = HistGradientBoostingRegressor(
                **(defaults | parameters), early_stopping=False, random_state=42,
            )
        else:
            raise ValueError("INVALID_SCHEMA: unsupported model algorithm")
        model.fit(rows[list(FEATURES)], rows.normalized_power.to_numpy(dtype=float))
        models[turbine] = model
    return models


def predict_estimators(models, frame):
    """Return predictions in input positional order, independent of pandas index labels."""
    if (
        not isinstance(frame, pd.DataFrame) or frame.empty
        or not frame.columns.is_unique or "turbine_id" not in frame
    ):
        raise ValueError("INVALID_SCHEMA: nonempty feature frame required")
    numeric_values(frame, FEATURES)
    if (frame.wind_speed_ms < 0).any():
        raise ValueError("NONFINITE_FEATURE: wind speed must be nonnegative")
    if not frame.turbine_id.isin(models).all():
        raise ValueError("UNSUPPORTED_TURBINE: model missing requested turbine")
    result = np.empty(len(frame), dtype=float)
    for turbine, model in models.items():
        positions = np.flatnonzero(frame.turbine_id.to_numpy() == turbine)
        if len(positions):
            values = np.asarray(model.predict(frame.iloc[positions][list(FEATURES)]))
            if values.shape != (len(positions),) or np.iscomplexobj(values):
                raise ValueError("NONFINITE_PREDICTION: estimator output shape/type invalid")
            result[positions] = values
    if not np.isfinite(result).all():
        raise ValueError("NONFINITE_PREDICTION: estimator returned a nonfinite result")
    return result
