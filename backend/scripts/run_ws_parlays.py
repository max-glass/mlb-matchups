"""Run the World Series model and build three same-game parlays from the simulation."""

from __future__ import annotations

import json
import urllib.request
from itertools import combinations
from pathlib import Path

import gzip

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ws_parlay_report.json"


def post_analysis() -> dict:
    body = json.dumps(
        {
            "date": "2026-10-23",
            "away_team_id": 139,
            "home_team_id": 158,
            "simulation_count": 2000,
            "seed": 23,
        }
    ).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/matchups/analyze",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read().decode())


def load_sim(path: str) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def leg_mask(sim: dict, leg: dict) -> list[bool]:
    market = leg["market"]
    if market == "moneyline":
        wins = sim["away_win"]
        if leg["side"] == "away":
            return [bool(flag) for flag in wins]
        return [not bool(flag) for flag in wins]
    if market == "team_total_over":
        key = "away_runs" if leg["side"] == "away" else "home_runs"
        return [value > leg["line"] for value in sim[key]]
    if market == "total_over":
        return [a + h > leg["line"] for a, h in zip(sim["away_runs"], sim["home_runs"])]
    player = sim["batters"][str(leg["player_id"])]
    stat = player[leg["stat"]]
    return [value > leg["line"] for value in stat]


def summarize(analysis: dict, sim: dict) -> dict:
    n = analysis["simulation_count"]
    players = []
    for side in ("away", "home"):
        for batter in analysis[side]["batters"]:
            samples = sim["batters"][str(batter["player_id"])]
            players.append(
                {
                    "side": side,
                    "team": analysis["game"]["away_abbreviation"] if side == "away" else analysis["game"]["home_abbreviation"],
                    "player_id": batter["player_id"],
                    "name": batter["name"],
                    "order": batter["order"],
                    "p_hit": sum(v >= 1 for v in samples["hits"]) / n,
                    "p_hr": sum(v >= 1 for v in samples["home_runs"]) / n,
                    "p_tb2": sum(v >= 2 for v in samples["total_bases"]) / n,
                    "p_rbi": sum(v >= 1 for v in samples["rbis"]) / n,
                    "expected_hits": sum(samples["hits"]) / n,
                    "support": batter["data_support"],
                }
            )
    return {
        "n": n,
        "away_win": sum(sim["away_win"]) / n,
        "home_win": 1 - sum(sim["away_win"]) / n,
        "players": players,
        "away_runs": sum(sim["away_runs"]) / n,
        "home_runs": sum(sim["home_runs"]) / n,
    }


def build_candidates(analysis: dict, sim: dict) -> list[dict]:
    """Two- and three-leg same-game parlays. Joint rate is the simulation, not a product."""
    catalog = []
    for side, abbr in (
        ("away", analysis["game"]["away_abbreviation"]),
        ("home", analysis["game"]["home_abbreviation"]),
    ):
        catalog.append(
            {
                "id": f"ml-{side}",
                "player_id": None,
                "label": f"{analysis['game']['away_name'] if side == 'away' else analysis['game']['home_name']} moneyline",
                "market": "moneyline",
                "side": side,
                "stat": None,
                "line": None,
                "group": f"ml-{side}",
            }
        )
        for batter in analysis[side]["batters"]:
            pid = batter["player_id"]
            base = {"player_id": pid, "side": side, "team": abbr, "name": batter["name"]}
            catalog.append({**base, "id": f"hit-{pid}", "label": f"{batter['name']} over 0.5 hits", "market": "prop", "stat": "hits", "line": 0.5, "group": f"player-{pid}"})
            catalog.append({**base, "id": f"tb-{pid}", "label": f"{batter['name']} over 1.5 total bases", "market": "prop", "stat": "total_bases", "line": 1.5, "group": f"player-{pid}"})
            catalog.append({**base, "id": f"hr-{pid}", "label": f"{batter['name']} over 0.5 home runs", "market": "prop", "stat": "home_runs", "line": 0.5, "group": f"player-{pid}-hr"})
            catalog.append({**base, "id": f"rbi-{pid}", "label": f"{batter['name']} over 0.5 RBIs", "market": "prop", "stat": "rbis", "line": 0.5, "group": f"player-{pid}-rbi"})

    masks = {leg["id"]: leg_mask(sim, leg) for leg in catalog}
    n = len(next(iter(masks.values())))
    marginal = {leg_id: sum(mask) / n for leg_id, mask in masks.items()}
    by_id = {leg["id"]: leg for leg in catalog}

    found = []
    for size in (2, 3):
        for combo in combinations(catalog, size):
            groups = [leg["group"] for leg in combo]
            if len(groups) != len(set(groups)):
                continue
            # A home run already includes a hit and total bases. Do not pair those for the same player.
            players = [leg.get("player_id") for leg in combo if leg.get("player_id")]
            if len(players) != len(set(players)):
                continue
            joint_hits = sum(all(masks[leg["id"]][i] for leg in combo) for i in range(n))
            joint = joint_hits / n
            if joint <= 0:
                continue
            product = 1.0
            for leg in combo:
                product *= marginal[leg["id"]]
            legs_out = []
            for leg in combo:
                legs_out.append(
                    {
                        "label": leg["label"],
                        "model_probability": round(marginal[leg["id"]], 4),
                    }
                )
            found.append(
                {
                    "legs": legs_out,
                    "leg_ids": [leg["id"] for leg in combo],
                    "joint_probability": round(joint, 4),
                    "independent_product": round(product, 4),
                    "correlation_gap": round(joint - product, 4),
                    "loss_probability": round(1 - joint, 4),
                    "break_even_decimal": round(1 / joint, 3),
                    "break_even_american": american_from_decimal(1 / joint),
                    "size": size,
                }
            )
    return found


def american_from_decimal(decimal_odds: float) -> str:
    if decimal_odds == 2:
        return "+100"
    if decimal_odds > 2:
        return f"+{round((decimal_odds - 1) * 100)}"
    return str(round(-100 / (decimal_odds - 1)))


def pick(candidates: list[dict]) -> dict:
    two = [row for row in candidates if row["size"] == 2]
    three = [row for row in candidates if row["size"] == 3]

    def both_safe(row: dict) -> bool:
        return all(leg["model_probability"] >= 0.62 for leg in row["legs"])

    conservative_pool = [row for row in two if both_safe(row) and row["joint_probability"] >= 0.40]
    conservative = max(conservative_pool, key=lambda row: (row["joint_probability"], -abs(row["correlation_gap"])))

    used = set(conservative["leg_ids"])
    balanced_pool = [
        row
        for row in two
        if set(row["leg_ids"]).isdisjoint(used)
        and 0.28 <= row["joint_probability"] <= 0.45
        and all(0.50 <= leg["model_probability"] <= 0.72 for leg in row["legs"])
    ]
    balanced = max(balanced_pool, key=lambda row: row["joint_probability"])
    used |= set(balanced["leg_ids"])

    aggressive_pool = [
        row
        for row in three
        if set(row["leg_ids"]).isdisjoint(used)
        and any("home runs" in leg["label"] or "RBIs" in leg["label"] for leg in row["legs"])
        and 0.08 <= row["joint_probability"] <= 0.20
        and min(leg["model_probability"] for leg in row["legs"]) >= 0.18
    ]
    aggressive = max(aggressive_pool, key=lambda row: row["joint_probability"])
    return {"conservative": conservative, "balanced": balanced, "aggressive": aggressive}


def latest_saved() -> tuple[dict, dict]:
    import sqlite3

    connection = sqlite3.connect(ROOT / "app" / "data" / "app.db")
    row = connection.execute(
        "select id, payload_json, sim_path from analyses order by created_at desc limit 1"
    ).fetchone()
    connection.close()
    analysis = json.loads(row[1])
    return analysis, load_sim(row[2])


def main() -> None:
    analysis, sim = latest_saved()
    summary = summarize(analysis, sim)
    candidates = build_candidates(analysis, sim)
    chosen = pick(candidates)
    report = {
        "game": analysis["game"],
        "series": analysis.get("series"),
        "win": analysis["win"],
        "warnings": analysis["warnings"],
        "starters": {
            "away": analysis["away"]["pitchers"][0],
            "home": analysis["home"]["pitchers"][0],
        },
        "summary": summary,
        "parlays": chosen,
        "price_note": "Break-even prices are calculations from the simulation frequency. They are not Hard Rock Bet quotes. No sportsbook parlay price was available, so expected value is not claimed.",
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
