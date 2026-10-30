import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = json.dumps(
    {
        "date": "2026-10-26",
        "away_team_id": 158,
        "home_team_id": 139,
        "simulation_count": 2000,
        "seed": 26,
    }
).encode()
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/matchups/analyze",
    data=body,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=300) as response:
    data = json.loads(response.read().decode())
print("id", data["analysis_id"])
print(data["game"]["away_name"], "at", data["game"]["home_name"], data["game"]["status"], data["game"].get("lineup_status"))
print("start", data["game"]["start_time_utc"], data["game"]["venue_name"])
print("series", data.get("series"))
print("warnings", data["warnings"])
print("win", data["win"])
for side in ("away", "home"):
    starter = data[side]["pitchers"][0]
    print(
        "SP",
        side,
        starter["name"],
        starter["player_id"],
        "IP",
        round(starter["expected_innings"], 2),
        "K",
        round(starter["expected_strikeouts"], 2),
        "rest",
        starter.get("days_since_last"),
        "p1",
        starter.get("pitches_1d"),
        "p2",
        starter.get("pitches_2d"),
        "p3",
        starter.get("pitches_3d"),
    )
    print(
        " lineup",
        [
            (b["order"], b["name"], b["player_id"], round(b["hit_probability"], 3), round(b["home_run_probability"], 3), round(b["expected_hits"], 2))
            for b in data[side]["batters"]
        ],
    )
    print(" pen")
    for pitcher in data[side]["pitchers"][1:]:
        print(
            " ",
            pitcher["name"],
            pitcher["availability_label"],
            "avail",
            None if pitcher["availability"] is None else round(pitcher["availability"], 2),
            "rest",
            pitcher.get("days_since_last"),
            "p1",
            pitcher.get("pitches_1d"),
            "p2",
            pitcher.get("pitches_2d"),
            "p3",
            pitcher.get("pitches_3d"),
            "appear",
            None if pitcher["appearance_probability"] is None else round(pitcher["appearance_probability"], 2),
        )
(ROOT / "game3_id.txt").write_text(data["analysis_id"], encoding="utf-8")
