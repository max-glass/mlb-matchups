# MLB Prediction Center

Pregame matchup projections and a value-based betting worksheet. The Prediction Center is the home page. My Pregame Card stores the exact price and model probability you selected.

## What this is not

Hard Rock Bet does not publish an official odds API. This project does not scrape hardrock.bet. A recommendation appears only after you enter a price or import a CSV. Those prices are labeled `manual` or `csv`. They are not described as a live feed.

The model does not promise profit. Tiers require a positive expected return at the quoted payout. A high win probability at a short price is not a recommendation.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000

cd ..\frontend
npm install
npm test
npm run dev
```

Open http://127.0.0.1:5173. The default matchup request is Guardians at Rays on 2026-10-19, because that game is on the MLB schedule. Change the teams and date for any other game.

SQLite is created at `backend/app/data/app.db`. Set `DATABASE_URL` only if you point `make_engine` at Postgres; the tables use ordinary SQLAlchemy types.

## Odds CSV

`GET /api/betting/odds/template` returns headers and no sample prices. Required columns include `sportsbook`, `quoted_at`, `game_date`, `market_type`, `selection`, and `american`.

## Tests

```powershell
cd backend
.\.venv\Scripts\python -m pytest
cd ..\frontend
npm test
```

## Limitations

- Plate-appearance rates use fixed shrinkage and a log5 matchup. They are not weights fit to beat a sportsbook.
- Savant season leaderboards can include games after the prediction date. Their effect is capped, and the matchup page says so.
- Earned-run props are refused because errors are not modeled.
- Same-game parlays need the stored simulation. Without it, the parlay stays informational. A missing combined price is labeled theoretical and is not a Hard Rock quote.
- Historical betting ROI is not computed unless you import historical prices. Closing-line value stays empty when no later pregame quote was stored.
- Bullpen availability is a workload estimate, not a manager's decision.
- Park factors are a shrunk reference table, not a live feed. Closed roofs do not take an outdoor weather adjustment.
