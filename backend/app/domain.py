"""Counting-stat parsing and plate-appearance outcome helpers.

Sportsbook markets and the simulator share these outcome names so a prop
cannot be attached to a different statistic by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

OUTCOMES = (
    "strikeout",
    "walk",
    "hit_by_pitch",
    "single",
    "double",
    "triple",
    "home_run",
    "out",
)

MARKET_STAT = {
    "player_hits": "hits",
    "player_total_bases": "total_bases",
    "player_home_runs": "home_runs",
    "player_walks": "walks",
    "player_strikeouts": "strikeouts",
    "player_runs": "runs",
    "player_rbis": "rbis",
    "player_stolen_bases": "stolen_bases",
    "player_hits_runs_rbis": "hits_runs_rbis",
    "pitcher_strikeouts": "strikeouts",
    "pitcher_walks": "walks",
    "pitcher_hits_allowed": "hits",
    "pitcher_outs": "outs",
    "pitcher_innings": "outs",
}

# Earned runs are not modeled because errors are not modeled.
UNSUPPORTED_MARKETS = {"pitcher_earned_runs"}

GAME_TYPES = {
    "R": "Regular season",
    "F": "Wild Card Series",
    "D": "Division Series",
    "L": "League Championship Series",
    "W": "World Series",
    "P": "Postseason",
    "S": "Spring training",
    "A": "All-Star",
}

POSTSEASON_TYPES = {"F", "D", "L", "W", "P"}


def num(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in {"-.--", ".---", "-", "null"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_ip(value) -> float | None:
    """MLB innings notation: the digit after the decimal is outs, not tenths."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text in {"-.--", ".---", "-"}:
        return None
    if "." not in text:
        try:
            return float(text)
        except ValueError:
            return None
    whole, frac = text.split(".", 1)
    try:
        innings = int(whole)
    except ValueError:
        return None
    outs_digit = frac[0] if frac else "0"
    if outs_digit not in {"0", "1", "2"}:
        return None
    return innings + int(outs_digit) / 3.0


@dataclass
class CountingLine:
    pa: float = 0.0
    ab: float = 0.0
    h: float = 0.0
    doubles: float = 0.0
    triples: float = 0.0
    hr: float = 0.0
    bb: float = 0.0
    k: float = 0.0
    hbp: float = 0.0
    sf: float = 0.0
    sh: float = 0.0
    runs: float = 0.0
    rbi: float = 0.0
    sb: float = 0.0
    pitches: float = 0.0
    ip: float = 0.0
    bf: float = 0.0
    er: float = 0.0
    r_allowed: float = 0.0
    gs: float = 0.0
    g: float = 0.0
    saves: float = 0.0
    holds: float = 0.0
    go: float = 0.0
    ao: float = 0.0
    games: int = 0

    def add(self, other: "CountingLine") -> "CountingLine":
        for field in fields(self):
            setattr(self, field.name, getattr(self, field.name) + getattr(other, field.name))
        return self


def line_from_stat(stat: dict | None, *, appearance: bool) -> CountingLine:
    stat = stat or {}
    ip = parse_ip(stat.get("inningsPitched"))
    line = CountingLine(
        pa=num(stat.get("plateAppearances")),
        ab=num(stat.get("atBats")),
        h=num(stat.get("hits")),
        doubles=num(stat.get("doubles")),
        triples=num(stat.get("triples")),
        hr=num(stat.get("homeRuns")),
        bb=num(stat.get("baseOnBalls")),
        k=num(stat.get("strikeOuts")),
        hbp=num(stat.get("hitByPitch")),
        sf=num(stat.get("sacFlies")),
        sh=num(stat.get("sacBunts")),
        runs=num(stat.get("runs")),
        rbi=num(stat.get("rbi")),
        sb=num(stat.get("stolenBases")),
        pitches=num(stat.get("numberOfPitches")),
        ip=0.0 if ip is None else ip,
        bf=num(stat.get("battersFaced")),
        er=num(stat.get("earnedRuns")),
        r_allowed=num(stat.get("runs")),
        gs=num(stat.get("gamesStarted")),
        g=num(stat.get("gamesPlayed")),
        saves=num(stat.get("saves")),
        holds=num(stat.get("holds")),
        go=num(stat.get("groundOuts")),
        ao=num(stat.get("airOuts")),
        games=1 if appearance else int(num(stat.get("gamesPlayed"))),
    )
    if line.bf <= 0 and line.ip > 0:
        line.bf = line.ab + line.bb + line.hbp + line.sf + line.sh
    if line.pa <= 0 and line.ab > 0 and line.ip == 0 and line.bf == 0:
        line.pa = line.ab + line.bb + line.hbp + line.sf + line.sh
    return line


def sample_size(line: CountingLine, *, pitching: bool) -> float:
    if pitching:
        return line.bf if line.bf > 0 else line.pa
    return line.pa


def proportions(line: CountingLine, *, pitching: bool = False) -> dict[str, float] | None:
    n = sample_size(line, pitching=pitching)
    if n <= 0:
        return None
    singles = max(line.h - line.doubles - line.triples - line.hr, 0.0)
    raw = {
        "strikeout": max(line.k, 0.0),
        "walk": max(line.bb, 0.0),
        "hit_by_pitch": max(line.hbp, 0.0),
        "single": singles,
        "double": max(line.doubles, 0.0),
        "triple": max(line.triples, 0.0),
        "home_run": max(line.hr, 0.0),
    }
    used = sum(raw.values())
    if used > n:
        scale = n / used
        raw = {key: value * scale for key, value in raw.items()}
        raw["out"] = 0.0
    else:
        raw["out"] = n - used
    return {key: raw[key] / n for key in OUTCOMES}


def hit_rate(dist: dict[str, float]) -> float:
    return dist["single"] + dist["double"] + dist["triple"] + dist["home_run"]


def normalize_dist(dist: dict[str, float]) -> dict[str, float]:
    total = sum(max(dist.get(key, 0.0), 0.0) for key in OUTCOMES)
    if total <= 0:
        even = 1.0 / len(OUTCOMES)
        return {key: even for key in OUTCOMES}
    return {key: max(dist.get(key, 0.0), 0.0) / total for key in OUTCOMES}


def dist_vector(dist: dict[str, float]) -> list[float]:
    return [dist[key] for key in OUTCOMES]
