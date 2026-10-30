"""Pregame card storage. Saved versions are immutable JSON snapshots."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db import CardRow, CardVersionRow, SettingsRow
from app.evaluate import BankrollSettings


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_settings() -> dict:
    return {
        "bankroll": None,
        "unit_dollars": None,
        "unit_fraction": None,
        "risk_profile": "balanced",
        "use_kelly": True,
        "max_wager_fraction": 0.02,
        "max_card_fraction": 0.10,
        "max_game_fraction": 0.04,
        "max_parlay_fraction": 0.01,
    }


def settings_from_row(document: dict) -> BankrollSettings:
    bankroll = document.get("bankroll")
    return BankrollSettings(
        bankroll=None if bankroll in (None, "") else Decimal(str(bankroll)),
        unit_dollars=None if document.get("unit_dollars") in (None, "") else Decimal(str(document["unit_dollars"])),
        unit_fraction=None if document.get("unit_fraction") in (None, "") else Decimal(str(document["unit_fraction"])),
        risk_profile=document.get("risk_profile") or "balanced",
        use_kelly=bool(document.get("use_kelly", True)),
        max_wager_fraction=Decimal(str(document.get("max_wager_fraction", "0.02"))),
        max_card_fraction=Decimal(str(document.get("max_card_fraction", "0.10"))),
        max_game_fraction=Decimal(str(document.get("max_game_fraction", "0.04"))),
        max_parlay_fraction=Decimal(str(document.get("max_parlay_fraction", "0.01"))),
    )


def get_settings(session) -> dict:
    row = session.get(SettingsRow, 1)
    if row is None:
        row = SettingsRow(id=1, document_json=json.dumps(default_settings()))
        session.add(row)
        session.commit()
    return json.loads(row.document_json)


def save_settings(session, document: dict) -> dict:
    current = get_settings(session)
    current.update(document)
    if current.get("unit_dollars") not in (None, "") and current.get("unit_fraction") not in (None, ""):
        raise ValueError("Set a dollar unit or a percent of bankroll, not both.")
    row = session.get(SettingsRow, 1)
    row.document_json = json.dumps(current)
    session.commit()
    return current


def empty_document(name: str = "My Pregame Card") -> dict:
    return {"name": name, "selections": [], "parlays": [], "history": []}


def get_or_create_active(session) -> CardRow:
    row = session.scalar(select(CardRow).where(CardRow.status == "draft").order_by(CardRow.updated_at.desc()))
    if row:
        return row
    now = utcnow()
    row = CardRow(
        id=str(uuid.uuid4()),
        name="My Pregame Card",
        status="draft",
        created_at=now,
        updated_at=now,
        version=0,
        document_json=json.dumps(empty_document()),
    )
    session.add(row)
    session.commit()
    return row


def document_of(row: CardRow) -> dict:
    return json.loads(row.document_json)


def _write(session, row: CardRow, document: dict) -> dict:
    row.document_json = json.dumps(document)
    row.updated_at = utcnow()
    row.name = document.get("name") or row.name
    session.commit()
    return document


def save_version(session, row: CardRow) -> dict:
    document = document_of(row)
    row.version += 1
    snapshot = json.loads(json.dumps(document))
    snapshot["saved_version"] = row.version
    snapshot["saved_at"] = utcnow()
    session.add(
        CardVersionRow(
            card_id=row.id,
            version=row.version,
            created_at=snapshot["saved_at"],
            snapshot_json=json.dumps(snapshot),
        )
    )
    document["saved_version"] = row.version
    return _write(session, row, document)


def get_version(session, card_id: str, version: int) -> dict | None:
    row = session.scalar(
        select(CardVersionRow).where(CardVersionRow.card_id == card_id, CardVersionRow.version == version)
    )
    if row is None:
        return None
    return json.loads(row.snapshot_json)


def add_selection(session, row: CardRow, captured: dict, *, allow_duplicate: bool = False) -> dict:
    document = document_of(row)
    identity = (
        captured.get("analysis_id"),
        captured.get("market_type"),
        captured.get("player_id"),
        captured.get("team_side"),
        captured.get("selection"),
        str(captured.get("line")),
        captured.get("quote_id"),
    )
    for existing in document["selections"]:
        other = existing["captured"]
        other_identity = (
            other.get("analysis_id"),
            other.get("market_type"),
            other.get("player_id"),
            other.get("team_side"),
            other.get("selection"),
            str(other.get("line")),
            other.get("quote_id"),
        )
        if identity == other_identity and not allow_duplicate:
            raise ValueError("That price and selection are already on the card.")
    evaluation = captured["evaluation"]
    if evaluation.get("status") == "Insufficient Confidence" and evaluation.get("ev_per_unit") is None:
        raise ValueError("A selection needs a model probability and a verified price.")
    stake = evaluation.get("stake_dollars") or 0
    document["selections"].append(
        {
            "id": str(uuid.uuid4()),
            "added_at": utcnow(),
            "captured": json.loads(json.dumps(captured)),
            "stake_dollars": stake,
            "result": None,
        }
    )
    document["history"].append({"at": utcnow(), "action": "add_selection", "market": captured.get("market_type")})
    _write(session, row, document)
    return document


def add_tier(session, row: CardRow, recommendation: dict, tier: str) -> dict:
    added = 0
    for item in recommendation["tiers"].get(tier, []):
        if item["evaluation"]["status"] != "Qualified Value":
            continue
        try:
            add_selection(session, row, item)
            added += 1
        except ValueError:
            continue
    document = document_of(row)
    if document["parlays"]:
        pass
    document["history"].append(
        {
            "at": utcnow(),
            "action": "add_tier",
            "tier": tier,
            "added": added,
            "note": "Tier selections were added as individual wagers, not as one parlay.",
        }
    )
    return _write(session, row, document)


def remove_selection(session, row: CardRow, selection_id: str) -> dict:
    document = document_of(row)
    document["selections"] = [item for item in document["selections"] if item["id"] != selection_id]
    for parlay in document["parlays"]:
        parlay["selection_ids"] = [sid for sid in parlay.get("selection_ids", []) if sid != selection_id]
    document["parlays"] = [parlay for parlay in document["parlays"] if len(parlay.get("selection_ids", [])) >= 2]
    return _write(session, row, document)


def update_stake(session, row: CardRow, selection_id: str, stake: Decimal, settings: BankrollSettings) -> dict:
    if stake < 0:
        raise ValueError("A stake cannot be negative.")
    document = document_of(row)
    bankroll = settings.bankroll or Decimal(0)
    if bankroll and stake > bankroll * settings.max_wager_fraction:
        raise ValueError("That stake is above the configured per-wager cap.")
    game_totals: dict[str, Decimal] = {}
    for item in document["selections"]:
        if item["id"] == selection_id:
            continue
        key = str(item["captured"].get("game_id"))
        game_totals[key] = game_totals.get(key, Decimal(0)) + Decimal(str(item.get("stake_dollars") or 0))
    target = next(item for item in document["selections"] if item["id"] == selection_id)
    key = str(target["captured"].get("game_id"))
    if bankroll and game_totals.get(key, Decimal(0)) + stake > bankroll * settings.max_game_fraction:
        raise ValueError("That stake would exceed the per-game exposure limit.")
    total_other = sum(Decimal(str(item.get("stake_dollars") or 0)) for item in document["selections"] if item["id"] != selection_id)
    if bankroll and total_other + stake > bankroll * settings.max_card_fraction:
        raise ValueError("That stake would exceed the card exposure limit.")
    target["stake_dollars"] = float(stake)
    return _write(session, row, document)


def add_parlay(session, row: CardRow, selection_ids: list[str], combined_american: int | None, evaluation: dict, detail: dict) -> dict:
    document = document_of(row)
    known = {item["id"] for item in document["selections"]}
    if any(sid not in known for sid in selection_ids):
        raise ValueError("Every parlay leg must already be an individual card selection.")
    if evaluation.get("status") == "Informational Only":
        # Store it, but it is not a qualified wager. The caller decides.
        pass
    document["parlays"].append(
        {
            "id": str(uuid.uuid4()),
            "selection_ids": list(selection_ids),
            "combined_american": combined_american,
            "theoretical_price": evaluation.get("theoretical_price", False),
            "evaluation": evaluation,
            "detail": detail,
            "stake_dollars": evaluation.get("stake_dollars") or 0,
            "added_at": utcnow(),
        }
    )
    return _write(session, row, document)


def card_exposure(document: dict) -> dict:
    by_game: dict[str, float] = {}
    by_player: dict[str, float] = {}
    for item in document.get("selections", []):
        stake = float(item.get("stake_dollars") or 0)
        game_id = str(item["captured"].get("game_id"))
        by_game[game_id] = by_game.get(game_id, 0.0) + stake
        player = item["captured"].get("player_id")
        if player:
            by_player[str(player)] = by_player.get(str(player), 0.0) + stake
    overlaps = []
    selections = document.get("selections", [])
    for index, left in enumerate(selections):
        for right in selections[index + 1 :]:
            same_game = left["captured"].get("game_id") == right["captured"].get("game_id")
            same_player = left["captured"].get("player_id") and left["captured"].get("player_id") == right["captured"].get("player_id")
            if same_game:
                overlaps.append(
                    {
                        "selection_ids": [left["id"], right["id"]],
                        "reason": "Same player in the same game." if same_player else "Same game. Outcomes can move together.",
                    }
                )
    stake = sum(float(item.get("stake_dollars") or 0) for item in selections)
    expected = 0.0
    for item in selections:
        ev = item["captured"]["evaluation"].get("ev_per_unit")
        if ev is not None:
            expected += float(item.get("stake_dollars") or 0) * float(ev)
    parlay_stake = sum(float(item.get("stake_dollars") or 0) for item in document.get("parlays", []))
    parlay_expected = 0.0
    for item in document.get("parlays", []):
        ev = (item.get("evaluation") or {}).get("ev_per_unit")
        if ev is not None:
            parlay_expected += float(item.get("stake_dollars") or 0) * float(ev)
    total_stake = stake + parlay_stake
    return {
        "selection_count": len(selections),
        "parlay_count": len(document.get("parlays", [])),
        "staked_dollars": total_stake,
        "expected_profit_dollars": expected + parlay_expected,
        "expected_roi": None if total_stake == 0 else (expected + parlay_expected) / total_stake,
        "by_game": by_game,
        "by_player": by_player,
        "overlaps": overlaps,
        "roi_method": "Sum of expected profits divided by total stake. This is not the average of each wager's ROI.",
    }
