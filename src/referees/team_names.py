"""Team-name mapping between football-data.org (API) and football-data.co.uk (CSV).

The mapping file has the columns: league, api_name, csv_name, checked
- 'checked' is 'yes' once a human has confirmed the row, 'no' for automatic suggestions.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from referees.config import MAPPING_FILE

MAPPING_COLUMNS = ["league", "api_name", "csv_name", "checked"]

# Words that only add noise when comparing names ("Manchester United FC" vs "Man United").
_NOISE = {
    "fc",
    "cf",
    "afc",
    "sc",
    "ac",
    "as",
    "sv",
    "ssv",
    "vfb",
    "vfl",
    "tsg",
    "rc",
    "ogc",
    "ud",
    "cd",
    "rcd",
    "sd",
    "de",
    "club",
    "1",
    "osc",
    "stade",
    "hsc",
    "fsv",
    "and",
}


def normalise(name: str) -> str:
    text = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    )
    for ch in ".-'&":
        text = text.replace(ch, " ")
    return " ".join(w for w in text.split() if w not in _NOISE)


def similarity(a: str, b: str) -> float:
    na, nb = normalise(a), normalise(b)
    score = SequenceMatcher(None, na, nb).ratio()
    if (
        na and nb and (na in nb or nb in na)
    ):  # e.g. "brighton" inside "brighton hove albion"
        score = max(score, 0.9)
    return score


def load_mapping(path: Path = MAPPING_FILE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=MAPPING_COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return df[MAPPING_COLUMNS]


def save_mapping(df: pd.DataFrame, path: Path = MAPPING_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df[MAPPING_COLUMNS].sort_values(["league", "api_name"]).reset_index(drop=True)
    df.to_csv(path, index=False, lineterminator="\n")


def apply_mapping(api: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """Add csv_home / csv_away columns (empty where a name has no mapping yet)."""
    lookup = {(r.league, r.api_name): r.csv_name for r in mapping.itertuples()}
    out = api.copy()
    out["csv_home"] = [
        lookup.get((lg, t)) for lg, t in zip(out["league"], out["api_home"])
    ]
    out["csv_away"] = [
        lookup.get((lg, t)) for lg, t in zip(out["league"], out["api_away"])
    ]
    return out


def unmapped_teams(api: pd.DataFrame, mapping: pd.DataFrame) -> list[tuple[str, str]]:
    known = {(r.league, r.api_name) for r in mapping.itertuples()}
    teams = set(zip(api["league"], api["api_home"])) | set(
        zip(api["league"], api["api_away"])
    )
    return sorted(t for t in teams if t not in known and isinstance(t[1], str))


def suggest_mapping(api: pd.DataFrame, csv: pd.DataFrame) -> pd.DataFrame:
    """Guess api_name -> csv_name by looking at which teams play on the same day.

    For every finished API match we look at the CSV matches of the same league on the
    same date, pick the one whose team names look most alike, and count a 'vote' for
    each pairing. A team that plays ~30 times a season gets ~30 votes, so the winner is
    very reliable even when the names look nothing alike.
    """
    finished = api[api["status"] == "FINISHED"]
    csv_by_day = defaultdict(list)
    for r in csv.itertuples():
        csv_by_day[(r.league, r.date)].append((r.csv_home, r.csv_away))

    votes: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for r in finished.itertuples():
        candidates = csv_by_day.get((r.league, r.date), [])
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda c: similarity(r.api_home, c[0]) + similarity(r.api_away, c[1]),
        )
        votes[(r.league, r.api_home)][best[0]] += 1
        votes[(r.league, r.api_away)][best[1]] += 1

    rows = []
    for (league, api_name), counter in votes.items():
        csv_name, top = counter.most_common(1)[0]
        total = sum(counter.values())
        rows.append(
            {
                "league": league,
                "api_name": api_name,
                "csv_name": csv_name,
                "votes": top,
                "share": round(top / total, 2),
            }
        )
    return pd.DataFrame(
        rows, columns=["league", "api_name", "csv_name", "votes", "share"]
    )


def merge_suggestions(
    existing: pd.DataFrame, suggestions: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add suggestions for teams not yet in the file. Existing rows are never changed.

    Returns (new mapping, the rows that were added).
    """
    known = {(r.league, r.api_name) for r in existing.itertuples()}
    new = suggestions[
        [
            (lg, n) not in known
            for lg, n in zip(suggestions["league"], suggestions["api_name"])
        ]
    ]
    added = new.assign(checked="no")[MAPPING_COLUMNS]
    merged = pd.concat([existing, added], ignore_index=True)
    return merged, new
