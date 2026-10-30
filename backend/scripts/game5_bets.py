import gzip
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
connection = sqlite3.connect(ROOT / "app" / "data" / "app.db")
payload, path = connection.execute(
    "select payload_json, sim_path from analyses where id = ?",
    ("6bf25815-d10c-4430-ac4f-f4ad5eba7c44",),
).fetchone()
connection.close()
with gzip.open(path, "rt", encoding="utf-8") as handle:
    sim = json.load(handle)
n = len(sim["away_win"])


def pct(mask):
    return sum(1 for flag in mask if flag) / n


def joint(parts):
    return pct(all(flags) for flags in zip(*parts))


mis = sim["pitchers"]["694819"]["strikeouts"]
ras = sim["pitchers"]["656876"]["strikeouts"]
total = [a + h for a, h in zip(sim["away_runs"], sim["home_runs"])]
mil = [bool(v) for v in sim["away_win"]]
tb = [not flag for flag in mil]
print("n", n)
print("mis K", round(sum(mis) / n, 2), "ras K", round(sum(ras) / n, 2), "total", round(sum(total) / n, 2))
print("mis dist", {k: round(sum(v == k for v in mis) / n, 3) for k in range(0, 13)})
print("ras dist", {k: round(sum(v == k for v in ras) / n, 3) for k in range(0, 13)})
for line in (3.5, 4.5, 5.5, 6.5):
    print(f"Mis over {line}", round(pct(v > line for v in mis), 3), f"Ras over {line}", round(pct(v > line for v in ras), 3))
for line in (5.5, 6.5, 7.5, 8.5):
    print(f"over {line}", round(pct(v > line for v in total), 3), f"under {line}", round(pct(v < line for v in total), 3))
print("MIL", round(pct(mil), 3), "TB", round(pct(tb), 3))

ids = {
    "Chourio": "694192",
    "Bauers": "641343",
    "Yelich": "592885",
    "Contreras": "661388",
    "Turang": "668930",
    "Frelick": "686217",
    "Pratt": "806198",
    "Mitchell": "669003",
    "Hamilton": "666152",
    "Diaz": "650490",
    "Aranda": "666018",
    "Caminero": "691406",
    "Hicks": "689414",
    "DeLuca": "676356",
    "Simpson": "802415",
}
hits = {name: [v >= 1 for v in sim["batters"][pid]["hits"]] for name, pid in ids.items()}
hrs = {name: [v >= 1 for v in sim["batters"][pid]["home_runs"]] for name, pid in ids.items()}
for name in ids:
    print(f"{name:12} hit {pct(hits[name]):.3f} hr {pct(hrs[name]):.3f}")

under = [v < 6.5 for v in total]
cards = {
    "Chourio hit": pct(hits["Chourio"]),
    "Simpson hit": pct(hits["Simpson"]),
    "Diaz hit": pct(hits["Diaz"]),
    "Contreras hit": pct(hits["Contreras"]),
    "Chourio + Contreras": joint([hits["Chourio"], hits["Contreras"]]),
    "Chourio + Simpson": joint([hits["Chourio"], hits["Simpson"]]),
    "Simpson + Diaz": joint([hits["Simpson"], hits["Diaz"]]),
    "Pratt hit": pct(hits["Pratt"]),
    "Chourio + Pratt": joint([hits["Chourio"], hits["Pratt"]]),
    "under 6.5": pct(under),
    "under 6.5 + Chourio": joint([under, hits["Chourio"]]),
    "Ras over 4.5 + TB ML": joint([[v >= 5 for v in ras], tb]),
    "Mis over 4.5 + MIL ML": joint([[v >= 5 for v in mis], mil]),
    "Simpson + Hicks": joint([hits["Simpson"], hits["Hicks"]]),
    "Frelick + Yelich": joint([hits["Frelick"], hits["Yelich"]]),
}
for label, value in sorted(cards.items(), key=lambda item: -item[1]):
    print(f"{value:.3f}  {label}")
