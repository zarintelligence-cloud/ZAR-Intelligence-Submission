from __future__ import annotations
import math


class SplitConformalRegressor:
    """Calibrated symmetric interval from held-out absolute residuals."""

    def __init__(self) -> None:
        self.q: float | None = None

    def fit(self, actual: list[float], predicted: list[float], coverage: float = 0.9):
        if len(actual) != len(predicted) or not actual:
            raise ValueError("Calibration data required")
        residuals = sorted(abs(a - p) for a, p in zip(actual, predicted))
        rank = min(len(residuals) - 1, max(0, math.ceil((len(residuals) + 1) * coverage) - 1))
        self.q = residuals[rank]
        return self

    def interval(self, prediction: float) -> tuple[float, float]:
        if self.q is None:
            raise ValueError("Call fit first")
        return prediction - self.q, prediction + self.q
