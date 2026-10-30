import numpy as np

from app.simulation import SimBatter, SimClub, SimPitcher, dist_from_rates, simulate_games


def _dist(**overrides):
    base = {
        "strikeout": 0.22,
        "walk": 0.08,
        "hit_by_pitch": 0.01,
        "single": 0.15,
        "double": 0.05,
        "triple": 0.005,
        "home_run": 0.03,
        "out": 0.455,
    }
    base.update(overrides)
    return dist_from_rates(base)


def _club(team_id, starter_role, typical, batter_ids, pen_ids):
    starter = SimPitcher(team_id * 10, "Starter", "R", starter_role, 0.9, typical)
    pen = [SimPitcher(pid, f"Pen {pid}", "L" if pid % 2 == 0 else "R", "reliever", 0.8, 16) for pid in pen_ids]
    pitchers = [starter, *pen]
    batters = []
    for bid in batter_ids:
        batters.append(
            SimBatter(
                bid,
                f"Batter {bid}",
                "L",
                {pitcher.player_id: _dist() for pitcher in pitchers},
            )
        )
    return SimClub(team_id, batters, starter, pen)


def test_simulation_is_reproducible_and_a_home_run_is_a_hit():
    away = _club(1, "starter", 90, list(range(1, 10)), [101, 102])
    home = _club(2, "starter", 90, list(range(11, 20)), [201, 202])
    first = simulate_games(away, home, 40, 7, postseason=True)
    second = simulate_games(away, home, 40, 7, postseason=True)
    assert first.away_runs == second.away_runs
    assert abs(sum(first.away_win) / 40 + (40 - sum(first.away_win)) / 40 - 1) < 1e-9
    hits = np.array(first.batter[1]["hits"])
    home_runs = np.array(first.batter[1]["home_runs"])
    assert np.all(hits >= home_runs)
    assert min(first.away_runs) >= 0


def test_opener_leaves_early():
    away = _club(1, "opener_or_reliever", 14, list(range(1, 10)), [101, 102, 103])
    home = _club(2, "starter", 95, list(range(11, 20)), [201])
    result = simulate_games(away, home, 60, 3, postseason=True)
    mean_outs = float(np.mean(result.pitcher[10]["outs"]))
    assert mean_outs < 12
