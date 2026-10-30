import json
import urllib.request

request = urllib.request.Request(
    "https://statsapi.mlb.com/api/v1/game/849803/boxscore",
    headers={"User-Agent": "Mozilla/5.0"},
)
with urllib.request.urlopen(request, timeout=45) as response:
    box = json.loads(response.read().decode())
for side in ("away", "home"):
    team = box["teams"][side]
    print("==", team["team"]["name"])
    rows = []
    for key, player in team["players"].items():
        order = player.get("battingOrder")
        if not order or int(order) % 100:
            continue
        stat = (player.get("stats") or {}).get("batting") or {}
        rows.append((int(order), player["person"]["fullName"], stat.get("summary"), stat.get("homeRuns"), stat.get("totalBases"), stat.get("rbi"), stat.get("strikeOuts")))
    for row in sorted(rows):
        print(f"  {row[1]:22} {row[2]} HR {row[3]} TB {row[4]} RBI {row[5]} K {row[6]}")
    for pid in team.get("pitchers") or []:
        player = team["players"][f"ID{pid}"]
        stat = player["stats"]["pitching"]
        print(f"  P {player['person']['fullName']:20} {stat.get('inningsPitched')} IP {stat.get('strikeOuts')} K {stat.get('numberOfPitches')} pitches {stat.get('note','')}")
