import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}


def get(url: str) -> dict:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode())


schedule = get(
    "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2026-10-23&endDate=2026-10-26"
    "&gameType=W&hydrate=probablePitcher,lineups,team,seriesStatus,decisions"
)
for block in schedule.get("dates") or []:
    for game in block.get("games") or []:
        sides = game["teams"]
        print(
            block["date"],
            game["gamePk"],
            sides["away"]["team"]["abbreviation"] if "abbreviation" in sides["away"]["team"] else sides["away"]["team"]["name"],
            sides["away"].get("score"),
            "at",
            sides["home"]["team"].get("abbreviation") or sides["home"]["team"]["name"],
            sides["home"].get("score"),
            game["status"]["detailedState"],
            game.get("gameDate"),
        )
        print(
            "  prob",
            (sides["away"].get("probablePitcher") or {}).get("fullName"),
            "/",
            (sides["home"].get("probablePitcher") or {}).get("fullName"),
        )
        series = game.get("seriesStatus") or {}
        print("  series", series.get("shortDescription"), series.get("result"), series.get("wins"), series.get("losses"))
        lineups = game.get("lineups") or {}
        for key in ("awayPlayers", "homePlayers"):
            print(" ", key, [player.get("fullName") for player in (lineups.get(key) or [])])
