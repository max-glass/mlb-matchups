from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.evaluate import BankrollSettings, evaluate_parlay, expected_net_return

NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 19, 0, 8, tzinfo=timezone.utc)


def _leg(**kwargs):
    base = dict(
        game_id="g1",
        player_id=1,
        market_type="player_hits",
        side="over",
        line=0.5,
        p_win=Decimal("0.80"),
        american=-150,
        data_support=0.8,
        n_sims=4000,
        samples=[1, 1, 1, 1, 0],
    )
    base.update(kwargs)
    return base


def test_high_probability_parlay_with_a_bad_price_is_not_value():
    # Each leg wins often. Combined -400 is too short for a 0.64 joint if independent,
    # and these samples are identical so the joint is 0.80. 0.80 * 1.25 - 1 = 0.
    # Use a worse price: -900, decimal 1.111, 0.80 * 1.111 - 1 < 0.
    legs = [
        _leg(samples=[1] * 8 + [0] * 2, p_win=Decimal("0.8")),
        _leg(player_id=2, market_type="player_total_bases", samples=[1] * 8 + [0] * 2, p_win=Decimal("0.8")),
    ]
    evaluation, detail = evaluate_parlay(
        legs,
        combined_american=-900,
        quoted_at=NOW - timedelta(minutes=10),
        now=NOW,
        game_start=START,
        settings=BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive"),
        n_sims=10,
        data_support=0.8,
    )
    assert detail["method"] == "same_game_simulation"
    assert evaluation.ev_per_unit < 0
    assert evaluation.status == "No Value"
    assert evaluation.tier is None
    assert evaluation.presented_as_current_opportunity is False


def test_same_game_without_a_simulation_is_not_qualified():
    legs = [
        _leg(samples=None),
        _leg(player_id=2, market_type="player_total_bases", samples=None),
    ]
    evaluation, detail = evaluate_parlay(
        legs,
        combined_american=300,
        quoted_at=NOW,
        now=NOW,
        game_start=START,
    )
    assert detail["method"] == "unsupported"
    assert evaluation.status == "Informational Only"
    assert evaluation.presented_as_current_opportunity is False
    assert evaluation.ev_per_unit is None


def test_correlated_legs_are_not_priced_as_independent():
    # Every home run is a hit. Product of marginals is 0.5 * 0.5 = 0.25.
    # The joint frequency is 0.5.
    hits = [1, 1, 0, 0]
    home_runs = [1, 1, 0, 0]
    legs = [
        _leg(samples=hits, p_win=Decimal("0.5"), side="over", line=0.5),
        _leg(
            player_id=1,
            market_type="player_home_runs",
            samples=home_runs,
            p_win=Decimal("0.5"),
            side="over",
            line=0.5,
        ),
    ]
    evaluation, detail = evaluate_parlay(
        legs,
        combined_american=150,
        quoted_at=NOW,
        now=NOW,
        game_start=START,
        settings=BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive"),
        n_sims=4,
        data_support=0.8,
    )
    assert detail["dependent"] is True
    assert detail["method"] == "same_game_simulation"
    assert detail["joint_probability"] == pytest_almost(0.5)
    assert detail["joint_probability"] != 0.25
    assert evaluation.p_win == Decimal("0.5")


def test_missing_combined_price_stays_theoretical():
    legs = [
        _leg(game_id="g1", samples=[1, 0, 1, 0]),
        _leg(game_id="g2", player_id=9, samples=[1, 1, 0, 0], p_win=Decimal("0.5")),
    ]
    evaluation, detail = evaluate_parlay(
        legs,
        combined_american=None,
        quoted_at=NOW,
        now=NOW,
        game_start=START,
    )
    assert evaluation.status == "Informational Only"
    assert evaluation.theoretical_price is True
    assert detail["theoretical_decimal"] is not None
    assert evaluation.presented_as_current_opportunity is False
    assert any("not" in line.lower() and "hard rock" in line.lower() for line in evaluation.explanation)


def test_parlay_ev_uses_the_combined_price():
    legs = [
        _leg(game_id="g1", samples=[1, 1, 1, 0], p_win=Decimal("0.75")),
        _leg(game_id="g2", player_id=3, samples=[1, 1, 0, 0], p_win=Decimal("0.5")),
    ]
    evaluation, detail = evaluate_parlay(
        legs,
        combined_american=250,
        quoted_at=NOW,
        now=NOW,
        game_start=START,
        n_sims=4,
        data_support=0.8,
        settings=BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive"),
    )
    joint = Decimal(str(detail["joint_probability"]))
    assert evaluation.ev_per_unit == expected_net_return(joint, Decimal("3.5"))
    assert detail["method"] == "independent_games"


def test_a_large_payout_alone_does_not_qualify():
    legs = [
        _leg(game_id="g1", samples=[1, 0, 0, 0], p_win=Decimal("0.25")),
        _leg(game_id="g2", player_id=4, samples=[1, 0, 0, 0], p_win=Decimal("0.25")),
    ]
    # Joint about 0.0625 at +200 (decimal 3) is deeply negative: 0.0625 * 3 - 1.
    evaluation, _detail = evaluate_parlay(
        legs,
        combined_american=200,
        quoted_at=NOW,
        now=NOW,
        game_start=START,
        n_sims=4,
        data_support=0.9,
        settings=BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive"),
    )
    assert evaluation.p_win < Decimal("0.1")
    assert evaluation.ev_per_unit < 0
    assert evaluation.status == "No Value"


def pytest_almost(value):
    return value
