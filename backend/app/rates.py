"""Hierarchical shrinkage and log5 matchup rates.

Priors are fixed in plate appearances or batters faced. A 4-for-7
head-to-head sample cannot overwhelm a season line.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import OUTCOMES, hit_rate, normalize_dist


@dataclass(frozen=True)
class ModelPriors:
    career_pa: float = 400.0
    season_pa: float = 250.0
    recent_pa: float = 150.0
    platoon_pa: float = 280.0
    bvp_pa: float = 80.0
    pitcher_bf: float = 300.0
    pitcher_recent_bf: float = 120.0


def shrink(observed: dict[str, float] | None, n: float, prior: dict[str, float], prior_n: float) -> dict[str, float]:
    if observed is None or n <= 0:
        return normalize_dist(prior)
    weight_n = max(n, 0.0)
    strength = max(prior_n, 0.0)
    mixed = {
        key: (weight_n * observed.get(key, 0.0) + strength * prior.get(key, 0.0)) / (weight_n + strength)
        for key in OUTCOMES
    }
    return normalize_dist(mixed)


def log5(batter: dict[str, float], pitcher: dict[str, float], league: dict[str, float]) -> dict[str, float]:
    """Multinomial log5. If the pitcher matches the league, the batter rates are unchanged."""
    raw = {}
    for key in OUTCOMES:
        league_p = max(league.get(key, 0.0), 1e-6)
        raw[key] = max(batter.get(key, 0.0), 1e-8) * max(pitcher.get(key, 0.0), 1e-8) / league_p
    return normalize_dist(raw)


def rescale_share(dist: dict[str, float], key: str, multiplier: float, cap: float = 0.55) -> dict[str, float]:
    """Change one outcome's share and spread the difference across the rest."""
    updated = dict(dist)
    new_value = min(cap, max(0.0, updated[key] * multiplier))
    rest_keys = [name for name in OUTCOMES if name != key]
    rest = sum(updated[name] for name in rest_keys)
    new_rest = 1.0 - new_value
    if rest <= 0:
        updated[key] = new_value
        return normalize_dist(updated)
    for name in rest_keys:
        updated[name] = updated[name] / rest * new_rest
    updated[key] = new_value
    return normalize_dist(updated)


def move_mass(dist: dict[str, float], source: str, dest: str, multiplier_on_dest: float) -> dict[str, float]:
    """Move probability between two outcomes. The distribution still sums to 1."""
    updated = dict(dist)
    delta = updated[dest] * (multiplier_on_dest - 1.0)
    delta = max(-updated[dest] * 0.5, min(delta, updated[source] * 0.5))
    updated[dest] += delta
    updated[source] -= delta
    return normalize_dist(updated)


def nested_skill(
    league: dict[str, float],
    career: tuple[dict[str, float] | None, float] | None,
    season: tuple[dict[str, float] | None, float] | None,
    recent: tuple[dict[str, float] | None, float] | None,
    platoon: tuple[dict[str, float] | None, float] | None,
    priors: ModelPriors,
    *,
    pitching: bool = False,
) -> list[tuple[str, dict[str, float]]]:
    season_strength = priors.pitcher_bf if pitching else priors.season_pa
    recent_strength = priors.pitcher_recent_bf if pitching else priors.recent_pa
    steps: list[tuple[str, dict[str, float]]] = [("league", normalize_dist(league))]
    current = steps[0][1]
    if career and career[0] is not None:
        current = shrink(career[0], career[1], current, priors.career_pa)
    steps.append(("career", current))
    if season and season[0] is not None:
        current = shrink(season[0], season[1], current, season_strength)
    steps.append(("season", current))
    if recent and recent[0] is not None:
        current = shrink(recent[0], recent[1], current, recent_strength)
    steps.append(("recent", current))
    if platoon and platoon[0] is not None:
        current = shrink(platoon[0], platoon[1], current, priors.platoon_pa)
    steps.append(("platoon", current))
    return steps


def waterfall_hit_rates(steps: list[tuple[str, dict[str, float]]]) -> list[dict]:
    rows = []
    previous = None
    for name, dist in steps:
        rate = hit_rate(dist)
        rows.append(
            {
                "name": name,
                "hit_per_pa": rate,
                "delta_vs_previous": 0.0 if previous is None else rate - previous,
            }
        )
        previous = rate
    return rows
