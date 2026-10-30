"""Bullpen workload, availability, and a separate effectiveness adjustment.

A short outing yesterday does not automatically make a reliever unavailable.
Availability and the performance adjustment are different numbers.
"""

from __future__ import annotations

import math
from datetime import date


def days_between(game_day: date, pitched_on: date) -> int:
    return (game_day - pitched_on).days


def workload(appearances: list[dict], game_day: date) -> dict:
    """appearances: {date: date, pitches: float, outs: float} before the game."""
    pitches = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    last = None
    pitched_yesterday = False
    consecutive = 0
    for row in appearances:
        delta = days_between(game_day, row["date"])
        if delta <= 0:
            continue
        if last is None or row["date"] > last:
            last = row["date"]
        if delta in pitches:
            pitches[delta] += float(row.get("pitches") or 0.0)
        if delta == 1:
            pitched_yesterday = True
    cursor = 1
    by_day = {}
    for row in appearances:
        delta = days_between(game_day, row["date"])
        if delta > 0:
            by_day.setdefault(delta, 0.0)
            by_day[delta] += float(row.get("pitches") or 0.0)
    while by_day.get(cursor, 0.0) > 0 and cursor <= 6:
        consecutive += 1
        cursor += 1
    days_since = None if last is None else days_between(game_day, last)
    penalty = pitches[1] / 28.0 + pitches[2] / 45.0 + pitches[3] / 70.0 + max(0, consecutive - 1) * 0.18
    availability = math.exp(-penalty)
    if days_since is not None and days_since >= 4:
        availability = max(availability, 0.90)
    elif days_since is not None and days_since >= 3:
        availability = max(availability, 0.82)
    availability = min(0.98, max(0.05, availability))
    severity = max(0.0, (0.85 - availability) / 0.85)
    return {
        "pitches_1d": pitches[1],
        "pitches_2d": pitches[2],
        "pitches_3d": pitches[3],
        "consecutive_days": consecutive,
        "days_since_last": days_since,
        "pitched_yesterday": pitched_yesterday,
        "availability": availability,
        "k_multiplier": 1.0 - 0.12 * severity,
        "bb_multiplier": 1.0 + 0.15 * severity,
        "hr_multiplier": 1.0 + 0.10 * severity,
        "label": availability_label(availability),
    }


def availability_label(availability: float) -> str:
    if availability >= 0.75:
        return "Likely available"
    if availability >= 0.55:
        return "Available with workload concerns"
    if availability >= 0.35:
        return "Limited availability"
    return "Uncertain"


def pitcher_role(games: float, starts: float, saves: float, holds: float, innings: float) -> str:
    apps = max(games, 1.0)
    ip_per = innings / apps
    if starts >= 8 and innings / max(starts, 1.0) >= 3.5:
        return "starter"
    if starts <= 2 and ip_per < 2.2:
        if saves >= 8:
            return "closer"
        if holds >= 8:
            return "setup"
        return "reliever"
    if saves >= 8:
        return "closer"
    if holds >= 8:
        return "setup"
    if ip_per >= 2.0:
        return "long"
    return "reliever"


def listed_starter_role(games: float, starts: float, innings: float) -> str:
    apps = max(games, 1.0)
    if starts >= 5 and innings / max(starts, 1.0) >= 3.5:
        return "starter"
    if starts <= 2 and innings / apps < 2.5:
        return "opener_or_reliever"
    return "swing"
