"""Single pipeline for odds conversion, expected value, tiers, and stakes.

The Prediction Center, parlay builder, card math, and tests all call this
module. Win probability is an input to expected value. It is not a tier rule.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from scipy.stats import norm

RULES_PATH = Path(__file__).resolve().parent / "tier_rules.json"
MONEY = Decimal("0.01")
EV_QUANT = Decimal("0.0001")


def load_rules(path: Path | None = None) -> dict:
    raw = json.loads((path or RULES_PATH).read_text(encoding="utf-8"))
    return raw


def _d(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def american_to_decimal(american: int) -> Decimal:
    odds = int(american)
    if odds == 0 or abs(odds) < 100:
        raise ValueError("American odds must be at least 100 in absolute value.")
    if odds > 0:
        return (Decimal(odds) / Decimal(100)) + Decimal(1)
    return (Decimal(100) / Decimal(abs(odds))) + Decimal(1)


def decimal_to_american(decimal_odds: Decimal) -> int:
    decimal_odds = _d(decimal_odds)
    if decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1.")
    if decimal_odds == 2:
        return 100
    if decimal_odds > 2:
        return int(((decimal_odds - 1) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return int((Decimal(-100) / (decimal_odds - 1)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def implied_probability_from_decimal(decimal_odds: Decimal) -> Decimal:
    decimal_odds = _d(decimal_odds)
    if decimal_odds <= 0:
        raise ValueError("Decimal odds must be positive.")
    return Decimal(1) / decimal_odds


def implied_probability_from_american(american: int) -> Decimal:
    odds = int(american)
    if odds > 0:
        return Decimal(100) / (Decimal(odds) + Decimal(100))
    if odds < 0:
        absolute = abs(Decimal(odds))
        return absolute / (absolute + Decimal(100))
    raise ValueError("American odds cannot be zero.")


def no_vig(probabilities: list[Decimal]) -> list[Decimal] | None:
    if len(probabilities) < 2:
        return None
    if any(p <= 0 for p in probabilities):
        return None
    if all(p == probabilities[0] for p in probabilities):
        share = Decimal(1) / Decimal(len(probabilities))
        return [share for _ in probabilities]
    total = sum(probabilities, Decimal(0))
    if total <= 0:
        return None
    return [p / total for p in probabilities]


def expected_net_return(p_win: Decimal, decimal_odds: Decimal, p_push: Decimal = Decimal(0)) -> Decimal:
    """Net return per unit staked. Pushes return the stake and contribute 0.

    Binary case with no push: p * d - 1.
    With pushes: p_win * d - 1 + p_push.
    """
    return _d(p_win) * _d(decimal_odds) - Decimal(1) + _d(p_push)


def break_even_decimal(p_win: Decimal, p_push: Decimal = Decimal(0)) -> Decimal | None:
    """Decimal odds at which expected return is zero."""
    p_win = _d(p_win)
    p_push = _d(p_push)
    if p_win <= 0:
        return None
    if p_push < 0 or p_win + p_push > 1:
        raise ValueError("Outcome probabilities must fit in a distribution.")
    return (Decimal(1) - p_push) / p_win


def break_even_probability(decimal_odds: Decimal, p_push: Decimal = Decimal(0)) -> Decimal:
    """Win probability at which expected return is zero for this quoted price."""
    decimal_odds = _d(decimal_odds)
    if decimal_odds <= 0:
        raise ValueError("Decimal odds must be positive.")
    return (Decimal(1) - _d(p_push)) / decimal_odds


def unit_pnl_variance(p_win: Decimal, p_push: Decimal, decimal_odds: Decimal) -> Decimal:
    p_win = _d(p_win)
    p_push = _d(p_push)
    p_loss = Decimal(1) - p_win - p_push
    win_pay = _d(decimal_odds) - Decimal(1)
    mean = expected_net_return(p_win, decimal_odds, p_push)
    second = p_win * win_pay * win_pay + p_loss * Decimal(1)
    variance = second - mean * mean
    return variance if variance > 0 else Decimal(0)


def prop_probabilities(samples: list[int | float], line: float, side: str) -> dict[str, Decimal]:
    """Over/under probabilities from the outcome sample, never from a mean."""
    if not samples:
        raise ValueError("A prop needs an outcome sample.")
    if side not in {"over", "under"}:
        raise ValueError("Prop side must be over or under.")
    wins = pushes = 0
    for value in samples:
        if abs(float(value) - float(line)) < 1e-9:
            pushes += 1
        elif side == "over" and float(value) > float(line):
            wins += 1
        elif side == "under" and float(value) < float(line):
            wins += 1
    n = Decimal(len(samples))
    p_win = Decimal(wins) / n
    p_push = Decimal(pushes) / n
    return {
        "p_win": p_win,
        "p_push": p_push,
        "p_loss": Decimal(1) - p_win - p_push,
        "n": n,
    }


def moneyline_probabilities(wins: list[int]) -> dict[str, Decimal]:
    if not wins:
        raise ValueError("Moneyline needs simulated games.")
    n = Decimal(len(wins))
    p_win = Decimal(sum(1 for flag in wins if flag)) / n
    return {"p_win": p_win, "p_push": Decimal(0), "p_loss": Decimal(1) - p_win, "n": n}


def spread_probabilities(margins: list[float], line: float) -> dict[str, Decimal]:
    """Cover when team margin + line > 0. An exact zero is a push."""
    if not margins:
        raise ValueError("Run line needs simulated margins.")
    wins = pushes = 0
    for margin in margins:
        adjusted = float(margin) + float(line)
        if abs(adjusted) < 1e-9:
            pushes += 1
        elif adjusted > 0:
            wins += 1
    n = Decimal(len(margins))
    p_win = Decimal(wins) / n
    p_push = Decimal(pushes) / n
    return {"p_win": p_win, "p_push": p_push, "p_loss": Decimal(1) - p_win - p_push, "n": n}


def odds_timing(
    quoted_at: datetime | None,
    now: datetime,
    game_start: datetime | None,
    stale_after_minutes: int,
) -> str:
    if quoted_at is None:
        return "missing"
    if quoted_at.tzinfo is None:
        quoted_at = quoted_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if game_start is not None and game_start.tzinfo is None:
        game_start = game_start.replace(tzinfo=timezone.utc)
    if game_start is not None and quoted_at >= game_start:
        return "after_start"
    if game_start is not None and now >= game_start and quoted_at < game_start:
        age_at_start = (game_start - quoted_at).total_seconds() / 60.0
        if age_at_start > stale_after_minutes:
            return "stale_before_start"
        return "historical_pregame"
    age = (now - quoted_at).total_seconds() / 60.0
    if age > stale_after_minutes:
        return "stale"
    return "fresh"


def _float_prob(value: Decimal) -> float:
    return float(value)


def probability_ev_is_positive(p_win: Decimal, se: float, breakeven: Decimal) -> float | None:
    if se < 0:
        return None
    if se == 0:
        return 1.0 if p_win > breakeven else 0.0
    return float(norm.sf(float(breakeven), loc=float(p_win), scale=se))


@dataclass
class BankrollSettings:
    bankroll: Decimal | None = None
    unit_dollars: Decimal | None = None
    unit_fraction: Decimal | None = None
    risk_profile: str = "balanced"
    use_kelly: bool = True
    max_wager_fraction: Decimal = Decimal("0.02")
    max_card_fraction: Decimal = Decimal("0.10")
    max_game_fraction: Decimal = Decimal("0.04")
    max_parlay_fraction: Decimal = Decimal("0.01")
    card_staked: Decimal = Decimal(0)
    game_staked: Decimal = Decimal(0)
    correlated_with_card: bool = False

    def unit_value(self, rules: dict) -> Decimal | None:
        if self.bankroll is None:
            return self.unit_dollars
        if self.unit_dollars is not None and self.unit_fraction is not None:
            raise ValueError("Set a dollar unit or a percent of bankroll, not both.")
        if self.unit_dollars is not None:
            return self.unit_dollars
        fraction = self.unit_fraction
        if fraction is None:
            fraction = _d(rules["default_unit_fraction_of_bankroll"])
        return (self.bankroll * fraction).quantize(MONEY)


@dataclass
class Evaluation:
    status: str
    tier: str | None
    presented_as_current_opportunity: bool
    p_win: Decimal | None
    p_push: Decimal
    p_loss: Decimal | None
    decimal_odds: Decimal | None
    american_odds: int | None
    implied_raw: Decimal | None
    implied_novig: Decimal | None
    edge_vs_raw: Decimal | None
    edge_vs_novig: Decimal | None
    edge_benchmark: str | None
    ev_per_unit: Decimal | None
    conservative_ev: Decimal | None
    p_positive_ev: float | None
    unit_stdev: Decimal | None
    variance_label: str | None
    data_support: float | None
    uncertainty_method: str
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reason: str = ""
    explanation: list[str] = field(default_factory=list)
    stake_fraction: Decimal = Decimal(0)
    stake_dollars: Decimal | None = None
    stake_units: Decimal | None = None
    potential_profit_dollars: Decimal | None = None
    expected_profit_dollars: Decimal | None = None
    stake_explanation: str = ""
    profile_eligible: bool = False
    exposure_reduced: bool = False
    odds_timing: str = "missing"
    price_source: str = "missing"
    theoretical_price: bool = False
    rule_version: str = ""
    loss_probability: Decimal | None = None

    def public(self) -> dict:
        payload = asdict(self)
        for key, value in list(payload.items()):
            if isinstance(value, Decimal):
                payload[key] = float(value)
        return payload


def _variance_label(stdev: Decimal) -> str:
    if stdev <= Decimal("0.95"):
        return "lower"
    if stdev <= Decimal("1.35"):
        return "moderate"
    if stdev <= Decimal("1.8"):
        return "high"
    return "extreme"


def _tier_checks(name: str, metrics: dict, rules: dict, *, long_shot_shape: bool) -> tuple[bool, list[str], list[str]]:
    passed: list[str] = []
    failed: list[str] = []

    def check(ok: bool, label: str) -> None:
        (passed if ok else failed).append(label)

    check(metrics["ev"] > 0, "positive_ev")
    check(metrics["support"] >= rules["min_data_support"][name], "data_support")
    check(
        metrics["p_positive_ev"] is not None and metrics["p_positive_ev"] >= rules["min_p_positive_ev"][name],
        "uncertainty",
    )
    check(metrics["stdev"] <= _d(rules["max_unit_stdev"][name]), "variance")
    check(metrics["decimal"] <= _d(rules["max_decimal_odds"][name]), "price_length")
    check(metrics["p_loss"] <= _d(rules["max_loss_probability"][name]), "downside")
    if name in rules["require_conservative_edge_for"]:
        check(metrics["conservative_ev"] > 0, "edge_survives_lower_probability")
    if name == "long_shot":
        check(long_shot_shape, "long_shot_shape")
    return not failed, passed, failed


def classify_and_size(
    *,
    p_win: Decimal | None,
    p_push: Decimal = Decimal(0),
    american: int | None = None,
    decimal_odds: Decimal | None = None,
    other_side_american: int | None = None,
    quoted_at: datetime | None = None,
    now: datetime | None = None,
    game_start: datetime | None = None,
    data_support: float | None = None,
    n_sims: int | None = None,
    market_supported: bool = True,
    price_source: str = "manual",
    theoretical_price: bool = False,
    model_notes: list[str] | None = None,
    settings: BankrollSettings | None = None,
    rules: dict | None = None,
    is_parlay: bool = False,
    joint_method: str | None = None,
) -> Evaluation:
    rules = rules or load_rules()
    settings = settings or BankrollSettings()
    now = now or datetime.now(timezone.utc)
    version = rules["rule_version"]
    notes = list(model_notes or [])

    def base(**kwargs) -> Evaluation:
        return Evaluation(
            p_push=_d(p_push),
            rule_version=version,
            price_source=price_source,
            theoretical_price=theoretical_price,
            uncertainty_method=(
                "Normal approximation around the simulation frequency. "
                "The data-support allowance is a configured gap, not a calibrated interval."
            ),
            **kwargs,
        )

    if is_parlay and joint_method in {None, "unsupported", "theoretical"}:
        return base(
            status="Informational Only",
            tier=None,
            presented_as_current_opportunity=False,
            p_win=None if p_win is None else _d(p_win),
            p_loss=None,
            decimal_odds=_d(decimal_odds) if decimal_odds is not None else None,
            american_odds=american,
            implied_raw=None,
            implied_novig=None,
            edge_vs_raw=None,
            edge_vs_novig=None,
            edge_benchmark=None,
            ev_per_unit=None,
            conservative_ev=None,
            p_positive_ev=None,
            unit_stdev=None,
            variance_label=None,
            data_support=data_support,
            reason="No defensible joint probability or verified parlay price is available.",
            explanation=list(model_notes or [])
            + [
                "This parlay is not a qualified value recommendation.",
                "Same-game legs are not priced by multiplying their probabilities.",
            ],
            stake_explanation="No stake. The parlay is informational only.",
        )

    if not market_supported or p_win is None:
        return base(
            status="Insufficient Confidence",
            tier=None,
            presented_as_current_opportunity=False,
            p_win=None if p_win is None else _d(p_win),
            p_loss=None,
            decimal_odds=None,
            american_odds=american,
            implied_raw=None,
            implied_novig=None,
            edge_vs_raw=None,
            edge_vs_novig=None,
            edge_benchmark=None,
            ev_per_unit=None,
            conservative_ev=None,
            p_positive_ev=None,
            unit_stdev=None,
            variance_label=None,
            data_support=data_support,
            reason="The market or the model probability is missing, so no value can be estimated.",
            explanation=["This wager is ineligible until a supported market and a model probability exist."],
            stake_explanation="No stake. The wager is not eligible.",
        )

    p_win = _d(p_win)
    p_push = _d(p_push)
    if p_win < 0 or p_push < 0 or p_win + p_push > 1:
        raise ValueError("Probabilities must be non-negative and sum to at most 1.")
    p_loss = Decimal(1) - p_win - p_push

    if theoretical_price or (is_parlay and joint_method in {None, "unsupported", "theoretical"}):
        return base(
            status="Informational Only",
            tier=None,
            presented_as_current_opportunity=False,
            p_win=p_win,
            p_loss=p_loss,
            decimal_odds=_d(decimal_odds) if decimal_odds is not None else None,
            american_odds=american,
            implied_raw=None,
            implied_novig=None,
            edge_vs_raw=None,
            edge_vs_novig=None,
            edge_benchmark=None,
            ev_per_unit=None,
            conservative_ev=None,
            p_positive_ev=None,
            unit_stdev=None,
            variance_label=None,
            data_support=data_support,
            odds_timing="missing",
            reason="No verified combined sportsbook price or no defensible joint probability is available.",
            explanation=[
                "A theoretical parlay price or an unsupported joint probability is not a qualified recommendation.",
                "Hard Rock Bet's actual parlay price was not supplied.",
            ],
            stake_explanation="No stake. Informational parlays are not sized as wagers.",
        )

    if american is None and decimal_odds is None:
        return base(
            status="Insufficient Confidence",
            tier=None,
            presented_as_current_opportunity=False,
            p_win=p_win,
            p_loss=p_loss,
            decimal_odds=None,
            american_odds=None,
            implied_raw=None,
            implied_novig=None,
            edge_vs_raw=None,
            edge_vs_novig=None,
            edge_benchmark=None,
            ev_per_unit=None,
            conservative_ev=None,
            p_positive_ev=None,
            unit_stdev=None,
            variance_label=None,
            data_support=data_support,
            reason="No verified price was supplied.",
            explanation=["Expected value needs the payout that is actually offered. No price was entered."],
            stake_explanation="No stake without a price.",
        )

    try:
        if american is not None and decimal_odds is not None:
            converted = american_to_decimal(american)
            if abs(converted - _d(decimal_odds)) > Decimal("0.01"):
                raise ValueError("American and decimal odds do not describe the same price.")
            decimal_odds = converted
        elif american is not None:
            decimal_odds = american_to_decimal(american)
        else:
            decimal_odds = _d(decimal_odds)
            american = decimal_to_american(decimal_odds)
    except ValueError as exc:
        return base(
            status="Insufficient Confidence",
            tier=None,
            presented_as_current_opportunity=False,
            p_win=p_win,
            p_loss=p_loss,
            decimal_odds=None,
            american_odds=american,
            implied_raw=None,
            implied_novig=None,
            edge_vs_raw=None,
            edge_vs_novig=None,
            edge_benchmark=None,
            ev_per_unit=None,
            conservative_ev=None,
            p_positive_ev=None,
            unit_stdev=None,
            variance_label=None,
            data_support=data_support,
            reason=str(exc),
            explanation=[str(exc)],
            stake_explanation="No stake. The price is invalid.",
        )

    implied_raw = implied_probability_from_decimal(decimal_odds)
    american_implied = implied_probability_from_american(american)
    if abs(implied_raw - american_implied) > Decimal("0.001"):
        implied_raw = american_implied
    novig = None
    if other_side_american is not None:
        try:
            other_implied = implied_probability_from_american(other_side_american)
            pair = no_vig([implied_raw, other_implied])
            novig = None if pair is None else pair[0]
        except ValueError:
            novig = None

    ev = expected_net_return(p_win, decimal_odds, p_push)
    edge_raw = p_win - implied_raw
    edge_novig = None if novig is None else p_win - novig
    timing = odds_timing(quoted_at, now, game_start, int(rules["stale_after_minutes"]))

    se = None
    if n_sims and n_sims > 1 and p_push == 0:
        se = math.sqrt(_float_prob(p_win * p_loss) / n_sims)
    elif n_sims and n_sims > 1:
        se = math.sqrt(_float_prob(p_win * (Decimal(1) - p_win)) / n_sims)
    support = 0.0 if data_support is None else float(data_support)
    gap = (1.0 - support) * float(rules["support_gap_cap"])
    breakeven_price = break_even_decimal(p_win, p_push)
    breakeven_prob = break_even_probability(decimal_odds, p_push)
    p_pos = None if se is None else probability_ev_is_positive(p_win, se, breakeven_prob)
    p_low = p_win - _d(se or 0) - _d(gap)
    if p_low < 0:
        p_low = Decimal(0)
    conservative_ev = expected_net_return(p_low, decimal_odds, p_push)
    stdev = unit_pnl_variance(p_win, p_push, decimal_odds).sqrt()
    long_shot_shape = p_win <= _d(rules["long_shot_max_win_probability"]) and decimal_odds >= _d(
        rules["long_shot_min_decimal_odds"]
    )
    metrics = {
        "ev": ev,
        "support": support,
        "p_positive_ev": p_pos,
        "stdev": stdev,
        "decimal": decimal_odds,
        "p_loss": p_loss,
        "conservative_ev": conservative_ev,
    }

    explanation = []
    explanation.extend(notes)
    explanation.append(
        f"Model win probability is {float(p_win):.1%}. Loss probability is {float(p_loss):.1%}."
        + (f" Push probability is {float(p_push):.1%}." if p_push else "")
    )
    explanation.append(
        f"The quoted price implies {float(implied_raw):.1%}. That raw number includes the sportsbook margin and is not the break-even probability by itself."
    )
    if novig is not None:
        explanation.append(
            f"The proportional no-vig estimate for this side is {float(novig):.1%}. Expected return still uses the quoted payout, not the no-vig price."
        )
    else:
        explanation.append("No opposing price was supplied, so a no-vig estimate was not calculated.")
    explanation.append(
        f"Expected net return per unit staked is {float(ev):.1%}, from the quoted decimal price {float(decimal_odds):.3f}."
    )
    if breakeven_price is not None:
        explanation.append(
            f"Break-even decimal odds for this probability are {float(breakeven_price):.3f}. Prices past that are calculations, not sportsbook offers."
        )
    if p_pos is None:
        explanation.append("The probability that the edge is positive was not estimated because the simulation size is missing.")
    else:
        explanation.append(
            f"Using only the simulation's sampling error, the normal approximation puts P(expected return > 0) near {p_pos:.0%}."
        )

    if timing in {"missing", "stale", "stale_before_start", "after_start"}:
        reason = {
            "missing": "The quote has no timestamp, so it cannot be treated as a current price.",
            "stale": "The quote is older than the freshness window.",
            "stale_before_start": "The quote was already old at first pitch.",
            "after_start": "The quote is at or after first pitch and is not a pregame price.",
        }[timing]
        explanation.append(reason)
        return _with_stake_fields(
            base(
                status="Stale Odds",
                tier=None,
                presented_as_current_opportunity=False,
                p_win=p_win,
                p_loss=p_loss,
                decimal_odds=decimal_odds,
                american_odds=american,
                implied_raw=implied_raw,
                implied_novig=novig,
                edge_vs_raw=edge_raw,
                edge_vs_novig=edge_novig,
                edge_benchmark="raw" if novig is None else "both",
                ev_per_unit=ev,
                conservative_ev=conservative_ev,
                p_positive_ev=p_pos,
                unit_stdev=stdev,
                variance_label=_variance_label(stdev),
                data_support=support,
                odds_timing=timing,
                failed=["fresh_price"],
                reason=reason,
                explanation=explanation,
                loss_probability=p_loss,
            ),
            settings,
            rules,
            decimal_odds,
            is_parlay=is_parlay,
        )

    if ev <= 0:
        explanation.append(
            "The expected return is not positive at this payout. A high win probability does not change that."
        )
        return _with_stake_fields(
            base(
                status="No Value",
                tier=None,
                presented_as_current_opportunity=False,
                p_win=p_win,
                p_loss=p_loss,
                decimal_odds=decimal_odds,
                american_odds=american,
                implied_raw=implied_raw,
                implied_novig=novig,
                edge_vs_raw=edge_raw,
                edge_vs_novig=edge_novig,
                edge_benchmark="quoted payout",
                ev_per_unit=ev,
                conservative_ev=conservative_ev,
                p_positive_ev=p_pos,
                unit_stdev=stdev,
                variance_label=_variance_label(stdev),
                data_support=support,
                odds_timing=timing,
                failed=["positive_ev"],
                passed=["fresh_price"] if timing == "fresh" else ["historical_pregame_quote"],
                reason="The quoted payout does not support a positive expected return.",
                explanation=explanation,
                loss_probability=p_loss,
            ),
            settings,
            rules,
            decimal_odds,
            is_parlay=is_parlay,
        )

    chosen = None
    chosen_passed: list[str] = []
    chosen_failed: list[str] = []
    failures: dict[str, list[str]] = {}
    for name in ("conservative", "balanced", "aggressive", "long_shot"):
        ok, passed, failed = _tier_checks(name, metrics, rules, long_shot_shape=long_shot_shape)
        failures[name] = failed
        if ok and chosen is None:
            chosen = name
            chosen_passed = passed
            chosen_failed = []
    if chosen is None:
        explanation.append(
            "The point estimate of expected return is positive, but the evidence or the variance does not meet any tier."
        )
        return _with_stake_fields(
            base(
                status="Insufficient Confidence",
                tier=None,
                presented_as_current_opportunity=False,
                p_win=p_win,
                p_loss=p_loss,
                decimal_odds=decimal_odds,
                american_odds=american,
                implied_raw=implied_raw,
                implied_novig=novig,
                edge_vs_raw=edge_raw,
                edge_vs_novig=edge_novig,
                edge_benchmark="quoted payout",
                ev_per_unit=ev,
                conservative_ev=conservative_ev,
                p_positive_ev=p_pos,
                unit_stdev=stdev,
                variance_label=_variance_label(stdev),
                data_support=support,
                odds_timing=timing,
                passed=["positive_ev"],
                failed=sorted({item for failed in failures.values() for item in failed}),
                reason="The apparent edge is too uncertain, too thinly supported, or too volatile for every tier.",
                explanation=explanation,
                loss_probability=p_loss,
            ),
            settings,
            rules,
            decimal_odds,
            is_parlay=is_parlay,
        )

    profile = settings.risk_profile if settings.risk_profile in rules["profile_tiers"] else "balanced"
    profile_eligible = chosen in rules["profile_tiers"][profile]
    historical = timing == "historical_pregame"
    explanation.append(
        f"It meets the {chosen.replace('_', ' ')} tier: positive expected return, and the configured evidence and variance checks for that tier."
    )
    if not profile_eligible:
        explanation.append(
            f"Your {profile} risk profile does not stake the {chosen.replace('_', ' ')} tier. The probability estimate is unchanged."
        )
    if historical:
        explanation.append("The game has started or finished. This is a historical pregame quote, not a current offer.")

    status = "Qualified Value"
    presented = True
    if historical:
        status = "Watchlist"
        presented = False
    elif not profile_eligible:
        status = "Potential Value"
        presented = False

    evaluation = base(
        status=status,
        tier=chosen,
        presented_as_current_opportunity=presented,
        p_win=p_win,
        p_loss=p_loss,
        decimal_odds=decimal_odds,
        american_odds=american,
        implied_raw=implied_raw,
        implied_novig=novig,
        edge_vs_raw=edge_raw,
        edge_vs_novig=edge_novig,
        edge_benchmark="quoted payout for expected return; raw and no-vig edges are separate",
        ev_per_unit=ev,
        conservative_ev=conservative_ev,
        p_positive_ev=p_pos,
        unit_stdev=stdev,
        variance_label=_variance_label(stdev),
        data_support=support,
        odds_timing=timing,
        passed=chosen_passed + (["fresh_price"] if timing == "fresh" else ["historical_pregame_quote"]),
        failed=chosen_failed,
        reason=f"Qualifies for {chosen.replace('_', ' ')} because expected return is positive and the tier's evidence and variance rules pass.",
        explanation=explanation,
        profile_eligible=profile_eligible and not historical,
        loss_probability=p_loss,
    )
    evaluation = _with_stake_fields(evaluation, settings, rules, decimal_odds, is_parlay=is_parlay, tier=chosen)
    if evaluation.stake_fraction == 0 and evaluation.exposure_reduced and status == "Qualified Value":
        evaluation.status = "Exposure Limit Reached"
        evaluation.presented_as_current_opportunity = False
        evaluation.reason = "The tier rules pass, but the configured exposure limits leave no room for a stake."
        evaluation.explanation.append(evaluation.reason)
    return evaluation


def fractional_kelly(p_win: Decimal, decimal_odds: Decimal, p_push: Decimal = Decimal(0)) -> Decimal:
    b = _d(decimal_odds) - Decimal(1)
    if b <= 0:
        return Decimal(0)
    edge = expected_net_return(p_win, decimal_odds, p_push)
    if edge <= 0:
        return Decimal(0)
    return edge / b


def _with_stake_fields(
    evaluation: Evaluation,
    settings: BankrollSettings,
    rules: dict,
    decimal_odds: Decimal | None,
    *,
    is_parlay: bool,
    tier: str | None = None,
) -> Evaluation:
    if (
        evaluation.ev_per_unit is None
        or evaluation.ev_per_unit <= 0
        or tier is None
        or decimal_odds is None
        or evaluation.p_win is None
        or not evaluation.profile_eligible
        or evaluation.odds_timing != "fresh"
    ):
        evaluation.stake_fraction = Decimal(0)
        evaluation.stake_dollars = Decimal(0) if settings.bankroll is not None else None
        evaluation.stake_units = Decimal(0)
        evaluation.stake_explanation = "No positive stake. The wager is outside the current eligibility rules."
        if evaluation.ev_per_unit is not None and evaluation.ev_per_unit <= 0:
            evaluation.stake_explanation = "No positive stake. Estimated expected return is not positive."
        return evaluation

    kelly = fractional_kelly(evaluation.p_win, decimal_odds, evaluation.p_push)
    multiplier = _d(rules["kelly_fraction"][tier])
    uncertainty_scale = Decimal(1)
    if evaluation.p_positive_ev is not None:
        uncertainty_scale = _d(f"{evaluation.p_positive_ev:.6f}")
    if settings.use_kelly:
        fraction = kelly * multiplier * uncertainty_scale
        method = f"Fractional Kelly ({float(multiplier):.0%} of full Kelly) scaled by P(edge > 0)."
    else:
        unit = settings.unit_value(rules)
        if settings.bankroll and unit and settings.bankroll > 0:
            fraction = unit / settings.bankroll
        else:
            fraction = _d(rules["default_unit_fraction_of_bankroll"])
        if tier == "long_shot":
            fraction *= Decimal("0.5")
        method = "Flat unit sizing, with a smaller unit for the Long Shot tier. Kelly is off."
    if settings.correlated_with_card:
        fraction *= _d(rules["correlation_stake_multiplier"])
        method += " Correlation with the current card reduced the stake."
    cap = min(
        _d(rules["max_bankroll_fraction"][tier]),
        settings.max_wager_fraction,
        settings.max_parlay_fraction if is_parlay else settings.max_wager_fraction,
    )
    bankroll = settings.bankroll
    if bankroll is None or bankroll <= 0:
        evaluation.stake_fraction = min(fraction, cap)
        evaluation.stake_dollars = None
        evaluation.stake_units = None
        evaluation.stake_explanation = method + " Enter a hypothetical bankroll to convert the fraction into dollars."
        return evaluation

    room_card = max(Decimal(0), settings.max_card_fraction * bankroll - settings.card_staked)
    room_game = max(Decimal(0), settings.max_game_fraction * bankroll - settings.game_staked)
    room_wager = cap * bankroll
    raw_dollars = fraction * bankroll
    allowed = min(raw_dollars, room_card, room_game, room_wager)
    if allowed < 0:
        allowed = Decimal(0)
    evaluation.exposure_reduced = allowed + Decimal("0.0000001") < raw_dollars
    evaluation.stake_dollars = allowed.quantize(MONEY)
    evaluation.stake_fraction = (evaluation.stake_dollars / bankroll) if bankroll else Decimal(0)
    unit = settings.unit_value(rules)
    if unit and unit > 0:
        evaluation.stake_units = (evaluation.stake_dollars / unit).quantize(Decimal("0.01"))
    win_profit = evaluation.stake_dollars * (decimal_odds - Decimal(1))
    evaluation.potential_profit_dollars = win_profit.quantize(MONEY)
    evaluation.expected_profit_dollars = (evaluation.stake_dollars * evaluation.ev_per_unit).quantize(MONEY)
    evaluation.stake_explanation = (
        method
        + f" Tier cap is {float(rules['max_bankroll_fraction'][tier]):.1%} of bankroll."
        + (" The stake was reduced to stay inside an exposure limit." if evaluation.exposure_reduced else "")
    )
    evaluation.explanation.append(
        f"Suggested stake is ${float(evaluation.stake_dollars):.2f}. "
        f"Expected net profit at that stake is ${float(evaluation.expected_profit_dollars):.2f}. "
        f"Potential net profit if the wager wins is ${float(evaluation.potential_profit_dollars):.2f}. "
        "Expected profit is an average under the model, not a promised result."
    )
    return evaluation


def sort_key(evaluation: Evaluation) -> tuple:
    """Sort by conservative expected return, not by win probability."""
    conservative = evaluation.conservative_ev or Decimal("-1")
    ev = evaluation.ev_per_unit or Decimal("-1")
    return (conservative, ev)


def filter_logs(logs: list[dict], as_of: str, exclude_game_pk: int | None = None) -> list[dict]:
    """Keep appearances that were knowable before the game. Drops that game and later games."""
    visible = []
    for row in logs:
        day = row.get("date")
        game_pk = row.get("game_pk")
        if day is None:
            continue
        if day > as_of:
            continue
        if exclude_game_pk is not None and game_pk == exclude_game_pk:
            continue
        if day == as_of and exclude_game_pk is not None and game_pk == exclude_game_pk:
            continue
        visible.append(row)
    return visible


def same_game_dependent(left: dict, right: dict) -> bool:
    if left.get("game_id") != right.get("game_id"):
        return False
    if left.get("player_id") and left.get("player_id") == right.get("player_id"):
        return True
    left_market = left.get("market_type", "")
    right_market = right.get("market_type", "")
    if left_market.startswith("pitcher") and right_market.startswith("player"):
        return True
    if right_market.startswith("pitcher") and left_market.startswith("player"):
        return True
    return True


def joint_from_masks(masks: list[list[bool]]) -> Decimal | None:
    if not masks or any(len(mask) == 0 for mask in masks):
        return None
    length = len(masks[0])
    if any(len(mask) != length for mask in masks):
        return None
    wins = sum(1 for index in range(length) if all(mask[index] for mask in masks))
    return Decimal(wins) / Decimal(length)


def leg_win_mask(samples: list[float], line: float | None, side: str) -> list[bool]:
    if side in {"over", "under"}:
        mask = []
        for value in samples:
            if line is None:
                raise ValueError("A total needs a line.")
            if abs(float(value) - float(line)) < 1e-9:
                mask.append(False)
            elif side == "over":
                mask.append(float(value) > float(line))
            else:
                mask.append(float(value) < float(line))
        return mask
    if side == "moneyline":
        return [bool(value) for value in samples]
    raise ValueError(f"Unsupported parlay side {side}.")


# Re-export for market code that wants the outcome list beside evaluations.
SUPPORTED_PLAYER_MARKETS = {
    "player_hits",
    "player_total_bases",
    "player_home_runs",
    "player_walks",
    "player_strikeouts",
    "player_runs",
    "player_rbis",
    "player_stolen_bases",
    "player_hits_runs_rbis",
    "pitcher_strikeouts",
    "pitcher_walks",
    "pitcher_hits_allowed",
    "pitcher_outs",
}
SUPPORTED_GAME_MARKETS = {"moneyline", "run_line", "total", "team_total", "f5_moneyline", "f5_total"}


def market_is_supported(market_type: str) -> bool:
    return market_type in SUPPORTED_PLAYER_MARKETS or market_type in SUPPORTED_GAME_MARKETS


def evaluate_parlay(
    legs: list[dict],
    *,
    combined_american: int | None,
    quoted_at: datetime | None,
    now: datetime | None = None,
    game_start: datetime | None = None,
    price_source: str = "manual",
    settings: BankrollSettings | None = None,
    rules: dict | None = None,
    data_support: float | None = None,
    n_sims: int | None = None,
) -> tuple[Evaluation, dict]:
    """Price a parlay from joint outcomes. Never invents a Hard Rock combined price."""
    if len(legs) < 2:
        evaluation = classify_and_size(
            p_win=None,
            is_parlay=True,
            joint_method="unsupported",
            model_notes=["A parlay needs at least two legs."],
            settings=settings,
            rules=rules,
            price_source=price_source,
        )
        return evaluation, {"method": "unsupported", "joint_probability": None, "theoretical_decimal": None}

    same_game = len({leg.get("game_id") for leg in legs}) == 1
    dependent = any(same_game_dependent(legs[0], other) for other in legs[1:]) if same_game else False
    theoretical = None
    decimals = []
    for leg in legs:
        if leg.get("american") is not None:
            decimals.append(american_to_decimal(int(leg["american"])))
    if len(decimals) == len(legs):
        theoretical = Decimal(1)
        for price in decimals:
            theoretical *= price

    if same_game:
        if any(not leg.get("samples") for leg in legs):
            evaluation = classify_and_size(
                p_win=None,
                is_parlay=True,
                joint_method="unsupported",
                theoretical_price=False,
                model_notes=[
                    "These legs share a game, so multiplying their probabilities would ignore correlation.",
                    "No same-game simulation was available for the joint outcome.",
                    (
                        f"A theoretical product of the leg prices is {float(theoretical):.3f}. That number is not a Hard Rock Bet parlay price."
                        if theoretical is not None
                        else "No combined sportsbook price was supplied."
                    ),
                ],
                settings=settings,
                rules=rules,
                price_source=price_source,
                quoted_at=quoted_at,
                now=now,
                game_start=game_start,
            )
            return evaluation, {
                "method": "unsupported",
                "joint_probability": None,
                "theoretical_decimal": None if theoretical is None else float(theoretical),
                "dependent": True,
            }
        masks = [leg_win_mask(leg["samples"], leg.get("line"), leg["side"]) for leg in legs]
        joint = joint_from_masks(masks)
        method = "same_game_simulation"
    else:
        joint = Decimal(1)
        for leg in legs:
            joint *= _d(leg["p_win"])
        method = "independent_games"
        dependent = False

    if combined_american is None:
        evaluation = classify_and_size(
            p_win=joint,
            is_parlay=True,
            joint_method="theoretical",
            theoretical_price=True,
            model_notes=[
                f"Joint win frequency under {method.replace('_', ' ')} is {float(joint):.1%}.",
                (
                    f"Theoretical product of leg decimals is {float(theoretical):.3f}. This is not an available Hard Rock Bet price."
                    if theoretical is not None
                    else "No combined sportsbook price was supplied."
                ),
            ],
            settings=settings,
            rules=rules,
            price_source=price_source,
            data_support=data_support,
            n_sims=n_sims,
            quoted_at=quoted_at,
            now=now,
            game_start=game_start,
        )
        return evaluation, {
            "method": method,
            "joint_probability": float(joint),
            "theoretical_decimal": None if theoretical is None else float(theoretical),
            "dependent": dependent or same_game,
            "qualified": False,
        }

    evaluation = classify_and_size(
        p_win=joint,
        p_push=Decimal(0),
        american=combined_american,
        quoted_at=quoted_at,
        now=now,
        game_start=game_start,
        data_support=data_support if data_support is not None else min(leg.get("data_support", 0.5) for leg in legs),
        n_sims=n_sims if n_sims is not None else legs[0].get("n_sims"),
        price_source=price_source,
        settings=settings,
        rules=rules,
        is_parlay=True,
        joint_method=method,
        model_notes=[
            f"Joint probability uses {method.replace('_', ' ')} and is {float(joint):.1%}.",
            "The expected return uses the combined sportsbook price, not a multiplied margin.",
        ],
    )
    return evaluation, {
        "method": method,
        "joint_probability": float(joint),
        "theoretical_decimal": None if theoretical is None else float(theoretical),
        "dependent": dependent or same_game,
        "qualified": evaluation.presented_as_current_opportunity,
    }


def sample_stat_name(market_type: str) -> str | None:
    return {
        "player_hits": "hits",
        "player_total_bases": "total_bases",
        "player_home_runs": "home_runs",
        "player_walks": "walks",
        "player_strikeouts": "strikeouts",
        "player_runs": "runs",
        "player_rbis": "rbis",
        "player_stolen_bases": "stolen_bases",
        "player_hits_runs_rbis": "hits_runs_rbis",
        "pitcher_strikeouts": "strikeouts",
        "pitcher_walks": "walks",
        "pitcher_hits_allowed": "hits",
        "pitcher_outs": "outs",
        "pitcher_innings": "outs",
    }.get(market_type)
