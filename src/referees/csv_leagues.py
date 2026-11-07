"""Matches and fixtures for leagues that football-data.org's free plan lacks (Greece, Belgium,
Scotland), shaped like API matches so the rest of the pipeline (join, stats, model, picks)
treats every league the same.

- Played matches come from the football-data.co.uk card file. Team names are the CSV names
  (an identity mapping is added for these leagues, see `identity_mapping`).
- Past referees: Scotland's card file has a Referee column; Greece and Belgium use the
  referee.observer history (referee_observer.py), linked to CSV matches by league + date +
  teams through config/ro_team_mapping.csv (suggested automatically by same-day votes).
- Upcoming fixtures come from football-data.co.uk/fixtures.csv (the coming week).
- Referee ids: football-data.org ids don't exist here, so a stable id is made from the name.
"""

from __future__ import annotations

import unicodedata
import zlib
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd

from referees.config import CSV_LEAGUES, LEAGUES_BY_KEY, PROJECT_ROOT
from referees.team_names import similarity

RO_MAPPING_FILE = PROJECT_ROOT / "config" / "ro_team_mapping.csv"
RO_MAPPING_COLUMNS = ["league", "ro_name", "csv_name", "checked"]
API_COLUMNS = [
    "league",
    "season",
    "api_match_id",
    "utc_kickoff",
    "date",
    "status",
    "matchday",
    "api_home",
    "api_away",
    "referee_id",
    "referee",
    "referee_nationality",
]


def stable_referee_id(name: str) -> int:
    """A stable id from a referee's name (accents and case ignored), above API ids."""
    norm = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode()
        .lower()
        .strip()
    )
    return 2_000_000_000 + zlib.crc32(norm.encode()) % 100_000_000


def _match_id(league: str, day, home: str, away: str) -> int:
    return (
        1_000_000_000
        + zlib.crc32(f"{league}|{day}|{home}|{away}".encode()) % 900_000_000
    )


def _utc(day, hhmm) -> str:
    """football-data.co.uk times are UK times; turn date + time into UTC ISO text."""
    text = hhmm if isinstance(hhmm, str) and ":" in hhmm else "15:00"
    local = pd.Timestamp(f"{day} {text}").tz_localize(
        "Europe/London", ambiguous=True, nonexistent="shift_forward"
    )
    return local.tz_convert("UTC").isoformat()


# --- team names: referee.observer -> CSV ------------------------------------------------


def load_ro_mapping(path: Path = RO_MAPPING_FILE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=RO_MAPPING_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)[RO_MAPPING_COLUMNS]


def suggest_ro_mapping(
    history: pd.DataFrame, csv: pd.DataFrame, existing: pd.DataFrame
) -> pd.DataFrame:
    """Vote, like team_names.suggest_mapping: a referee.observer match on a date is paired with
    the CSV match of that league on the same date (or a day either side) whose names look
    most alike. Adds only names not yet in the file; never changes existing rows."""
    by_day = defaultdict(list)
    for r in csv.itertuples():
        by_day[(r.league, r.date)].append((r.csv_home, r.csv_away))
    votes: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for r in history.itertuples():
        cands = [
            c
            for d in (0, -1, 1)
            for c in by_day.get((r.league, r.date + timedelta(days=d)), [])
        ]
        if not cands:
            continue
        best = max(
            cands,
            key=lambda c: similarity(r.ro_home, c[0]) + similarity(r.ro_away, c[1]),
        )
        votes[(r.league, r.ro_home)][best[0]] += 1
        votes[(r.league, r.ro_away)][best[1]] += 1
    known = {(r.league, r.ro_name) for r in existing.itertuples()}
    rows = [
        {
            "league": lg,
            "ro_name": name,
            "csv_name": c.most_common(1)[0][0],
            "checked": "no",
        }
        for (lg, name), c in votes.items()
        if (lg, name) not in known
    ]
    merged = pd.concat(
        [existing, pd.DataFrame(rows, columns=RO_MAPPING_COLUMNS)], ignore_index=True
    )
    return merged.sort_values(["league", "ro_name"]).reset_index(drop=True)


def ro_referees_for_csv(
    history: pd.DataFrame, csv: pd.DataFrame, mapping: pd.DataFrame
) -> dict:
    """{(league, date, csv_home, csv_away): referee name} from the referee.observer history."""
    lookup = {(r.league, r.ro_name): r.csv_name for r in mapping.itertuples()}
    played = {(r.league, r.date, r.csv_home, r.csv_away) for r in csv.itertuples()}
    out = {}
    for r in history.itertuples():
        home, away = (
            lookup.get((r.league, r.ro_home)),
            lookup.get((r.league, r.ro_away)),
        )
        if not home or not away:
            continue
        for d in (0, -1, 1):
            key = (r.league, r.date + timedelta(days=d), home, away)
            if key in played:
                out[key] = r.referee
                break
    return out


# --- matches and fixtures shaped like API rows -----------------------------------------


def csv_matches(csv: pd.DataFrame, ro_referees: dict) -> pd.DataFrame:
    keys = {lg.key for lg in CSV_LEAGUES}
    rows = []
    for r in csv[csv["league"].isin(keys)].itertuples():
        lg = LEAGUES_BY_KEY[r.league]
        if lg.referee_source == "csv":
            ref = (
                r.csv_referee
                if isinstance(r.csv_referee, str) and r.csv_referee
                else None
            )
        else:
            ref = ro_referees.get((r.league, r.date, r.csv_home, r.csv_away))
        rows.append(
            {
                "league": r.league,
                "season": r.season,
                "api_match_id": _match_id(r.league, r.date, r.csv_home, r.csv_away),
                "utc_kickoff": _utc(r.date, getattr(r, "time", None)),
                "date": r.date,
                "status": "FINISHED",
                "matchday": None,
                "api_home": r.csv_home,
                "api_away": r.csv_away,
                "referee_id": stable_referee_id(ref) if ref else None,
                "referee": ref,
                "referee_nationality": None,
            }
        )
    return pd.DataFrame(rows, columns=API_COLUMNS)


def csv_fixtures(fixtures: pd.DataFrame, season: int) -> pd.DataFrame:
    rows = [
        {
            "league": r.league,
            "season": season,
            "api_match_id": _match_id(r.league, r.date, r.csv_home, r.csv_away),
            "utc_kickoff": _utc(r.date, r.time),
            "date": r.date,
            "status": "TIMED",
            "matchday": None,
            "api_home": r.csv_home,
            "api_away": r.csv_away,
            "referee_id": None,
            "referee": None,
            "referee_nationality": None,
        }
        for r in fixtures.itertuples()
    ]
    return pd.DataFrame(rows, columns=API_COLUMNS)


def identity_mapping(csv: pd.DataFrame) -> pd.DataFrame:
    """For CSV leagues the 'API' names are the CSV names: map each team to itself."""
    keys = {lg.key for lg in CSV_LEAGUES}
    sub = csv[csv["league"].isin(keys)]
    teams = set(zip(sub["league"], sub["csv_home"])) | set(
        zip(sub["league"], sub["csv_away"])
    )
    return pd.DataFrame(
        [
            {"league": lg, "api_name": t, "csv_name": t, "checked": "yes"}
            for lg, t in sorted(teams)
        ],
        columns=["league", "api_name", "csv_name", "checked"],
    )
