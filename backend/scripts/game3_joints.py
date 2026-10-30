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
with gzip.open(path, "rt", encoding="utf-8") as handle:
    sim = json.load(handle)
n = len(sim["away_win"])


def pct(mask):
    return sum(1 for flag in mask if flag) / n


def joint(parts):
    return pct(all(flags) for flags in zip(*parts))


# Away is Milwaukee.
mil = [bool(v) for v in sim["away_win"]]
tb = [not flag for flag in mil]
gasser = [v >= 5 for v in sim["pitchers"]["688107"]["strikeouts"]]  # over 4.5
peralta = [v >= 5 for v in sim["pitchers"]["642547"]["strikeouts"]]
total = [a + h for a, h in zip(sim["away_runs"], sim["home_runs"])]
under75 = [v < 7.5 for v in total]
under65 = [v < 6.5 for v in total]
chourio = [v >= 1 for v in sim["batters"]["694192"]["hits"]]
contreras = [v >= 1 for v in sim["batters"]["661388"]["hits"]]
diaz = [v >= 1 for v in sim["batters"]["650490"]["hits"]]
caminero = [v >= 1 for v in sim["batters"]["691406"]["hits"]]
simpson = [v >= 1 for v in sim["batters"]["802415"]["hits"]]
frelick = [v >= 1 for v in sim["batters"]["686217"]["hits"]]
caminero_hr = [v >= 1 for v in sim["batters"]["691406"]["home_runs"]]
contreras_rbi = [v >= 1 for v in sim["batters"]["661388"]["rbis"]]
simpson_caminero = joint([simpson, caminero])
print("Simpson + Caminero", round(simpson_caminero, 4))
print("MIL", pct(mil), "TB", pct(tb))
cards = {
    "MIL ML + Chourio hit": joint([mil, chourio]),
    "MIL ML + Contreras hit": joint([mil, contreras]),
    "TB ML + Diaz hit": joint([tb, diaz]),
    "TB ML + Caminero hit": joint([tb, caminero]),
    "TB ML + Simpson hit": joint([tb, simpson]),
    "under 7.5 + Chourio + Diaz": joint([under75, chourio, diaz]),
    "Chourio + Contreras": joint([chourio, contreras]),
    "Chourio + Diaz": joint([chourio, diaz]),
    "Gasser over 4.5 + MIL ML": joint([gasser, mil]),
    "Peralta over 4.5 + TB ML": joint([peralta, tb]),
    "MIL ML + Chourio + Contreras hit": joint([mil, chourio, contreras]),
    "under 7.5 + Contreras hit": joint([under75, contreras]),
    "Caminero HR + Chourio hit": joint([caminero_hr, chourio]),
    "Frelick hit + Chourio hit": joint([frelick, chourio]),
}
for label, value in sorted(cards.items(), key=lambda item: -item[1]):
    print(f"{value:.3f}  {label}")
