"""API-Football (api-sports.io) client and a probe of what the user's free plan offers.

Free plan: 100 requests per day. The key lives in .env as API_FOOTBALL_KEY (never printed).
`uv run referees api-football-probe` makes about 8 requests and reports:
- the plan and today's remaining requests
- which seasons and data (events, statistics, odds) each league allows
- whether upcoming fixtures already name a referee
- which bet types exist, and whether any are card markets
"""

from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

from referees.config import PROJECT_ROOT, USER_AGENT

BASE_URL = "https://v3.football.api-sports.io"
LEAGUE_IDS = {"PL": 39, "PD": 140, "BL1": 78, "FL1": 61}
CARD_WORDS = ("card", "booking")


def get_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("API_FOOTBALL_KEY", "").strip()
    if not key:
        raise SystemExit(
            "No API-Football key found. Add a line API_FOOTBALL_KEY=<your key> to .env "
            "(register free at dashboard.api-football.com)."
        )
    return key


def get(path: str, key: str, **params) -> dict:
    resp = requests.get(
        f"{BASE_URL}/{path}",
        headers={"x-apisports-key": key, "User-Agent": USER_AGENT},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors")
    if errors:  # API-Football reports plan limits as errors inside a 200 response
        raise RuntimeError(f"{path}: {errors}")
    return data


def probe(season: int) -> list[str]:
    key = get_key()
    lines = []
    status = get("status", key)["response"]
    sub, req = status.get("subscription", {}), status.get("requests", {})
    lines.append(
        f"Plan: {sub.get('plan')} (active: {sub.get('active')}); requests today {req.get('current')}/{req.get('limit_day')}"
    )

    for lg, lid in LEAGUE_IDS.items():
        try:
            info = get("leagues", key, id=lid)["response"][0]
        except Exception as exc:  # noqa: BLE001 - report and continue
            lines.append(f"{lg}: leagues lookup failed: {exc}")
            continue
        seasons = info.get("seasons", [])
        this = next((s for s in seasons if s.get("year") == season), None)
        cov = (this or {}).get("coverage", {})
        fx = cov.get("fixtures", {})
        lines.append(
            f"{lg}: season {season} listed={this is not None}; events={fx.get('events')}, "
            f"statistics={fx.get('statistics_fixtures')}, lineups={fx.get('lineups')}, odds={cov.get('odds')}"
        )

    try:
        nxt = get("fixtures", key, league=LEAGUE_IDS["BL1"], season=season, next=5)[
            "response"
        ]
        refs = [
            f"{f['teams']['home']['name']} v {f['teams']['away']['name']}: {f['fixture'].get('referee')}"
            for f in nxt
        ]
        lines.append(
            "Next Bundesliga fixtures and referee field: "
            + ("; ".join(refs) or "none returned")
        )
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Upcoming fixtures: not available on this plan ({exc})")

    try:
        bets = get("odds/bets", key)["response"]
        cards = [
            f"{b['id']}: {b['name']}"
            for b in bets
            if any(w in b["name"].lower() for w in CARD_WORDS)
        ]
        lines.append(
            f"Bet types: {len(bets)} in total; card markets: "
            + (", ".join(cards) or "none")
        )
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Bet types: not available ({exc})")
    return lines
