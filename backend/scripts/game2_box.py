import json
import urllib.request

request = urllib.request.Request(
    "https://statsapi.mlb.com/api/v1/game/849802/boxscore",
    headers={"User-Agent": "Mozilla/5.0"},
)
with urllib.request.urlopen(request, timeout=45) as response:
    box = json.loads(response.read().decode())

for side in ("away", "home"):
    team = box["teams"][side]
    print("==", team["team"]["name"])
    for pid in team.get("battingOrder") or []:
        player = team["players"][f"ID{pid}"]
        stat = player["stats"]["batting"]
        print(
            f"  {player['person']['fullName']:22} {stat.get('summary')} HR {stat.get('homeRuns')} TB {stat.get('totalBases')} R {stat.get('runs')} RBI {stat.get('rbi')} K {stat.get('strikeOuts')}"
        )
    for pid in team.get("pitchers") or []:
        player = team["players"][f"ID{pid}"]
        stat = player["stats"]["pitching"]
        print(
            f"  P {player['person']['fullName']:20} {stat.get('inningsPitched')} IP {stat.get('strikeOuts')} K {stat.get('numberOfPitches')} pitches {stat.get('note','')}"
        )
