"""Monte Carlo game simulation.

Player props read these samples. They do not treat an expected count as a probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.domain import OUTCOMES

K, BB, HBP, SINGLE, DOUBLE, TRIPLE, HR, OUT = range(8)


@dataclass
class SimPitcher:
    player_id: int
    name: str
    throws: str
    role: str
    availability: float
    typical_pitches: float
    pitches_per_batter: float = 3.9


@dataclass
class SimBatter:
    player_id: int
    name: str
    bats: str
    distributions: dict[int, np.ndarray]
    stolen_base_rate: float = 0.02


@dataclass
class SimClub:
    team_id: int
    batters: list[SimBatter]
    starter: SimPitcher
    bullpen: list[SimPitcher]


@dataclass
class SimResult:
    away_runs: list[int] = field(default_factory=list)
    home_runs: list[int] = field(default_factory=list)
    away_runs_5: list[int] = field(default_factory=list)
    home_runs_5: list[int] = field(default_factory=list)
    away_win: list[int] = field(default_factory=list)
    batter: dict[int, dict[str, list[int]]] = field(default_factory=dict)
    pitcher: dict[int, dict[str, list[int]]] = field(default_factory=dict)


def _empty_batter():
    return {key: [] for key in ("pa", "hits", "total_bases", "home_runs", "walks", "strikeouts", "runs", "rbis", "stolen_bases", "hits_runs_rbis")}


def _empty_pitcher():
    return {key: [] for key in ("batters_faced", "strikeouts", "walks", "hits", "runs", "outs", "pitches")}


def _choose_reliever(rng: np.random.Generator, club: SimClub, batter: SimBatter, used: dict[int, int], inning: int, margin: int) -> SimPitcher | None:
    candidates = []
    weights = []
    for pitcher in club.bullpen:
        if pitcher.player_id == club.starter.player_id:
            continue
        if used.get(pitcher.player_id, 0) > max(25, pitcher.typical_pitches * 1.5):
            continue
        if pitcher.availability < 0.08:
            continue
        weight = max(pitcher.availability, 0.05)
        if inning >= 9 and abs(margin) <= 2 and pitcher.role == "closer":
            weight *= 3.0
        elif inning >= 7 and abs(margin) <= 3 and pitcher.role == "setup":
            weight *= 2.2
        elif inning <= 5 and pitcher.role == "long":
            weight *= 1.8
        elif pitcher.role == "closer" and (inning < 8 or abs(margin) >= 4):
            weight *= 0.25
        if pitcher.throws == batter.bats and pitcher.throws in {"L", "R"}:
            weight *= 1.35
        candidates.append(pitcher)
        weights.append(weight)
    if not candidates:
        return None
    probs = np.asarray(weights, dtype=float)
    probs /= probs.sum()
    return candidates[int(rng.choice(len(candidates), p=probs))]


def _advance(bases: list[dict | None], batter_id: int, pitcher_id: int, outcome: int, rng: np.random.Generator):
    scored: list[dict] = []

    def score(runner):
        if runner is not None:
            scored.append(runner)

    if outcome in {BB, HBP}:
        new = [None, None, None]
        new[0] = {"batter_id": batter_id, "pitcher_id": pitcher_id}
        if bases[0] is not None:
            new[1] = bases[0]
            if bases[1] is not None:
                new[2] = bases[1]
                if bases[2] is not None:
                    score(bases[2])
            elif bases[2] is not None:
                new[2] = bases[2]
        else:
            new[1] = bases[1]
            new[2] = bases[2]
        return new, scored
    if outcome == SINGLE:
        score(bases[2])
        if bases[1] is not None and rng.random() < 0.45:
            score(bases[1])
            third = None
        else:
            third = bases[1]
        return [{"batter_id": batter_id, "pitcher_id": pitcher_id}, bases[0], third], scored
    if outcome == DOUBLE:
        score(bases[2])
        score(bases[1])
        if bases[0] is not None and rng.random() < 0.55:
            score(bases[0])
            third = None
        else:
            third = bases[0]
        return [None, {"batter_id": batter_id, "pitcher_id": pitcher_id}, third], scored
    if outcome == TRIPLE:
        for runner in bases:
            score(runner)
        return [None, None, {"batter_id": batter_id, "pitcher_id": pitcher_id}], scored
    if outcome == HR:
        for runner in bases:
            score(runner)
        scored.append({"batter_id": batter_id, "pitcher_id": pitcher_id})
        return [None, None, None], scored
    return bases, scored


def simulate_games(away: SimClub, home: SimClub, n: int, seed: int, *, postseason: bool) -> SimResult:
    rng = np.random.default_rng(seed)
    result = SimResult()
    clubs = [away, home]
    for club in clubs:
        for batter in club.batters:
            result.batter[batter.player_id] = _empty_batter()
        for pitcher in [club.starter, *club.bullpen]:
            result.pitcher.setdefault(pitcher.player_id, _empty_pitcher())

    for _ in range(n):
        scores = [0, 0]
        index = [0, 0]
        inning_runs = [[0, 0] for _ in range(16)]
        current = [away.starter, home.starter]
        pitches = {away.starter.player_id: 0, home.starter.player_id: 0}
        hooks = {}
        for club, pitcher in ((away, away.starter), (home, home.starter)):
            center = pitcher.typical_pitches if pitcher.role == "opener_or_reliever" else max(75.0, pitcher.typical_pitches)
            scale = 4.0 if pitcher.role == "opener_or_reliever" else 8.0
            hooks[pitcher.player_id] = float(np.clip(rng.normal(center, scale), 8, 120))
        game_bat = {bid: {k: 0 for k in result.batter[bid]} for club in clubs for bid in [b.player_id for b in club.batters]}
        game_pit = {pid: {k: 0 for k in ("batters_faced", "strikeouts", "walks", "hits", "runs", "outs", "pitches")} for pid in result.pitcher}
        runs_charged = {pid: 0 for pid in result.pitcher}

        for inning in range(1, 16):
            for half, club in enumerate(clubs):
                if inning >= 9 and half == 1 and scores[1] > scores[0]:
                    break
                if inning >= 10 and not postseason:
                    prev = club.batters[(index[half] - 1) % len(club.batters)]
                    bases = [None, {"batter_id": prev.player_id, "pitcher_id": None}, None]
                else:
                    bases = [None, None, None]
                outs = 0
                while outs < 3:
                    batter = club.batters[index[half] % len(club.batters)]
                    index[half] += 1
                    defense = clubs[1 - half]
                    pitcher = current[1 - half]
                    if pitcher.player_id not in hooks:
                        hooks[pitcher.player_id] = float(np.clip(rng.normal(max(12.0, pitcher.typical_pitches), 3.0), 8, 45))
                    probs = batter.distributions.get(pitcher.player_id)
                    if probs is None:
                        probs = np.full(8, 1 / 8)
                    outcome = int(rng.choice(8, p=np.asarray(probs, dtype=float)))
                    thrown = max(1, int(rng.normal(pitcher.pitches_per_batter, 0.8)))
                    pitches[pitcher.player_id] = pitches.get(pitcher.player_id, 0) + thrown
                    row = game_bat[batter.player_id]
                    prow = game_pit[pitcher.player_id]
                    row["pa"] += 1
                    prow["batters_faced"] += 1
                    prow["pitches"] += thrown
                    if outcome == K:
                        row["strikeouts"] += 1
                        prow["strikeouts"] += 1
                        outs += 1
                    elif outcome == OUT:
                        prow["outs"] += 1
                        outs += 1
                    elif outcome in {BB, HBP}:
                        row["walks"] += 1
                        prow["walks"] += 1
                        bases, scored = _advance(bases, batter.player_id, pitcher.player_id, outcome, rng)
                        _score(scored, scores, half, inning, inning_runs, row, game_bat, runs_charged)
                    else:
                        bases_added = {SINGLE: 1, DOUBLE: 2, TRIPLE: 3, HR: 4}[outcome]
                        row["hits"] += 1
                        row["total_bases"] += bases_added
                        prow["hits"] += 1
                        if outcome == HR:
                            row["home_runs"] += 1
                        bases, scored = _advance(bases, batter.player_id, pitcher.player_id, outcome, rng)
                        _score(scored, scores, half, inning, inning_runs, row, game_bat, runs_charged)
                    if bases[0] is not None and bases[1] is None and outs < 2 and rng.random() < batter.stolen_base_rate:
                        if rng.random() < 0.75:
                            bases[1] = bases[0]
                            bases[0] = None
                            row["stolen_bases"] += 1
                        else:
                            bases[0] = None
                            outs += 1
                    if pitcher.player_id == defense.starter.player_id and pitches[pitcher.player_id] >= hooks[pitcher.player_id]:
                        relief = _choose_reliever(rng, defense, batter, pitches, inning, scores[1 - half] - scores[half])
                        if relief is not None:
                            current[1 - half] = relief
                    elif pitcher.player_id != defense.starter.player_id and pitches.get(pitcher.player_id, 0) >= hooks.get(pitcher.player_id, 20):
                        relief = _choose_reliever(rng, defense, batter, pitches, inning, scores[1 - half] - scores[half])
                        if relief is not None and relief.player_id != pitcher.player_id:
                            current[1 - half] = relief
                if inning >= 9 and half == 1 and scores[1] != scores[0]:
                    break
            if inning >= 9 and scores[0] != scores[1]:
                break
        if scores[0] == scores[1]:
            scores[int(rng.integers(0, 2))] += 1
        for club in clubs:
            for batter in club.batters:
                row = game_bat[batter.player_id]
                row["hits_runs_rbis"] = row["hits"] + row["runs"] + row["rbis"]
                for key, value in row.items():
                    result.batter[batter.player_id][key].append(value)
        for pid, row in game_pit.items():
            row["runs"] = runs_charged.get(pid, 0)
            for key, value in row.items():
                result.pitcher[pid][key].append(value)
        result.away_runs.append(scores[0])
        result.home_runs.append(scores[1])
        result.away_runs_5.append(sum(pair[0] for pair in inning_runs[:5]))
        result.home_runs_5.append(sum(pair[1] for pair in inning_runs[:5]))
        result.away_win.append(1 if scores[0] > scores[1] else 0)
    return result


def _score(scored, scores, half, inning, inning_runs, row, game_bat, runs_charged):
    for runner in scored:
        scores[half] += 1
        if inning <= 16:
            inning_runs[inning - 1][half] += 1
        row["rbis"] += 1
        batter_id = runner["batter_id"]
        if batter_id in game_bat:
            game_bat[batter_id]["runs"] += 1
        responsible = runner.get("pitcher_id")
        if responsible is not None:
            runs_charged[responsible] = runs_charged.get(responsible, 0) + 1


def dist_from_rates(rates: dict[str, float]) -> np.ndarray:
    vector = np.array([rates[name] for name in OUTCOMES], dtype=float)
    total = vector.sum()
    if total <= 0:
        return np.full(8, 1 / 8)
    return vector / total
