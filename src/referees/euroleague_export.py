"""EuroLeague tables for the dashboard (written to data/dashboard/el_*.json).

el_games          next 7 days: crew, expected team fouls and free throws made
el_game_players   each upcoming game's players: expected minutes, fouls, fouls drawn, FT made
el_hot_picks      up to 5 player bets furthest from a typical player in the same role
el_teams          team fouls, fouls drawn and free throws per game (this season, all seasons)
el_players        player season rows plus a "Last 10" row per current player
el_referees       per referee: all seasons (adjusted, factors, player foul effect) and each season
el_pick_record    backtest of the picks rule (docs/el_backtest.json)
el_calibration    backtest calibration of all over-line chances
el_league         league averages per game
"""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from referees.euroleague import nice_case, season_label
from referees.euroleague_model import (
    LAST_N,
    bet_label,
    crew_adjustment,
    distributions,
    fit,
    game_players,
    min_odds,
    p_over,
    pick,
    pick_chance,
    team_expectations,
    why,
)
from referees.euroleague_stats import METRICS, crew_figures

FEED_TZ = ZoneInfo("Europe/Madrid")  # the schedule's times are Central European time
ATHENS = ZoneInfo("Europe/Athens")
MIN_CARD_MINUTES = 8.0


def athens_time(day: str, hhmm: str) -> str:
    """'2026-11-12', '20:30' (CET) -> 'Thu 12 Nov 21:30' (Athens)."""
    try:
        local = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(
            tzinfo=FEED_TZ
        )
    except ValueError:
        return day
    return local.astimezone(ATHENS).strftime("%a %d %b %H:%M")


def team_rows(games: pd.DataFrame) -> list[dict]:
    names = {}
    for side in ("home", "away"):
        names.update(dict(zip(games[f"{side}_code"], games[side], strict=True)))
    current = games["season"].max()
    sides = []
    for side, opp in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "season": games["season"],
                    "team": games[f"{side}_code"],
                    "fouls": games[f"{side}_fouls"],
                    "drawn": games[f"{opp}_fouls"],
                    "ftm": games[f"{side}_ftm"],
                    "fta": games[f"{side}_fta"],
                    "opp_ftm": games[f"{opp}_ftm"],
                }
            )
        )
    t = pd.concat(sides, ignore_index=True)
    out = []
    for scope, x in (("This season", t[t["season"] == current]), ("All seasons", t)):
        for team, y in x.groupby("team"):
            fta = int(y["fta"].sum())
            out.append(
                {
                    "key": f"{team}|{scope}",
                    "team_code": team,
                    "team": nice_case(names.get(team, team)),
                    "scope": scope,
                    "games": len(y),
                    "fouls_per_game": round(float(y["fouls"].mean()), 2),
                    "fouls_drawn_per_game": round(float(y["drawn"].mean()), 2),
                    "ftm_per_game": round(float(y["ftm"].mean()), 2),
                    "fta_per_game": round(float(y["fta"].mean()), 2),
                    "ft_pct": round(100 * y["ftm"].sum() / fta, 1) if fta else None,
                    "opp_ftm_per_game": round(float(y["opp_ftm"].mean()), 2),
                }
            )
    return out


def player_rows(report: dict, players: pd.DataFrame, games: pd.DataFrame) -> list[dict]:
    out = []
    for r in report["players"]:
        out.append(
            {"key": f"{r['season']}|{r['player_id']}", "scope": r["season"], **r}
        )
    current = games["season"].max()
    dated = players.merge(
        games[["season", "game", "date"]], on=["season", "game"]
    ).sort_values("date")
    active = set(dated[dated["season"] == current]["player_id"])
    for pid, x in dated[dated["player_id"].isin(active)].groupby("player_id"):
        last = x.tail(LAST_N)
        fta = int(last["fta"].sum())
        out.append(
            {
                "key": f"Last 10|{pid}",
                "scope": "Last 10",
                "season": "Last 10",
                "player_id": pid,
                "player": last["player"].iloc[-1],
                "team": last["team"].iloc[-1],
                "games": len(last),
                "minutes_per_game": round(float(last["minutes"].mean()), 1),
                "ftm_per_game": round(float(last["ftm"].mean()), 2),
                "fta_per_game": round(float(last["fta"].mean()), 2),
                "ft_pct": round(100 * last["ftm"].sum() / fta, 1) if fta else None,
                "fouls_committed_per_game": round(
                    float(last["fouls_committed"].mean()), 2
                ),
                "fouls_drawn_per_game": round(float(last["fouls_drawn"].mean()), 2),
            }
        )
    return out


def referee_rows(report: dict) -> list[dict]:
    out = []
    for r in report["referees"]:
        base = {"referee": r["referee"], "last_game": r["last_game"]}
        out.append(
            {
                "key": f"{r['referee']}|All seasons",
                "scope": "All seasons",
                "games": r["games"],
                **base,
                **{m: r["adjusted"][m] for m in METRICS},
                "factor_fouls": r["factor"]["fouls_total"],
                "factor_ftm": r["factor"]["ftm_total"],
                "effect_committed": r["player_foul_effect"]["committed"],
                "effect_drawn": r["player_foul_effect"]["drawn"],
            }
        )
    return out


def referee_season_rows(games: pd.DataFrame) -> list[dict]:
    from referees.euroleague_stats import _by_referee, game_metrics

    out = []
    for (name, season), x in _by_referee(game_metrics(games)).groupby(
        ["referee", "season"]
    ):
        out.append(
            {
                "key": f"{name}|{season_label(season)}",
                "referee": name,
                "scope": season_label(season),
                "games": len(x),
                **{m: round(float(x[m].mean()), 2) for m in METRICS},
            }
        )
    return out


def build_tables(
    games: pd.DataFrame,
    players: pd.DataFrame,
    report: dict,
    upcoming: list[dict],
    backtest: dict | None,
    today: str | None = None,
) -> dict[str, list[dict]]:
    today = today or date.today().isoformat()
    f = fit(
        games, players, (pd.Timestamp(today) + pd.Timedelta(days=1)).date().isoformat()
    )
    league = report["league_average"]

    game_rows, card_rows, entries = [], [], []
    for order, g in enumerate(upcoming):
        key = f"{g['season']}|{g['game']}"
        crew_names = g.get("referees") or []
        crew = crew_adjustment(crew_names, report["referees"])
        figures = crew_figures(crew_names, report) if len(crew_names) == 3 else None
        exp = team_expectations(f, g, figures["factor"] if figures else {}, league)
        game_rows.append(
            {
                "key": key,
                "order": order,
                "date": g["date"],
                "kickoff": athens_time(g["date"], g["time"]),
                "home": nice_case(g["home"]),
                "away": nice_case(g["away"]),
                "home_code": g["home_code"],
                "away_code": g["away_code"],
                "match": f"{nice_case(g['home'])} v {nice_case(g['away'])}",
                "crew": ", ".join(crew_names) if crew_names else "Not announced",
                "crew_known": len(crew_names) == 3,
                "exp_home_fouls": round(exp["home_fouls"], 1),
                "exp_away_fouls": round(exp["away_fouls"], 1),
                "exp_home_ftm": round(exp["home_ftm"], 1),
                "exp_away_ftm": round(exp["away_ftm"], 1),
                "crew_fouls_factor": figures["factor"]["fouls_total"]
                if figures
                else None,
                "crew_ftm_factor": figures["factor"]["ftm_total"] if figures else None,
                "crew_effect_committed": round(crew["fc"], 3) if figures else None,
                "crew_effect_drawn": round(crew["fd"], 3) if figures else None,
            }
        )
        projs = game_players(f, g, crew)
        entries.append((g, crew, projs))
        for proj in projs:
            if proj["exp_minutes"] < MIN_CARD_MINUTES:
                continue
            model, _ = distributions(proj, f.league_ft_pct)
            card_rows.append(
                {
                    "key": f"{key}|{proj['player_id']}",
                    "game": key,
                    "side": proj["side"],
                    "team": proj["team"],
                    "player": proj["player"],
                    "role": proj["role"],
                    "exp_minutes": round(proj["exp_minutes"], 1),
                    "exp_fouls": round(proj["mu"]["fc"], 2),
                    "exp_fouls_drawn": round(proj["mu"]["fd"], 2),
                    "exp_ftm": round(proj["mu_ftm"], 2),
                    "ft_pct": round(100 * proj["ft_pct"], 1),
                    "p_fouls_over_2_5": round(
                        100 * p_over(model["fouls_committed"], 2.5), 1
                    ),
                    "p_drawn_over_2_5": round(
                        100 * p_over(model["fouls_drawn"], 2.5), 1
                    ),
                    "p_ftm_over_2_5": round(100 * p_over(model["ftm"], 2.5), 1),
                }
            )
    card_rows.sort(key=lambda r: (r["game"], r["side"] != "home", -r["exp_minutes"]))

    hot = []
    for rank, pk in enumerate(pick(entries, f.league_ft_pct), start=1):
        g, proj, bet, crew = pk["game"], pk["proj"], pk["bet"], pk["crew"]
        pc = pick_chance(bet["p"])
        hot.append(
            {
                "key": str(rank),
                "rank": rank,
                "game": f"{g['season']}|{g['game']}",
                "kickoff": athens_time(g["date"], g["time"]),
                "match": f"{nice_case(g['home'])} v {nice_case(g['away'])}",
                "player": proj["player"],
                "team": proj["team"],
                "role": proj["role"],
                "bet": f"{proj['player']}: {bet_label(bet)}",
                "market": bet["market"],
                "selection": bet["selection"],
                "line": bet["line"],
                "model_prob": round(100 * pc, 1),
                "raw_model_prob": round(100 * bet["p"], 1),
                "typical_prob": round(100 * bet["typical"], 1),
                "fair_odds": round(1 / pc, 2),
                "min_odds": min_odds(pc),
                "crew": ", ".join(g.get("referees") or []) or "Not announced",
                "why": why(proj, bet, crew),
            }
        )

    record, calibration = [], []
    if backtest:
        for period, r in backtest["pick_record"].items():
            record.append(
                {
                    "key": period,
                    "period": period,
                    "picks": r["picks"],
                    "won": r["won"],
                    "hit_rate_pct": r["hit_rate_pct"],
                    "avg_model_pct": r.get("avg_corrected_pct", r["avg_model_pct"]),
                    "avg_typical_pct": r["avg_typical_pct"],
                    "break_even_odds": r["break_even_odds"],
                }
            )
        calibration = [{"key": b["bin"], **b} for b in backtest["calibration"]]

    return {
        "el_games": game_rows,
        "el_game_players": card_rows,
        "el_hot_picks": hot,
        "el_teams": team_rows(games),
        "el_players": player_rows(report, players, games),
        "el_referees": referee_rows(report) + referee_season_rows(games),
        "el_pick_record": record,
        "el_calibration": calibration,
        "el_league": [
            {
                "key": "league",
                "games": report["games"],
                "seasons": " to ".join([report["seasons"][0], report["seasons"][-1]]),
                **{m: league[m] for m in METRICS},
            }
        ],
    }


def load_backtest(path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
