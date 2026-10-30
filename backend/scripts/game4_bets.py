import gzip
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
connection = sqlite3.connect(ROOT / "app" / "data" / "app.db")
payload, path = connection.execute(
    "select payload_json, sim_path from analyses where id = ?",
    ("f012d1af-6bef-48da-a882-2bffc0e50910",),
).fetchone()
connection.close()
with gzip.open(path, "rt", encoding="utf-8") as handle:
    sim = json.load(handle)
n = len(sim["away_win"])


def pct(mask):
    return sum(1 for flag in mask if flag) / n


def joint(parts):
    return pct(all(flags) for flags in zip(*parts))


mil = [bool(v) for v in sim["away_win"]]
tb = [not flag for flag in mil]
jax = sim["pitchers"]["643377"]["strikeouts"]
total = [a + h for a, h in zip(sim["away_runs"], sim["home_runs"])]
print("n", n, "jax K", sum(jax) / n, "total", sum(total) / n)
print("jax over 3.5", pct(v >= 4 for v in jax), "over 4.5", pct(v >= 5 for v in jax), "over 5.5", pct(v >= 6 for v in jax))
print("under 7.5", pct(v < 7.5 for v in total), "over 6.5", pct(v > 6.5 for v in total), "under 8.5", pct(v < 8.5 for v in total))
print("MIL", pct(mil), "TB", pct(tb))

ids = {
    "Chourio": "694192",
    "Bauers": "641343",
    "Yelich": "592885",
    "Contreras": "661388",
    "Turang": "668930",
    "Mitchell": "669003",
    "Pratt": "806198",
    "Frelick": "686217",
    "Hamilton": "666152",
    "Diaz": "650490",
    "Vilade": "668723",
    "Simpson": "802415",
    "Caminero": "691406",
    "DeLuca": "676356",
}
hits = {name: [v >= 1 for v in sim["batters"][pid]["hits"]] for name, pid in ids.items()}
hrs = {name: [v >= 1 for v in sim["batters"][pid]["home_runs"]] for name, pid in ids.items()}
rbis = {name: [v >= 1 for v in sim["batters"][pid]["rbis"]] for name, pid in ids.items()}
for name in ids:
    print(f"{name:12} hit {pct(hits[name]):.3f} hr {pct(hrs[name]):.3f} rbi {pct(rbis[name]):.3f}")

cards = {
    "Chourio + Contreras hits": joint([hits["Chourio"], hits["Contreras"]]),
    "Chourio + Yelich hits": joint([hits["Chourio"], hits["Yelich"]]),
    "Chourio + Turang hits": joint([hits["Chourio"], hits["Turang"]]),
    "Diaz hit only rate": pct(hits["Diaz"]),
    "Diaz + DeLuca hits": joint([hits["Diaz"], hits["DeLuca"]]),
    "Yelich + Frelick hits": joint([hits["Yelich"], hits["Frelick"]]),
    "MIL ML + Chourio hit": joint([mil, hits["Chourio"]]),
    "MIL ML + Turang hit": joint([mil, hits["Turang"]]),
    "TB ML + Diaz hit": joint([tb, hits["Diaz"]]),
    "Chourio HR + Contreras hit": joint([hrs["Chourio"], hits["Contreras"]]),
    "Caminero HR + Diaz hit": joint([hrs["Caminero"], hits["Diaz"]]),
    "Turang hit + Frelick hit + Chourio hit": joint([hits["Turang"], hits["Frelick"], hits["Chourio"]]),
    "under 8.5 + Diaz hit": joint([[v < 8.5 for v in total], hits["Diaz"]]),
}
for label, value in sorted(cards.items(), key=lambda item: -item[1]):
    print(f"{value:.3f}  {label}")
