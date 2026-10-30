import gzip
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
connection = sqlite3.connect(ROOT / "app" / "data" / "app.db")
payload, path = connection.execute(
    "select payload_json, sim_path from analyses where id = ?",
    ("905b54e4-62e8-4f5c-a702-28e08b1a8914",),
).fetchone()
connection.close()
analysis = json.loads(payload)
with gzip.open(path, "rt", encoding="utf-8") as handle:
    sim = json.load(handle)
n = len(sim["away_win"])


def pct(mask):
    return sum(mask) / n


gasser = sim["pitchers"]["688107"]["strikeouts"]
peralta = sim["pitchers"]["642547"]["strikeouts"]
total = [a + h for a, h in zip(sim["away_runs"], sim["home_runs"])]
print("n", n, "gasser K", sum(gasser) / n, "peralta K", sum(peralta) / n, "total", sum(total) / n)
print("gasser dist", {k: round(sum(v == k for v in gasser) / n, 3) for k in range(0, 12)})
print("peralta dist", {k: round(sum(v == k for v in peralta) / n, 3) for k in range(0, 12)})
for line in (3.5, 4.5, 5.5, 6.5):
    print(f"Gasser over {line}", round(pct(v > line for v in gasser), 3), f"Peralta over {line}", round(pct(v > line for v in peralta), 3))
for line in (5.5, 6.5, 7.5, 8.5):
    print(f"total over {line}", round(pct(v > line for v in total), 3), f"under {line}", round(pct(v < line for v in total), 3))
print("MIL win", round(pct(not w for w in sim["away_win"]), 3), "TB win", round(sum(sim["away_win"]) / n, 3))
# away is MIL
print("MIL runs mean", sum(sim["away_runs"]) / n, "TB", sum(sim["home_runs"]) / n)
print("MIL over 2.5", round(pct(v > 2.5 for v in sim["away_runs"]), 3), "under 4.5", round(pct(v < 4.5 for v in sim["away_runs"]), 3))
print("TB over 2.5", round(pct(v > 2.5 for v in sim["home_runs"]), 3), "under 4.5", round(pct(v < 4.5 for v in sim["home_runs"]), 3))

names = {}
for side in ("away", "home"):
    for batter in analysis[side]["batters"]:
        names[str(batter["player_id"])] = (side, batter["name"])

hits = {}
hrs = {}
rbis = {}
for pid, stats in sim["batters"].items():
    if "hits" not in stats:
        continue
    hits[pid] = [v >= 1 for v in stats["hits"]]
    hrs[pid] = [v >= 1 for v in stats["home_runs"]]
    rbis[pid] = [v >= 1 for v in stats["rbis"]]
    side, name = names.get(pid, ("?", pid))
    print(f"{side:4} {name:22} hit {pct(hits[pid]):.3f} hr {pct(hrs[pid]):.3f} rbi {pct(rbis[pid]):.3f}")

mil_win = [not w for w in sim["away_win"]]
tb_win = [bool(w) for w in sim["away_win"]]
under = [v < 7.5 for v in total]
over = [v > 6.5 for v in total]


def joint(parts):
    return pct(all(flags) for flags in zip(*parts))


candidates = {
    "Chourio hit + Contreras hit": joint([hits["694192"], hits["661388"]]),
    "Chourio hit + Diaz hit": joint([hits["694192"], hits["650490"]]),
    "Chourio hit + Caminero hit": joint([hits["694192"], hits["691406"]]),
    "Contreras hit + Simpson hit": joint([hits["661388"], hits["802415"]]),
    "MIL ML + Chourio hit": joint([mil_win, hits["694192"]]),
    "MIL ML + Contreras hit": joint([mil_win, hits["661388"]]),
    "TB ML + Caminero hit": joint([tb_win, hits["691406"]]),
    "TB ML + Diaz hit": joint([tb_win, hits["650490"]]),
    "under 7.5 + Chourio hit": joint([under, hits["694192"]]),
    "under 7.5 + Diaz hit": joint([under, hits["650490"]]),
    "Gasser over 4.5 + MIL ML": joint([[v > 4.5 for v in gasser], mil_win]),
    "Peralta over 4.5 + TB ML": joint([[v > 4.5 for v in peralta], tb_win]),
    "Chourio hit + Caminero HR": joint([hits["694192"], hrs["691406"]]),
    "Frelick hit + Chourio hit + Contreras hit": joint([hits["686217"], hits["694192"], hits["661388"]]),
    "MIL ML + Chourio hit + Contreras RBI": joint([mil_win, hits["694192"], rbis["661388"]]),
    "TB ML + Diaz hit + Caminero RBI": joint([tb_win, hits["650490"], rbis["691406"]]),
    "Caminero hit + Diaz hit": joint([hits["691406"], hits["650490"]]),
    "Bauers hit + Yelich hit": joint([hits["641343"], hits["592885"]]),
}
for label, value in sorted(candidates.items(), key=lambda item: -item[1]):
    print(f"{value:.3f}  {label}")
