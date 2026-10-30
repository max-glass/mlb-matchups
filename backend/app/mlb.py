"""MLB Stats API and Baseball Savant access. No sportsbook scraping."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

from app.paths import CACHE_DIR

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 mlb-matchups/0.2"
STATS = "https://statsapi.mlb.com"


class DataError(RuntimeError):
    pass


def _path_for(url: str) -> tuple[Path, Path]:
    key = hashlib.sha256(url.encode()).hexdigest()
    folder = CACHE_DIR / key[:2]
    folder.mkdir(parents=True, exist_ok=True)
    return folder / key, folder / f"{key}.meta.json"


async def fetch(client: httpx.AsyncClient, url: str, ttl: int, source: str) -> tuple[bytes | None, dict]:
    body_path, meta_path = _path_for(url)
    now = time.time()
    if body_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if now - meta.get("fetched_at", 0) < ttl:
            meta["cache"] = "fresh"
            return body_path.read_bytes(), meta
    try:
        response = await client.get(url, timeout=45)
        response.raise_for_status()
        content = response.content
        body_path.write_bytes(content)
        meta = {
            "source": source,
            "url": url,
            "fetched_at": now,
            "fetched_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "status": "ok",
            "cache": "miss",
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return content, meta
    except Exception as exc:
        if body_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = "stale_cache"
            meta["detail"] = str(exc)
            return body_path.read_bytes(), meta
        return None, {"source": source, "url": url, "status": "error", "detail": str(exc), "fetched_at_iso": None}


async def fetch_json(client, url: str, ttl: int, source: str) -> tuple[dict | None, dict]:
    content, meta = await fetch(client, url, ttl, source)
    if content is None:
        return None, meta
    try:
        return json.loads(content.decode("utf-8")), meta
    except json.JSONDecodeError as exc:
        meta["status"] = "error"
        meta["detail"] = f"Invalid JSON: {exc}"
        return None, meta


def schedule_url(date: str) -> str:
    return (
        f"{STATS}/api/v1/schedule?sportId=1&date={date}"
        "&hydrate=probablePitcher,lineups,team,venue,seriesStatus,weather,decisions"
    )


def season_url(team_id: int, group: str, season: int) -> str:
    return (
        f"{STATS}/api/v1/stats?stats=season&group={group}&season={season}"
        f"&sportId=1&teamId={team_id}&playerPool=All&limit=80&gameType=R"
    )


def league_url(group: str, season: int) -> str:
    return f"{STATS}/api/v1/teams/stats?season={season}&group={group}&stats=season&sportIds=1&gameType=R"


def log_url(player_id: int, group: str, season: int, game_type: str) -> str:
    return (
        f"{STATS}/api/v1/people/{player_id}/stats?stats=gameLog&group={group}"
        f"&season={season}&sportId=1&gameType={game_type}"
    )
