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
    assert fixture["basis"] == "model: referee, both teams and league"
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
    assert {
        "1|ALL|All seasons",
        "1|PL|All seasons",
        "1|PL|2025/26",
        "1|PL|2026/27",
        "2|PD|All seasons",
    } <= keys
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


# --- referee appointments (web lookup) ------------------------------------------------


def test_match_referee_names():
    from referees.appointments import match_referee

    known = [
        (1, "Sven Jablonski"),
        (2, "Alejandro Hernández Hernández"),
        (3, "Daniel Siebert"),
        (4, "Daniel Schlager"),
    ]
    assert match_referee("Sven Jablonski", known) == 1
    assert match_referee("S. Jablonski", known) == 1
    assert match_referee("Hernández Hernández", known) == 2  # Spanish surnames only
    assert (
        match_referee("hernandez hernandez", known) == 2
    )  # accents and case don't matter
    assert match_referee("Daniel", known) is None  # two Daniels: never guess
    assert match_referee("Felix Zwayer", known) is None  # unknown referee
    assert (
        match_referee("Rob Jones", known + [(5, "Robert Jones")]) == 5
    )  # short first name
    assert match_referee("Dan Siebert", known) == 3
    assert match_referee("Dan", known) is None  # a short form alone is not enough
    spanish = [
        (10, "Francisco Hernández Maeso"),
        (11, "Alejandro Hernández Hernández"),
        (12, "Miguel Ortiz Arias"),
        (13, "Juan Martínez Munuera"),
        (14, "José Sánchez Martínez"),
    ]
    assert (
        match_referee("Francisco José Hernández", spanish) == 10
    )  # RFEF full middle name
    assert match_referee("Miguel Ángel Ortiz", spanish) == 12
    assert match_referee("Juan Martínez", spanish) == 13  # not José Sánchez Martínez
    assert (
        match_referee("Pedro José Hernández", spanish) is None
    )  # first name fits nobody
    scottish = [(20, "N Walsh"), (21, "J Beaton"), (22, "N Smith")]
    assert (
        match_referee("Nicholas Walsh", scottish) == 20
    )  # the file abbreviates first names
    assert match_referee("John Beaton", scottish) == 21
    assert match_referee("Nicholas Jones", scottish) is None  # surname must still match


def _fixture(home="A", away="B", day=date(2026, 11, 8), referee=None, referee_id=None):
    return {
        "league": "PL",
        "season": 2026,
        "api_match_id": 5,
        "utc_kickoff": f"{day}T15:00:00+00:00",
        "date": day,
        "status": "TIMED",
        "matchday": 11,
        "api_home": home,
        "api_away": away,
        "referee_id": referee_id,
        "referee": referee,
        "referee_nationality": None,
    }


def _appointment(
    referee,
    home="A",
    away="B",
    day="2026-11-08",
    url="https://www.example.org/appointments",
):
    return {
        "league": "PL",
        "date": day,
        "home": home,
        "away": away,
        "referee": referee,
        "source_url": url,
        "found_at": "2026-11-06",
    }


def test_looked_up_referee_drives_projection():
    rows = [_match(2026, 7, 4, 4) for _ in range(10)] + [
        _match(2026, 8, 1, 1) for _ in range(10)
    ]
    api = pd.DataFrame([_fixture()])
    appts = pd.DataFrame(
        [_appointment("R. 8")]
    )  # "Ref 8" in the history, written as an initial
    report = stats.build_report(
        pd.DataFrame(rows),
        api,
        pd.DataFrame(),
        today=date(2026, 11, 6),
        appointments=appts,
    )
    fx = report["upcoming_fixtures"][0]
    assert fx["referee_id"] == 8 and fx["referee"] == "Ref 8"
    assert fx["basis"] == "model: referee, both teams and league"
    assert fx["referee_source"] == "example.org"
    assert (
        fx["projected_cards"] < 5
    )  # the low-card referee, not the league average of 5
    assert report["data_quality"]["appointments_used"] == 1
    assert (
        next(r for r in report["referees"] if r["id"] == 8)["next_fixture"]["home"]
        == "A"
    )


def test_api_referee_wins_and_bad_rows_are_reported():
    rows = [_match(2026, 7, 2, 2) for _ in range(5)]
    api = pd.DataFrame(
        [_fixture(referee="Ref 7", referee_id=7), _fixture(home="C", away="D")]
    )
    appts = pd.DataFrame(
        [
            _appointment(
                "Someone Else"
            ),  # A v B already has the API's referee: ignored
            _appointment("Ref 7", home="X", away="Y"),  # no such fixture
            _appointment(
                "Nobody Known", home="C", away="D"
            ),  # unknown name: shown, flagged
        ]
    )
    report = stats.build_report(
        pd.DataFrame(rows),
        api,
        pd.DataFrame(),
        today=date(2026, 11, 6),
        appointments=appts,
    )
    by_home = {f["home"]: f for f in report["upcoming_fixtures"]}
    assert (
        by_home["A"]["referee"] == "Ref 7"
        and by_home["A"]["referee_source"] == "football-data.org"
    )
    assert by_home["C"]["referee"] == "Nobody Known"
    assert (
        by_home["C"]["basis"] == "model: both teams and league (referee has no history)"
    )
    problems = report["data_quality"]["appointment_problems"]
    assert len(problems) == 2
    assert any("no upcoming fixture" in p for p in problems)
    assert any("does not match exactly one" in p for p in problems)


# --- team card statistics -----------------------------------------------------------


def test_team_stats_and_fixture_split():
    from referees.dashboard_export import build_tables

    # Team A (home) always gets 3 cards, team B (away) 1. Referee 7 in every match.
    rows = [_match(2026, 7, 3, 1, home="A", away="B") for _ in range(10)]
    api = pd.DataFrame([_fixture(home="A", away="B", referee="Ref 7", referee_id=7)])
    report = stats.build_report(
        pd.DataFrame(rows), api, pd.DataFrame(), today=date(2026, 11, 6)
    )
    teams = {t["team"]: t["overall"] for t in report["teams"]}
    assert (
        teams["A"]["cards_per_match"] == 3
        and teams["A"]["opponent_cards_per_match"] == 1
    )
    assert (
        teams["B"]["away_cards_per_match"] == 1
        and teams["B"]["home_cards_per_match"] is None
    )
    assert (
        teams["A"]["pct_team_over_2_5"] == 100.0
        and teams["B"]["pct_team_over_1_5"] == 0.0
    )
    fx = report["upcoming_fixtures"][0]
    assert fx["projected_home_cards"] + fx["projected_away_cards"] == pytest.approx(
        fx["projected_cards"], abs=0.011
    )
    assert (
        fx["projected_home_cards"] > fx["projected_away_cards"]
    )  # A gets more cards at home
    assert fx["referee_home_matches"] == 10 and fx["referee_home_cards_per_match"] == 3
    t = build_tables(
        pd.DataFrame(rows).astype({c: "Int64" for c in stats.CARD_COUNT_COLUMNS}),
        report,
        today=date(2026, 11, 6),
    )
    keys = {r["key"] for r in t["team_stats"]}
    assert {"PL|A|All seasons", "PL|A|2026/27", "PL|B|All seasons"} <= keys


# --- fouls and home/away split -----------------------------------------------------


def test_fouls_and_home_away_split():
    rows = [dict(_match(2026, 1, 1, 3), home_fouls=10, away_fouls=15) for _ in range(4)]
    df = stats.add_match_metrics(pd.DataFrame(rows), 2026)
    b = stats._basic(df)
    assert b["home_cards_per_match"] == 1 and b["away_cards_per_match"] == 3
    assert b["away_share_pct"] == 75.0
    assert b["fouls_per_match"] == 25.0
    assert b["cards_per_10_fouls"] == 1.6  # 16 cards from 100 fouls
    report = stats.build_report(
        pd.DataFrame(rows), _empty_api(), pd.DataFrame(), today=date(2026, 11, 6)
    )
    away = next(t for t in report["teams"] if t["team"] == "B")["overall"]
    assert away["fouls_per_match"] == 15.0 and away["fouls_suffered_per_match"] == 10.0
    assert away["cards_per_10_fouls"] == 2.0  # 12 cards from 60 fouls


# --- probability model ---------------------------------------------------------------


def test_negative_binomial_distribution():
    from referees.model import nb_pmf_array, prob_over

    for phi in (0.0, 0.1):
        d = nb_pmf_array(4.0, phi, 30)
        assert sum(d) == pytest.approx(1.0, abs=1e-9)
        mean = sum(k * p for k, p in enumerate(d[:-1]))
        assert mean == pytest.approx(4.0, abs=0.01)
    poisson, wider = nb_pmf_array(4.0, 0.0, 30), nb_pmf_array(4.0, 0.2, 30)
    assert prob_over(wider, 7.5) > prob_over(
        poisson, 7.5
    )  # over-dispersion fattens the tail
    assert prob_over([0.5, 0.5, 0.0], 0.5) == pytest.approx(0.5)


def test_model_recovers_strict_referee_and_card_prone_team():
    import numpy as np

    from referees.model import fit, side_observations

    rng = np.random.default_rng(1)
    teams = ["A", "B", "C", "D", "E", "F"]
    true_recv = {"A": 1.6, "B": 1.0, "C": 1.0, "D": 1.0, "E": 1.0, "F": 0.7}
    true_ref = {1: 1.5, 2: 1.0, 3: 0.7}
    rows = []
    for i in range(1500):
        h, a = rng.choice(teams, 2, replace=False)
        ref = int(rng.choice([1, 2, 3]))
        hc = rng.poisson(1.9 * true_ref[ref] * true_recv[h])
        ac = rng.poisson(2.1 * true_ref[ref] * true_recv[a])
        rows.append(
            {
                "league": "PL",
                "date": date(2025, 1, 1) + pd.Timedelta(days=i % 300),
                "referee_id": ref,
                "api_home": h,
                "api_away": a,
                "home_cards": hc,
                "away_cards": ac,
            }
        )
    df = pd.DataFrame(rows)
    m = fit(
        side_observations(df),
        as_of=date(2026, 1, 1),
        half_life_days=10_000,
        k_referee=5,
        k_team=5,
    )
    assert m.referee[1] / m.referee[3] == pytest.approx(1.5 / 0.7, rel=0.08)
    assert m.receives[("PL", "A")] / m.receives[("PL", "F")] == pytest.approx(
        1.6 / 0.7, rel=0.08
    )
    strict = m.predict("PL", "A", "B", referee_id=1)
    lenient = m.predict("PL", "F", "B", referee_id=3)
    assert strict["expected_cards"] > lenient["expected_cards"]
    assert strict["prob_over_4_5"] > lenient["prob_over_4_5"]
    assert strict["expected_home_cards"] + strict[
        "expected_away_cards"
    ] == pytest.approx(strict["expected_cards"])
    assert sum(strict["distribution"]) == pytest.approx(1.0)


# --- bet log -------------------------------------------------------------------------


def test_bets_are_settled_from_card_data():
    from referees.bets import COLUMNS, settle

    joined = pd.DataFrame(
        [
            {
                **_match(2026, 1, 3, 2, 0, 1),
                "date": "2026-11-01",
                "api_home": "A",
                "api_away": "B",
            }
        ]
    )
    base = {
        "placed_at": "2026-10-30",
        "league": "PL",
        "date": "2026-11-01",
        "home": "A",
        "away": "B",
        "odds": "2.0",
        "stake": "10",
        "model_prob": "55",
        "note": "",
    }
    bets = pd.DataFrame(
        [
            {
                **base,
                "market": "match_cards",
                "selection": "over",
                "line": "4.5",
            },  # 6 cards: won
            {
                **base,
                "market": "home_cards",
                "selection": "over",
                "line": "3.5",
            },  # 3: lost
            {
                **base,
                "market": "match_booking_points",
                "selection": "under",
                "line": "70.5",
            },  # 75: lost
            {
                **base,
                "market": "match_cards",
                "selection": "over",
                "line": "4.5",
                "date": "2026-12-01",
            },  # pending
            {
                **base,
                "market": "match_cards",
                "selection": "over",
                "line": "4",
            },  # whole line: invalid
        ]
    )[COLUMNS]
    rows = settle(bets, joined)
    assert [r["status"] for r in rows] == ["won", "lost", "lost", "pending", "invalid"]
    assert rows[0]["actual"] == 6 and rows[0]["profit"] == 10.0
    assert rows[1]["profit"] == -10.0
    assert rows[2]["actual"] == 75
    assert rows[0]["implied_prob"] == 50.0


# --- hot predictions ------------------------------------------------------------------


def test_hot_pick_prefers_the_biggest_shift_from_a_typical_match():
    from referees.picks import best_candidate, is_hit, min_odds

    def probs(over35, over45, over55, h15, h25, a15, a25):
        return {
            "prob_over_3_5": over35,
            "prob_over_4_5": over45,
            "prob_over_5_5": over55,
            "home_prob_over_1_5": h15,
            "home_prob_over_2_5": h25,
            "away_prob_over_1_5": a15,
            "away_prob_over_2_5": a25,
        }

    typical = probs(0.55, 0.40, 0.25, 0.45, 0.25, 0.55, 0.30)
    model = probs(
        0.75, 0.58, 0.40, 0.50, 0.28, 0.60, 0.33
    )  # strict referee: totals go up
    pick = best_candidate(model, typical)
    assert (pick["market"], pick["selection"], pick["line"]) == (
        "match_cards",
        "over",
        3.5,
    )
    assert pick["score"] == pytest.approx(0.20)
    assert (
        best_candidate(probs(*[0.5] * 7), typical, min_prob=0.9) is None
    )  # nothing likely enough
    assert is_hit(pick, 2, 2) and not is_hit(pick, 1, 2)
    assert min_odds(0.625) == 1.68  # fair 1.60 + 5%


def test_safe_treble_targets_about_two_and_prefers_known_referees():
    from referees.picks import best_treble

    def leg(p, base):
        return {
            "market": "match_cards",
            "selection": "under",
            "line": 4.5,
            "p": p,
            "base": base,
            "score": p - base,
        }

    # Three known-referee matches whose legs multiply to ~2.00, plus a tempting unknown-referee one.
    pool = [
        ("A", True, [leg(0.80, 0.70)]),
        ("B", True, [leg(0.79, 0.65), leg(0.95, 0.94)]),
        ("C", True, [leg(0.79, 0.60)]),
        ("D", False, [leg(0.80, 0.30)]),
    ]
    chosen = best_treble(pool)
    assert [m for m, _ in chosen] == ["A", "B", "C"]  # known referees first
    odds = 1 / (chosen[0][1]["p"] * chosen[1][1]["p"] * chosen[2][1]["p"])
    assert 1.85 <= odds <= 2.15
    assert (
        best_treble([("A", True, [leg(0.99, 0.5)])] * 2) is None
    )  # fewer than 3 matches


# --- CSV leagues (Greece, Belgium, Scotland) and referee.observer ----------------------


def test_referee_observer_history_page_is_parsed():
    from referees.referee_observer import parse_history, referee_name

    page = (
        "<html><head><title>Ana Example \u2014 All Matches</title></head><body>"
        "<div>Super League Greece</div><div>07/11/2026</div><div>Volos</div><div>1</div><div>\u2013</div>"
        "<div>3</div><div>Kifisia</div><div>\U0001f4fa</div><div>VAR</div><div>2026/27</div>"
        "<div>Super League Greece</div><div>24/10/2026</div><div>Iraklis</div><div>2</div><div>\u2013</div>"
        "<div>3</div><div>Kifisia</div><div>Referee</div><div>2026/27</div>"
        "<script>ignored 01/01/2020</script></body></html>"
    )
    rows = parse_history(page)
    assert referee_name(page) == "Ana Example"
    assert [(r["home"], r["away"], r["role"]) for r in rows] == [
        ("Volos", "Kifisia", "VAR"),
        ("Iraklis", "Kifisia", "Referee"),
    ]
    assert rows[1]["date"] == date(2026, 10, 24)


def test_csv_league_matches_get_referees_from_history_or_file():
    from referees.csv_leagues import (
        csv_matches,
        ro_referees_for_csv,
        stable_referee_id,
        suggest_ro_mapping,
    )

    csv = pd.DataFrame(
        [
            {
                "league": "GR",
                "season": 2026,
                "date": date(2026, 10, 24),
                "time": "17:00",
                "csv_home": "Iraklis",
                "csv_away": "Kifisia",
                "csv_referee": None,
            },
            {
                "league": "SCO",
                "season": 2026,
                "date": date(2026, 10, 25),
                "time": "15:00",
                "csv_home": "Celtic",
                "csv_away": "Hearts",
                "csv_referee": "J Beaton",
            },
        ]
    )
    history = pd.DataFrame(
        [
            {
                "league": "GR",
                "date": date(2026, 10, 24),
                "ro_home": "Iraklis FC",
                "ro_away": "AE Kifisia",
                "referee": "Ana Example",
                "ro_path": "/referee/gre/x",
            }
        ]
    )
    empty = pd.DataFrame(columns=["league", "ro_name", "csv_name", "checked"])
    mapping = suggest_ro_mapping(history, csv, empty)
    assert dict(zip(mapping["ro_name"], mapping["csv_name"])) == {
        "AE Kifisia": "Kifisia",
        "Iraklis FC": "Iraklis",
    }
    rows = csv_matches(csv, ro_referees_for_csv(history, csv, mapping)).set_index(
        "league"
    )
    assert rows.loc["GR", "referee"] == "Ana Example"
    assert rows.loc["SCO", "referee"] == "J Beaton"
    assert rows.loc["SCO", "referee_id"] == stable_referee_id("j beaton")
    assert rows.loc["GR", "utc_kickoff"].startswith(
        "2026-10-24T16:00"
    )  # 17:00 UK summer time
