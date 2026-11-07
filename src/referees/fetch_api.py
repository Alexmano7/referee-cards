"""Download matches (with referees) from the football-data.org API v4."""

from __future__ import annotations

import json
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from referees.config import (
    API_BASE_URL,
    API_LEAGUES,
    RAW_API_DIR,
    USER_AGENT,
    League,
    current_season_start_year,
    get_api_token,
)

# Free tier: 10 requests per minute. 6.5 s between calls keeps us safely below that.
SECONDS_BETWEEN_CALLS = 6.5
UPCOMING_STATUSES = {"SCHEDULED", "TIMED"}


class ApiError(RuntimeError):
    pass


def api_path(league: League, season: int, base_dir: Path = RAW_API_DIR) -> Path:
    return base_dir / str(season) / f"{league.api_code}.json"


def _get(url: str, token: str, params: dict) -> dict:
    headers = {"X-Auth-Token": token, "User-Agent": USER_AGENT}
    for attempt in range(3):
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:  # too many requests: wait for the counter to reset
            wait = int(resp.headers.get("X-RequestCounter-Reset", "60")) + 1
            print(f"  rate limit reached, waiting {wait}s ...")
            time.sleep(wait)
            continue
        if resp.status_code in (400, 401, 403, 404):
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            raise ApiError(f"HTTP {resp.status_code}: {message}")
        resp.raise_for_status()
        return resp.json()
    raise ApiError("Still rate-limited after 3 attempts")


def download_matches(
    league: League, season: int, token: str, force: bool = False
) -> bool:
    """Save the season's matches as JSON. Returns True if a network call was made."""
    path = api_path(league, season)
    is_current = season == current_season_start_year()
    if path.exists() and not force and not is_current:
        return False
    url = f"{API_BASE_URL}/competitions/{league.api_code}/matches"
    data = _get(url, token, {"season": season})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return True


def download_all(seasons: list[int]) -> list[str]:
    """Download every league/season. Returns a list of problems (empty = all fine)."""
    token = get_api_token()
    problems = []
    called_before = False
    for season in seasons:
        for league in API_LEAGUES:
            if called_before:
                time.sleep(SECONDS_BETWEEN_CALLS)
            try:
                called_before = download_matches(league, season, token)
                print(f"  {league.key} {season}: ok")
            except ApiError as exc:
                called_before = True
                problems.append(f"{league.key} {season}: {exc}")
                print(f"  {league.key} {season}: FAILED {exc}")
    return problems


def _main_referee(match: dict) -> dict | None:
    for ref in match.get("referees") or []:
        if ref.get("type") == "REFEREE":
            return ref
    return None


def parse_matches(data: dict, league: League, season: int) -> pd.DataFrame:
    """Turn the API's JSON into a table with one row per match."""
    tz = ZoneInfo(league.timezone)
    rows = []
    for m in data.get("matches", []):
        utc = pd.Timestamp(m["utcDate"])
        ref = _main_referee(m)
        rows.append(
            {
                "league": league.key,
                "season": season,
                "api_match_id": m["id"],
                "utc_kickoff": utc.isoformat(),
                "date": utc.tz_convert(tz).date(),
                "status": m.get("status"),
                "matchday": m.get("matchday"),
                "api_home": (m.get("homeTeam") or {}).get("name"),
                "api_away": (m.get("awayTeam") or {}).get("name"),
                "referee_id": ref.get("id") if ref else None,
                "referee": ref.get("name") if ref else None,
                "referee_nationality": ref.get("nationality") if ref else None,
            }
        )
    return pd.DataFrame(rows)


def load_all_api_matches(
    seasons: list[int], base_dir: Path = RAW_API_DIR
) -> pd.DataFrame:
    frames = []
    for season in seasons:
        for league in API_LEAGUES:
            path = api_path(league, season, base_dir)
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                frames.append(parse_matches(data, league, season))
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["referee_id"] = df["referee_id"].astype("Int64")
    return df
