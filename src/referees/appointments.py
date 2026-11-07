"""Referee appointments found by a web lookup, for upcoming fixtures.

football-data.org's free tier names a referee only after the match. Leagues announce
appointments a few days before, so Claude looks them up on request and writes them to
config/referee_appointments.csv, one row per fixture, with the page it came from.

Columns: league, date, home, away, referee, source_url, found_at
- league/date/home/away identify the fixture exactly as football-data.org names it
  (`uv run referees upcoming` prints them).
- The API's own referee always wins when it has one.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from referees.config import PROJECT_ROOT

APPOINTMENTS_FILE = PROJECT_ROOT / "config" / "referee_appointments.csv"
COLUMNS = ["league", "date", "home", "away", "referee", "source_url", "found_at"]
API_SOURCE = "football-data.org"


def load_appointments(path: Path = APPOINTMENTS_FILE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing columns {missing}")
    return df[COLUMNS]


def source_label(url: str | None) -> str:
    """'https://datencenter.dfb.de/x' -> 'datencenter.dfb.de'."""
    if not url:
        return ""
    host = urlparse(url).netloc or url
    return host.removeprefix("www.")


def _tokens(name: str) -> list[str]:
    text = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    )
    for ch in ".-'’,":
        text = text.replace(ch, " ")
    return text.split()


def match_referee(name: str, candidates: list[tuple[int, str]]) -> int | None:
    """Find the one known referee this announced name refers to, or None.

    Every word of the announced name must appear in the known full name. A single letter
    counts as an initial, and the first word may be a short form of 3+ letters ("Rob" for
    "Robert"). So "S. Jablonski" and "Sven Jablonski" both match "Sven Jablonski", "Rob
    Jones" matches "Robert Jones", and the Spanish surname-only "Hernández Hernández"
    matches "Alejandro Hernández Hernández". Two possible matches count as none: never guess.
    """
    wanted = _tokens(name)
    if not wanted:
        return None

    def word_ok(i: int, t: str, have: list[str]) -> bool:
        if t in have:
            return True
        if len(t) == 1 or (i == 0 and len(wanted) > 1 and len(t) >= 3):
            return any(h.startswith(t) for h in have)
        return False

    hits = set()
    for ref_id, full in candidates:
        have = _tokens(full)
        if all(word_ok(i, t, have) for i, t in enumerate(wanted)):
            hits.add(ref_id)
    if not hits and len(wanted) >= 3:
        # Fallback for full middle names (RFEF writes "Francisco José Hernández" for
        # "Francisco Hernández Maeso"): first name and last announced word must match,
        # middle words may be missing. Still only when exactly one referee fits.
        for ref_id, full in candidates:
            have = _tokens(full)
            if word_ok(0, wanted[0], have) and wanted[-1] in have:
                hits.add(ref_id)
    return hits.pop() if len(hits) == 1 else None


def apply_appointments(
    future: pd.DataFrame,
    appointments: pd.DataFrame,
    known: pd.DataFrame,
    today: str | None = None,
) -> tuple[pd.DataFrame, list[str], int]:
    """Fill in referees for upcoming fixtures from the appointments file.

    `known` has columns referee_id, referee, league (every referee in the history).
    Returns (fixtures with referee columns filled, problems to report, rows used).
    """
    out = future.copy()
    out["referee_source"] = [
        API_SOURCE if isinstance(r, str) else None for r in out["referee"]
    ]
    out["source_url"] = None
    problems: list[str] = []
    used = 0
    if appointments.empty:
        return out, problems, used

    index = {
        (r.league, str(r.date), r.api_home, r.api_away): i
        for i, r in zip(out.index, out.itertuples())
    }
    for a in appointments.itertuples():
        if today and a.date < today:
            continue  # the match has been played: the row is history, not a problem
        label = f"{a.league} {a.date} {a.home} v {a.away}"
        i = index.get((a.league, a.date, a.home, a.away))
        if i is None and today and a.date == today:
            continue  # today's match has already kicked off, so it is no longer upcoming
        if i is None:
            problems.append(
                f"{label}: no upcoming fixture with these exact names and date"
            )
            continue
        if isinstance(out.at[i, "referee"], str):
            continue  # the API has its own referee now; it wins
        in_league = known[known["league"] == a.league]
        cands = list(dict.fromkeys(zip(in_league["referee_id"], in_league["referee"])))
        ref_id = match_referee(a.referee, cands)
        if (
            ref_id is None
        ):  # maybe a referee who has only worked in another league so far
            ref_id = match_referee(
                a.referee,
                list(dict.fromkeys(zip(known["referee_id"], known["referee"]))),
            )
        if ref_id is None:
            problems.append(
                f"{label}: '{a.referee}' does not match exactly one known referee"
            )
            out.at[i, "referee"] = a.referee
        else:
            out.at[i, "referee_id"] = ref_id
            out.at[i, "referee"] = known.loc[
                known["referee_id"] == ref_id, "referee"
            ].iloc[0]
        out.at[i, "referee_source"] = source_label(a.source_url)
        out.at[i, "source_url"] = a.source_url or None
        used += 1
    return out, problems, used
