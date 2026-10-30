"""Rank Game 2 outcomes by simulation frequency. No sportsbook prices."""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def american(decimal_odds: float) -> str:
    if decimal_odds >= 2:
        return f"+{round((decimal_odds - 1) * 100)}"
    return str(round(-100 / (decimal_odds - 1)))


def main() -> None:
    connection = sqlite3.connect(ROOT / "app" / "data" / "app.db")
    row = connection.execute(
        "select payload_json, sim_path from analyses where id = ?",
        ("6dc32260-e340-4566-ba3f-fec6c7994138",),
    ).fetchone()
    connection.close()
    analysis = json.loads(row[0])
    with gzip.open(row[1], "rt", encoding="utf-8") as handle:
        sim = json.load(handle)
    n = len(sim["away_win"])
    names = {}
    for side in ("away", "home"):
        for batter in analysis[side]["batters"]:
            names[str(batter["player_id"])] = batter["name"]
        for pitcher in analysis[side]["pitchers"]:
            names[str(pitcher["player_id"])] = pitcher["name"]

    bets = []
    bets.append(("Milwaukee Brewers moneyline", sum(1 - w for w in sim["away_win"]) / n))
    bets.append(("Tampa Bay Rays moneyline", sum(sim["away_win"]) / n))
    bets.append(("Game total under 7.5", sum(a + h < 7.5 for a, h in zip(sim["away_runs"], sim["home_runs"])) / n))
    bets.append(("Game total under 6.5", sum(a + h < 6.5 for a, h in zip(sim["away_runs"], sim["home_runs"])) / n))
    bets.append(("Game total over 5.5", sum(a + h > 5.5 for a, h in zip(sim["away_runs"], sim["home_runs"])) / n))

    for side, key in (("Rays", "away_runs"), ("Brewers", "home_runs")):
        runs = sim[key]
        bets.append((f"{side} team total under 3.5", sum(v < 3.5 for v in runs) / n))
        bets.append((f"{side} team total over 1.5", sum(v > 1.5 for v in runs) / n))

    for bucket, label in (("batters", "batter"), ("pitchers", "pitcher")):
        for pid, stats in sim[bucket].items():
            name = names.get(pid, pid)
            if "hits" in stats and "total_bases" in stats:
                bets.append((f"{name} over 0.5 hits", sum(v >= 1 for v in stats["hits"]) / n))
                bets.append((f"{name} over 1.5 total bases", sum(v >= 2 for v in stats["total_bases"]) / n))
                bets.append((f"{name} over 0.5 home runs", sum(v >= 1 for v in stats["home_runs"]) / n))
                bets.append((f"{name} over 0.5 RBIs", sum(v >= 1 for v in stats["rbis"]) / n))
            if "strikeouts" in stats and bucket == "pitchers":
                ks = stats["strikeouts"]
                if sum(stats["batters_faced"]) / n > 2:
                    bets.append((f"{name} over 3.5 strikeouts", sum(v >= 4 for v in ks) / n))
                    bets.append((f"{name} over 4.5 strikeouts", sum(v >= 5 for v in ks) / n))
                    bets.append((f"{name} over 5.5 strikeouts", sum(v >= 6 for v in ks) / n))

    bets.sort(key=lambda item: item[1], reverse=True)
    print(f"n={n}")
    for label, probability in bets:
        if probability < 0.55:
            continue
        decimal = 1 / probability
        print(f"{probability:.3f}  {american(decimal):>6}  {label}")

    # Sensible parlay: the two highest hit rates on opposite teams, joint from the sim.
    hitters = []
    for pid, stats in sim["batters"].items():
        hitters.append((names.get(pid, pid), pid, [v >= 1 for v in stats["hits"]]))
    hitters.sort(key=lambda item: sum(item[2]), reverse=True)
    print("--- top hits ---")
    for name, pid, mask in hitters[:8]:
        print(name, sum(mask) / n)

    a, b = hitters[0], hitters[1]
    # prefer opposite teams if the top two are same team
    teams = {}
    for side in ("away", "home"):
        for batter in analysis[side]["batters"]:
            teams[str(batter["player_id"])] = side
    pair = None
    for left in hitters[:6]:
        for right in hitters[:6]:
            if left[1] >= right[1]:
                continue
            if teams[left[1]] == teams[right[1]]:
                continue
            joint = sum(x and y for x, y in zip(left[2], right[2])) / n
            if pair is None or joint > pair[0]:
                pair = (joint, left[0], right[0])
    print("best cross-team hit parlay", pair)


if __name__ == "__main__":
    main()
