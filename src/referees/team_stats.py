"""Card statistics for teams: cards a team receives, cards its opponents receive, and how
each upcoming fixture's projected cards split between the two teams.

Works on the match table produced by stats.add_match_metrics (one row per match, with
home_/away_ yellows and reds, `cards`, `weight` and `referee_id`).
"""

from __future__ import annotations

import pandas as pd

from referees.config import RED_CARD_COUNTS_AS, season_label
from referees.stats import PRIOR_MATCHES, RED_POINTS, YELLOW_POINTS, _shrink, _wmean

TEAM_LINES = [1.5, 2.5]
MATCH_LINE = 4.5


def _line_key(prefix: str, line: float) -> str:
    return f"{prefix}{str(line).replace('.', '_')}"


def team_long(df: pd.DataFrame) -> pd.DataFrame:
    """One row per team per match ('long' format)."""

    def side(me: str, them: str, venue: str) -> pd.DataFrame:
        out = pd.DataFrame(
            {
                "league": df["league"],
                "season": df["season"],
                "date": df["date"],
                "weight": df["weight"],
                "referee_id": df["referee_id"],
                "team": df[f"api_{me}"],
                "opponent": df[f"api_{them}"],
                "venue": venue,
                "yellows": df[f"{me}_yellows"],
                "reds": df[f"{me}_reds"],
                "opp_cards": df[f"{them}_yellows"]
                + RED_CARD_COUNTS_AS * df[f"{them}_reds"],
                "match_cards": df["cards"],
                "fouls": pd.to_numeric(df.get(f"{me}_fouls"), errors="coerce"),
                "fouls_suffered": pd.to_numeric(
                    df.get(f"{them}_fouls"), errors="coerce"
                ),
            }
        )
        out["cards"] = out["yellows"] + RED_CARD_COUNTS_AS * out["reds"]
        out["booking_points"] = (
            YELLOW_POINTS * out["yellows"] + RED_POINTS * out["reds"]
        )
        return out

    return pd.concat(
        [side("home", "away", "home"), side("away", "home", "away")], ignore_index=True
    )


def _round(v, nd):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def basic(g: pd.DataFrame) -> dict:
    home, away = g[g["venue"] == "home"], g[g["venue"] == "away"]
    out = {
        "matches": len(g),
        "home_matches": len(home),
        "away_matches": len(away),
        "yellows": int(g["yellows"].sum()),
        "reds": int(g["reds"].sum()),
        "yellows_per_match": _round(g["yellows"].mean(), 2),
        "reds_per_match": _round(g["reds"].mean(), 2),
        "cards_per_match": _round(g["cards"].mean(), 2),
        "booking_points_per_match": _round(g["booking_points"].mean(), 1),
        "opponent_cards_per_match": _round(g["opp_cards"].mean(), 2),
        "match_cards_per_match": _round(g["match_cards"].mean(), 2),
        "home_cards_per_match": _round(home["cards"].mean(), 2) if len(home) else None,
        "away_cards_per_match": _round(away["cards"].mean(), 2) if len(away) else None,
    }
    for line in TEAM_LINES:
        out[_line_key("pct_team_over_", line)] = _round(
            100 * (g["cards"] > line).mean(), 1
        )
    out[_line_key("pct_match_over_", MATCH_LINE)] = _round(
        100 * (g["match_cards"] > MATCH_LINE).mean(), 1
    )
    # Fouls committed and suffered, and how often the team's fouls become cards
    f = g[g["fouls"].notna()]
    fouls = f["fouls"].sum() if len(f) else 0
    out["fouls_per_match"] = _round(f["fouls"].mean(), 1) if len(f) else None
    out["fouls_suffered_per_match"] = (
        _round(f["fouls_suffered"].mean(), 1) if len(f) else None
    )
    out["cards_per_10_fouls"] = (
        _round(10 * f["cards"].sum() / fouls, 2) if fouls else None
    )
    return out


def league_team_averages(long: pd.DataFrame) -> dict[str, dict]:
    """Weighted average cards a team receives in each league: overall, at home, away."""
    out = {}
    for league, g in long.groupby("league"):
        h, a = g[g["venue"] == "home"], g[g["venue"] == "away"]
        out[league] = {
            "cards": _wmean(g["cards"], g["weight"]),
            "home": _wmean(h["cards"], h["weight"]),
            "away": _wmean(a["cards"], a["weight"]),
        }
    return out


def _adj(g: pd.DataFrame, prior: float) -> float | None:
    if g.empty:
        return None
    return _shrink(
        _wmean(g["cards"], g["weight"]), float(g["weight"].sum()), prior, PRIOR_MATCHES
    )


def team_rates(
    long: pd.DataFrame, avgs: dict[str, dict]
) -> dict[tuple[str, str], dict]:
    """Adjusted cards received per match: overall, at home and away (recency + shrinkage)."""
    rates = {}
    for (league, team), g in long.groupby(["league", "team"]):
        a = avgs[league]
        rates[(league, team)] = {
            "received": _adj(g, a["cards"]),
            "home": _adj(g[g["venue"] == "home"], a["home"]) or a["home"],
            "away": _adj(g[g["venue"] == "away"], a["away"]) or a["away"],
        }
    return rates


def team_report(long: pd.DataFrame, avgs: dict[str, dict]) -> list[dict]:
    """Per team: a block per season and an overall block with the adjusted rate."""
    rates = team_rates(long, avgs)
    teams = []
    for (league, team), g in long.groupby(["league", "team"]):
        teams.append(
            {
                "team": team,
                "league": league,
                "seasons": [
                    {"season": season_label(int(s)), **basic(sg)}
                    for s, sg in g.groupby("season")
                ],
                "overall": {
                    **basic(g),
                    "adjusted_cards_per_match": _round(
                        rates[(league, team)]["received"], 2
                    ),
                    "adjusted_home_cards_per_match": _round(
                        rates[(league, team)]["home"], 2
                    ),
                    "adjusted_away_cards_per_match": _round(
                        rates[(league, team)]["away"], 2
                    ),
                },
            }
        )
    return teams


def enrich_fixtures(
    fixtures: list[dict], long: pd.DataFrame, avgs: dict[str, dict]
) -> None:
    """Add per-team figures to each projected fixture (in place).

    The match projection is split between the teams in proportion to the home team's
    adjusted cards at home and the away team's adjusted cards away, so the two team
    projections always add up to the match projection.
    """
    rates = team_rates(long, avgs)
    for f in fixtures:
        lg = f["league"]
        a = avgs.get(lg, {"cards": 2.0, "home": 2.0, "away": 2.0})
        hr = rates.get((lg, f["home"]))
        ar = rates.get((lg, f["away"]))
        h_rate = hr["home"] if hr else a["home"]
        a_rate = ar["away"] if ar else a["away"]
        share = h_rate / (h_rate + a_rate) if (h_rate + a_rate) > 0 else 0.5
        f["home_cards_received"] = _round(hr["received"], 2) if hr else None
        f["away_cards_received"] = _round(ar["received"], 2) if ar else None
        f["projected_home_cards"] = _round(f["projected_cards"] * share, 2)
        f["projected_away_cards"] = _round(f["projected_cards"] * (1 - share), 2)
        for side in ("home", "away"):
            ref_id = f.get("referee_id")
            hist = (
                long[
                    (long["referee_id"] == ref_id)
                    & (long["team"] == f[side])
                    & (long["league"] == lg)
                ]
                if ref_id
                else long.iloc[0:0]
            )
            f[f"referee_{side}_matches"] = len(hist)
            f[f"referee_{side}_cards_per_match"] = (
                _round(hist["cards"].mean(), 2) if len(hist) else None
            )
