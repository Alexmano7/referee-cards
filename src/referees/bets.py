"""Bet log: bets the user placed, settled automatically from the card data.

config/bets.csv columns:
  placed_at   date the bet was placed (YYYY-MM-DD)
  league, date, home, away   the fixture, with football-data.org team names
  market      match_cards | home_cards | away_cards | match_booking_points
  selection   over | under
  line        e.g. 4.5 (half lines only, so there are no pushes)
  odds        decimal odds taken
  stake       amount staked
  model_prob  the model's probability (0-100) for this selection when the bet was logged
  note        free text

A bet is settled once its match is in the joined data (cards known). Cards are counted with
RED_CARD_COUNTS_AS, booking points as 10 per yellow and 25 per red.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from referees.config import PROJECT_ROOT, RED_CARD_COUNTS_AS
from referees.stats import RED_POINTS, YELLOW_POINTS

BETS_FILE = PROJECT_ROOT / "config" / "bets.csv"
COLUMNS = [
    "placed_at",
    "league",
    "date",
    "home",
    "away",
    "market",
    "selection",
    "line",
    "odds",
    "stake",
    "model_prob",
    "note",
]
MARKETS = {"match_cards", "home_cards", "away_cards", "match_booking_points"}


def load_bets(path: Path = BETS_FILE) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing columns {missing}")
    return df[COLUMNS]


def _actual(market: str, m) -> float:
    hy, ay, hr, ar = (
        int(m.home_yellows),
        int(m.away_yellows),
        int(m.home_reds),
        int(m.away_reds),
    )
    if market == "home_cards":
        return hy + RED_CARD_COUNTS_AS * hr
    if market == "away_cards":
        return ay + RED_CARD_COUNTS_AS * ar
    if market == "match_booking_points":
        return YELLOW_POINTS * (hy + ay) + RED_POINTS * (hr + ar)
    return hy + ay + RED_CARD_COUNTS_AS * (hr + ar)


def settle(bets: pd.DataFrame, joined: pd.DataFrame) -> list[dict]:
    """One row per bet with status (pending / won / lost / invalid), actual value and profit."""
    played = {
        (r.league, str(r.date), r.api_home, r.api_away): r
        for r in joined.itertuples()
        if not pd.isna(r.home_yellows)
    }
    rows = []
    for i, b in enumerate(bets.itertuples()):
        odds, stake, line = float(b.odds), float(b.stake), float(b.line)
        row = {
            "key": f"{i}|{b.placed_at}|{b.home}|{b.market}|{b.selection}|{b.line}",
            "placed_at": b.placed_at,
            "league": b.league,
            "date": b.date,
            "match": f"{b.home} v {b.away}",
            "bet": f"{b.market.replace('_', ' ')} {b.selection} {b.line}",
            "odds": odds,
            "stake": stake,
            "model_prob": float(b.model_prob) if b.model_prob else None,
            "implied_prob": round(100 / odds, 1) if odds > 0 else None,
            "actual": None,
            "status": "pending",
            "profit": 0.0,
            "note": b.note,
        }
        if (
            b.market not in MARKETS
            or b.selection not in ("over", "under")
            or line == int(line)
        ):
            row["status"] = "invalid"
        else:
            m = played.get((b.league, b.date, b.home, b.away))
            if m is not None:
                actual = _actual(b.market, m)
                won = actual > line if b.selection == "over" else actual < line
                row["actual"] = actual
                row["status"] = "won" if won else "lost"
                row["profit"] = round(stake * (odds - 1), 2) if won else -stake
        rows.append(row)
    return rows
