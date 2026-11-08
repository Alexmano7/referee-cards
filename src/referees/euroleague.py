"""EuroLeague games, referees and player box scores from the official live data feed.

Sources (free, no key; tested 2026-11-08):
- Schedule (XML): https://api-live.euroleague.net/v1/schedules?seasonCode=E2026
  every game of a season with its game code, date, time, home/away team and `played`.
- Box score (JSON): https://live.euroleague.net/api/Boxscore?gamecode=N&seasoncode=E2026
  the crew ("JAVOR, DAMIR, ALIAGA, JORDI, GIOVANNETTI, GUIDO" = surname, first name pairs)
  and per player: minutes, free throws made/attempted, fouls committed ("FoulsCommited") and
  fouls drawn ("FoulsReceived").
- Game (XML): https://api-live.euroleague.net/v1/games?seasonCode=E2026&gameCode=N
  `<referees>` with each referee's name once the crew is announced (empty before that).

Never use euroleaguebasketball.net: it blocks automated requests. Requests are spaced
REQUEST_DELAY seconds apart. Finished games are saved under data/raw/euroleague/<season>/
and never downloaded twice.
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import requests

from referees.config import DATA_DIR, USER_AGENT

EL_SEASONS = ["E2024", "E2025", "E2026"]  # last three seasons, oldest first
RAW_EL_DIR = DATA_DIR / "raw" / "euroleague"
EL_JSON = DATA_DIR / "euroleague_referees.json"
SCHEDULE_URL = "https://api-live.euroleague.net/v1/schedules?seasonCode={season}"
BOXSCORE_URL = (
    "https://live.euroleague.net/api/Boxscore?gamecode={game}&seasoncode={season}"
)
GAME_URL = (
    "https://api-live.euroleague.net/v1/games?seasonCode={season}&gameCode={game}"
)
REQUEST_DELAY = 1.0
ROUND_NAMES = {
    "RS": "Regular season",
    "PI": "Play-in",
    "PO": "Playoffs",
    "FF": "Final Four",
}


class FeedBlocked(RuntimeError):
    """The feed answered 403 or 429: stop and report, never work around it."""


def season_label(code: str) -> str:
    """'E2025' -> '2025/26'."""
    year = int(code[1:])
    return f"{year}/{str(year + 1)[-2:]}"


def person_name(surname_first: str) -> str:
    """'JAVOR, DAMIR' -> 'Damir Javor' (feed names are upper case, surname first)."""
    parts = [p.strip() for p in surname_first.split(",")]
    if len(parts) >= 2 and parts[1]:
        surname, first = parts[0], " ".join(parts[1:])
        return f"{first} {surname}".title()
    return surname_first.strip().title()


def parse_referees(text: str | None) -> list[str]:
    """'JAVOR, DAMIR, ALIAGA, JORDI, GIOVANNETTI, GUIDO' -> ['Damir Javor', 'Jordi Aliaga', ...].

    The box score joins (surname, first name) pairs with commas. An odd number of parts
    means a name without a first name; then each part is taken as one name.
    """
    if not text or not text.strip():
        return []
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) % 2:
        return [p.title() for p in parts]
    return [person_name(f"{parts[i]}, {parts[i + 1]}") for i in range(0, len(parts), 2)]


def minutes(value) -> float:
    """'33:21' -> 33.35; anything else (DNP, blank) -> 0."""
    m = re.fullmatch(r"\s*(\d+):(\d{1,2})\s*", str(value or ""))
    return int(m.group(1)) + int(m.group(2)) / 60 if m else 0.0


def parse_schedule(xml_bytes: bytes, season: str) -> list[dict]:
    """One dict per game in a season's schedule."""
    root = ET.fromstring(xml_bytes)
    games = []
    for it in root.findall("item"):
        code = it.findtext("game")
        if not code or not code.strip().isdigit():
            continue
        games.append(
            {
                "season": season,
                "game": int(code),
                "round": (it.findtext("round") or "").strip(),
                "date": datetime.strptime(it.findtext("date").strip(), "%b %d, %Y")
                .date()
                .isoformat(),
                "time": (it.findtext("startime") or "").strip(),
                "home": (it.findtext("hometeam") or "").strip(),
                "home_code": (it.findtext("homecode") or "").strip(),
                "away": (it.findtext("awayteam") or "").strip(),
                "away_code": (it.findtext("awaycode") or "").strip(),
                "played": (it.findtext("played") or "").strip() == "true",
            }
        )
    return games


def parse_game_referees(xml_bytes: bytes) -> list[str]:
    """Referee names from a /v1/games answer (empty until the crew is announced)."""
    root = ET.fromstring(xml_bytes)
    refs = root.find("referees")
    if refs is None:
        return []
    return [
        person_name(r.get("name", "")) for r in refs.findall("referee") if r.get("name")
    ]


def parse_boxscore(box: dict, sched: dict) -> tuple[dict, list[dict]]:
    """A box score -> (one game row, one row per player who played).

    Home and away are taken from the schedule's team codes, matched to each team's
    `Team` code in the box score.
    """
    side_of = {sched["home_code"]: "home", sched["away_code"]: "away"}
    game = {
        k: sched[k]
        for k in (
            "season",
            "game",
            "round",
            "date",
            "time",
            "home",
            "home_code",
            "away",
            "away_code",
        )
    }
    game["referees"] = parse_referees(box.get("Referees"))
    players = []
    for team in box.get("Stats") or []:
        rows = team.get("PlayersStats") or []
        code = next((str(r.get("Team", "")).strip() for r in rows if r.get("Team")), "")
        side = side_of.get(code)
        if side is None:
            raise ValueError(
                f"team code {code!r} is neither {sched['home_code']} nor {sched['away_code']}"
            )
        tot = team.get("totr") or {}
        game[f"{side}_fouls"] = int(tot.get("FoulsCommited") or 0)
        game[f"{side}_ftm"] = int(tot.get("FreeThrowsMade") or 0)
        game[f"{side}_fta"] = int(tot.get("FreeThrowsAttempted") or 0)
        for r in rows:
            mins = minutes(r.get("Minutes"))
            if mins <= 0:
                continue
            players.append(
                {
                    "season": sched["season"],
                    "game": sched["game"],
                    "player_id": str(r.get("Player_ID", "")).strip(),
                    "player": person_name(str(r.get("Player", ""))),
                    "team": code,
                    "side": side,
                    "starter": bool(r.get("IsStarter")),
                    "minutes": round(mins, 2),
                    "ftm": int(r.get("FreeThrowsMade") or 0),
                    "fta": int(r.get("FreeThrowsAttempted") or 0),
                    "fouls_committed": int(r.get("FoulsCommited") or 0),
                    "fouls_drawn": int(r.get("FoulsReceived") or 0),
                }
            )
    for side in ("home", "away"):
        if f"{side}_fouls" not in game:
            raise ValueError(f"no {side} team in box score")
    return game, players


# --- Downloading -------------------------------------------------------------------------


RETRY_WAITS = (5, 20, 60)  # seconds to wait after a network error before trying again


def _get(session: requests.Session, url: str) -> requests.Response:
    """GET with the polite delay. Network blips (DNS, timeouts) are retried a few times;
    a block (403/429) is never retried."""
    for wait in (*RETRY_WAITS, None):
        try:
            resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            break
        except (requests.ConnectionError, requests.Timeout):
            if wait is None:
                raise
            time.sleep(wait)
    if resp.status_code in (403, 429):
        raise FeedBlocked(
            f"HTTP {resp.status_code} from {url}: stopped (not working around it)"
        )
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp


def fetch(seasons: list[str] | None = None, progress=print) -> dict:
    """Download schedules and every new finished game's box score. Returns counts."""
    seasons = seasons or EL_SEASONS
    session = requests.Session()
    counts = {"downloaded": 0, "cached": 0, "skipped": 0}
    for season in seasons:
        folder = RAW_EL_DIR / season
        folder.mkdir(parents=True, exist_ok=True)
        sched_file = folder / "schedule.xml"
        finished = season != seasons[-1] and sched_file.exists()
        if not finished:
            sched_file.write_bytes(
                _get(session, SCHEDULE_URL.format(season=season)).content
            )
        games = parse_schedule(sched_file.read_bytes(), season)
        played = [g for g in games if g["played"]]
        new = [g for g in played if not (folder / f"{g['game']}.json").exists()]
        progress(
            f"  {season_label(season)}: {len(played)} games played, {len(new)} to download"
        )
        for i, g in enumerate(new, start=1):
            box = _get(
                session, BOXSCORE_URL.format(game=g["game"], season=season)
            ).json()
            if box.get("Live"):
                counts["skipped"] += 1  # still in progress: try again next time
                continue
            (folder / f"{g['game']}.json").write_text(json.dumps(box), encoding="utf-8")
            counts["downloaded"] += 1
            if i % 50 == 0:
                progress(f"    {i} of {len(new)}")
        counts["cached"] += len(played) - len(new)
    return counts


def load(
    seasons: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Read the saved schedules and box scores -> (games, players, problems)."""
    seasons = seasons or EL_SEASONS
    games, players, problems = [], [], []
    for season in seasons:
        folder = RAW_EL_DIR / season
        sched_file = folder / "schedule.xml"
        if not sched_file.exists():
            problems.append(
                f"{season}: no schedule downloaded (run `referees el-fetch`)"
            )
            continue
        for g in parse_schedule(sched_file.read_bytes(), season):
            f = folder / f"{g['game']}.json"
            if not g["played"] or not f.exists():
                continue
            try:
                game, rows = parse_boxscore(
                    json.loads(f.read_text(encoding="utf-8")), g
                )
            except (ValueError, KeyError, TypeError) as e:
                problems.append(f"{season} game {g['game']}: {e}")
                continue
            if len(game["referees"]) != 3:
                problems.append(
                    f"{season} game {g['game']}: {len(game['referees'])} referees listed"
                )
            games.append(game)
            players.extend(rows)
    return pd.DataFrame(games), pd.DataFrame(players), problems


def upcoming(days: int = 7, today=None, progress=print) -> list[dict]:
    """Unplayed games of the current season in the next `days` days, with announced crews."""
    from datetime import date, timedelta

    today = today or date.today()
    session = requests.Session()
    season = EL_SEASONS[-1]
    sched = parse_schedule(
        _get(session, SCHEDULE_URL.format(season=season)).content, season
    )
    horizon = (today + timedelta(days=days)).isoformat()
    out = []
    for g in sched:
        if g["played"] or not (today.isoformat() <= g["date"] <= horizon):
            continue
        xml = _get(session, GAME_URL.format(season=season, game=g["game"])).content
        out.append({**g, "referees": parse_game_referees(xml)})
    return sorted(out, key=lambda g: (g["date"], g["time"]))
