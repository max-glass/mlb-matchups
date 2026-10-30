import gzip
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
connection = sqlite3.connect(ROOT / "app" / "data" / "app.db")
payload, path = connection.execute(
    "select payload_json, sim_path from analyses where id = ?",
    ("6dc32260-e340-4566-ba3f-fec6c7994138",),
).fetchone()
connection.close()
analysis = json.loads(payload)
with gzip.open(path, "rt", encoding="utf-8") as handle:
    sim = json.load(handle)
n = len(sim["away_win"])
names = {}
for side in ("away", "home"):
    for batter in analysis[side]["batters"]:
        names[str(batter["player_id"])] = batter["name"]

def rate(mask):
    return sum(mask) / n

ras = sim["pitchers"]["656876"]["strikeouts"]
hend = sim["pitchers"]["701656"]["strikeouts"]
total = [a + h for a, h in zip(sim["away_runs"], sim["home_runs"])]
rays_win = [bool(v) for v in sim["away_win"]]
diaz = [v >= 1 for v in sim["batters"]["650490"]["hits"]]
chourio = [v >= 1 for v in sim["batters"]["694192"]["hits"]]
contreras = [v >= 1 for v in sim["batters"]["661388"]["hits"]]
ras45 = [v >= 5 for v in ras]
ras55 = [v >= 6 for v in ras]
under75 = [v < 7.5 for v in total]
under65 = [v < 6.5 for v in total]
hend35 = [v >= 4 for v in hend]
brewers_under = [v < 3.5 for v in sim["home_runs"]]

cards = {
    "Rasmussen over 4.5 K and total under 7.5": [a and b for a, b in zip(ras45, under75)],
    "Rasmussen over 5.5 K and Rays moneyline": [a and b for a, b in zip(ras55, rays_win)],
    "Diaz hit and Chourio hit": [a and b for a, b in zip(diaz, chourio)],
    "Diaz hit and Contreras hit": [a and b for a, b in zip(diaz, contreras)],
    "Rays moneyline and Diaz hit": [a and b for a, b in zip(rays_win, diaz)],
    "Henderson over 3.5 K and Brewers under 3.5": [a and b for a, b in zip(hend35, brewers_under)],
    "Chourio hit and total under 7.5": [a and b for a, b in zip(chourio, under75)],
}
for label, mask in cards.items():
    print(f"{rate(mask):.3f}  {label}")
print("ras K mean", sum(ras) / n, "hend K mean", sum(hend) / n)
print("ras K dist", {k: sum(v == k for v in ras) / n for k in range(0, 13)})
