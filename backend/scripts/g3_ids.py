import json
import urllib.request

request = urllib.request.Request(
    "https://statsapi.mlb.com/api/v1/game/849804/boxscore",
    headers={"User-Agent": "Mozilla/5.0"},
)
with urllib.request.urlopen(request, timeout=40) as response:
    box = json.loads(response.read().decode())
for side in ("away", "home"):
    team = box["teams"][side]
    ids = []
    for pid in team["battingOrder"]:
        name = team["players"][f"ID{pid}"]["person"]["fullName"]
        ids.append(int(pid))
        print(side, pid, name)
    print(side, "IDS", ids)
