"""Project settings: leagues, seasons, file locations and the API token."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# src/referees/config.py -> parents[2] is the project folder
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_API_DIR = DATA_DIR / "raw" / "api"
RAW_CSV_DIR = DATA_DIR / "raw" / "csv"
MAPPING_FILE = PROJECT_ROOT / "config" / "team_name_mapping.csv"
MATCHES_FILE = DATA_DIR / "matches.csv"
UNMATCHED_FILE = DATA_DIR / "unmatched.csv"
REFEREES_JSON = DATA_DIR / "referees.json"

API_BASE_URL = "https://api.football-data.org/v4"
CSV_BASE_URL = "https://football-data.co.uk/mmz4281"
USER_AGENT = "referees-card-tracker/0.1"

# --- Bookmaker settlement --------------------------------------------------------------
# How many "cards" a red card counts as in card markets. The user bets with Stoiximan/Betano;
# their exact rule is not confirmed yet, so the data's own count (1) is the default.
# Set to 2 if the bookmaker counts a red as two cards. Booking points stay 10 / 25.
RED_CARD_COUNTS_AS = 1

# How many seasons to keep: the current one plus the two before it.
SEASONS_TO_FETCH = 3


@dataclass(frozen=True)
class League:
    key: str  # short id used everywhere in our own files (same as the API code)
    name: str
    api_code: str  # football-data.org competition code
    csv_code: str  # football-data.co.uk file name
    timezone: str  # used to turn the API's UTC kick-off time into a local date


LEAGUES: list[League] = [
    League("PL", "Premier League", "PL", "E0", "Europe/London"),
    League("PD", "La Liga", "PD", "SP1", "Europe/Madrid"),
    League("BL1", "Bundesliga", "BL1", "D1", "Europe/Berlin"),
    League("FL1", "Ligue 1", "FL1", "F1", "Europe/Paris"),
]
LEAGUES_BY_KEY = {lg.key: lg for lg in LEAGUES}


def current_season_start_year(today: date | None = None) -> int:
    """European seasons start in summer: Nov 2026 -> 2026 (the 2026/27 season)."""
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def seasons_to_fetch(
    today: date | None = None, count: int = SEASONS_TO_FETCH
) -> list[int]:
    """Start years, oldest first, e.g. [2024, 2025, 2026]."""
    current = current_season_start_year(today)
    return list(range(current - count + 1, current + 1))


def season_label(start_year: int) -> str:
    """2026 -> '2026/27' (human readable)."""
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def csv_season_code(start_year: int) -> str:
    """2026 -> '2627' (the folder name football-data.co.uk uses)."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def get_api_token() -> str:
    """Read the token from the environment or the .env file. Never print it."""
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("FOOTBALL_DATA_API_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "No football-data.org token found.\n"
            "Copy .env.example to .env and put your token after FOOTBALL_DATA_API_TOKEN="
        )
    return token
