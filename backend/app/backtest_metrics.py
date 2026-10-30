"""Calibration and betting-result summaries.

Realized profit is not treated as proof that a tier had positive expected value.
"""

from __future__ import annotations

import math

from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, root_mean_squared_error


def calibration_report(
    probabilities: list[float],
    outcomes: list[int],
    *,
    realized_profit: float | None = None,
    stake: float | None = None,
    expected_profit: float | None = None,
) -> dict:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("Probabilities and outcomes need the same non-empty length.")
    sample = len(outcomes)
    report = {
        "sample_size": sample,
        "sample_is_small": sample < 30,
        "brier": float(brier_score_loss(outcomes, probabilities)),
        "log_loss": float(log_loss(outcomes, probabilities, labels=[0, 1])),
        "expected_profit": expected_profit,
        "realized_profit": realized_profit,
        "realized_roi": None if not stake else realized_profit / stake,
        "warning": (
            "A realized profit does not prove the wagers had positive expected value, "
            "and a loss does not by itself prove the recommendations were poor. "
            "Samples under 30 settled bets are too small for a tier verdict."
        ),
    }
    return report


def count_errors(predictions: list[float], actuals: list[float]) -> dict:
    if len(predictions) != len(actuals) or not predictions:
        raise ValueError("Predictions and actuals need the same non-empty length.")
    return {
        "sample_size": len(actuals),
        "mae": float(mean_absolute_error(actuals, predictions)),
        "rmse": float(root_mean_squared_error(actuals, predictions)),
        "note": "Count error is separate from betting profit.",
    }


def max_drawdown(profits: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for profit in profits:
        equity += profit
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst if worst < 0 else 0.0


def closing_line_value(open_implied: float | None, close_implied: float | None) -> float | None:
    if open_implied is None or close_implied is None:
        return None
    return close_implied - open_implied
