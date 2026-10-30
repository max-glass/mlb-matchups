from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.evaluate import BankrollSettings, classify_and_size, fractional_kelly

NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 19, 0, 8, tzinfo=timezone.utc)


def evaluate(**kwargs):
    settings = kwargs.pop("settings", None) or BankrollSettings(
        bankroll=Decimal("1000"), risk_profile="aggressive"
    )
    n_sims = kwargs.pop("n_sims", 5000)
    return classify_and_size(
        quoted_at=NOW - timedelta(minutes=15),
        now=NOW,
        game_start=START,
        data_support=0.85,
        n_sims=n_sims,
        price_source="manual",
        settings=settings,
        **kwargs,
    )


def test_negative_ev_has_no_positive_stake():
    result = evaluate(p_win=Decimal("0.90"), american=-1200)
    assert result.stake_dollars == 0
    assert fractional_kelly(Decimal("0.90"), result.decimal_odds) == 0 or result.ev_per_unit < 0


def test_kelly_never_returns_a_negative_stake():
    assert fractional_kelly(Decimal("0.40"), Decimal("2.0")) == 0
    assert fractional_kelly(Decimal("0.60"), Decimal("2.0")) > 0
    result = evaluate(p_win=Decimal("0.62"), american=-125)
    assert result.stake_dollars >= 0


def test_uncertainty_and_correlation_reduce_the_stake():
    solid = evaluate(p_win=Decimal("0.57"), american=-125, n_sims=8000)
    noisy = evaluate(p_win=Decimal("0.57"), american=-125, n_sims=40)
    correlated = evaluate(
        p_win=Decimal("0.57"),
        american=-125,
        n_sims=8000,
        settings=BankrollSettings(
            bankroll=Decimal("1000"),
            risk_profile="aggressive",
            correlated_with_card=True,
        ),
    )
    assert solid.stake_dollars > noisy.stake_dollars
    assert solid.stake_dollars > correlated.stake_dollars
    assert solid.p_win == noisy.p_win == correlated.p_win


def test_long_shot_stake_is_not_larger_because_the_payout_is_larger():
    favorite = evaluate(p_win=Decimal("0.62"), american=-110)
    long_shot = evaluate(p_win=Decimal("0.22"), american=500)
    assert long_shot.tier == "long_shot"
    assert favorite.tier == "conservative" or favorite.ev_per_unit > 0
    assert long_shot.stake_dollars <= Decimal("5.00")
    assert long_shot.stake_dollars < favorite.stake_dollars
    assert long_shot.potential_profit_dollars > long_shot.expected_profit_dollars


def test_exposure_limits_are_not_bypassed():
    blocked = evaluate(
        p_win=Decimal("0.62"),
        american=-110,
        settings=BankrollSettings(
            bankroll=Decimal("1000"),
            risk_profile="aggressive",
            max_game_fraction=Decimal("0.04"),
            game_staked=Decimal("40"),
        ),
    )
    assert blocked.stake_dollars == 0
    assert blocked.status == "Exposure Limit Reached"
    assert blocked.presented_as_current_opportunity is False


def test_caps_bind_even_when_kelly_is_large():
    result = evaluate(p_win=Decimal("0.70"), american=100)
    # Full Kelly would be large at even money with p=0.70. The tier cap is 2% or less.
    assert result.stake_dollars <= Decimal("20.00")
    assert result.stake_dollars > 0
