"""Flat tables (lists of row dicts) for the claude.ai dashboard.

The dashboard reads tables, not nested JSON, and shows every number exactly as it is
in a table. So everything the page shows is computed here, for every combination of
league scope ("ALL" or a league key) and season scope ("All" or e.g. "2026/27").
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from referees.config import LEAGUES, LEAGUES_BY_KEY, season_label
from referees.stats import (
    CARD_COUNT_COLUMNS,
    _adjusted,
    _basic,
    add_match_metrics,
    current_season_start_year,
    league_averages,
)

ALL_LEAGUES = "ALL"
ALL_SEASONS = "All seasons"
ATHENS = ZoneInfo("Europe/Athens")
DASHBOARD_DIR_NAME = "dashboard"


def load_matches_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["referee_id"] = df["referee_id"].astype("Int64")
    for c in CARD_COUNT_COLUMNS:
        df[c] = df[c].astype("Int64")
    return df


def _scopes(df: pd.DataFrame):
    """Yield (league scope, season scope, rows) for every combination with data."""
    leagues = [ALL_LEAGUES] + sorted(df["league"].unique())
    for lg in leagues:
        g = df if lg == ALL_LEAGUES else df[df["league"] == lg]
        if g.empty:
            continue
        yield lg, ALL_SEASONS, g
        for season, sg in g.groupby("season"):
            yield lg, season_label(int(season)), sg


def _fixture_text(fx: dict | None) -> str:
    if not fx:
        return ""
    when = pd.Timestamp(fx["utc_kickoff"]).tz_convert(ATHENS).strftime("%a %d %b")
    return f"{when}: {fx['home']} v {fx['away']}"


def build_tables(
    matches: pd.DataFrame, report: dict, today: date | None = None
) -> dict[str, list[dict]]:
    today = today or date.today()
    played = matches[~matches[CARD_COUNT_COLUMNS].isna().any(axis=1)].astype(
        dict.fromkeys(CARD_COUNT_COLUMNS, int)
    )
    df = add_match_metrics(played, current_season_start_year(today))
    league_avg = league_averages(df)
    next_by_ref = {r["id"]: r["next_fixture"] for r in report["referees"]}

    # --- referee_stats --------------------------------------------------------------
    stats_rows = []
    with_ref = df[df["referee_id"].notna()]
    for ref_id, g in with_ref.groupby("referee_id"):
        ref_id = int(ref_id)
        last = g.sort_values("date").iloc[-1]
        leagues_text = ", ".join(
            LEAGUES_BY_KEY[k].name for k in sorted(g["league"].unique())
        )
        for lg, season, sg in _scopes(g):
            base = _basic(sg)
            adj = (
                _adjusted(g if lg == ALL_LEAGUES else g[g["league"] == lg], league_avg)
                if season == ALL_SEASONS
                else {}
            )
            stats_rows.append(
                {
                    "key": f"{ref_id}|{lg}|{season}",
                    "referee_id": ref_id,
                    "referee": last["referee"],
                    "leagues": leagues_text,
                    "league": lg,
                    "season": season,
                    "matches": base["matches"],
                    "yellows": base["yellows"],
                    "reds": base["reds"],
                    "cards": base["cards"],
                    "yellows_per_match": base["yellows_per_match"],
                    "reds_per_match": round(base["reds_per_match"], 2),
                    "cards_per_match": base["cards_per_match"],
                    "booking_points_per_match": base["booking_points_per_match"],
                    "adjusted_cards_per_match": adj.get("adjusted_cards_per_match"),
                    "adjusted_booking_points_per_match": adj.get(
                        "adjusted_booking_points_per_match"
                    ),
                    "pct_over_3_5": base["pct_over_3_5"],
                    "pct_over_4_5": base["pct_over_4_5"],
                    "pct_over_5_5": base["pct_over_5_5"],
                    "next_fixture": _fixture_text(next_by_ref.get(ref_id)),
                }
            )

    # --- league_summary (headline figures for each tab and season) ------------------
    summary_rows = []
    for lg, season, sg in _scopes(df):
        base = _basic(sg)
        summary_rows.append(
            {
                "key": f"{lg}|{season}",
                "league": lg,
                "league_name": "All leagues"
                if lg == ALL_LEAGUES
                else LEAGUES_BY_KEY[lg].name,
                "season": season,
                "matches": base["matches"],
                "referees": int(sg["referee_id"].nunique()),
                "cards_per_match": base["cards_per_match"],
                "booking_points_per_match": base["booking_points_per_match"],
                "pct_over_4_5": base["pct_over_4_5"],
            }
        )

    # --- league_bars (cards per match by league, per season scope) ------------------
    bars = [
        {
            "key": r["key"],
            "league": r["league"],
            "league_name": r["league_name"],
            "season": r["season"],
            "cards_per_match": r["cards_per_match"],
        }
        for r in summary_rows
        if r["league"] != ALL_LEAGUES
    ]

    # --- fixtures ---------------------------------------------------------------------
    fixture_rows = []
    for fx in report["upcoming_fixtures"]:
        ko = pd.Timestamp(fx["utc_kickoff"]).tz_convert(ATHENS)
        fixture_rows.append(
            {
                "key": f"{fx['league']}|{fx['date']}|{fx['home']}",
                "date": fx["date"],
                "kickoff": ko.strftime("%a %d %b, %H:%M"),
                "league": fx["league"],
                "home": fx["home"],
                "away": fx["away"],
                "referee": fx["referee"] or "TBD",
                "projected_cards": fx["projected_cards"],
                "projected_booking_points": fx["projected_booking_points"],
                "basis": fx["basis"],
            }
        )

    # --- data_checks ------------------------------------------------------------------
    dq = report["data_quality"]
    missing = dq["finished_api_matches_without_referee"]
    checks = [
        {"check": "Matches joined across both sources", "value": dq["joined_matches"]},
        {"check": "Matches that failed to join", "value": dq["unmatched_rows"]},
        {
            "check": "Matches without card data (left out)",
            "value": len(dq["matches_without_card_data"]),
        },
        {
            "check": "Premier League matches where the two sources name different referees",
            "value": len(dq.get("referee_disagreements", [])),
        },
    ] + [
        {
            "check": f"{LEAGUES_BY_KEY[k].name} matches with no referee name",
            "value": int(missing.get(k, 0)),
        }
        for k in [lg.key for lg in LEAGUES]
    ]

    return {
        "referee_stats": stats_rows,
        "league_summary": summary_rows,
        "league_bars": bars,
        "fixtures": fixture_rows,
        "data_checks": checks,
    }


def write_tables(tables: dict[str, list[dict]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, rows in tables.items():
        path = out_dir / f"{name}.json"
        path.write_text(
            json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8"
        )
        paths.append(path)
    return paths
