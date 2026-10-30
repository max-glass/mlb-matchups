import json
import urllib.request

request = urllib.request.Request(
    "https://statsapi.mlb.com/api/v1/game/849804/boxscore",
    headers={"User-Agent": "Mozilla/5.0"},
)
with urllib.request.urlopen(request, timeout=40) as response:
    box = json.loads(response.read().decode())
for side in ("away", "home"):
    print("==", side)
    rows = []
    for key, player in box["teams"][side]["players"].items():
        order = player.get("battingOrder")
        if not order:
            continue
        stat = (player.get("stats") or {}).get("batting") or {}
        rows.append((int(order), player["person"]["fullName"], player["person"]["id"], stat.get("summary")))
    for row in sorted(rows):
        print(row)
