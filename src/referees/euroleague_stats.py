"""EuroLeague referee statistics: per referee, per crew of three, and the player foul effect.

Method (same ideas as the football card stats):
- Recency: a game from n seasons ago counts SEASON_DECAY**n (1, 0.6, 0.36).
- Shrinkage: each referee's rate is pulled toward the league average as if they had
  PRIOR_GAMES extra average games, so a handful of unusual games can't make them extreme.
- Every game counts for all three referees of its crew. A crew always works together, so one
  referee's effect can't be fully separated from their partners'; it averages out over many
  games with different partners.
- Player foul effect: each player's expected fouls in a game = their usual fouls per minute that
  season (without this game, pulled toward the league rate by PLAYER_PRIOR_MINUTES) x minutes
  played. A referee's effect = actual / expected over their games (shrunk toward 1). 1.10 means
  players commit 10% more fouls than usual with that referee. The same for fouls drawn.
- Crew figures: league average x the mean of the three referees' factors. If the same three
  have worked together, their own record is shown as well (usually a small sample).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from referees.euroleague import ROUND_NAMES, season_label

SEASON_DECAY = 0.6
PRIOR_GAMES = 8
PLAYER_PRIOR_MINUTES = 100

# Per-game figures, all from the home/away team totals.
METRICS = {
    "fouls_total": "Fouls per game (both teams)",
    "fouls_home": "Fouls by the home team",
    "fouls_away": "Fouls by the away team",
    "ftm_total": "Free throws made (both teams)",
    "ftm_home": "Free throws made by the home team",
    "ftm_away": "Free throws made by the away team",
    "fta_total": "Free throws attempted (both teams)",
    "fta_home": "Free throws attempted by the home team",
    "fta_away": "Free throws attempted by the away team",
}


def game_metrics(games: pd.DataFrame) -> pd.DataFrame:
    """Add the per-game metrics and the recency weight to the games table."""
    g = games.copy()
    g["fouls_total"] = g["home_fouls"] + g["away_fouls"]
    g["fouls_home"], g["fouls_away"] = g["home_fouls"], g["away_fouls"]
    g["ftm_total"] = g["home_ftm"] + g["away_ftm"]
    g["ftm_home"], g["ftm_away"] = g["home_ftm"], g["away_ftm"]
    g["fta_total"] = g["home_fta"] + g["away_fta"]
    g["fta_home"], g["fta_away"] = g["home_fta"], g["away_fta"]
    order = {s: i for i, s in enumerate(sorted(g["season"].unique()))}
    latest = max(order.values())
    g["weight"] = g["season"].map(lambda s: SEASON_DECAY ** (latest - order[s]))
    return g


def player_expectations(players: pd.DataFrame) -> pd.DataFrame:
    """Each player-game's expected fouls committed and drawn (leave-this-game-out rates)."""
    p = players.copy()
    league = {
        col: p[col].sum() / p["minutes"].sum()
        for col in ("fouls_committed", "fouls_drawn")
    }
    totals = p.groupby(["season", "player_id"])[
        ["minutes", "fouls_committed", "fouls_drawn"]
    ].transform("sum")
    for col, short in (("fouls_committed", "committed"), ("fouls_drawn", "drawn")):
        other_fouls = totals[col] - p[col]
        other_minutes = totals["minutes"] - p["minutes"]
        rate = (other_fouls + PLAYER_PRIOR_MINUTES * league[col]) / (
            other_minutes + PLAYER_PRIOR_MINUTES
        )
        p[f"expected_{short}"] = rate * p["minutes"]
    return p


def _by_referee(g: pd.DataFrame) -> pd.DataFrame:
    """One row per (game, referee)."""
    rows = g.explode("referees").rename(columns={"referees": "referee"})
    return rows[rows["referee"].notna() & (rows["referee"] != "")]


def build(
    games: pd.DataFrame, players: pd.DataFrame, problems: list[str] | None = None
) -> dict:
    """The full EuroLeague referee report (written to data/euroleague_referees.json)."""
    g = game_metrics(games)
    p = player_expectations(players)
    per_game = (
        p.groupby(["season", "game"])[
            ["fouls_committed", "expected_committed", "fouls_drawn", "expected_drawn"]
        ]
        .sum()
        .reset_index()
    )
    g = g.merge(per_game, on=["season", "game"], how="left")

    w = g["weight"]
    league = {m: float((g[m] * w).sum() / w.sum()) for m in METRICS}
    exp_c = float((g["expected_committed"] * w).sum() / w.sum())
    exp_d = float((g["expected_drawn"] * w).sum() / w.sum())
    league_by_season = {
        season_label(s): {
            "games": len(x),
            **{m: round(float(x[m].mean()), 2) for m in METRICS},
        }
        for s, x in g.groupby("season")
    }

    refs = []
    for name, x in _by_referee(g).groupby("referee"):
        wx = x["weight"]
        n_eff = float(wx.sum())
        adjusted = {
            m: (float((x[m] * wx).sum()) + PRIOR_GAMES * league[m])
            / (n_eff + PRIOR_GAMES)
            for m in METRICS
        }
        effect_c = (float((x["fouls_committed"] * wx).sum()) + PRIOR_GAMES * exp_c) / (
            float((x["expected_committed"] * wx).sum()) + PRIOR_GAMES * exp_c
        )
        effect_d = (float((x["fouls_drawn"] * wx).sum()) + PRIOR_GAMES * exp_d) / (
            float((x["expected_drawn"] * wx).sum()) + PRIOR_GAMES * exp_d
        )
        refs.append(
            {
                "referee": name,
                "games": len(x),
                "games_by_season": {
                    season_label(s): int(n)
                    for s, n in x.groupby("season").size().items()
                },
                "playoff_games": int((x["round"] != "RS").sum()),
                "raw": {m: round(float(x[m].mean()), 2) for m in METRICS},
                "adjusted": {m: round(v, 2) for m, v in adjusted.items()},
                "factor": {m: round(adjusted[m] / league[m], 3) for m in METRICS},
                "player_foul_effect": {
                    "committed": round(effect_c, 3),
                    "drawn": round(effect_d, 3),
                },
                "last_game": str(x["date"].max()),
            }
        )
    refs.sort(key=lambda r: (-r["games"], r["referee"]))

    players_out = player_table(p)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "competition": "EuroLeague",
        "seasons": [season_label(s) for s in sorted(g["season"].unique())],
        "method": {
            "season_decay": SEASON_DECAY,
            "prior_games": PRIOR_GAMES,
            "player_prior_minutes": PLAYER_PRIOR_MINUTES,
            "notes": __doc__.strip(),
        },
        "metrics": METRICS,
        "rounds": ROUND_NAMES,
        "league_average": {m: round(v, 2) for m, v in league.items()},
        "league_by_season": league_by_season,
        "games": len(g),
        "referees": refs,
        "players": players_out,
        "data_quality": {"problems": problems or []},
    }


def player_table(p: pd.DataFrame) -> list[dict]:
    """Per player and season: games, minutes, free throws and fouls per game."""
    out = []
    for (season, pid), x in p.groupby(["season", "player_id"]):
        n = len(x)
        fta = int(x["fta"].sum())
        out.append(
            {
                "season": season_label(season),
                "player_id": pid,
                "player": x["player"].iloc[-1],
                "team": x["team"].iloc[-1],
                "games": n,
                "minutes_per_game": round(float(x["minutes"].mean()), 1),
                "ftm_per_game": round(float(x["ftm"].mean()), 2),
                "fta_per_game": round(float(x["fta"].mean()), 2),
                "ft_pct": round(100 * x["ftm"].sum() / fta, 1) if fta else None,
                "fouls_committed_per_game": round(
                    float(x["fouls_committed"].mean()), 2
                ),
                "fouls_drawn_per_game": round(float(x["fouls_drawn"].mean()), 2),
            }
        )
    return out


# --- Crews -------------------------------------------------------------------------------


def find_referee(name: str, report: dict) -> dict | None:
    """The one known referee an announced name refers to (forgiving: initials, surname only)."""
    from referees.appointments import match_referee

    candidates = list(enumerate(r["referee"] for r in report["referees"]))
    i = match_referee(name, candidates)
    return report["referees"][i] if i is not None else None


def crew_figures(
    names: list[str], report: dict, games: pd.DataFrame | None = None
) -> dict:
    """Expected per-game figures for a crew, from each referee's factors.

    Unknown names (new referees) count as average (factor 1) and are listed in `unknown`.
    With `games`, the crew's own record together is added (same three referees, any order).
    """
    found, unknown = [], []
    for n in names:
        r = find_referee(n, report)
        (found.append(r) if r else unknown.append(n))
    league = report["league_average"]
    k = len(names) or 1

    def mean_factor(key, sub):
        return (sum(r[key][sub] for r in found) + (k - len(found))) / k

    out = {
        "referees": [r["referee"] for r in found],
        "unknown": unknown,
        "expected": {
            m: round(league[m] * mean_factor("factor", m), 2) for m in METRICS
        },
        "factor": {m: round(mean_factor("factor", m), 3) for m in METRICS},
        "player_foul_effect": {
            s: round(mean_factor("player_foul_effect", s), 3)
            for s in ("committed", "drawn")
        },
        "league_average": league,
        "together": None,
    }
    if games is not None and len(found) == len(names) == 3:
        crew = {r["referee"] for r in found}
        g = game_metrics(games)
        same = g[g["referees"].map(lambda refs: set(refs) == crew)]
        if len(same):
            out["together"] = {
                "games": len(same),
                **{m: round(float(same[m].mean()), 2) for m in METRICS},
            }
    return out
