import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cards import add_selection, add_tier, card_exposure, get_or_create_active, get_version, save_version, update_stake
from app.db import Base
from app.evaluate import BankrollSettings
from app.recommend import recommend_quotes

NOW = datetime(2026, 10, 18, 18, tzinfo=timezone.utc)
START = "2026-10-19T00:08:00+00:00"


def _analysis():
    hits = [1] * 1680 + [0] * 2320
    wins = [1] * 3600 + [0] * 400
    return {
        "analysis_id": "a1",
        "model_version": "0.2.0",
        "simulation_count": 4000,
        "game_data_support": 0.8,
        "game": {
            "game_pk": 849815,
            "date": "2026-10-19",
            "start_time_utc": START,
            "away_abbreviation": "CLE",
            "home_abbreviation": "TB",
        },
        "players": {"5": {"name": "Contact Hitter", "data_support": 0.8, "notes": ["Season contact rate, shrunk toward career."]}},
    }, {
        "away_win": wins,
        "away_runs": [4] * 4000,
        "home_runs": [3] * 4000,
        "away_runs_5": [2] * 4000,
        "home_runs_5": [2] * 4000,
        "batters": {"5": {"hits": hits, "total_bases": hits, "home_runs": [0] * 4000, "walks": [0] * 4000, "strikeouts": [0] * 4000, "runs": [0] * 4000, "rbis": [0] * 4000, "stolen_bases": [0] * 4000, "hits_runs_rbis": hits}},
        "pitchers": {},
    }


def _quote(**kwargs):
    base = {
        "id": 1,
        "sportsbook": "Hard Rock Bet",
        "source": "manual",
        "quoted_at": (NOW - timedelta(minutes=20)).isoformat(),
        "market_type": "player_hits",
        "selection": "over",
        "line": "0.5",
        "player_id": 5,
        "american": 150,
    }
    base.update(kwargs)
    return base


def test_high_probability_negative_price_does_not_fill_a_tier_and_a_longer_price_can():
    analysis, sim = _analysis()
    settings = BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive")
    bad = recommend_quotes(
        analysis,
        sim,
        [_quote(id=1, market_type="moneyline", selection="away", player_id=None, line=None, american=-1200)],
        settings,
        now=NOW,
    )
    assert bad["tiers"]["conservative"] == []
    assert bad["summary"]["qualified_current"] == 0
    assert bad["rows"][0]["evaluation"]["p_win"] == 0.9
    assert bad["rows"][0]["evaluation"]["status"] == "No Value"

    good = recommend_quotes(analysis, sim, [_quote(id=2, american=150)], settings, now=NOW)
    placed = [row for rows in good["tiers"].values() for row in rows]
    assert placed
    assert placed[0]["evaluation"]["p_win"] == 0.42
    assert placed[0]["evaluation"]["ev_per_unit"] > 0
    assert placed[0]["source"] == "manual"
    assert good["tiers"]["conservative"] == [] or placed[0]["evaluation"]["tier"] != "conservative"


def test_card_tier_adds_singles_and_snapshots_stay_put():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    analysis, sim = _analysis()
    settings = BankrollSettings(bankroll=Decimal("1000"), risk_profile="aggressive")
    report = recommend_quotes(analysis, sim, [_quote(american=150)], settings, now=NOW)
    tier = next(name for name, rows in report["tiers"].items() if rows)
    card = get_or_create_active(session)
    document = add_tier(session, card, report, tier)
    assert document["parlays"] == []
    assert len(document["selections"]) == 1
    assert document["selections"][0]["captured"]["evaluation"]["p_win"] == 0.42
    save_version(session, card)
    frozen = get_version(session, card.id, 1)
    update_stake(session, card, document["selections"][0]["id"], Decimal("1.00"), settings)
    assert get_version(session, card.id, 1)["selections"][0]["stake_dollars"] == frozen["selections"][0]["stake_dollars"]
    exposure = card_exposure(json.loads(card.document_json))
    assert exposure["parlay_count"] == 0
    assert exposure["roi_method"].startswith("Sum of expected profits")
    with_session_error = False
    try:
        update_stake(session, card, document["selections"][0]["id"], Decimal("500"), settings)
    except ValueError:
        with_session_error = True
    assert with_session_error
    session.close()
