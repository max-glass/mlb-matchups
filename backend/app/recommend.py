"""Turn model samples and verified prices into tiered recommendations.

This module does not estimate probabilities from expected counts, and it does
not invent prices. Every qualified row is an Evaluation from app.evaluate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.evaluate import (
    BankrollSettings,
    classify_and_size,
    load_rules,
    market_is_supported,
    prop_probabilities,
    sample_stat_name,
    sort_key,
    spread_probabilities,
)

TIERS = ("conservative", "balanced", "aggressive", "long_shot")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _samples_for(sim: dict, analysis: dict, quote: dict) -> tuple[list | None, str | None]:
    market = quote["market_type"]
    side = quote["selection"]
    if market == "moneyline":
        wins = sim.get("away_win") or []
        if side == "home":
            return [0 if flag else 1 for flag in wins], "moneyline"
        if side == "away":
            return list(wins), "moneyline"
        return None, None
    if market == "f5_moneyline":
        away = sim.get("away_runs_5") or []
        home = sim.get("home_runs_5") or []
        if len(away) != len(home) or not away:
            return None, None
        if side == "away":
            return [1 if a > h else 0 if a < h else 0 for a, h in zip(away, home)], "f5"
        if side == "home":
            return [1 if h > a else 0 for a, h in zip(away, home)], "f5"
        return None, None
    if market in {"total", "f5_total"}:
        if market == "total":
            away, home = sim.get("away_runs") or [], sim.get("home_runs") or []
        else:
            away, home = sim.get("away_runs_5") or [], sim.get("home_runs_5") or []
        if len(away) != len(home) or not away:
            return None, None
        return [a + h for a, h in zip(away, home)], "total"
    if market == "team_total":
        key = "away_runs" if side == "away" else "home_runs" if side == "home" else None
        # selection is over/under; team_side says which team
        team_side = quote.get("team_side")
        key = "away_runs" if team_side == "away" else "home_runs" if team_side == "home" else None
        if key is None:
            return None, None
        return list(sim.get(key) or []), "total"
    if market == "run_line":
        away = sim.get("away_runs") or []
        home = sim.get("home_runs") or []
        team_side = quote.get("team_side")
        if team_side == "away":
            return [a - h for a, h in zip(away, home)], "spread"
        if team_side == "home":
            return [h - a for a, h in zip(away, home)], "spread"
        return None, None
    stat = sample_stat_name(market)
    if stat is None:
        return None, None
    player_id = str(quote.get("player_id") or "")
    bucket = "pitchers" if market.startswith("pitcher_") else "batters"
    player = (sim.get(bucket) or {}).get(player_id) or (sim.get(bucket) or {}).get(quote.get("player_id"))
    if not player or stat not in player:
        return None, None
    if market == "pitcher_innings":
        return [outs / 3 for outs in player.get("outs") or []], "prop"
    return list(player[stat]), "prop"


def _probabilities(kind: str, samples: list, quote: dict) -> dict[str, Decimal] | None:
    if not samples:
        return None
    if kind == "moneyline":
        wins = Decimal(sum(samples))
        n = Decimal(len(samples))
        # F5 moneyline can push. Full-game samples are 0/1 with a winner.
        if kind == "moneyline" and quote["market_type"] == "f5_moneyline":
            away = None
        p_win = wins / n
        return {"p_win": p_win, "p_push": Decimal(0), "p_loss": Decimal(1) - p_win}
    if kind == "f5":
        # samples are 1 for the selected side lead, 0 otherwise. Ties are not wins.
        # Reconstruct pushes as games where nobody leads: both stored as 0 is not available here.
        # The f5 sample uses 1 only for a lead, so ties are losses in this encoding.
        # Recompute from raw runs when present.
        return None
    if kind == "spread":
        if quote.get("line") is None:
            return None
        return spread_probabilities(samples, float(quote["line"]))
    if quote.get("line") is None or quote["selection"] not in {"over", "under"}:
        return None
    return prop_probabilities(samples, float(quote["line"]), quote["selection"])


def _f5_probabilities(sim: dict, side: str) -> dict[str, Decimal] | None:
    away = sim.get("away_runs_5") or []
    home = sim.get("home_runs_5") or []
    if not away or len(away) != len(home):
        return None
    wins = pushes = 0
    for a, h in zip(away, home):
        if a == h:
            pushes += 1
        elif (side == "away" and a > h) or (side == "home" and h > a):
            wins += 1
    n = Decimal(len(away))
    p_win = Decimal(wins) / n
    p_push = Decimal(pushes) / n
    return {"p_win": p_win, "p_push": p_push, "p_loss": Decimal(1) - p_win - p_push}


def recommend_quotes(
    analysis: dict,
    sim: dict,
    quotes: list[dict],
    settings: BankrollSettings,
    *,
    now: datetime | None = None,
    card_selections: list[dict] | None = None,
) -> dict:
    rules = load_rules()
    now = now or datetime.now(timezone.utc)
    game = analysis.get("game") or {}
    start = _parse_time(game.get("start_time_utc"))
    players = analysis.get("players") or {}
    selections = card_selections or []
    card_staked = sum(Decimal(str(item.get("stake_dollars") or 0)) for item in selections)
    by_game: dict[str, Decimal] = {}
    for item in selections:
        key = str(item.get("game_id") or game.get("game_pk") or game.get("date"))
        by_game[key] = by_game.get(key, Decimal(0)) + Decimal(str(item.get("stake_dollars") or 0))
    game_key = str(game.get("game_pk") or game.get("date"))

    rows = []
    for quote in quotes:
        market = quote["market_type"]
        supported = market_is_supported(market) or market == "pitcher_innings"
        notes = []
        player = players.get(str(quote.get("player_id"))) if quote.get("player_id") else None
        if player:
            notes.extend(player.get("notes") or [])
        data_support = None if player is None and market.startswith(("player_", "pitcher_")) else (
            player.get("data_support") if player else analysis.get("game_data_support", 0.75)
        )
        if market.startswith(("player_", "pitcher_")) and player is None:
            supported = False
            notes.append("The price did not match a player in this model's lineup or staff.")
        probs = None
        if market == "f5_moneyline":
            probs = _f5_probabilities(sim, quote["selection"])
        elif supported:
            samples, kind = _samples_for(sim, analysis, quote)
            if kind == "f5":
                probs = _f5_probabilities(sim, quote["selection"])
            elif samples is not None and kind is not None:
                probs = _probabilities(kind, samples, quote)
        if market == "pitcher_earned_runs":
            supported = False
            notes.append("Earned runs are not modeled because errors are not modeled. Runs allowed are a different market.")
        settings_for_quote = BankrollSettings(
            bankroll=settings.bankroll,
            unit_dollars=settings.unit_dollars,
            unit_fraction=settings.unit_fraction,
            risk_profile=settings.risk_profile,
            use_kelly=settings.use_kelly,
            max_wager_fraction=settings.max_wager_fraction,
            max_card_fraction=settings.max_card_fraction,
            max_game_fraction=settings.max_game_fraction,
            max_parlay_fraction=settings.max_parlay_fraction,
            card_staked=card_staked,
            game_staked=by_game.get(game_key, Decimal(0)),
            correlated_with_card=bool(selections),
        )
        evaluation = classify_and_size(
            p_win=None if probs is None else probs["p_win"],
            p_push=Decimal(0) if probs is None else probs["p_push"],
            american=quote.get("american"),
            other_side_american=quote.get("other_side_american"),
            quoted_at=_parse_time(quote.get("quoted_at")),
            now=now,
            game_start=start,
            data_support=data_support,
            n_sims=analysis.get("simulation_count"),
            market_supported=supported and probs is not None,
            price_source=quote.get("source") or "manual",
            model_notes=notes,
            settings=settings_for_quote,
            rules=rules,
        )
        rows.append(
            {
                "quote_id": quote.get("id"),
                "sportsbook": quote.get("sportsbook"),
                "source": quote.get("source"),
                "quoted_at": quote.get("quoted_at"),
                "market_type": market,
                "selection": quote.get("selection"),
                "line": quote.get("line"),
                "player_id": quote.get("player_id"),
                "player_name": (player or {}).get("name") or quote.get("player_name"),
                "team_side": quote.get("team_side"),
                "game_id": game.get("game_pk"),
                "game_label": f"{game.get('away_abbreviation')} at {game.get('home_abbreviation')}",
                "start_time_utc": game.get("start_time_utc"),
                "model_version": analysis.get("model_version"),
                "analysis_id": analysis.get("analysis_id"),
                "evaluation": evaluation.public(),
            }
        )

    def tier_rows(name: str) -> list[dict]:
        chosen = [
            row
            for row in rows
            if row["evaluation"]["tier"] == name and row["evaluation"]["status"] in {"Qualified Value", "Potential Value"}
        ]
        chosen.sort(key=lambda row: sort_key_public(row["evaluation"]), reverse=True)
        return chosen

    tiers = {name: tier_rows(name) for name in TIERS}
    current = [row for row in rows if row["evaluation"]["presented_as_current_opportunity"]]
    return {
        "rule_version": rules["rule_version"],
        "risk_profile": settings.risk_profile,
        "tiers": tiers,
        "not_current": [
            row
            for row in rows
            if row["evaluation"]["status"] in {"Watchlist", "Stale Odds", "Exposure Limit Reached"}
        ],
        "ineligible": [
            row
            for row in rows
            if row["evaluation"]["status"] in {"No Value", "Insufficient Confidence", "Informational Only"}
        ],
        "summary": {
            "markets_analyzed": len(rows),
            "qualified_current": len(current),
            "by_tier": {name: len(tiers[name]) for name in TIERS},
            "empty_tiers": [name for name in TIERS if not tiers[name]],
            "highest_ev": max((row["evaluation"]["ev_per_unit"] or -1) for row in current) if current else None,
            "note": "Tier membership requires positive expected value at the quoted price, not a high win probability. Empty tiers are intentional.",
        },
        "rows": rows,
    }


def sort_key_public(evaluation: dict) -> tuple:
    return (
        evaluation.get("conservative_ev") if evaluation.get("conservative_ev") is not None else -1,
        evaluation.get("ev_per_unit") if evaluation.get("ev_per_unit") is not None else -1,
    )
