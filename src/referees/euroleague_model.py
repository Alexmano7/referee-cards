"""EuroLeague player projections and Hot Prediction picks (fouls committed, fouls drawn, FT made).

For each player in an upcoming game:
- Minutes: their average over their last LAST_N games.
- Rates per minute (fouls committed, fouls drawn, free-throw attempts): their own games, recent
  games counting more (weight 0.5 ** (age in days / HALF_LIFE_DAYS)), pulled toward the average
  player in the same role (starter or bench) as if they had PRIOR_MINUTES extra average minutes.
- FT%: their own made / attempted, pulled toward the league FT% by FT_PRIOR_ATTEMPTS.
- Adjustments: venue (home teams foul a little less), the opponent (teams that draw many fouls
  push up an opponent's fouls committed; teams that foul a lot give more free throws), and the
  crew's player foul effect / free-throw factor once the crew is announced (else average).
- Chances: fouls committed and drawn are Poisson; free throws come in pairs, so attempts are
  2 x Poisson(trips) and made ones Binomial(attempts, FT%).

A "typical player in that role" is the same minutes with the role's average rates and the league
FT%, with no venue, opponent or crew effect. A pick is the bet (over/under a line) the model rates
at least MIN_PROB likely and furthest above that typical player. Bookmakers know each player's
habits too, so the typical player is a reference point, not the bookmaker's price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

HALF_LIFE_DAYS = 365
PRIOR_MINUTES = 120
FT_PRIOR_ATTEMPTS = 20
TEAM_PRIOR_GAMES = 8
LAST_N = 10
MIN_EXP_MINUTES = 12.0
MIN_GAMES = 5
MIN_PROB = 0.55
MARGIN = 0.05
N_PICKS = 5
MAX_PER_GAME = 2
# The picks are the bets furthest from a typical player, which also selects some noise: in the
# walk-forward backtest (docs/el_backtest.md) they won 65.6% when the model said 70.6%. Their shown
# chance (and the "Bet at" odds) is therefore pulled toward 50% by this factor:
# 0.5 + 0.75 x (70.6% - 50%) = 65.5%.
PICK_SHRINK = 0.75


def pick_chance(p: float) -> float:
    """A pick's chance after the backtest correction (see PICK_SHRINK)."""
    return 0.5 + PICK_SHRINK * (p - 0.5)


MARKETS = {
    "fouls_committed": ("fouls", [1.5, 2.5, 3.5]),
    "fouls_drawn": ("fouls drawn", [1.5, 2.5, 3.5, 4.5]),
    "ftm": ("free throws made", [1.5, 2.5, 3.5, 4.5]),
}
STATS = {"fouls_committed": "fc", "fouls_drawn": "fd", "fta": "fta"}


def min_odds(p: float, margin: float = MARGIN) -> float:
    """Smallest decimal odds worth taking: fair odds plus the margin, rounded up."""
    return math.ceil(100 * (1 + margin) / p) / 100


# --- chances ---------------------------------------------------------------------------


def poisson_pmf(mu: float, n_max: int = 30) -> list[float]:
    out, p = [], math.exp(-mu)
    for k in range(n_max + 1):
        out.append(p)
        p *= mu / (k + 1)
    return out


def ftm_pmf(mu_fta: float, pct: float, n_max: int = 30) -> list[float]:
    """Made free throws: attempts = 2 x Poisson(trips), made ~ Binomial(attempts, pct)."""
    out = [0.0] * (n_max + 1)
    for trips, pt in enumerate(poisson_pmf(mu_fta / 2, n_max // 2)):
        n = 2 * trips
        for made in range(n + 1):
            if made <= n_max:
                out[made] += (
                    pt * math.comb(n, made) * pct**made * (1 - pct) ** (n - made)
                )
    return out


def p_over(pmf: list[float], line: float) -> float:
    """P(X > line) for a half line."""
    return float(sum(pmf[math.floor(line) + 1 :]))


# --- fitting ---------------------------------------------------------------------------


@dataclass
class Fit:
    as_of: str
    players: pd.DataFrame  # one row per player_id: role, team, minutes, rates, ft_pct
    role_rates: dict  # role -> {"fc","fd","fta"} per minute
    league_ft_pct: float
    teams: (
        pd.DataFrame
    )  # one row per team code: committed/drawn/fta/fta_allowed factors
    venue: dict  # side -> {"fc","fd","fta"} factor
    rosters: dict = field(
        default_factory=dict
    )  # team code -> player_ids seen in its last 3 games


def _with_dates(games: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    keys = games[["season", "game", "date", "home_code", "away_code"]]
    return players.merge(keys, on=["season", "game"], how="inner")


def fit(games: pd.DataFrame, players: pd.DataFrame, as_of: str) -> Fit:
    """Rates from games played before `as_of` (ISO date)."""
    g = games[games["date"] < as_of].copy()
    p = _with_dates(g, players)
    p = p[p["date"] < as_of].copy()
    age = (pd.Timestamp(as_of) - pd.to_datetime(p["date"])).dt.days
    p["w"] = 0.5 ** (age / HALF_LIFE_DAYS)
    p["role"] = p["starter"].map({True: "starter", False: "bench"})

    role_rates = {}
    for role, x in p.groupby("role"):
        wm = (x["w"] * x["minutes"]).sum()
        role_rates[role] = {
            s: float((x["w"] * x[c]).sum() / wm) for c, s in STATS.items()
        }
    league_ft_pct = float(p["ftm"].sum() / max(p["fta"].sum(), 1))

    p = p.sort_values(["date", "game"])
    rows = []
    for pid, x in p.groupby("player_id", sort=False):
        last = x.tail(LAST_N)
        role = "starter" if last["starter"].mean() >= 0.5 else "bench"
        prior = role_rates[role]
        wm = float((x["w"] * x["minutes"]).sum())
        rate = {
            s: (float((x["w"] * x[c]).sum()) + PRIOR_MINUTES * prior[s])
            / (wm + PRIOR_MINUTES)
            for c, s in STATS.items()
        }
        fta, ftm = float(x["fta"].sum()), float(x["ftm"].sum())
        season_now = x["season"].iloc[-1]
        rows.append(
            {
                "player_id": pid,
                "player": x["player"].iloc[-1],
                "team": x["team"].iloc[-1],
                "role": role,
                "games_season": int((x["season"] == season_now).sum()),
                "exp_minutes": float(last["minutes"].mean()),
                "ft_pct": (ftm + FT_PRIOR_ATTEMPTS * league_ft_pct)
                / (fta + FT_PRIOR_ATTEMPTS),
                **{f"rate_{s}": v for s, v in rate.items()},
            }
        )
    fit_players = pd.DataFrame(rows).set_index("player_id")

    # Team factors: what a team commits, draws, shoots and allows, against the league average.
    ga = g.copy()
    age_g = (pd.Timestamp(as_of) - pd.to_datetime(ga["date"])).dt.days
    ga["w"] = 0.5 ** (age_g / HALF_LIFE_DAYS)
    sides = []
    for side, opp in (("home", "away"), ("away", "home")):
        sides.append(
            pd.DataFrame(
                {
                    "team": ga[f"{side}_code"],
                    "w": ga["w"],
                    "committed": ga[f"{side}_fouls"],
                    "drawn": ga[f"{opp}_fouls"],
                    "fta": ga[f"{side}_fta"],
                    "fta_allowed": ga[f"{opp}_fta"],
                }
            )
        )
    tg = pd.concat(sides, ignore_index=True)
    lg = {
        c: float((tg[c] * tg["w"]).sum() / tg["w"].sum())
        for c in ("committed", "drawn", "fta", "fta_allowed")
    }
    trows = []
    for team, x in tg.groupby("team"):
        n = float(x["w"].sum())
        trows.append(
            {
                "team": team,
                **{
                    f"{c}_factor": (
                        (float((x[c] * x["w"]).sum()) + TEAM_PRIOR_GAMES * lg[c])
                        / (n + TEAM_PRIOR_GAMES)
                    )
                    / lg[c]
                    for c in lg
                },
            }
        )
    teams = pd.DataFrame(trows).set_index("team")

    team_fouls = (ga["home_fouls"].mean() + ga["away_fouls"].mean()) / 2
    team_fta = (ga["home_fta"].mean() + ga["away_fta"].mean()) / 2
    venue = {
        "home": {
            "fc": ga["home_fouls"].mean() / team_fouls,
            "fd": ga["away_fouls"].mean() / team_fouls,
            "fta": ga["home_fta"].mean() / team_fta,
        },
        "away": {
            "fc": ga["away_fouls"].mean() / team_fouls,
            "fd": ga["home_fouls"].mean() / team_fouls,
            "fta": ga["away_fta"].mean() / team_fta,
        },
    }

    rosters = {}
    for team, x in p.groupby("team"):
        recent = (
            x[["season", "game", "date"]].drop_duplicates().sort_values("date").tail(3)
        )
        ids = x.merge(recent, on=["season", "game", "date"])["player_id"]
        rosters[team] = sorted(set(ids))
    return Fit(as_of, fit_players, role_rates, league_ft_pct, teams, venue, rosters)


# --- projections -----------------------------------------------------------------------


def crew_adjustment(crew: list[str], referees: list[dict]) -> dict:
    """The crew's mean player foul effects and free-throw factor (1 = average or unknown)."""
    from referees.appointments import match_referee

    candidates = list(enumerate(r["referee"] for r in referees))
    found = []
    for name in crew:
        i = match_referee(name, candidates)
        if i is not None:
            found.append(referees[i])
    k = len(crew) or 1

    def mean(get):
        return (sum(get(r) for r in found) + (k - len(found))) / k

    return {
        "known": len(crew) == 3,
        "matched": len(found),
        "fc": mean(lambda r: r["player_foul_effect"]["committed"]),
        "fd": mean(lambda r: r["player_foul_effect"]["drawn"]),
        "fta": mean(lambda r: r["factor"]["fta_total"]),
    }


def project_player(f: Fit, pid: str, side: str, opp: str, crew: dict) -> dict | None:
    """Expected minutes, fouls, fouls drawn and free throws for one player in one game."""
    if pid not in f.players.index:
        return None
    pl = f.players.loc[pid]
    mins = float(pl["exp_minutes"])
    o = f.teams.loc[opp] if opp in f.teams.index else None
    opp_draws = float(o["drawn_factor"]) if o is not None else 1.0
    opp_commits = float(o["committed_factor"]) if o is not None else 1.0
    opp_allows = float(o["fta_allowed_factor"]) if o is not None else 1.0
    v = f.venue[side]
    mu = {
        "fc": pl["rate_fc"] * mins * opp_draws * v["fc"] * crew["fc"],
        "fd": pl["rate_fd"] * mins * opp_commits * v["fd"] * crew["fd"],
        "fta": pl["rate_fta"] * mins * opp_allows * v["fta"] * crew["fta"],
    }
    typ = f.role_rates[pl["role"]]
    return {
        "player_id": pid,
        "player": pl["player"],
        "team": pl["team"],
        "role": pl["role"],
        "games_season": int(pl["games_season"]),
        "exp_minutes": mins,
        "ft_pct": float(pl["ft_pct"]),
        "mu": {k: float(x) for k, x in mu.items()},
        "mu_ftm": float(mu["fta"] * pl["ft_pct"]),
        "typical_mu": {s: typ[s] * mins for s in ("fc", "fd", "fta")},
        "factors": {
            "opp_draws": opp_draws,
            "opp_commits": opp_commits,
            "opp_allows": opp_allows,
        },
        "rate_fc": float(pl["rate_fc"]),
        "typical_rate_fc": typ["fc"],
    }


def distributions(proj: dict, league_ft_pct: float) -> tuple[dict, dict]:
    """(model, typical) probability tables per market."""
    model = {
        "fouls_committed": poisson_pmf(proj["mu"]["fc"]),
        "fouls_drawn": poisson_pmf(proj["mu"]["fd"]),
        "ftm": ftm_pmf(proj["mu"]["fta"], proj["ft_pct"]),
    }
    typical = {
        "fouls_committed": poisson_pmf(proj["typical_mu"]["fc"]),
        "fouls_drawn": poisson_pmf(proj["typical_mu"]["fd"]),
        "ftm": ftm_pmf(proj["typical_mu"]["fta"], league_ft_pct),
    }
    return model, typical


def candidates(proj: dict, league_ft_pct: float) -> list[dict]:
    """Every over/under bet for this player with the model's and the typical chance."""
    model, typical = distributions(proj, league_ft_pct)
    out = []
    for market, (_, lines) in MARKETS.items():
        for line in lines:
            pm, pt = p_over(model[market], line), p_over(typical[market], line)
            out.append(
                {
                    "market": market,
                    "line": line,
                    "selection": "over",
                    "p": pm,
                    "typical": pt,
                }
            )
            out.append(
                {
                    "market": market,
                    "line": line,
                    "selection": "under",
                    "p": 1 - pm,
                    "typical": 1 - pt,
                }
            )
    return out


def bet_label(c: dict) -> str:
    return f"{c['selection']} {c['line']} {MARKETS[c['market']][0]}"


def best_candidate(proj: dict, league_ft_pct: float) -> dict | None:
    if proj["exp_minutes"] < MIN_EXP_MINUTES or proj["games_season"] < MIN_GAMES:
        return None
    ok = [c for c in candidates(proj, league_ft_pct) if c["p"] >= MIN_PROB]
    if not ok:
        return None
    best = max(ok, key=lambda c: c["p"] - c["typical"])
    return {**best, "score": best["p"] - best["typical"]}


def why(proj: dict, c: dict, crew: dict) -> str:
    m = c["market"]
    parts = [f"{proj['exp_minutes']:.0f} min a game ({proj['role']})"]
    if m == "fouls_committed":
        parts.append(
            f"{36 * proj['rate_fc']:.1f} fouls per 36 min vs {36 * proj['typical_rate_fc']:.1f} for a typical {proj['role']}"
        )
        parts.append(f"opponent draws x{proj['factors']['opp_draws']:.2f}")
    elif m == "fouls_drawn":
        parts.append(
            f"expects {proj['mu']['fd']:.1f} fouls drawn vs {proj['typical_mu']['fd']:.1f} typical"
        )
        parts.append(f"opponent fouls x{proj['factors']['opp_commits']:.2f}")
    else:
        parts.append(
            f"{proj['mu']['fta']:.1f} attempts at {100 * proj['ft_pct']:.0f}% vs {proj['typical_mu']['fta']:.1f} typical"
        )
        parts.append(f"opponent allows x{proj['factors']['opp_allows']:.2f}")
    key = {"fouls_committed": "fc", "fouls_drawn": "fd", "ftm": "fta"}[m]
    parts.append(f"crew x{crew[key]:.2f}" if crew["known"] else "crew not announced")
    return "; ".join(parts)


def game_players(
    f: Fit, game: dict, crew: dict, played: set[str] | None = None
) -> list[dict]:
    """Projections for both teams' players in one game.

    `played`: the player ids who actually played (backtest); otherwise the players seen in each
    team's last three games.
    """
    out = []
    for side, team, opp in (
        ("home", game["home_code"], game["away_code"]),
        ("away", game["away_code"], game["home_code"]),
    ):
        ids = (
            played_for(team, played, f)
            if played is not None
            else f.rosters.get(team, [])
        )
        for pid in ids:
            proj = project_player(f, pid, side, opp, crew)
            if proj and proj["team"] == team:
                out.append({**proj, "side": side})
    return out


def played_for(team: str, played: set[str], f: Fit) -> list[str]:
    return [
        pid
        for pid in played
        if pid in f.players.index and f.players.loc[pid, "team"] == team
    ]


def pick(
    games_with_players: list[tuple[dict, dict, list[dict]]],
    league_ft_pct: float,
    n: int = N_PICKS,
) -> list[dict]:
    """Top picks across games: best bet per player, at most MAX_PER_GAME per game,
    games with an announced crew first, then the biggest shift from a typical player."""
    scored = []
    for game, crew, projs in games_with_players:
        for proj in projs:
            c = best_candidate(proj, league_ft_pct)
            if c:
                scored.append((crew["known"], c["score"], game, crew, proj, c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out, per_game = [], {}
    for known, score, game, crew, proj, c in scored:
        gk = (game["season"], game["game"])
        if per_game.get(gk, 0) >= MAX_PER_GAME:
            continue
        per_game[gk] = per_game.get(gk, 0) + 1
        out.append({"game": game, "crew": crew, "proj": proj, "bet": c})
        if len(out) == n:
            break
    return out


def team_expectations(f: Fit, game: dict, crew_factors: dict, league: dict) -> dict:
    """Expected team fouls and FT made per side: league side average x team x opponent x crew."""
    out = {}
    for side, team, opp in (
        ("home", game["home_code"], game["away_code"]),
        ("away", game["away_code"], game["home_code"]),
    ):
        t = f.teams.loc[team] if team in f.teams.index else None
        o = f.teams.loc[opp] if opp in f.teams.index else None
        tf = lambda row, c: float(row[c]) if row is not None else 1.0
        out[f"{side}_fouls"] = (
            league[f"fouls_{side}"]
            * tf(t, "committed_factor")
            * tf(o, "drawn_factor")
            * crew_factors.get(f"fouls_{side}", 1.0)
        )
        out[f"{side}_ftm"] = (
            league[f"ftm_{side}"]
            * tf(t, "fta_factor")
            * tf(o, "fta_allowed_factor")
            * crew_factors.get(f"ftm_{side}", 1.0)
        )
    return out
