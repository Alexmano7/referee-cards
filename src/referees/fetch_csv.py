"""Download and read the football-data.co.uk CSV files (cards per match)."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

from referees.config import (
    CSV_BASE_URL,
    LEAGUES,
    RAW_CSV_DIR,
    USER_AGENT,
    League,
    csv_season_code,
    current_season_start_year,
)

CARD_COLUMNS = ["HY", "AY", "HR", "AR"]


def csv_path(league: League, season: int, base_dir: Path = RAW_CSV_DIR) -> Path:
    return base_dir / csv_season_code(season) / f"{league.csv_code}.csv"


def download_csv(league: League, season: int, force: bool = False) -> Path:
    """Save one league/season file. Finished seasons are only downloaded once."""
    path = csv_path(league, season)
    is_current = season == current_season_start_year()
    if path.exists() and not force and not is_current:
        return path
    url = f"{CSV_BASE_URL}/{csv_season_code(season)}/{league.csv_code}.csv"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    if not resp.content.strip():
        raise RuntimeError(f"Empty file from {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(resp.content)
    tmp.replace(path)  # only replace the old file once the new one is fully written
    return path


def download_all(seasons: list[int]) -> list[Path]:
    paths = []
    for season in seasons:
        for league in LEAGUES:
            paths.append(download_csv(league, season))
    return paths


def _decode(raw: bytes) -> str:
    # Files start with a byte-order mark; a few older files are not UTF-8.
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def read_csv_matches(path: Path, league: League, season: int) -> pd.DataFrame:
    """Return one row per played match with tidy column names."""
    df = pd.read_csv(io.StringIO(_decode(path.read_bytes())))
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "Date"])  # blank trailing lines
    missing = [c for c in CARD_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing card columns {missing}")
    out = pd.DataFrame(
        {
            "league": league.key,
            "season": season,
            "date": pd.to_datetime(df["Date"], dayfirst=True, format="mixed").dt.date,
            # UK time, as football-data.co.uk publishes it (used for CSV-only leagues)
            "time": df["Time"].astype(str).str.strip()
            if "Time" in df.columns
            else None,
            "csv_home": df["HomeTeam"].str.strip(),
            "csv_away": df["AwayTeam"].str.strip(),
            # "Int64" allows a blank (unknown) value, e.g. an abandoned match
            "home_yellows": df["HY"].astype("Int64"),
            "away_yellows": df["AY"].astype("Int64"),
            "home_reds": df["HR"].astype("Int64"),
            "away_reds": df["AR"].astype("Int64"),
            # Fouls committed by each side (blank in a few rows, e.g. abandoned matches)
            "home_fouls": df["HF"].astype("Int64") if "HF" in df.columns else pd.NA,
            "away_fouls": df["AF"].astype("Int64") if "AF" in df.columns else pd.NA,
            "csv_referee": df["Referee"].str.strip()
            if "Referee" in df.columns
            else None,
        }
    )
    return out.reset_index(drop=True)


def load_all_csv_matches(
    seasons: list[int], base_dir: Path = RAW_CSV_DIR
) -> pd.DataFrame:
    frames = []
    for season in seasons:
        for league in LEAGUES:
            path = csv_path(league, season, base_dir)
            if path.exists():
                frames.append(read_csv_matches(path, league, season))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# --- the coming week's fixtures (used for leagues that football-data.org's free plan lacks) ---


def download_fixtures() -> Path:
    """football-data.co.uk/fixtures.csv: next week's fixtures for all its leagues."""
    from referees.config import FIXTURES_CSV_URL

    path = RAW_CSV_DIR / "fixtures.csv"
    resp = requests.get(
        FIXTURES_CSV_URL, headers={"User-Agent": USER_AGENT}, timeout=30
    )
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(resp.content)
    tmp.replace(path)
    return path


def read_fixtures(leagues: list[League], path: Path | None = None) -> pd.DataFrame:
    """Upcoming fixtures for the given leagues from fixtures.csv (empty if not downloaded)."""
    path = path or RAW_CSV_DIR / "fixtures.csv"
    cols = ["league", "date", "time", "csv_home", "csv_away"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(io.StringIO(_decode(path.read_bytes())))
    by_code = {lg.csv_code: lg.key for lg in leagues}
    df = df[df["Div"].isin(by_code)].dropna(subset=["HomeTeam", "AwayTeam", "Date"])
    return pd.DataFrame(
        {
            "league": df["Div"].map(by_code),
            "date": pd.to_datetime(df["Date"], dayfirst=True, format="mixed").dt.date,
            "time": df["Time"].astype(str).str.strip(),
            "csv_home": df["HomeTeam"].str.strip(),
            "csv_away": df["AwayTeam"].str.strip(),
        }
    ).reset_index(drop=True)
