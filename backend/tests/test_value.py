from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.evaluate import (
    BankrollSettings,
    american_to_decimal,
    classify_and_size,
    decimal_to_american,
    expected_net_return,
    implied_probability_from_american,
    implied_probability_from_decimal,
    no_vig,
    prop_probabilities,
)

NOW = datetime(2026, 10, 18, 18, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 19, 0, 8, tzinfo=timezone.utc)


def _quote(minutes_old=30):
    return NOW - timedelta(minutes=minutes_old)


def evaluate(**kwargs):
    defaults = dict(
        quoted_at=_quote(),
        now=NOW,
        game_start=START,
        data_support=0.8,
        n_sims=4000,
        price_source="manual",
        settings=BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive"),
    )
    defaults.update(kwargs)
    return classify_and_size(**defaults)


def test_american_decimal_implied_and_ev_are_consistent():
    assert american_to_decimal(120) == Decimal("2.2")
    assert american_to_decimal(-110) == Decimal(21) / Decimal(11)
    assert decimal_to_american(Decimal("2.5")) == 150
    assert decimal_to_american(american_to_decimal(-200)) == -200
    assert implied_probability_from_american(120) == Decimal(100) / Decimal(220)
    assert implied_probability_from_decimal(Decimal("2.2")) == Decimal(1) / Decimal("2.2")
    assert expected_net_return(Decimal("0.55"), Decimal("2.2")) == Decimal("0.21")
    # $100 stake illustration: 0.55 * 2.00 - 1 = 0.10
    assert expected_net_return(Decimal("0.55"), Decimal("2")) == Decimal("0.10")


def test_no_vig_is_not_the_raw_implied_price():
    over = implied_probability_from_american(-110)
    under = implied_probability_from_american(-110)
    assert over != Decimal("0.5")
    fair = no_vig([over, under])
    assert fair[0] == Decimal("0.5")
    assert fair[1] == Decimal("0.5")


def test_push_uses_the_full_distribution_not_the_binary_formula():
    # p_win 0.40, push 0.20, decimal 2.00 -> 0.40 * 2 - 1 + 0.20 = 0
    assert expected_net_return(Decimal("0.4"), Decimal(2), Decimal("0.2")) == Decimal(0)
    samples = [0, 1, 1, 2]
    props = prop_probabilities(samples, 1, "over")
    assert props["p_win"] == Decimal("0.25")
    assert props["p_push"] == Decimal("0.5")
    assert props["p_loss"] == Decimal("0.25")


def test_high_probability_negative_ev_is_not_a_recommendation():
    # 90% to win at -1200. Break-even decimal is 1.111; -1200 pays 1.0833.
    result = evaluate(p_win=Decimal("0.90"), american=-1200)
    assert result.ev_per_unit < 0
    assert result.tier is None
    assert result.status == "No Value"
    assert result.presented_as_current_opportunity is False
    assert result.stake_fraction == 0


def test_lower_probability_can_qualify_when_the_price_supports_it():
    result = evaluate(p_win=Decimal("0.42"), american=150)
    assert result.p_win < Decimal("0.5")
    assert result.ev_per_unit > 0
    assert result.tier in {"balanced", "aggressive", "long_shot"}
    assert result.status == "Qualified Value"
    assert result.presented_as_current_opportunity is True


def test_positive_point_estimate_is_not_enough_when_uncertainty_is_wide():
    result = evaluate(p_win=Decimal("0.51"), american=100, n_sims=20, data_support=0.8)
    assert result.ev_per_unit > 0
    assert result.tier is None
    assert result.status == "Insufficient Confidence"
    assert "uncertainty" in result.failed or "long_shot_shape" in result.failed or "data_support" in result.failed


def test_stale_and_missing_prices_are_not_current_opportunities():
    stale = evaluate(p_win=Decimal("0.62"), american=-125, quoted_at=NOW - timedelta(hours=12))
    assert stale.status == "Stale Odds"
    assert stale.presented_as_current_opportunity is False
    missing = evaluate(p_win=Decimal("0.62"), american=None, decimal_odds=None)
    assert missing.status == "Insufficient Confidence"
    assert missing.presented_as_current_opportunity is False


def test_weak_evidence_is_not_a_confident_recommendation():
    result = evaluate(p_win=Decimal("0.62"), american=-125, data_support=0.2, n_sims=5000)
    assert result.status == "Insufficient Confidence"
    assert result.tier is None


def test_a_tier_stays_empty_when_nothing_qualifies():
    bets = [
        evaluate(p_win=Decimal("0.90"), american=-1200),
        evaluate(p_win=Decimal("0.80"), american=-400),
    ]
    conservative = [bet for bet in bets if bet.tier == "conservative" and bet.presented_as_current_opportunity]
    assert conservative == []
    assert all(bet.status == "No Value" for bet in bets)


def test_risk_profile_does_not_change_the_probability():
    settings_low = BankrollSettings(bankroll=Decimal("1000"), risk_profile="conservative")
    settings_high = BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive")
    kwargs = dict(p_win=Decimal("0.22"), american=500, n_sims=5000, data_support=0.8)
    low = evaluate(settings=settings_low, **kwargs)
    high = evaluate(settings=settings_high, **kwargs)
    assert low.p_win == high.p_win
    assert low.tier == high.tier == "long_shot"
    assert low.profile_eligible is False
    assert high.profile_eligible is True
    assert low.stake_dollars == 0
    assert high.stake_dollars > 0


def test_expected_value_uses_quoted_payout_not_the_no_vig_price():
    result = evaluate(p_win=Decimal("0.50"), american=-110, other_side_american=-110)
    assert result.implied_raw > Decimal("0.5")
    assert result.implied_novig == Decimal("0.5")
    assert result.ev_per_unit == expected_net_return(Decimal("0.50"), american_to_decimal(-110))
    assert result.ev_per_unit < 0
    assert result.status == "No Value"
