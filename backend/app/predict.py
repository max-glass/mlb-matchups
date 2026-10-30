"""Build a pregame analysis from MLB data and one Monte Carlo simulation.

The October 19, 2026 Rays-Guardians game is only the default request.
Nothing in the rate or simulation code assumes those teams.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.domain import (
    GAME_TYPES,
    POSTSEASON_TYPES,
    CountingLine,
    hit_rate,
    line_from_stat,
    proportions,
    sample_size,
)
from app.environment import air_density, roof_is_closed, shrunk_park_multiplier, standard_density, weather_hr_multiplier
from app.evaluate import filter_logs
from app.fatigue import availability_label, listed_starter_role, pitcher_role, workload
from app.mlb import fetch, fetch_json, league_url, log_url, schedule_url, season_url, STATS
from app.paths import DATA_DIR, MODEL_VERSION, SIM_DIR
from app.rates import ModelPriors, log5, move_mass, nested_skill, rescale_share, shrink
from app.simulation import SimBatter, SimClub, SimPitcher, dist_from_rates, simulate_games

PRIORS = ModelPriors()
PA_BY_ORDER = [4.65, 4.55, 4.45, 4.35, 4.25, 4.15, 4.05, 3.95, 3.85]


def _loads(content: bytes | None) -> dict | None:
    if not content:
        return None
    return json.loads(content.decode("utf-8"))


def _sum_league(payload: dict | None) -> CountingLine:
    total = CountingLine()
    if not payload:
        return total
    for block in payload.get("stats") or []:
        for split in block.get("splits") or []:
            total.add(line_from_stat(split.get("stat"), appearance=False))
    return total


def _index_players(payload: dict | None) -> dict[int, dict]:
    found = {}
    if not payload:
        return found
    for block in payload.get("stats") or []:
        for split in block.get("splits") or []:
            player = split.get("player") or {}
            if "id" not in player:
                continue
            found[int(player["id"])] = {
                "name": player.get("fullName"),
                "position": (split.get("position") or {}).get("abbreviation"),
                "line": line_from_stat(split.get("stat"), appearance=False),
            }
    return found


def _logs(payload: dict | None) -> list[dict]:
    rows = []
    if not payload:
        return rows
    for block in payload.get("stats") or []:
        for split in block.get("splits") or []:
            game = split.get("game") or {}
            rows.append(
                {
                    "date": split.get("date"),
                    "game_pk": game.get("gamePk"),
                    "line": line_from_stat(split.get("stat"), appearance=True),
                }
            )
    return rows


def _sum_logs(rows: list[dict], as_of: str, game_pk: int | None) -> CountingLine:
    total = CountingLine()
    for row in filter_logs(rows, as_of, game_pk):
        total.add(row["line"])
    return total


def _recent(rows: list[dict], as_of: str, game_pk: int | None, days: int) -> CountingLine:
    cutoff = (datetime.fromisoformat(as_of) - timedelta(days=days)).date().isoformat()
    total = CountingLine()
    for row in filter_logs(rows, as_of, game_pk):
        if row["date"] >= cutoff:
            total.add(row["line"])
    return total


def _appearances(rows: list[dict], as_of: str, game_pk: int | None) -> list[dict]:
    appearances = []
    for row in filter_logs(rows, as_of, game_pk):
        appearances.append(
            {
                "date": datetime.fromisoformat(row["date"]).date(),
                "pitches": row["line"].pitches,
                "outs": row["line"].ip * 3,
            }
        )
    return appearances


def _data_support(batter_pa: float, pitcher_bf: float, confirmed: bool, starter_role: str, savant: bool) -> float:
    score = 0.0
    score += 0.30 if batter_pa >= 200 else 0.15 if batter_pa >= 50 else 0.0
    score += 0.15 if pitcher_bf >= 150 else 0.08 if pitcher_bf >= 40 else 0.0
    score += 0.15 if confirmed else 0.0
    score += 0.10 if starter_role == "starter" else 0.0
    score += 0.10 if savant else 0.0
    score += 0.10
    return min(score, 1.0)


def _support_label(score: float) -> str:
    if score >= 0.7:
        return "adequate"
    if score >= 0.45:
        return "partial"
    return "limited"


async def _many(client, urls: list[tuple[str, int, str]]):
    results = []
    for url, ttl, source in urls:
        results.append(await fetch_json(client, url, ttl, source))
    return results


def _dist_pair(batter_line: CountingLine, pitcher_line: CountingLine, league: dict, recent_b, recent_p, bvp_line, park_hr: float, savant_mult: float | None):
    b_props = proportions(batter_line, pitching=False)
    p_props = proportions(pitcher_line, pitching=True)
    b_steps = nested_skill(
        league,
        None,
        (b_props, sample_size(batter_line, pitching=False)),
        (proportions(recent_b, pitching=False), sample_size(recent_b, pitching=False)) if sample_size(recent_b, pitching=False) else None,
        None,
        PRIORS,
    )
    p_steps = nested_skill(
        league,
        None,
        (p_props, sample_size(pitcher_line, pitching=True)),
        (proportions(recent_p, pitching=True), sample_size(recent_p, pitching=True)) if sample_size(recent_p, pitching=True) else None,
        None,
        PRIORS,
        pitching=True,
    )
    matched = log5(b_steps[-1][1], p_steps[-1][1], league)
    notes = [
        f"Season plate appearances used: {sample_size(batter_line, pitching=False):.0f}.",
        f"Opposing pitcher batters faced used: {sample_size(pitcher_line, pitching=True):.0f}.",
    ]
    if bvp_line and sample_size(bvp_line, pitching=False) > 0:
        matched = shrink(proportions(bvp_line, pitching=False), sample_size(bvp_line, pitching=False), matched, PRIORS.bvp_pa)
        notes.append(
            f"Head-to-head sample is {sample_size(bvp_line, pitching=False):.0f} plate appearances and is shrunk with an {PRIORS.bvp_pa:.0f} PA prior."
        )
        if sample_size(bvp_line, pitching=False) < 25:
            notes.append("The head-to-head sample is too small for a strong conclusion.")
    else:
        notes.append("No usable pregame head-to-head sample was applied.")
    before_park = hit_rate(matched)
    matched = move_mass(matched, "out", "home_run", park_hr)
    if savant_mult:
        matched = rescale_share(matched, "strikeout", savant_mult)
        notes.append("A bounded pitch-mix or contact adjustment from Baseball Savant was applied.")
    else:
        notes.append("No Savant adjustment was applied for this pitcher pairing.")
    notes.append(
        f"Park and weather move home-run mass against outs. Hit rate per plate appearance changed by {hit_rate(matched) - before_park:+.3f} at that step."
    )
    notes.append("These nested differences are path-dependent. They are not separate causal effects.")
    return matched, notes, b_steps


def _proj(values: list[int]) -> dict:
    n = max(len(values), 1)
    return {
        "expected": sum(values) / n,
        "p0": sum(v == 0 for v in values) / n,
        "p1": sum(v == 1 for v in values) / n,
        "p2": sum(v == 2 for v in values) / n,
        "p3_plus": sum(v >= 3 for v in values) / n,
        "p_at_least_1": sum(v >= 1 for v in values) / n,
        "p_at_least_2": sum(v >= 2 for v in values) / n,
    }


async def analyze(client: httpx.AsyncClient, body: dict) -> dict:
    warnings: list[str] = []
    freshness: list[dict] = []
    game_date = body["date"]
    season = int(game_date[:4])
    away_id = int(body["away_team_id"])
    home_id = int(body["home_team_id"])
    if away_id == home_id:
        raise ValueError("Away and home teams must be different.")
    sims = int(body.get("simulation_count") or 1200)
    seed = int(body.get("seed") or 20261019)

    teams_payload, teams_meta = await fetch_json(client, f"{STATS}/api/v1/teams?sportId=1&season={season}", 7 * 86400, "mlb-teams")
    freshness.append({"source": "MLB teams", **{k: teams_meta.get(k) for k in ("status", "fetched_at_iso", "detail")}})
    teams = {int(team["id"]): team for team in (teams_payload or {}).get("teams", []) if team.get("active")}
    if away_id not in teams or home_id not in teams:
        raise ValueError("One of the team ids is not an active MLB team for that season.")
    schedule, sched_meta = await fetch_json(client, schedule_url(game_date), 3600, "mlb-schedule")
    freshness.append({"source": "MLB schedule", **{k: sched_meta.get(k) for k in ("status", "fetched_at_iso", "detail")}})
    if schedule is None:
        raise RuntimeError(sched_meta.get("detail") or "The MLB schedule could not be loaded.")

    games = []
    for date_block in schedule.get("dates") or []:
        games.extend(date_block.get("games") or [])
    match = None
    for game in games:
        sides = game.get("teams") or {}
        if (sides.get("away") or {}).get("team", {}).get("id") == away_id and (sides.get("home") or {}).get("team", {}).get("id") == home_id:
            match = game
            break
    hypothetical = match is None or bool(body.get("hypothetical"))
    if match is None:
        warnings.append("No scheduled game matches these teams on this date. The run is hypothetical and uses only inputs you supply or the last available roster information.")

    game_pk = None if match is None else match.get("gamePk")
    game_type = (match or {}).get("gameType") or body.get("game_type") or "R"
    postseason = game_type in POSTSEASON_TYPES
    start = (match or {}).get("gameDate")
    venue = ((match or {}).get("venue") or teams[home_id].get("venue") or {})
    venue_id = venue.get("id")
    parks = json.loads((DATA_DIR / "reference_parks.json").read_text(encoding="utf-8"))
    park = parks["venues"].get(str(venue_id), {})
    roof = park.get("roof", "open")
    condition = ((match or {}).get("weather") or {}).get("condition")
    closed = roof_is_closed(roof, condition)
    park_hr = shrunk_park_multiplier(park.get("hr_factor"))
    weather_mult = 1.0
    weather_note = "No outdoor air-density adjustment was applied."
    if not closed and park.get("lat") is not None:
        wx_url = (
            "https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
            "&start_date={day}&end_date={day}&hourly=temperature_2m,relative_humidity_2m,pressure_msl,wind_speed_10m"
        ).format(lat=park["lat"], lon=park["lon"], day=game_date)
        wx, wx_meta = await fetch_json(client, wx_url, 86400, "open-meteo")
        freshness.append({"source": "Open-Meteo", **{k: wx_meta.get(k) for k in ("status", "fetched_at_iso", "detail")}})
        hourly = (wx or {}).get("hourly") or {}
        temps = hourly.get("temperature_2m") or []
        if temps:
            mid = temps[len(temps) // 2]
            rh = (hourly.get("relative_humidity_2m") or [50])[len(temps) // 2]
            pressure = (hourly.get("pressure_msl") or [1013])[len(temps) // 2]
            density = air_density(mid, rh, pressure, park.get("elevation_m") or 0)
            reference = standard_density(park.get("elevation_m") or 0)
            weather_mult = weather_hr_multiplier(density, reference, False)
            weather_note = f"Outdoor air density {density:.3f} kg/m3 versus a standard-atmosphere reference {reference:.3f}."
        else:
            warnings.append("Weather for this venue was not available. The environmental adjustment is neutral.")
    elif closed:
        weather_note = "The roof is treated as closed, so outdoor weather does not change the forecast."
    else:
        warnings.append("This venue has no curated coordinates. Weather was left neutral.")
    park_hr *= weather_mult

    box = None
    if game_pk and not body.get("hypothetical"):
        box, box_meta = await fetch_json(client, f"{STATS}/api/v1/game/{game_pk}/boxscore", 86400, "mlb-boxscore")
        freshness.append({"source": "MLB box score", **{k: box_meta.get(k) for k in ("status", "fetched_at_iso", "detail")}})

    def lineup_from_box(side: str) -> list[int]:
        if not box:
            return []
        order = ((box.get("teams") or {}).get(side) or {}).get("battingOrder") or []
        return [int(pid) for pid in order][:9]

    def lineup_from_schedule(key: str) -> list[int]:
        players = ((match or {}).get("lineups") or {}).get(key) or []
        return [int(player["id"]) for player in players if player.get("id")][:9]

    if body.get("away_lineup") or body.get("home_lineup"):
        away_order = body.get("away_lineup") or []
        home_order = body.get("home_lineup") or []
        lineup_status = "manual"
    else:
        away_order = lineup_from_box("away")
        home_order = lineup_from_box("home")
        lineup_status = "confirmed" if away_order and home_order else "missing"
        abstract = ((match or {}).get("status") or {}).get("abstractGameState")
        if lineup_status == "confirmed" and abstract not in {"Live", "Final"}:
            lineup_status = "announced"
            warnings.append("The batting order is on the MLB feed before first pitch. It is the announced lineup, not a locked card.")
        if lineup_status == "missing":
            away_order = lineup_from_schedule("awayPlayers")
            home_order = lineup_from_schedule("homePlayers")
            if away_order and home_order:
                lineup_status = "schedule_feed"
                warnings.append("Lineups come from the MLB schedule feed for this game. They are not treated as a confirmed first-pitch card.")
    if not away_order or not home_order:
        raise ValueError("A nine-batter lineup was not available. Enter both lineups. The app will not invent one.")

    hit_payloads = {}
    pit_payloads = {}
    for team_id in (away_id, home_id):
        hitting, meta_h = await fetch_json(client, season_url(team_id, "hitting", season), 86400, "mlb-season-hitting")
        pitching, meta_p = await fetch_json(client, season_url(team_id, "pitching", season), 86400, "mlb-season-pitching")
        freshness.append({"source": f"Season hitting {team_id}", **{k: meta_h.get(k) for k in ("status", "fetched_at_iso", "detail")}})
        freshness.append({"source": f"Season pitching {team_id}", **{k: meta_p.get(k) for k in ("status", "fetched_at_iso", "detail")}})
        hit_payloads[team_id] = _index_players(hitting)
        pit_payloads[team_id] = _index_players(pitching)

    league_raw, league_meta = await fetch_json(client, league_url("hitting", season), 86400, "mlb-league")
    freshness.append({"source": "League hitting", **{k: league_meta.get(k) for k in ("status", "fetched_at_iso", "detail")}})
    league_line = _sum_league(league_raw)
    league = proportions(league_line, pitching=False)
    if league is None:
        raise RuntimeError("League hitting totals were unavailable, so rates were not shrunk toward an invented average.")

    def staff(team_id: int, listed_id: int | None) -> list[int]:
        rows = []
        for pid, info in pit_payloads[team_id].items():
            line = info["line"]
            if info.get("position") not in {None, "P"} and line.ip < 5:
                continue
            if line.ip < 5 and pid != listed_id:
                continue
            rows.append((line.saves * 3 + line.holds + line.ip, pid))
        rows.sort(reverse=True)
        ids = [pid for _, pid in rows[:8]]
        if listed_id and listed_id not in ids:
            ids.insert(0, listed_id)
        named = [pid for pid in ids if pid > 0]
        if listed_id and listed_id < 0:
            return [listed_id] + named
        return named

    away_listed = body.get("away_starter_id") or (((match or {}).get("teams") or {}).get("away") or {}).get("probablePitcher", {}).get("id")
    home_listed = body.get("home_starter_id") or (((match or {}).get("teams") or {}).get("home") or {}).get("probablePitcher", {}).get("id")
    if not away_listed:
        away_listed = -1
        warnings.append(
            f"{teams[away_id]['name']}'s starter is not announced. Opposing hitters face a league-average starter. No player name was chosen, and no strikeout bet is made on that arm."
        )
    if not home_listed:
        home_listed = -2
        warnings.append(
            f"{teams[home_id]['name']}'s starter is not announced. Opposing hitters face a league-average starter. No player name was chosen, and no strikeout bet is made on that arm."
        )
    away_listed = int(away_listed)
    home_listed = int(home_listed)
    away_staff = staff(away_id, away_listed)
    home_staff = staff(home_id, home_listed)
    batter_ids = away_order + home_order
    pitcher_ids = [pid for pid in dict.fromkeys(away_staff + home_staff) if pid > 0]
    people_ids = ",".join(str(pid) for pid in dict.fromkeys(batter_ids + pitcher_ids))
    people, people_meta = await fetch_json(client, f"{STATS}/api/v1/people?personIds={people_ids}", 86400, "mlb-people")
    freshness.append({"source": "MLB people", **{k: people_meta.get(k) for k in ("status", "fetched_at_iso", "detail")}})
    bios = {int(person["id"]): person for person in (people or {}).get("people", [])}

    async def logs_for(pid: int, group: str, game_type_code: str):
        payload, _meta = await fetch_json(client, log_url(pid, group, season, game_type_code), 86400, "mlb-gamelog")
        return _logs(payload)

    batter_logs = {}
    pitcher_logs = {}
    semaphore = asyncio.Semaphore(8)

    async def guarded(pid: int, group: str, game_type_code: str):
        async with semaphore:
            return pid, await logs_for(pid, group, game_type_code)

    batter_jobs = [guarded(pid, "hitting", "R") for pid in batter_ids]
    if postseason:
        batter_jobs += [guarded(pid, "hitting", "P") for pid in batter_ids]
    pitcher_jobs = [guarded(pid, "pitching", "R") for pid in pitcher_ids]
    if postseason:
        pitcher_jobs += [guarded(pid, "pitching", "P") for pid in pitcher_ids]
    for pid, rows in await asyncio.gather(*batter_jobs):
        batter_logs.setdefault(pid, []).extend(rows)
    for pid, rows in await asyncio.gather(*pitcher_jobs):
        pitcher_logs.setdefault(pid, []).extend(rows)

    savant = {}
    savant_url = (
        f"https://baseballsavant.mlb.com/leaderboard/custom?year={season}&type=batter&filter=&min=q"
        "&selections=pa,xba,xslg,xwoba,exit_velocity_avg,barrel_batted_rate,whiff_percent"
        "&chart=false&x=xba&y=xslg&r=no&chartType=beeswarm&csv=true"
    )
    content, savant_meta = await fetch(client, savant_url, 12 * 3600, "baseball-savant")
    freshness.append({"source": "Baseball Savant", **{k: savant_meta.get(k) for k in ("status", "fetched_at_iso", "detail")}})
    if content:
        import pandas as pd
        from io import StringIO

        frame = pd.read_csv(StringIO(content.decode("utf-8")))
        for _, row in frame.iterrows():
            savant[int(row["player_id"])] = row
    else:
        warnings.append("Baseball Savant leaderboards were unavailable. Contact-quality adjustments were omitted.")

    def pitcher_line(team_id: int, pid: int) -> CountingLine:
        if postseason:
            official = pit_payloads[team_id].get(pid, {}).get("line")
            if official and sample_size(official, pitching=True) > 0:
                return official
        logged = _sum_logs(pitcher_logs.get(pid, []), game_date, game_pk)
        if sample_size(logged, pitching=True) > 0:
            return logged
        warnings.append(f"Pitcher {pid} has no as-of sample. League rates are used and the estimate is limited.")
        return CountingLine()

    def batter_line(team_id: int, pid: int) -> CountingLine:
        if postseason:
            official = hit_payloads[team_id].get(pid, {}).get("line")
            if official and sample_size(official, pitching=False) > 0:
                return official
        logged = _sum_logs(batter_logs.get(pid, []), game_date, game_pk)
        if sample_size(logged, pitching=False) > 0:
            return logged
        warnings.append(f"Batter {pid} has no as-of sample. League rates are used and the estimate is limited.")
        return CountingLine()

    def make_pitcher(team_id: int, pid: int, listed: int) -> SimPitcher:
        if pid < 0:
            blank = {"availability": None, "label": "Unannounced", "days_since_last": None, "pitches_1d": None, "pitches_2d": None, "pitches_3d": None, "k_multiplier": 1}
            return (
                SimPitcher(pid, "Unannounced starter", "R", "starter", 0.95, 85.0, pitches_per_batter=3.9),
                blank,
                CountingLine(),
            )
        line = pitcher_line(team_id, pid)
        apps = max(line.g, 1)
        role = listed_starter_role(line.g, line.gs, line.ip) if pid == listed else pitcher_role(line.g, line.gs, line.saves, line.holds, line.ip)
        if pid != listed and role == "starter":
            role = "long"
        typical = (line.pitches / line.gs) if role == "starter" and line.gs else (line.pitches / apps if apps else 15)
        load = workload(_appearances(pitcher_logs.get(pid, []), game_date, game_pk), datetime.fromisoformat(game_date).date())
        bio = bios.get(pid) or {}
        return SimPitcher(
            pid,
            (bio.get("fullName") or pit_payloads[team_id].get(pid, {}).get("name") or str(pid)),
            ((bio.get("pitchHand") or {}).get("code") or "R"),
            "opener_or_reliever" if pid == listed and role == "opener_or_reliever" else role if pid != listed else role,
            0.95 if pid == listed else load["availability"],
            max(8.0, typical),
            pitches_per_batter=max(3.2, (line.pitches / line.bf) if line.bf else 3.9),
        ), load, line

    def club(team_id: int, order: list[int], listed: int, staff_ids: list[int], opp_staff: list[int]) -> tuple[SimClub, dict]:
        sim_pitchers = {}
        loads = {}
        lines = {}
        for pid in staff_ids:
            pitcher, load, line = make_pitcher(team_id, pid, listed)
            sim_pitchers[pid] = pitcher
            loads[pid] = load
            lines[pid] = line
        starter = sim_pitchers[listed]
        bullpen = [sim_pitchers[pid] for pid in staff_ids if pid != listed]
        batters = []
        details = {}
        for slot, pid in enumerate(order, start=1):
            bio = bios.get(pid) or {}
            name = bio.get("fullName") or hit_payloads[team_id].get(pid, {}).get("name") or str(pid)
            line = batter_line(team_id, pid)
            recent = _recent(batter_logs.get(pid, []), game_date, game_pk, 30)
            distributions = {}
            notes = []
            savant_row = savant.get(pid)
            savant_mult = None
            if savant_row is not None and league_line.pa:
                try:
                    xba = float(savant_row["xba"])
                    league_xba = 0.250
                    ratio = min(1.08, max(0.92, xba / league_xba))
                    savant_mult = ratio
                    notes.append(f"Season xBA on the Savant leaderboard is {xba:.3f}. The leaderboard may include games after this date.")
                except Exception:
                    savant_mult = None
            for opp_id in opp_staff:
                opp_line = lines.get(opp_id) if opp_id in lines else pitcher_line(home_id if team_id == away_id else away_id, opp_id)
                opp_recent = _recent(pitcher_logs.get(opp_id, []), game_date, game_pk, 30)
                dist, dist_notes, _steps = _dist_pair(line, opp_line, league, recent, opp_recent, None, park_hr, savant_mult if opp_id == opp_staff[0] else None)
                distributions[opp_id] = dist_from_rates(dist)
                if opp_id == opp_staff[0]:
                    notes = dist_notes + notes
            starter_role_name = starter.role
            support = _data_support(sample_size(line, pitching=False), sample_size(lines.get(opp_staff[0], CountingLine()), pitching=True) if opp_staff else 0, lineup_status == "confirmed", "starter" if starter_role_name == "starter" else starter_role_name, savant_row is not None)
            batters.append(SimBatter(pid, name, (bio.get("batSide") or {}).get("code") or "R", distributions, stolen_base_rate=min(0.08, (line.sb / line.pa) if line.pa else 0.01)))
            position = None
            if box:
                player = ((box.get("teams") or {}).get("away" if team_id == away_id else "home") or {}).get("players", {}).get(f"ID{pid}") or {}
                position = (player.get("position") or {}).get("abbreviation")
            details[pid] = {
                "player_id": pid,
                "name": name,
                "order": slot,
                "position": position,
                "bats": (bio.get("batSide") or {}).get("code"),
                "data_support": support,
                "data_support_label": _support_label(support),
                "notes": notes,
                "team_id": team_id,
            }
        return SimClub(team_id, batters, starter, bullpen), {"pitchers": sim_pitchers, "loads": loads, "lines": lines, "batters": details}

    # Opponent staff lines are needed inside club(); pitcher_line reads logs, so order is safe.
    away_club, away_meta = club(away_id, away_order, away_listed, away_staff, home_staff)
    home_club, home_meta = club(home_id, home_order, home_listed, home_staff, away_staff)
    if away_club.starter.role == "opener_or_reliever":
        warnings.append(f"{away_club.starter.name} is the listed probable pitcher but the regular-season usage is a reliever or opener, not a traditional starter. The simulation removes that pitcher on a short hook. No other starter was invented.")
    if home_club.starter.role == "opener_or_reliever":
        warnings.append(f"{home_club.starter.name} is the listed probable pitcher but the regular-season usage is a reliever or opener, not a traditional starter. The simulation removes that pitcher on a short hook. No other starter was invented.")

    result = simulate_games(away_club, home_club, sims, seed, postseason=postseason)
    analysis_id = str(uuid.uuid4())
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    sim_path = SIM_DIR / f"{analysis_id}.json.gz"
    sim_payload = {
        "away_runs": result.away_runs,
        "home_runs": result.home_runs,
        "away_runs_5": result.away_runs_5,
        "home_runs_5": result.home_runs_5,
        "away_win": result.away_win,
        "batters": {str(pid): rows for pid, rows in result.batter.items()},
        "pitchers": {str(pid): rows for pid, rows in result.pitcher.items()},
    }
    with gzip.open(sim_path, "wt", encoding="utf-8") as handle:
        json.dump(sim_payload, handle)

    def batter_rows(meta, runs_key_win):
        rows = []
        for pid, info in meta["batters"].items():
            samples = result.batter[pid]
            hit = _proj(samples["hits"])
            rows.append(
                {
                    **info,
                    "expected_pa": round(sum(samples["pa"]) / sims, 2),
                    "hit_probability": hit["p_at_least_1"],
                    "expected_hits": hit["expected"],
                    "multi_hit_probability": hit["p_at_least_2"],
                    "expected_total_bases": _proj(samples["total_bases"])["expected"],
                    "home_run_probability": _proj(samples["home_runs"])["p_at_least_1"],
                    "expected_walks": _proj(samples["walks"])["expected"],
                    "expected_strikeouts": _proj(samples["strikeouts"])["expected"],
                    "expected_runs": _proj(samples["runs"])["expected"],
                    "expected_rbis": _proj(samples["rbis"])["expected"],
                    "distribution": {
                        "hits": hit,
                        "total_bases": _proj(samples["total_bases"]),
                        "home_runs": _proj(samples["home_runs"]),
                    },
                }
            )
        return rows

    def pitcher_rows(club_obj, meta):
        rows = []
        for pitcher in [club_obj.starter, *club_obj.bullpen]:
            samples = result.pitcher.get(pitcher.player_id) or {}
            outs = samples.get("outs") or [0]
            load = meta["loads"].get(pitcher.player_id) or {}
            rows.append(
                {
                    "player_id": pitcher.player_id,
                    "name": pitcher.name,
                    "throws": pitcher.throws,
                    "role": pitcher.role,
                    "listed_probable": pitcher.player_id == club_obj.starter.player_id,
                    "probable_vs_confirmed": "Listed probable pitcher. MLB does not mark the pitcher confirmed in this feed before first pitch.",
                    "expected_innings": sum(outs) / sims / 3,
                    "expected_strikeouts": sum(samples.get("strikeouts") or [0]) / sims,
                    "expected_walks": sum(samples.get("walks") or [0]) / sims,
                    "expected_hits": sum(samples.get("hits") or [0]) / sims,
                    "expected_runs": sum(samples.get("runs") or [0]) / sims,
                    "appearance_probability": sum(1 for bf in (samples.get("batters_faced") or []) if bf > 0) / sims,
                    "availability": pitcher.availability,
                    "availability_label": "Listed starter" if pitcher.player_id == club_obj.starter.player_id else load.get("label", availability_label(pitcher.availability)),
                    "days_since_last": load.get("days_since_last"),
                    "pitches_1d": load.get("pitches_1d"),
                    "pitches_2d": load.get("pitches_2d"),
                    "pitches_3d": load.get("pitches_3d"),
                    "fatigue": None
                    if pitcher.player_id == club_obj.starter.player_id
                    else f"K rate multiplier {load.get('k_multiplier', 1):.2f}; this is separate from availability.",
                }
            )
        return rows

    away_rows = batter_rows(away_meta, "away")
    home_rows = batter_rows(home_meta, "home")
    away_wins = sum(result.away_win)
    home_wins = sims - away_wins
    away_pct = round(away_wins / sims * 1000) / 10
    home_pct = round((1000 - away_pct * 10) / 10, 1)
    players = {str(pid): info for pid, info in {**away_meta["batters"], **home_meta["batters"]}.items()}
    for meta in (away_meta, home_meta):
        for pid, pitcher in meta["pitchers"].items():
            players.setdefault(
                str(pid),
                {
                    "name": pitcher.name,
                    "data_support": 0.7 if pitcher.role == "starter" else 0.55,
                    "notes": [f"Role from regular-season usage: {pitcher.role}. Availability is a workload estimate, not a managerial announcement."],
                },
            )

    def best(rows, key):
        return max(rows, key=lambda row: row[key]) if rows else None

    def least(rows, key):
        return min(rows, key=lambda row: row[key]) if rows else None

    takeaways = {
        "outlook": [
            f"Simulation win rate: {teams[away_id]['abbreviation']} {away_pct:.1f}%, {teams[home_id]['abbreviation']} {home_pct:.1f}%.",
            f"Expected runs: {teams[away_id]['abbreviation']} {sum(result.away_runs)/sims:.2f}, {teams[home_id]['abbreviation']} {sum(result.home_runs)/sims:.2f}.",
            "The win probability is the share of simulations, not a market price.",
        ],
        "hitting": [],
        "pitching": [],
        "uncertainty": [
            "The model is a hierarchical shrinkage plus log5 matchup inside a Monte Carlo game. Weights were not fit to maximize betting profit.",
            "Savant season leaderboards can include games after the prediction date. Their effect is capped.",
            "Hard Rock Bet prices are not part of this projection.",
        ],
    }
    if away_rows and home_rows:
        for side_name, rows in ((teams[away_id]["abbreviation"], away_rows), (teams[home_id]["abbreviation"], home_rows)):
            top_p = best(rows, "hit_probability")
            top_hits = best(rows, "expected_hits")
            low = least(rows, "hit_probability")
            takeaways["hitting"].append(
                f"{side_name}: highest probability of a hit is {top_p['name']} at {top_p['hit_probability']:.0%}. Highest expected hits is {top_hits['name']} at {top_hits['expected_hits']:.2f}. Lowest hit probability is {low['name']} at {low['hit_probability']:.0%}."
            )
    for club_obj in (away_club, home_club):
        samples = result.pitcher[club_obj.starter.player_id]
        takeaways["pitching"].append(
            f"{club_obj.starter.name} ({club_obj.starter.role}): expected strikeouts {sum(samples['strikeouts'])/sims:.2f}, expected innings {sum(samples['outs'])/sims/3:.2f}."
        )
    if hypothetical:
        takeaways["uncertainty"].insert(0, "This is a hypothetical matchup. Probable pitchers and lineups are only as real as the inputs.")

    payload = {
        "analysis_id": analysis_id,
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "seed": seed,
        "simulation_count": sims,
        "experimental": True,
        "experimental_reason": "Plate-appearance rates use fixed shrinkage priors and a log5 matchup, not weights trained to beat sportsbook prices. Treat probabilities as structured estimates.",
        "warnings": warnings,
        "freshness": freshness,
        "game": {
            "game_pk": game_pk,
            "date": game_date,
            "start_time_utc": start,
            "status": ((match or {}).get("status") or {}).get("detailedState") or ("Hypothetical" if hypothetical else "Unknown"),
            "game_type": game_type,
            "game_type_label": GAME_TYPES.get(game_type, game_type),
            "away_team_id": away_id,
            "home_team_id": home_id,
            "away_name": teams[away_id]["name"],
            "home_name": teams[home_id]["name"],
            "away_abbreviation": teams[away_id]["abbreviation"],
            "home_abbreviation": teams[home_id]["abbreviation"],
            "venue_id": venue_id,
            "venue_name": venue.get("name") or park.get("name") or "Unknown venue",
            "hypothetical": hypothetical,
            "lineup_status": lineup_status,
            "schedule_note": "Scheduled game." if match and not body.get("hypothetical") else "Hypothetical or overridden game.",
            "probable_pitchers": {"away": away_listed, "home": home_listed},
        },
        "series": _series(match, game_date) if match else None,
        "environment": {
            "venue_name": venue.get("name") or park.get("name"),
            "roof": "closed" if closed else roof,
            "park_run_factor": park.get("run_factor"),
            "park_hr_factor": park.get("hr_factor"),
            "park_factor_note": park.get("factor_note") or parks["note"],
            "hr_environment_multiplier": park_hr,
            "weather_note": weather_note,
            "mlb_weather": (match or {}).get("weather"),
        },
        "win": {
            "away_win_probability": away_wins / sims,
            "home_win_probability": home_wins / sims,
            "away_win_percent_display": away_pct,
            "home_win_percent_display": home_pct,
            "expected_away_runs": sum(result.away_runs) / sims,
            "expected_home_runs": sum(result.home_runs) / sims,
            "expected_total_runs": (sum(result.away_runs) + sum(result.home_runs)) / sims,
        },
        "takeaways": takeaways,
        "away": {"batters": away_rows, "pitchers": pitcher_rows(away_club, away_meta)},
        "home": {"batters": home_rows, "pitchers": pitcher_rows(home_club, home_meta)},
        "players": players,
        "game_data_support": 0.75 if lineup_status == "confirmed" else 0.45,
        "sim_path": str(sim_path),
    }
    return payload


def _series(match: dict, game_date: str) -> dict | None:
    status = match.get("seriesStatus") or {}
    if not status:
        return None
    description = status.get("description")
    total = status.get("totalGames")
    # The feed's series score is after the game when the game is final.
    # Reconstruct the pregame score from the winner and the final series tally.
    series_leader = (status.get("winningTeam") or {}).get("id")
    away_id = match["teams"]["away"]["team"]["id"]
    home_id = match["teams"]["home"]["team"]["id"]
    final_wins = status.get("wins")
    final_losses = status.get("losses")
    detailed = (match.get("status") or {}).get("abstractGameState")
    game_winner = away_id if (match["teams"]["away"].get("isWinner")) else home_id if match["teams"]["home"].get("isWinner") else None
    away_before = home_before = None
    note = "Series score is the schedule tally entering this game."
    if detailed != "Final" and final_wins is not None and final_losses is not None:
        if series_leader == away_id:
            away_before, home_before = final_wins, final_losses
        elif series_leader == home_id:
            home_before, away_before = final_wins, final_losses
        else:
            away_before, home_before = final_wins, final_losses
        if away_before == 0 and home_before == 0:
            note = "The series is tied at 0-0 entering this game."
    if detailed == "Final" and series_leader in {away_id, home_id} and game_winner in {away_id, home_id} and final_wins is not None and final_losses is not None:
        leader_before = final_wins - 1 if game_winner == series_leader else final_wins
        trailer_before = final_losses if game_winner == series_leader else final_losses - 1
        if series_leader == away_id:
            away_before, home_before = leader_before, trailer_before
        else:
            home_before, away_before = leader_before, trailer_before
        note = "Pregame series score removes this game's result from the final series tally. It is not assumed to be a best-of-seven."
    wins_needed = None if not total else total // 2 + 1
    return {
        "description": description,
        "game_number": status.get("gameNumber"),
        "series_length": total,
        "away_wins_before": away_before,
        "home_wins_before": home_before,
        "wins_needed": wins_needed,
        "note": note,
        "raw_result": status.get("result"),
    }
