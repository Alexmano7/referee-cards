"""EuroLeague referee stats: parsing, per-referee figures, player foul effect, crews (offline)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from referees import euroleague as el
from referees.euroleague_stats import build, crew_figures, player_expectations

SCHEDULE = b"""<schedule>
<item><round>RS</round><date>Sep 30, 2025</date><startime>20:45</startime><game>7</game>
<gamecode>E2025_7</gamecode><hometeam>VIRTUS BOLOGNA</hometeam><homecode>VIR</homecode>
<awayteam>REAL MADRID</awayteam><awaycode>MAD</awaycode><played>true</played></item>
<item><round>RS</round><date>Nov 13, 2026</date><startime>18:30</startime><game>109</game>
<gamecode>E2025_109</gamecode><hometeam>ANADOLU EFES ISTANBUL</hometeam><homecode>IST</homecode>
<awayteam>VIRTUS BOLOGNA</awayteam><awaycode>VIR</awaycode><played>false</played></item>
</schedule>"""


def _player(team, name, mins, ftm, fta, fc, fd, pid=None):
    return {
        "Player_ID": pid or name,
        "Team": team,
        "Player": name,
        "Minutes": mins,
        "IsStarter": 1,
        "FreeThrowsMade": ftm,
        "FreeThrowsAttempted": fta,
        "FoulsCommited": fc,
        "FoulsReceived": fd,
    }


def _box(away_first=True):
    home = {
        "PlayersStats": [
            _player("VIR", "SMITH, JOHN", "30:30", 4, 5, 3, 2),
            _player("VIR", "DOE, AL", "DNP", 0, 0, 0, 0),
        ],
        "totr": {"FoulsCommited": 20, "FreeThrowsMade": 14, "FreeThrowsAttempted": 18},
    }
    away = {
        "PlayersStats": [_player("MAD", "CAMPAZZO, FACUNDO", "25:00", 6, 7, 2, 5)],
        "totr": {"FoulsCommited": 22, "FreeThrowsMade": 16, "FreeThrowsAttempted": 20},
    }
    return {
        "Live": False,
        "Referees": "JAVOR, DAMIR, ALIAGA, JORDI, GIOVANNETTI, GUIDO",
        "Stats": [away, home] if away_first else [home, away],
    }


def test_referee_names_and_minutes_are_read():
    assert el.parse_referees("JAVOR, DAMIR, ALIAGA, JORDI, GIOVANNETTI, GUIDO") == [
        "Damir Javor",
        "Jordi Aliaga",
        "Guido Giovannetti",
    ]
    assert el.parse_referees("") == []
    assert el.person_name("VAN DEN BERG, JAN") == "Jan Van Den Berg"
    assert el.minutes("33:21") == pytest.approx(33.35)
    assert el.minutes("DNP") == 0
    assert el.season_label("E2025") == "2025/26"


def test_schedule_and_box_score_use_home_and_away_codes():
    sched = el.parse_schedule(SCHEDULE, "E2025")
    assert [g["game"] for g in sched] == [7, 109]
    assert (
        sched[0]["date"] == "2025-09-30"
        and sched[0]["played"]
        and not sched[1]["played"]
    )
    # The away team is listed first in the box score: sides still come from the team codes.
    game, players = el.parse_boxscore(_box(away_first=True), sched[0])
    assert (game["home_fouls"], game["away_fouls"]) == (20, 22)
    assert (game["home_ftm"], game["away_fta"]) == (14, 20)
    assert game["referees"] == ["Damir Javor", "Jordi Aliaga", "Guido Giovannetti"]
    # Players who did not play are left out.
    assert sorted(p["player"] for p in players) == ["Facundo Campazzo", "John Smith"]
    assert {p["player"]: p["side"] for p in players}["John Smith"] == "home"


def test_unknown_team_code_is_reported():
    sched = el.parse_schedule(SCHEDULE, "E2025")[0]
    bad = _box()
    bad["Stats"][0]["PlayersStats"][0]["Team"] = "XXX"
    with pytest.raises(ValueError):
        el.parse_boxscore(bad, sched)


def test_game_referees_from_the_games_feed():
    xml = b"""<game><referees><referee name="RYZHYK, BORYS" code="OACA" />
    <referee name="THEPENIER, HUGUES" code="QJLT" /><referee name="PERUGA, CARLOS" code="OJIZ" /></referees></game>"""
    assert el.parse_game_referees(xml) == [
        "Borys Ryzhyk",
        "Hugues Thepenier",
        "Carlos Peruga",
    ]
    assert el.parse_game_referees(b"<game><referees /></game>") == []


def _toy_games():
    """Six games in one season: crew A whistles more fouls than crew B."""
    rows, players = [], []
    for i in range(6):
        strict = i < 3
        crew = (
            ["Ann Strict", "Bob Common", "Cid Common"]
            if strict
            else ["Dan Soft", "Bob Common", "Cid Common"]
        )
        hf, af = (24, 22) if strict else (16, 14)
        rows.append(
            {
                "season": "E2025",
                "game": i,
                "round": "RS",
                "date": f"2025-10-0{i + 1}",
                "time": "20:00",
                "home": "H",
                "home_code": "H",
                "away": "A",
                "away_code": "A",
                "referees": crew,
                "home_fouls": hf,
                "away_fouls": af,
                "home_ftm": hf - 4,
                "away_ftm": af - 4,
                "home_fta": hf,
                "away_fta": af,
            }
        )
        for pid in ("p1", "p2"):
            players.append(
                {
                    "season": "E2025",
                    "game": i,
                    "player_id": pid,
                    "player": pid,
                    "team": "H",
                    "side": "home",
                    "starter": True,
                    "minutes": 30.0,
                    "ftm": 3,
                    "fta": 4,
                    "fouls_committed": 4 if strict else 2,
                    "fouls_drawn": 3 if strict else 2,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(players)


def test_referee_factors_and_player_effect_point_the_right_way():
    games, players = _toy_games()
    report = build(games, players)
    refs = {r["referee"]: r for r in report["referees"]}
    assert report["league_average"]["fouls_total"] == pytest.approx(38.0)
    assert refs["Ann Strict"]["raw"]["fouls_total"] == 46
    # Shrinkage: 3 games at 46 vs a 38 average -> pulled most of the way back.
    assert 38 < refs["Ann Strict"]["adjusted"]["fouls_total"] < 46
    assert (
        refs["Ann Strict"]["factor"]["fouls_total"]
        > 1
        > refs["Dan Soft"]["factor"]["fouls_total"]
    )
    assert refs["Bob Common"]["factor"]["fouls_total"] == pytest.approx(1.0)
    # Players foul more than their own usual rate with the strict referee.
    assert refs["Ann Strict"]["player_foul_effect"]["committed"] > 1
    assert refs["Dan Soft"]["player_foul_effect"]["committed"] < 1
    assert refs["Ann Strict"]["games_by_season"] == {"2025/26": 3}


def test_expected_fouls_leave_the_game_itself_out():
    _, players = _toy_games()
    p = player_expectations(players)
    first = p[(p["game"] == 0) & (p["player_id"] == "p1")].iloc[0]
    # Other five games: 2 x 4 + 3 x 2 = 14 fouls in 150 minutes, plus the league prior.
    league_rate = players["fouls_committed"].sum() / players["minutes"].sum()
    expected = (14 + 100 * league_rate) / (150 + 100) * 30
    assert first["expected_committed"] == pytest.approx(expected)


def test_crew_figures_match_names_and_flag_unknown_referees():
    games, players = _toy_games()
    report = build(games, players)
    crew = crew_figures(["A. Strict", "Bob Common", "Cid Common"], report, games)
    assert (
        crew["referees"] == ["Ann Strict", "Bob Common", "Cid Common"]
        and crew["unknown"] == []
    )
    assert crew["expected"]["fouls_total"] > report["league_average"]["fouls_total"]
    assert crew["together"]["games"] == 3 and crew["together"]["fouls_total"] == 46
    newbie = crew_figures(["Zed Newcomer", "Bob Common", "Cid Common"], report)
    assert newbie["unknown"] == ["Zed Newcomer"]
    assert newbie["factor"]["fouls_total"] == pytest.approx(1.0, abs=0.01)


# --- player model, picks and dashboard tables -------------------------------------------


def test_name_casing_keeps_initials():
    assert el.nice_case("FC BARCELONA") == "FC Barcelona"
    assert el.person_name("SHORTS, TJ") == "TJ Shorts"
    assert el.person_name("DE COLO, NANDO") == "Nando De Colo"


def test_free_throw_and_foul_chances_are_proper_distributions():
    from referees.euroleague_model import ftm_pmf, p_over, pick_chance, poisson_pmf

    pmf = ftm_pmf(6.0, 0.8)
    assert sum(pmf) == pytest.approx(1.0, abs=1e-6)
    assert sum(k * q for k, q in enumerate(pmf)) == pytest.approx(4.8, abs=0.01)
    fouls = poisson_pmf(2.0)
    assert p_over(fouls, 2.5) == pytest.approx(1 - sum(fouls[:3]))
    assert pick_chance(0.70) == pytest.approx(0.65)
    assert pick_chance(0.5) == 0.5


def _model_data():
    """Two teams, 8 games; player 'big' fouls far more often than 'guard'."""
    games, players = [], []
    for i in range(8):
        home, away = ("AAA", "BBB") if i % 2 == 0 else ("BBB", "AAA")
        games.append(
            {
                "season": "E2025",
                "game": i,
                "round": "RS",
                "date": f"2025-10-{i + 10}",
                "time": "20:00",
                "home": home,
                "home_code": home,
                "away": away,
                "away_code": away,
                "referees": ["Ann Ref", "Bob Ref", "Cid Ref"],
                "home_fouls": 20,
                "away_fouls": 20,
                "home_ftm": 15,
                "away_ftm": 15,
                "home_fta": 20,
                "away_fta": 20,
            }
        )
        for pid, team, fc, fd, fta, ftm in (
            ("big", "AAA", 4, 2, 2, 1),
            ("guard", "AAA", 1, 4, 6, 5),
            ("opp", "BBB", 2, 2, 3, 2),
        ):
            players.append(
                {
                    "season": "E2025",
                    "game": i,
                    "player_id": pid,
                    "player": pid.title(),
                    "team": team,
                    "side": "home" if team == home else "away",
                    "starter": True,
                    "minutes": 28.0,
                    "ftm": ftm,
                    "fta": fta,
                    "fouls_committed": fc,
                    "fouls_drawn": fd,
                }
            )
    return pd.DataFrame(games), pd.DataFrame(players)


def test_player_projections_follow_each_players_habits():
    from referees.euroleague_model import fit, project_player

    games, players = _model_data()
    f = fit(games, players, "2025-11-01")
    avg = {"known": False, "matched": 0, "fc": 1.0, "fd": 1.0, "fta": 1.0}
    big = project_player(f, "big", "home", "BBB", avg)
    guard = project_player(f, "guard", "home", "BBB", avg)
    assert big["exp_minutes"] == pytest.approx(28.0)
    assert big["mu"]["fc"] > guard["mu"]["fc"]
    assert guard["mu_ftm"] > big["mu_ftm"]
    assert f.rosters["AAA"] == ["big", "guard"]
    strict = project_player(f, "big", "home", "BBB", {**avg, "known": True, "fc": 1.2})
    assert strict["mu"]["fc"] == pytest.approx(1.2 * big["mu"]["fc"])


def test_dashboard_tables_from_toy_data():
    from referees.euroleague_export import athens_time, build_tables
    from referees.euroleague_stats import build

    games, players = _model_data()
    report = build(games, players)
    upcoming = [
        {
            "season": "E2025",
            "game": 99,
            "round": "RS",
            "date": "2025-11-05",
            "time": "20:30",
            "home": "AAA TEAM",
            "home_code": "AAA",
            "away": "BBB TEAM",
            "away_code": "BBB",
            "played": False,
            "referees": ["Ann Ref", "Bob Ref", "Cid Ref"],
        }
    ]
    t = build_tables(games, players, report, upcoming, None, today="2025-11-01")
    assert (
        t["el_games"][0]["crew_known"]
        and t["el_games"][0]["match"] == "Aaa Team v Bbb Team"
    )
    assert {r["player"] for r in t["el_game_players"]} == {"Big", "Guard", "Opp"}
    assert len(t["el_hot_picks"]) <= 5
    for h in t["el_hot_picks"]:
        assert h["model_prob"] >= 50 and h["min_odds"] > h["fair_odds"] - 0.01
    assert {r["scope"] for r in t["el_teams"]} == {"This season", "All seasons"}
    assert athens_time("2026-11-12", "20:30") == "Thu 12 Nov 21:30"


def test_treble_uses_the_football_rule_on_player_legs():
    from referees.euroleague_model import best_treble, pick_chance, treble_legs
    from referees.picks import TREBLE_BAND

    def proj(pid, mu_fd):
        return {
            "player_id": pid,
            "player": pid,
            "team": "T",
            "role": "starter",
            "games_season": 10,
            "exp_minutes": 30.0,
            "ft_pct": 0.8,
            "mu": {"fc": 2.0, "fd": mu_fd, "fta": 4.0},
            "mu_ftm": 3.2,
            "typical_mu": {"fc": 2.0, "fd": 2.0, "fta": 4.0},
            "factors": {},
            "rate_fc": 0.07,
            "typical_rate_fc": 0.07,
        }

    # One player per game who draws far more fouls than a typical player.
    entries = [
        ({"season": "E2026", "game": i}, {"known": False}, [proj(f"p{i}", 7.0)])
        for i in range(4)
    ]
    legs = treble_legs(entries[0][2], 0.78)
    assert legs and all(leg["p"] >= 0.60 and leg["p"] > leg["base"] for leg in legs)
    assert all(leg["p"] == pytest.approx(pick_chance(leg["raw_p"])) for leg in legs)
    chosen = best_treble(entries, 0.78)
    assert chosen and len({c["game"]["game"] for c in chosen}) == 3
    odds = 1 / math.prod(c["leg"]["p"] for c in chosen)
    assert TREBLE_BAND[0] <= odds <= TREBLE_BAND[1]
    # Players who barely play give no legs.
    assert treble_legs([{**proj("bench", 7.0), "exp_minutes": 5.0}], 0.78) == []
