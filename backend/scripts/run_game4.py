import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "scripts" / "game4_request.json").read_bytes()
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/matchups/analyze",
    data=body,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=300) as response:
    data = json.loads(response.read().decode())
print("id", data["analysis_id"])
print(data["game"]["status"], data["game"].get("lineup_status"), data["game"]["start_time_utc"], data["game"]["venue_name"])
print("series", data.get("series"))
print("warnings")
for warning in data["warnings"]:
    print(" -", warning)
print("win", data["win"])
for side in ("away", "home"):
    starter = data[side]["pitchers"][0]
    print(
        "SP",
        side,
        starter["name"],
        starter.get("player_id"),
        starter.get("role"),
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
        [(b["order"], b["name"], round(b["hit_probability"], 3), round(b["home_run_probability"], 3)) for b in data[side]["batters"]],
    )
    for pitcher in data[side]["pitchers"][1:]:
        print(
            " ",
            pitcher["name"],
            pitcher["availability_label"],
            "rest",
            pitcher.get("days_since_last"),
            "p1",
            pitcher.get("pitches_1d"),
            "p2",
            pitcher.get("pitches_2d"),
            "appear",
            None if pitcher["appearance_probability"] is None else round(pitcher["appearance_probability"], 2),
        )
(ROOT / "game4_id.txt").write_text(data["analysis_id"], encoding="utf-8")
