"""Link API matches (referee) to CSV matches (cards) on league + date + home + away."""

from __future__ import annotations

import pandas as pd

from referees.team_names import apply_mapping

KEY = ["league", "season", "date", "csv_home", "csv_away"]
# Matches that were played (or decided) and so should have a CSV row.
PLAYED_STATUSES = ["FINISHED", "AWARDED"]


def _surname_agrees(api_name, csv_name):
    """Cross-check for England: CSV 'T Bramall' vs API 'Thomas Bramall'."""
    if not isinstance(api_name, str) or not isinstance(csv_name, str) or not csv_name:
        return pd.NA
    return csv_name.split()[-1].lower() in api_name.lower()


def join_matches(
    api: pd.DataFrame, csv: pd.DataFrame, mapping: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (joined matches, unmatched rows with a reason)."""
    finished = apply_mapping(api[api["status"].isin(PLAYED_STATUSES)], mapping)
    # Only join seasons we have CSVs for (and vice versa).
    finished = finished[finished["season"].isin(csv["season"].unique())]
    csv = csv[csv["season"].isin(finished["season"].unique())]

    # 1) exact join on league + season + date + teams
    merged = finished.merge(
        csv, on=KEY, how="outer", indicator=True, suffixes=("", "_csv")
    )
    joined = merged[merged["_merge"] == "both"].copy()
    joined["date_diff_days"] = 0
    api_left = merged[merged["_merge"] == "left_only"][finished.columns]
    csv_left = merged[merged["_merge"] == "right_only"][csv.columns]

    # 2) fallback: same teams in the same season, kick-off date off by one day
    #    (a late kick-off near midnight, or a source that recorded the date differently).
    #    A home team meets the same away team only once per league season, so this is safe.
    if not api_left.empty and not csv_left.empty:
        fb = api_left.merge(
            csv_left,
            on=["league", "season", "csv_home", "csv_away"],
            suffixes=("", "_csv"),
        )
        fb["date_diff_days"] = (
            pd.to_datetime(fb["date_csv"]) - pd.to_datetime(fb["date"])
        ).dt.days
        fb = fb[fb["date_diff_days"].abs() <= 1].drop(columns=["date_csv"])
        if not fb.empty:
            joined = pd.concat([joined, fb], ignore_index=True)
            api_left = api_left[~api_left["api_match_id"].isin(fb["api_match_id"])]
            used = set(zip(fb["league"], fb["season"], fb["csv_home"], fb["csv_away"]))
            csv_left = csv_left[
                [
                    k not in used
                    for k in zip(
                        csv_left["league"],
                        csv_left["season"],
                        csv_left["csv_home"],
                        csv_left["csv_away"],
                    )
                ]
            ]

    joined = joined.drop(columns=["_merge"], errors="ignore")
    joined["csv_referee_agrees"] = [
        _surname_agrees(a, c) for a, c in zip(joined["referee"], joined["csv_referee"])
    ]
    joined["api_match_id"] = joined["api_match_id"].astype(int)
    joined = joined.sort_values(["league", "date", "api_home"]).reset_index(drop=True)

    unmatched = _describe_unmatched(api_left, csv_left)
    return joined, unmatched


def _describe_unmatched(api_left: pd.DataFrame, csv_left: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in api_left.itertuples():
        if not isinstance(r.csv_home, str) or not isinstance(r.csv_away, str):
            reason = "team name missing from mapping file"
        else:
            reason = "no CSV match with these teams on this date"
        rows.append(
            {
                "source": "football-data.org",
                "league": r.league,
                "season": r.season,
                "date": r.date,
                "home": r.api_home,
                "away": r.api_away,
                "reason": reason,
            }
        )
    for r in csv_left.itertuples():
        rows.append(
            {
                "source": "football-data.co.uk",
                "league": r.league,
                "season": r.season,
                "date": r.date,
                "home": r.csv_home,
                "away": r.csv_away,
                "reason": "no finished API match with these teams on this date",
            }
        )
    cols = ["source", "league", "season", "date", "home", "away", "reason"]
    return (
        pd.DataFrame(rows, columns=cols)
        .sort_values(["league", "date"])
        .reset_index(drop=True)
    )
