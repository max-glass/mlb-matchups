import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}


def get(url: str) -> dict:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode())


box = get("https://statsapi.mlb.com/api/v1/game/849804/boxscore")
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

game = get("https://statsapi.mlb.com/api/v1.1/game/849803/feed/live")
gd = game.get("gameData") or {}
print("STATUS", (gd.get("status") or {}).get("detailedState"))
probs = gd.get("probablePitchers") or {}
print("PROB", {side: (probs.get(side) or {}).get("fullName") for side in ("away", "home")})
print("DATETIME", gd.get("datetime"))
