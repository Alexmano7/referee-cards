"""Offline tests: tiny hand-made inputs where we know the right answer. No network."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from referees import stats
from referees.config import (
    LEAGUES_BY_KEY,
    csv_season_code,
    current_season_start_year,
    seasons_to_fetch,
)
from referees.fetch_api import parse_matches
from referees.fetch_csv import read_csv_matches
from referees.join import join_matches
from referees.team_names import merge_suggestions, normalise, suggest_mapping

FIXTURES = Path(__file__).parent / "fixtures"
PL = LEAGUES_BY_KEY["PL"]


@pytest.fixture
def csv_df():
    return read_csv_matches(FIXTURES / "E0_sample.csv", PL, 2026)


@pytest.fixture
def api_df():
    data = json.loads((FIXTURES / "api_sample.json").read_text(encoding="utf-8"))
    return parse_matches(data, PL, 2026)


@pytest.fixture
def mapping():
    return pd.DataFrame(
        {
            "league": ["PL"] * 4,
            "api_name": [
                "Arsenal FC",
                "Coventry City FC",
                "Hull City AFC",
                "Manchester United FC",
            ],
            "csv_name": ["Arsenal", "Coventry", "Hull", "Man United"],
            "checked": ["yes"] * 4,
        }
    )


# --- config -----------------------------------------------------------------------------


def test_seasons():
    assert current_season_start_year(date(2026, 11, 6)) == 2026
    assert current_season_start_year(date(2027, 3, 1)) == 2026
    assert seasons_to_fetch(date(2026, 11, 6)) == [2024, 2025, 2026]
    assert csv_season_code(2026) == "2627"
    assert csv_season_code(2099) == "9900"


# --- reading files ----------------------------------------------------------------------


def test_read_csv_handles_bom_and_blank_cards(csv_df):
    assert list(csv_df["csv_home"]) == [
        "Arsenal",
        "Hull",
        "Leeds",
    ]  # BOM did not break "Div"
    assert csv_df.loc[1, "away_reds"] == 1
    assert pd.isna(csv_df.loc[2, "home_yellows"])  # unknown, not zero
    assert csv_df.loc[0, "date"] == date(2026, 8, 21)


def test_parse_api_picks_main_referee_and_local_date(api_df):
    first = api_df.iloc[0]
    assert first["referee"] == "Thomas Bramall"  # not the VAR listed before him
    assert first["referee_id"] == 11
    # 23:30 UTC on 22 Aug is 00:30 on 23 Aug in London (summer time)
    assert api_df.iloc[1]["date"] == date(2026, 8, 23)
    assert pd.isna(api_df.iloc[1]["referee"])


# --- team names -------------------------------------------------------------------------


def test_normalise():
    assert normalise("Hull City AFC") == "hull city"
    assert normalise("1. FC Köln") == "koln"


def test_suggest_mapping_uses_same_day_matches(api_df, csv_df):
    sugg = suggest_mapping(api_df, csv_df)
    pairs = dict(zip(sugg["api_name"], sugg["csv_name"]))
    assert pairs["Arsenal FC"] == "Arsenal"
    assert pairs["Coventry City FC"] == "Coventry"


def test_merge_suggestions_never_overwrites(mapping):
    sugg = pd.DataFrame(
        {
            "league": ["PL", "PL"],
            "api_name": ["Arsenal FC", "Leeds United FC"],
            "csv_name": ["WRONG", "Leeds"],
            "votes": [5, 5],
            "share": [1.0, 1.0],
        }
    )
    merged, added = merge_suggestions(mapping, sugg)
    assert (
        merged.loc[merged["api_name"] == "Arsenal FC", "csv_name"].item() == "Arsenal"
    )
    assert list(added["api_name"]) == ["Leeds United FC"]
    assert merged.loc[merged["api_name"] == "Leeds United FC", "checked"].item() == "no"


# --- join -------------------------------------------------------------------------------


def test_join_exact_and_one_day_fallback(api_df, csv_df, mapping):
    joined, unmatched = join_matches(api_df, csv_df, mapping)
    assert len(joined) == 2
    row = joined.set_index("api_match_id").loc[2]
    assert row["date_diff_days"] == -1  # API local date 23rd, CSV says 22nd
    assert joined.set_index("api_match_id").loc[1, "csv_referee_agrees"]
    # The Leeds v Everton CSV row has no API match: reported, not dropped
    assert list(unmatched["home"]) == ["Leeds"]


def test_join_reports_missing_mapping(api_df, csv_df, mapping):
    joined, unmatched = join_matches(
        api_df, csv_df, mapping[mapping["api_name"] != "Hull City AFC"]
    )
    assert len(joined) == 1
    assert "team name missing from mapping file" in set(unmatched["reason"])


# --- stats ------------------------------------------------------------------------------


def _match(season, ref_id, hy, ay, hr=0, ar=0, league="PL", home="A", away="B"):
    return {
        "league": league,
        "season": season,
        "date": date(season, 9, 1),
        "referee_id": ref_id,
        "referee": f"Ref {ref_id}",
        "referee_nationality": None,
        "api_home": home,
        "api_away": away,
        "home_yellows": hy,
        "away_yellows": ay,
        "home_reds": hr,
        "away_reds": ar,
    }


def test_booking_points_and_weights():
    df = stats.add_match_metrics(
        pd.DataFrame([_match(2026, 1, 2, 1, 0, 1), _match(2024, 1, 1, 1)]), 2026
    )
    assert list(df["cards"]) == [4, 2]
    assert list(df["booking_points"]) == [3 * 10 + 25, 20]
    assert list(df["weight"]) == pytest.approx([1.0, 0.36])


def test_shrinkage_pulls_small_samples_toward_league():
    # Referee 1: 2 matches with 10 cards. Referee 2: 30 matches with 3 cards.
    rows = [_match(2026, 1, 5, 5) for _ in range(2)] + [
        _match(2026, 2, 2, 1) for _ in range(30)
    ]
    report = stats.build_report(
        pd.DataFrame(rows), _empty_api(), pd.DataFrame(), today=date(2026, 11, 6)
    )
    by_id = {r["id"]: r["overall"] for r in report["referees"]}
    league = (2 * 10 + 30 * 3) / 32
    assert by_id[1]["cards_per_match"] == 10
    assert (
        league < by_id[1]["adjusted_cards_per_match"] < 10
    )  # pulled most of the way down
    assert by_id[1]["adjusted_cards_per_match"] == pytest.approx(
        (2 * 10 + 8 * league) / 10, abs=0.01
    )
    assert by_id[1]["pct_over_4_5"] == 100.0


def test_matches_without_card_data_are_listed_not_counted():
    rows = [_match(2026, 1, 2, 2), _match(2026, 1, pd.NA, pd.NA, pd.NA, pd.NA)]
    df = pd.DataFrame(rows).astype({c: "Int64" for c in stats.CARD_COUNT_COLUMNS})
    report = stats.build_report(
        df, _empty_api(), pd.DataFrame(), today=date(2026, 11, 6)
    )
    assert report["referees"][0]["overall"]["matches"] == 1
    assert len(report["data_quality"]["matches_without_card_data"]) == 1


def test_projection_for_upcoming_fixture():
    rows = [_match(2026, 7, 2, 2) for _ in range(10)]
    api = pd.DataFrame(
        [
            {
                "league": "PL",
                "season": 2026,
                "api_match_id": 5,
                "utc_kickoff": "2026-11-08T15:00:00+00:00",
                "date": date(2026, 11, 8),
                "status": "TIMED",
                "matchday": 11,
                "api_home": "A",
                "api_away": "B",
                "referee_id": 7,
                "referee": "Ref 7",
                "referee_nationality": None,
            }
        ]
    )
    report = stats.build_report(
        pd.DataFrame(rows), api, pd.DataFrame(), today=date(2026, 11, 6)
    )
    fixture = report["upcoming_fixtures"][0]
    assert fixture["projected_cards"] == pytest.approx(
        4.0
    )  # everything is exactly average here
    assert fixture["basis"] == "referee"
    assert report["referees"][0]["next_fixture"]["home"] == "A"


def _empty_api():
    return pd.DataFrame(
        columns=[
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
    )


# --- dashboard export -------------------------------------------------------------------


def test_dashboard_tables_cover_every_scope():
    from referees.dashboard_export import build_tables

    rows = [
        _match(2026, 1, 2, 2),
        _match(2025, 1, 1, 0),
        _match(2026, 2, 3, 3, league="PD"),
    ]
    matches = pd.DataFrame(rows).astype({c: "Int64" for c in stats.CARD_COUNT_COLUMNS})
    report = stats.build_report(
        matches, _empty_api(), pd.DataFrame(), today=date(2026, 11, 6)
    )
    t = build_tables(matches, report, today=date(2026, 11, 6))
    keys = {r["key"] for r in t["referee_stats"]}
    assert {"1|ALL|All seasons", "1|PL|All seasons", "1|PL|2025/26", "1|PL|2026/27", "2|PD|All seasons"} <= keys
    by_key = {r["key"]: r for r in t["referee_stats"]}
    assert by_key["1|PL|All seasons"]["matches"] == 2
    assert (
        by_key["1|PL|2026/27"]["adjusted_cards_per_match"] is None
    )  # only for all seasons
    overall = {r["id"]: r["overall"] for r in report["referees"]}
    assert (
        by_key["1|ALL|All seasons"]["adjusted_cards_per_match"]
        == overall[1]["adjusted_cards_per_match"]
    )
    summary = {r["key"]: r for r in t["league_summary"]}
    assert summary["ALL|All seasons"]["matches"] == 3
    assert summary["PD|All seasons"]["cards_per_match"] == 6
