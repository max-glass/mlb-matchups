"""HTTP API for predictions, verified prices, tiers, and the pregame card."""

from __future__ import annotations

import csv
import gzip
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.cards import (
    add_parlay,
    add_selection,
    add_tier,
    card_exposure,
    document_of,
    get_or_create_active,
    get_settings,
    get_version,
    save_settings,
    save_version,
    settings_from_row,
    remove_selection,
    update_stake,
)
from app.db import AnalysisRow, CardRow, OddsRow, make_engine, make_session_factory
from app.evaluate import BankrollSettings, evaluate_parlay
from app.mlb import UA
from app.paths import DATA_DIR, MODEL_VERSION
from app.predict import analyze
from app.recommend import recommend_quotes
from decimal import Decimal

ENGINE = make_engine()
SESSIONS = make_session_factory(ENGINE)


def session() -> Session:
    return SESSIONS()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(headers={"User-Agent": UA, "Accept": "application/json,text/csv,*/*"}, follow_redirects=True)
    yield
    await app.state.http.aclose()


app = FastAPI(title="MLB Prediction Center", version=MODEL_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeBody(BaseModel):
    date: str
    away_team_id: int
    home_team_id: int
    simulation_count: int = Field(default=1200, ge=200, le=8000)
    seed: int = 20261019
    hypothetical: bool = False
    away_starter_id: int | None = None
    home_starter_id: int | None = None
    away_lineup: list[int] | None = None
    home_lineup: list[int] | None = None


class OddsBody(BaseModel):
    sportsbook: str = "Hard Rock Bet"
    source: str = "manual"
    quoted_at: str
    game_date: str
    game_pk: int | None = None
    away_team_id: int | None = None
    home_team_id: int | None = None
    market_type: str
    player_id: int | None = None
    player_name: str | None = None
    team_side: str | None = None
    selection: str
    line: float | None = None
    american: int
    other_side_american: int | None = None


class SettingsBody(BaseModel):
    bankroll: float | None = None
    unit_dollars: float | None = None
    unit_fraction: float | None = None
    risk_profile: str = "balanced"
    use_kelly: bool = True
    max_wager_fraction: float = 0.02
    max_card_fraction: float = 0.10
    max_game_fraction: float = 0.04
    max_parlay_fraction: float = 0.01


class StakeBody(BaseModel):
    stake_dollars: float


class ParlayBody(BaseModel):
    selection_ids: list[str]
    combined_american: int | None = None
    quoted_at: str | None = None


def _load_analysis(db: Session, analysis_id: str) -> tuple[dict, dict]:
    row = db.get(AnalysisRow, analysis_id)
    if row is None:
        raise HTTPException(404, "That analysis does not exist. Run the model before asking for recommendations.")
    payload = json.loads(row.payload_json)
    with gzip.open(row.sim_path, "rt", encoding="utf-8") as handle:
        sim = json.load(handle)
    return payload, sim


def _quotes_for(db: Session, analysis: dict) -> list[dict]:
    game = analysis["game"]
    rows = db.query(OddsRow).filter(OddsRow.game_date == game["date"]).all()
    matched = []
    for row in rows:
        if row.away_team_id and row.away_team_id != game["away_team_id"]:
            continue
        if row.home_team_id and row.home_team_id != game["home_team_id"]:
            continue
        if row.game_pk and game.get("game_pk") and row.game_pk != game["game_pk"]:
            continue
        matched.append(
            {
                "id": row.id,
                "sportsbook": row.sportsbook,
                "source": row.source,
                "quoted_at": row.quoted_at,
                "market_type": row.market_type,
                "player_id": row.player_id,
                "team_side": row.team_side,
                "selection": row.selection,
                "line": None if row.line is None else float(row.line),
                "american": row.american,
                "other_side_american": row.other_side_american,
            }
        )
    return matched


def _store_odds(db: Session, quote: OddsBody | dict) -> OddsRow:
    data = quote if isinstance(quote, dict) else quote.model_dump()
    if data["source"] not in {"manual", "csv", "provider"}:
        raise HTTPException(422, "Odds source must be manual, csv, or provider.")
    if not data.get("quoted_at"):
        raise HTTPException(422, "A quote timestamp is required. The app will not assume the price is current.")
    try:
        datetime.fromisoformat(data["quoted_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "quoted_at must be an ISO timestamp.") from exc
    row = OddsRow(
        created_at=datetime.now(timezone.utc).isoformat(),
        sportsbook=data["sportsbook"],
        source=data["source"],
        quoted_at=data["quoted_at"],
        game_date=data["game_date"],
        game_pk=data.get("game_pk"),
        away_team_id=data.get("away_team_id"),
        home_team_id=data.get("home_team_id"),
        market_type=data["market_type"],
        player_id=data.get("player_id"),
        team_side=data.get("team_side"),
        selection=data["selection"],
        line=None if data.get("line") in (None, "") else str(data["line"]),
        american=int(data["american"]),
        other_side_american=data.get("other_side_american"),
        context="pregame",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@app.get("/api/health")
def health():
    return {
        "model_version": MODEL_VERSION,
        "hard_rock_public_api": False,
        "odds_access": "Hard Rock Bet does not publish an official odds API. This server does not scrape the sportsbook. Prices are manual, CSV imports, or a configured provider payload in this app's own schema.",
        "live_betting": False,
    }


@app.get("/api/config/default")
def default_matchup():
    return json.loads((DATA_DIR / "default_matchup.json").read_text(encoding="utf-8"))


@app.get("/api/teams")
async def teams(season: int = 2026):
    payload, meta = await fetch_teams(season)
    if payload is None:
        raise HTTPException(503, meta.get("detail") or "MLB teams could not be loaded.")
    return {
        "teams": [
            {
                "id": team["id"],
                "name": team["name"],
                "abbreviation": team["abbreviation"],
                "venue_id": (team.get("venue") or {}).get("id"),
                "venue_name": (team.get("venue") or {}).get("name"),
            }
            for team in payload.get("teams", [])
            if team.get("active")
        ]
    }


async def fetch_teams(season: int):
    from app.mlb import fetch_json

    return await fetch_json(app.state.http, f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={season}", 86400, "mlb-teams")


@app.get("/api/schedule")
async def schedule(date: str):
    from app.mlb import fetch_json, schedule_url

    payload, meta = await fetch_json(app.state.http, schedule_url(date), 900, "mlb-schedule")
    if payload is None:
        raise HTTPException(503, meta.get("detail") or "Schedule unavailable.")
    games = []
    for block in payload.get("dates") or []:
        for game in block.get("games") or []:
            sides = game["teams"]
            games.append(
                {
                    "game_pk": game["gamePk"],
                    "date": game.get("officialDate") or date,
                    "start_time_utc": game.get("gameDate"),
                    "status": (game.get("status") or {}).get("detailedState"),
                    "game_type": game.get("gameType"),
                    "away": {"id": sides["away"]["team"]["id"], "name": sides["away"]["team"]["name"], "abbreviation": sides["away"]["team"].get("abbreviation")},
                    "home": {"id": sides["home"]["team"]["id"], "name": sides["home"]["team"]["name"], "abbreviation": sides["home"]["team"].get("abbreviation")},
                    "venue": (game.get("venue") or {}).get("name"),
                    "probable_away": (sides["away"].get("probablePitcher") or {}).get("fullName"),
                    "probable_home": (sides["home"].get("probablePitcher") or {}).get("fullName"),
                    "series": (game.get("seriesStatus") or {}).get("shortDescription"),
                }
            )
    return {"date": date, "games": games, "freshness": meta.get("fetched_at_iso"), "status": meta.get("status")}


@app.post("/api/matchups/analyze")
async def run_analysis(body: AnalyzeBody):
    try:
        payload = await analyze(app.state.http, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    db = session()
    try:
        db.add(
            AnalysisRow(
                id=payload["analysis_id"],
                created_at=payload["generated_at"],
                model_version=payload["model_version"],
                game_date=payload["game"]["date"],
                game_pk=payload["game"]["game_pk"],
                payload_json=json.dumps(payload),
                sim_path=payload["sim_path"],
            )
        )
        db.commit()
    finally:
        db.close()
    public = dict(payload)
    public.pop("sim_path", None)
    return public


@app.get("/api/matchups/{analysis_id}")
def get_analysis(analysis_id: str):
    db = session()
    try:
        payload, _sim = _load_analysis(db, analysis_id)
    finally:
        db.close()
    payload.pop("sim_path", None)
    return payload


@app.post("/api/betting/odds")
def create_odds(body: OddsBody):
    db = session()
    try:
        row = _store_odds(db, body)
        return {"id": row.id, "source": row.source, "sportsbook": row.sportsbook, "quoted_at": row.quoted_at}
    finally:
        db.close()


@app.get("/api/betting/odds/template")
def odds_template():
    header = "sportsbook,source,quoted_at,game_date,game_pk,away_team_id,home_team_id,market_type,player_id,player_name,team_side,selection,line,american,other_side_american"
    return {"csv": header + "\n", "note": "The template has headers only. Example prices are not included."}


@app.post("/api/betting/odds/import")
async def import_odds(file: UploadFile = File(...)):
    text = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    db = session()
    created = []
    try:
        for raw in reader:
            if not any((raw or {}).values()):
                continue
            quote = {
                "sportsbook": raw.get("sportsbook") or "Hard Rock Bet",
                "source": "csv",
                "quoted_at": raw.get("quoted_at"),
                "game_date": raw.get("game_date"),
                "game_pk": int(raw["game_pk"]) if raw.get("game_pk") else None,
                "away_team_id": int(raw["away_team_id"]) if raw.get("away_team_id") else None,
                "home_team_id": int(raw["home_team_id"]) if raw.get("home_team_id") else None,
                "market_type": raw.get("market_type"),
                "player_id": int(raw["player_id"]) if raw.get("player_id") else None,
                "player_name": raw.get("player_name"),
                "team_side": raw.get("team_side") or None,
                "selection": raw.get("selection"),
                "line": float(raw["line"]) if raw.get("line") not in (None, "") else None,
                "american": int(raw["american"]),
                "other_side_american": int(raw["other_side_american"]) if raw.get("other_side_american") else None,
            }
            created.append(_store_odds(db, quote).id)
    finally:
        db.close()
    return {"imported": len(created), "ids": created, "source": "csv"}


@app.get("/api/recommendations")
def recommendations(analysis_id: str):
    db = session()
    try:
        payload, sim = _load_analysis(db, analysis_id)
        settings = settings_from_row(get_settings(db))
        card = document_of(get_or_create_active(db))
        report = recommend_quotes(payload, sim, _quotes_for(db, payload), settings, card_selections=card["selections"])
        report["odds_access"] = {
            "hard_rock_public_api": False,
            "note": "No live Hard Rock Bet feed is connected. A row appears only after a manual or imported price is stored. The source field says which.",
        }
        return report
    finally:
        db.close()


@app.get("/api/settings")
def read_settings():
    db = session()
    try:
        return get_settings(db)
    finally:
        db.close()


@app.put("/api/settings")
def write_settings(body: SettingsBody):
    db = session()
    try:
        try:
            return save_settings(db, body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    finally:
        db.close()


@app.get("/api/cards/active")
def active_card():
    db = session()
    try:
        row = get_or_create_active(db)
        document = document_of(row)
        return {"id": row.id, "version": row.version, "status": row.status, "document": document, "exposure": card_exposure(document)}
    finally:
        db.close()


class AddBody(BaseModel):
    analysis_id: str
    quote_id: int


@app.post("/api/cards/active/selections")
def add_to_card(body: AddBody):
    db = session()
    try:
        payload, sim = _load_analysis(db, body.analysis_id)
        quotes = [quote for quote in _quotes_for(db, payload) if quote["id"] == body.quote_id]
        if not quotes:
            raise HTTPException(404, "That price is not stored for this game.")
        settings = settings_from_row(get_settings(db))
        report = recommend_quotes(payload, sim, quotes, settings)
        row = get_or_create_active(db)
        try:
            document = add_selection(db, row, report["rows"][0])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"document": document, "exposure": card_exposure(document)}
    finally:
        db.close()


class TierBody(BaseModel):
    analysis_id: str
    tier: str


@app.post("/api/cards/active/tier")
def add_tier_to_card(body: TierBody):
    if body.tier not in {"conservative", "balanced", "aggressive", "long_shot"}:
        raise HTTPException(422, "Unknown tier.")
    db = session()
    try:
        payload, sim = _load_analysis(db, body.analysis_id)
        settings = settings_from_row(get_settings(db))
        report = recommend_quotes(payload, sim, _quotes_for(db, payload), settings)
        row = get_or_create_active(db)
        document = add_tier(db, row, report, body.tier)
        return {
            "document": document,
            "exposure": card_exposure(document),
            "note": "Each qualifying wager was added separately. They were not combined into a parlay.",
        }
    finally:
        db.close()


@app.delete("/api/cards/active/selections/{selection_id}")
def delete_selection(selection_id: str):
    db = session()
    try:
        row = get_or_create_active(db)
        document = remove_selection(db, row, selection_id)
        return {"document": document, "exposure": card_exposure(document)}
    finally:
        db.close()


@app.patch("/api/cards/active/selections/{selection_id}")
def patch_stake(selection_id: str, body: StakeBody):
    db = session()
    try:
        row = get_or_create_active(db)
        try:
            document = update_stake(db, row, selection_id, Decimal(str(body.stake_dollars)), settings_from_row(get_settings(db)))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"document": document, "exposure": card_exposure(document)}
    finally:
        db.close()


@app.post("/api/cards/active/parlays")
def create_parlay(body: ParlayBody):
    db = session()
    try:
        row = get_or_create_active(db)
        document = document_of(row)
        selected = [item for item in document["selections"] if item["id"] in body.selection_ids]
        if len(selected) != len(body.selection_ids):
            raise HTTPException(422, "Every leg must be on the card.")
        analysis_ids = {item["captured"].get("analysis_id") for item in selected}
        legs = []
        for item in selected:
            captured = item["captured"]
            payload, sim = _load_analysis(db, captured["analysis_id"])
            quote = {
                "market_type": captured["market_type"],
                "selection": captured["selection"],
                "line": captured.get("line"),
                "player_id": captured.get("player_id"),
                "team_side": captured.get("team_side"),
            }
            from app.recommend import _samples_for

            samples, _kind = _samples_for(sim, payload, quote)
            legs.append(
                {
                    "game_id": captured.get("game_id"),
                    "player_id": captured.get("player_id"),
                    "market_type": captured["market_type"],
                    "side": "moneyline" if captured["market_type"] in {"moneyline", "f5_moneyline"} else captured["selection"],
                    "line": captured.get("line"),
                    "p_win": captured["evaluation"].get("p_win") or 0,
                    "american": None,
                    "samples": samples,
                    "data_support": captured["evaluation"].get("data_support") or 0.5,
                    "n_sims": payload.get("simulation_count"),
                }
            )
        evaluation, detail = evaluate_parlay(
            legs,
            combined_american=body.combined_american,
            quoted_at=datetime.fromisoformat(body.quoted_at.replace("Z", "+00:00")) if body.quoted_at else None,
            price_source="manual" if body.combined_american else "missing",
            settings=settings_from_row(get_settings(db)),
            n_sims=min(item["n_sims"] for item in legs) if legs else None,
        )
        document = add_parlay(db, row, body.selection_ids, body.combined_american, evaluation.public(), detail)
        return {"document": document, "exposure": card_exposure(document), "evaluation": evaluation.public(), "detail": detail}
    finally:
        db.close()


@app.post("/api/cards/active/save")
def save_card():
    db = session()
    try:
        row = get_or_create_active(db)
        document = save_version(db, row)
        return {"id": row.id, "version": row.version, "document": document}
    finally:
        db.close()


@app.get("/api/cards/{card_id}/versions/{version}")
def read_version(card_id: str, version: int):
    db = session()
    try:
        snapshot = get_version(db, card_id, version)
    finally:
        db.close()
    if snapshot is None:
        raise HTTPException(404, "That saved version does not exist.")
    return snapshot


@app.get("/api/performance")
def performance():
    db = session()
    try:
        cards = db.query(CardRow).all()
        settled = []
        for card in cards:
            for selection in document_of(card).get("selections", []):
                if selection.get("result"):
                    settled.append(selection)
        return {
            "sample_size": len(settled),
            "sample_is_small": len(settled) < 30,
            "realized_profit": sum((item["result"] or {}).get("profit") or 0 for item in settled),
            "warning": "No tier is called profitable from this sample. Realized profit is not expected value. Historical Hard Rock closing lines are included only when a later pregame snapshot was stored.",
            "closing_line_value": None,
        }
    finally:
        db.close()
