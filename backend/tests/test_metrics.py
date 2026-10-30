from sklearn.metrics import brier_score_loss, log_loss

from app.backtest_metrics import calibration_report
from app.evaluate import filter_logs


def test_calibration_metrics_match_sklearn_and_disclose_sample_size():
    probabilities = [0.2, 0.8, 0.6, 0.4]
    outcomes = [0, 1, 1, 0]
    report = calibration_report(probabilities, outcomes, realized_profit=-15.0, stake=100.0, expected_profit=4.0)
    assert report["sample_size"] == 4
    assert report["sample_is_small"] is True
    assert abs(report["brier"] - brier_score_loss(outcomes, probabilities)) < 1e-12
    assert abs(report["log_loss"] - log_loss(outcomes, probabilities)) < 1e-12
    assert report["realized_roi"] == -0.15
    assert report["expected_profit"] == 4.0
    assert report["realized_profit"] == -15.0
    assert "not proof" in report["warning"].lower() or "does not prove" in report["warning"].lower()


def test_historical_filter_drops_the_game_and_later_games():
    logs = [
        {"date": "2026-10-17", "game_pk": 1, "hits": 2},
        {"date": "2026-10-19", "game_pk": 849815, "hits": 3},
        {"date": "2026-10-20", "game_pk": 9, "hits": 1},
    ]
    visible = filter_logs(logs, "2026-10-19", exclude_game_pk=849815)
    assert [row["game_pk"] for row in visible] == [1]


def test_count_errors():
    from app.backtest_metrics import count_errors

    report = count_errors([1.0, 0.5, 2.0], [1, 0, 0])
    assert report["mae"] > 0
    assert report["rmse"] > report["mae"] or report["rmse"] == report["mae"]
    assert report["sample_size"] == 3
