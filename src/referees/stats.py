"""The stats engine: cards per referee, adjusted rates and projections for upcoming fixtures.

Definitions (all per match, both teams together):
- yellows = HY + AY, reds = HR + AR, cards = yellows + reds
- booking points = 10 per yellow + 25 per red (the usual bookmaker convention)

Accuracy improvements:
- Recency weighting: a match from n seasons ago counts SEASON_DECAY**n (1, 0.6, 0.36).
- Shrinkage: a referee's weighted rate is pulled toward their league average as if they had
  PRIOR_MATCHES extra matches at that average, so 3 wild matches can't top the table.
- Over-line percentages: share of matches with more than 3.5 / 4.5 / 5.5 cards.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd

from referees.appointments import apply_appointments
from referees.config import (
    LEAGUES_BY_KEY,
    RED_CARD_COUNTS_AS,
    current_season_start_year,
    season_label,
)

CARD_COUNT_COLUMNS = ["home_yellows", "away_yellows", "home_reds", "away_reds"]
YELLOW_POINTS = 10
RED_POINTS = 25
SEASON_DECAY = 0.6
PRIOR_MATCHES = 8
CARD_LINES = [3.5, 4.5, 5.5]
UPCOMING_DAYS = 14

METHOD_NOTES = {
    "cards": "yellows + reds per match, both teams",
    "booking_points": f"{YELLOW_POINTS} per yellow, {RED_POINTS} per red",
    "season_decay": SEASON_DECAY,
    "prior_matches": PRIOR_MATCHES,
    "card_lines": CARD_LINES,
    "projection": "probability model (model.py): league x referee x team x opponent factors "
    "fitted together, negative binomial probabilities; settings tuned by `referees backtest`",
    "england_second_yellow": "football-data.co.uk: in English and Scottish files a second-yellow "
    "dismissal counts as 1 red only; in the other leagues it counts as 1 yellow + 1 red. "
    "Compare Premier League and Scottish numbers with care across leagues.",
    "referee_sources": "PL/PD/BL1/SA/FL1/PPL: football-data.org; SCO: the card file; "
    "GR/BEL: referee.observer history (from mid-2025).",
}


def _line_key(line: float) -> str:
    return "pct_over_" + str(line).replace(".", "_")


def add_match_metrics(
    matches: pd.DataFrame, current_season: int | None = None
) -> pd.DataFrame:
    current_season = (
        current_season if current_season is not None else current_season_start_year()
    )
    df = matches.copy()
    df["yellows"] = df["home_yellows"] + df["away_yellows"]
    df["reds"] = df["home_reds"] + df["away_reds"]
    df["cards"] = df["yellows"] + RED_CARD_COUNTS_AS * df["reds"]
    df["home_cards"] = df["home_yellows"] + RED_CARD_COUNTS_AS * df["home_reds"]
    df["away_cards"] = df["away_yellows"] + RED_CARD_COUNTS_AS * df["away_reds"]
    df["booking_points"] = YELLOW_POINTS * df["yellows"] + RED_POINTS * df["reds"]
    if "home_fouls" in df.columns:
        df["fouls"] = pd.to_numeric(df["home_fouls"], errors="coerce") + pd.to_numeric(
            df["away_fouls"], errors="coerce"
        )
    else:
        df["fouls"] = float("nan")
    df["weight"] = SEASON_DECAY ** (current_season - df["season"])
    return df


def _wmean(values: pd.Series, weights: pd.Series) -> float:
    return float((values * weights).sum() / weights.sum())


def _shrink(rate: float, n_eff: float, prior: float, k: float = PRIOR_MATCHES) -> float:
    return (n_eff * rate + k * prior) / (n_eff + k)


def _basic(group: pd.DataFrame) -> dict:
    n = len(group)
    out = {
        "matches": n,
        "yellows": int(group["yellows"].sum()),
        "reds": int(group["reds"].sum()),
        "cards": int(group["cards"].sum()),
        "booking_points": int(group["booking_points"].sum()),
        "cards_per_match": round(group["cards"].mean(), 2),
        "yellows_per_match": round(group["yellows"].mean(), 2),
        "reds_per_match": round(group["reds"].mean(), 3),
        "booking_points_per_match": round(group["booking_points"].mean(), 1),
    }
    for line in CARD_LINES:
        out[_line_key(line)] = round(100 * (group["cards"] > line).mean(), 1)
    # Home/away split: does this referee (or league) book away teams more?
    if "home_cards" in group.columns:
        hc, ac = group["home_cards"].sum(), group["away_cards"].sum()
        out["home_cards_per_match"] = round(group["home_cards"].mean(), 2)
        out["away_cards_per_match"] = round(group["away_cards"].mean(), 2)
        out["away_share_pct"] = round(100 * ac / (hc + ac), 1) if (hc + ac) else None
    # Fouls: how many are whistled, and how often a foul becomes a card (strictness)
    if "fouls" in group.columns:
        with_fouls = group[group["fouls"].notna()]
        fouls = with_fouls["fouls"].sum()
        out["fouls_per_match"] = (
            round(with_fouls["fouls"].mean(), 1) if len(with_fouls) else None
        )
        out["cards_per_10_fouls"] = (
            round(10 * with_fouls["cards"].sum() / fouls, 2) if fouls else None
        )
    return out


def league_averages(df: pd.DataFrame) -> dict[str, dict]:
    """Weighted league averages used as the 'prior' for shrinkage and projections."""
    out = {}
    for league, g in df.groupby("league"):
        avg = {
            "cards": _wmean(g["cards"], g["weight"]),
            "booking_points": _wmean(g["booking_points"], g["weight"]),
        }
        for line in CARD_LINES:
            avg[_line_key(line)] = 100 * _wmean(
                (g["cards"] > line).astype(float), g["weight"]
            )
        out[league] = avg
    return out


def _adjusted(group: pd.DataFrame, league_avg: dict[str, dict]) -> dict:
    """Recency-weighted rates shrunk toward the (weighted) league average."""
    w = group["weight"]
    n_eff = float(w.sum())
    lw = group.groupby("league")["weight"].sum() / n_eff  # share of weight per league
    prior = {
        k: sum(lw[lg] * league_avg[lg][k] for lg in lw.index)
        for k in next(iter(league_avg.values()))
    }
    out = {
        "effective_matches": round(n_eff, 1),
        "weighted_cards_per_match": round(_wmean(group["cards"], w), 2),
        "adjusted_cards_per_match": round(
            _shrink(_wmean(group["cards"], w), n_eff, prior["cards"]), 2
        ),
        "adjusted_booking_points_per_match": round(
            _shrink(_wmean(group["booking_points"], w), n_eff, prior["booking_points"]),
            1,
        ),
    }
    for line in CARD_LINES:
        key = _line_key(line)
        rate = 100 * _wmean((group["cards"] > line).astype(float), w)
        out["adjusted_" + key] = round(_shrink(rate, n_eff, prior[key]), 1)
    return out


def team_factors(
    df: pd.DataFrame, league_avg: dict[str, dict]
) -> dict[tuple[str, str], dict]:
    """How card-heavy each team's matches are compared with its league (1.0 = average)."""
    long = pd.concat(
        [
            df[["league", "api_home", "cards", "booking_points", "weight"]].rename(
                columns={"api_home": "team"}
            ),
            df[["league", "api_away", "cards", "booking_points", "weight"]].rename(
                columns={"api_away": "team"}
            ),
        ]
    )
    out = {}
    for (league, team), g in long.groupby(["league", "team"]):
        n_eff = float(g["weight"].sum())
        avg = league_avg[league]
        out[(league, team)] = {
            "cards": _shrink(_wmean(g["cards"], g["weight"]), n_eff, avg["cards"])
            / avg["cards"],
            "booking_points": _shrink(
                _wmean(g["booking_points"], g["weight"]), n_eff, avg["booking_points"]
            )
            / avg["booking_points"],
        }
    return out


def project_fixtures(
    upcoming: pd.DataFrame,
    referee_rates: dict[int, dict],
    league_avg: dict[str, dict],
    factors: dict[tuple[str, str], dict],
) -> list[dict]:
    fixtures = []
    for r in upcoming.itertuples():
        hf = factors.get((r.league, r.api_home), {"cards": 1.0, "booking_points": 1.0})
        af = factors.get((r.league, r.api_away), {"cards": 1.0, "booking_points": 1.0})
        ref_id = None if pd.isna(r.referee_id) else int(r.referee_id)
        named = isinstance(r.referee, str)
        rates = referee_rates.get(ref_id)
        if rates:
            base_c, base_bp, basis = (
                rates["adjusted_cards_per_match"],
                rates["adjusted_booking_points_per_match"],
                "referee",
            )
        else:
            base_c, base_bp = (
                league_avg[r.league]["cards"],
                league_avg[r.league]["booking_points"],
            )
            basis = (
                "league average (referee has no history)"
                if named
                else "league average (referee not appointed yet)"
            )
        source = getattr(r, "referee_source", None)
        url = getattr(r, "source_url", None)
        fixtures.append(
            {
                "league": r.league,
                "date": str(r.date),
                "utc_kickoff": r.utc_kickoff,
                "matchday": None if pd.isna(r.matchday) else int(r.matchday),
                "home": r.api_home,
                "away": r.api_away,
                "referee_id": ref_id,
                "referee": r.referee if named else None,
                "referee_source": source
                if isinstance(source, str) and source
                else None,
                "source_url": url if isinstance(url, str) and url else None,
                "projected_cards": round(base_c * (hf["cards"] + af["cards"]) / 2, 2),
                "projected_booking_points": round(
                    base_bp * (hf["booking_points"] + af["booking_points"]) / 2, 1
                ),
                "basis": basis,
            }
        )
    return fixtures


def apply_model(fixtures: list[dict], df: pd.DataFrame, today: date, league_avg: dict):
    """Replace each fixture's projection with the probability model's (in place).

    The model (model.py) estimates referee, team and opponent effects together and gives
    probabilities for the card lines. Settings come from config/model_params.json (tuned by
    `referees backtest`).
    """
    from referees.model import TEAM_LINES, fit, load_params, side_observations

    model = fit(side_observations(df), as_of=today + timedelta(days=1), **load_params())
    for f in fixtures:
        p = model.predict(f["league"], f["home"], f["away"], f.get("referee_id"))
        mu = p["expected_cards"]
        avg = league_avg.get(f["league"], {"cards": 1.0, "booking_points": 10.0})
        f["projected_cards"] = round(mu, 2)
        f["projected_home_cards"] = round(p["expected_home_cards"], 2)
        f["projected_away_cards"] = round(p["expected_away_cards"], 2)
        f["projected_booking_points"] = round(
            mu * avg["booking_points"] / avg["cards"], 1
        )
        for line in CARD_LINES:
            k = str(line).replace(".", "_")
            prob = p[f"prob_over_{k}"]
            f[f"prob_over_{k}"] = round(100 * prob, 1)
            f[f"fair_over_{k}"] = round(1 / prob, 2) if prob > 0.001 else None
            f[f"fair_under_{k}"] = round(1 / (1 - prob), 2) if prob < 0.999 else None
        for side in ("home", "away"):
            for line in TEAM_LINES:
                k = str(line).replace(".", "_")
                f[f"{side}_prob_over_{k}"] = round(100 * p[f"{side}_prob_over_{k}"], 1)
        f["distribution"] = [round(x, 4) for x in p["distribution"][:11]] + [
            round(sum(p["distribution"][11:]), 4)
        ]
        ref_id = f.get("referee_id")
        f["referee_factor"] = (
            round(model.referee.get(int(ref_id), 1.0), 3) if ref_id else None
        )
        f["home_receives_factor"] = round(
            model.receives.get((f["league"], f["home"]), 1.0), 3
        )
        f["away_receives_factor"] = round(
            model.receives.get((f["league"], f["away"]), 1.0), 3
        )
        f["home_draws_factor"] = round(
            model.draws.get((f["league"], f["home"]), 1.0), 3
        )
        f["away_draws_factor"] = round(
            model.draws.get((f["league"], f["away"]), 1.0), 3
        )
        if f["basis"] == "referee":
            f["basis"] = "model: referee, both teams and league"
        elif f.get("referee"):
            f["basis"] = "model: both teams and league (referee has no history)"
        else:
            f["basis"] = "model: both teams and league (referee not appointed yet)"
    return model


def hot_picks(
    fixtures: list[dict], df: pd.DataFrame, model, today: date, referees: list[dict]
) -> tuple[list[dict], list[dict]]:
    """The 2-3 card bets where the model differs most from a typical match, and a safe
    treble (3 legs, combined fair odds of about 2.00). See picks.py."""
    from referees.model import fit, load_params, side_observations
    from referees.picks import (
        N_PICKS,
        WINDOW_DAYS,
        best_candidate,
        best_treble,
        bet_label,
        min_odds,
        treble_legs,
    )

    params = {
        **load_params(),
        "k_referee": 1e9,
        "k_team": 1e9,
    }  # league-only: a typical match
    typical = fit(side_observations(df), as_of=today + timedelta(days=1), **params)
    n_matches = {r["id"]: r["overall"]["matches"] for r in referees}
    horizon = str(today + timedelta(days=WINDOW_DAYS))
    scored = []
    treble_pool = []
    for i, f in enumerate(fixtures):
        if f["date"] > horizon:
            continue
        ref_id = f.get("referee_id")
        mp = model.predict(f["league"], f["home"], f["away"], ref_id)
        tp = typical.predict(f["league"], f["home"], f["away"], None)
        treble_pool.append(
            (i, ref_id is not None and ref_id in n_matches, treble_legs(mp, tp))
        )
        pick = best_candidate(mp, tp)
        if pick:
            scored.append(
                (ref_id is not None and ref_id in n_matches, pick["score"], f, pick)
            )
    scored.sort(
        key=lambda x: (x[0], x[1]), reverse=True
    )  # referee known first, then strongest
    out = []
    for rank, (has_ref, score, f, pick) in enumerate(scored[:N_PICKS], start=1):
        ref_id = f.get("referee_id")
        factors = (
            f"referee {f['referee']} {f['referee_factor']:.2f}x an average referee"
            if has_ref
            else "referee not known yet (teams and league only)"
        )
        out.append(
            {
                "rank": rank,
                "fixture": f"{f['league']}|{f['date']}|{f['home']}",
                "league": f["league"],
                "date": f["date"],
                "utc_kickoff": f["utc_kickoff"],
                "home": f["home"],
                "away": f["away"],
                "referee": f.get("referee") or "TBD",
                "referee_matches": n_matches.get(ref_id, 0) if ref_id else 0,
                "bet": bet_label(pick, f["home"], f["away"]),
                "market": pick["market"],
                "selection": pick["selection"],
                "line": pick["line"],
                "model_prob": round(100 * pick["p"], 1),
                "typical_prob": round(100 * pick["base"], 1),
                "shift_pp": round(100 * score, 1),
                "fair_odds": round(1 / pick["p"], 2),
                "min_odds": min_odds(pick["p"]),
                "why": (
                    f"{factors}; {f['home']} {f['home_receives_factor']:.2f}x and "
                    f"{f['away']} {f['away_receives_factor']:.2f}x their league's usual cards"
                ),
            }
        )
    treble = []
    for leg_no, (i, leg) in enumerate(best_treble(treble_pool) or [], start=1):
        f = fixtures[i]
        treble.append(
            {
                "leg": leg_no,
                "fixture": f"{f['league']}|{f['date']}|{f['home']}",
                "league": f["league"],
                "date": f["date"],
                "utc_kickoff": f["utc_kickoff"],
                "home": f["home"],
                "away": f["away"],
                "referee": f.get("referee") or "TBD",
                "bet": bet_label(leg, f["home"], f["away"]),
                "market": leg["market"],
                "selection": leg["selection"],
                "line": leg["line"],
                "model_prob": round(100 * leg["p"], 1),
                "typical_prob": round(100 * leg["base"], 1),
                "fair_odds": round(1 / leg["p"], 2),
            }
        )
    return out, treble


def build_report(
    joined: pd.DataFrame,
    api: pd.DataFrame,
    unmatched: pd.DataFrame,
    today: date | None = None,
    appointments: pd.DataFrame | None = None,
) -> dict:
    """Everything that goes into data/referees.json.

    `appointments`: referees for upcoming fixtures found by a web lookup
    (see appointments.py); the API's own referee always wins.
    """
    today = today or date.today()
    current = current_season_start_year(today)
    # Matches with no card data (e.g. abandoned and awarded) are left out of the stats,
    # but listed under data_quality so nothing disappears silently.
    no_cards_mask = joined[CARD_COUNT_COLUMNS].isna().any(axis=1)
    no_cards = joined[no_cards_mask]
    df = add_match_metrics(
        joined[~no_cards_mask].astype(dict.fromkeys(CARD_COUNT_COLUMNS, int)), current
    )
    league_avg = league_averages(df)
    with_ref = df[df["referee_id"].notna()]

    referees = []
    rates: dict[int, dict] = {}
    for ref_id, g in with_ref.groupby("referee_id"):
        ref_id = int(ref_id)
        last = g.sort_values("date").iloc[-1]
        seasons = []
        for (season, league), sg in g.groupby(["season", "league"]):
            seasons.append(
                {"season": season_label(int(season)), "league": league, **_basic(sg)}
            )
        overall = {**_basic(g), **_adjusted(g, league_avg)}
        rates[ref_id] = overall
        referees.append(
            {
                "id": ref_id,
                "name": last["referee"],
                "nationality": last["referee_nationality"]
                if isinstance(last["referee_nationality"], str)
                else None,
                "leagues": sorted(g["league"].unique().tolist()),
                "last_match": str(last["date"]),
                "seasons": seasons,
                "overall": overall,
                "next_fixture": None,
            }
        )

    # Upcoming fixtures: the next UPCOMING_DAYS days for the panel; any date for "next fixture".
    # Upcoming = kick-off still ahead. On a run for today, use the current time, so a match
    # that started earlier today (still "TIMED" in data downloaded before kick-off) drops out;
    # for any other date (tests, backfills), use the start of that day.
    cutoff = (
        pd.Timestamp.now(tz="UTC")
        if today >= date.today()
        else pd.Timestamp(today).tz_localize("UTC")
    )
    future = api[
        api["status"].isin(["SCHEDULED", "TIMED"])
        & (pd.to_datetime(api["date"]) >= pd.Timestamp(today))
        & (pd.to_datetime(api["utc_kickoff"], utc=True) > cutoff)
    ]
    future = future.sort_values("utc_kickoff")
    known = with_ref[["referee_id", "referee", "league"]].drop_duplicates()
    known = known.assign(referee_id=known["referee_id"].astype(int))
    future, appointment_problems, appointments_used = apply_appointments(
        future,
        appointments if appointments is not None else pd.DataFrame(),
        known,
        today=str(today),
    )
    factors = team_factors(df, league_avg)
    all_projected = project_fixtures(future, rates, league_avg, factors)
    # Team card statistics, and each fixture's projection split between the two teams.
    from referees.team_stats import (
        enrich_fixtures,
        league_team_averages,
        team_long,
        team_report,
    )

    long = team_long(df)
    team_avgs = league_team_averages(long)
    enrich_fixtures(all_projected, long, team_avgs)
    teams = team_report(long, team_avgs)
    model = apply_model(all_projected, df, today, league_avg)
    for r in referees:
        r["overall"]["model_factor"] = round(model.referee.get(r["id"], 1.0), 3)
    for t in teams:
        t["overall"]["model_receives_factor"] = round(
            model.receives.get((t["league"], t["team"]), 1.0), 3
        )
        t["overall"]["model_draws_factor"] = round(
            model.draws.get((t["league"], t["team"]), 1.0), 3
        )
    horizon = str(today + timedelta(days=UPCOMING_DAYS))
    upcoming = [f for f in all_projected if f["date"] <= horizon]

    picks, treble = hot_picks(all_projected, df, model, today, referees)
    by_ref = {r["id"]: r for r in referees}
    for (
        f
    ) in all_projected:  # sorted by kick-off, so the first one per referee is the next
        ref = by_ref.get(f["referee_id"])
        if ref and ref["next_fixture"] is None:
            ref["next_fixture"] = f

    referees.sort(key=lambda r: -r["overall"]["adjusted_cards_per_match"])

    leagues = []
    for key, avg in league_avg.items():
        g = df[df["league"] == key]
        leagues.append(
            {
                "key": key,
                "name": LEAGUES_BY_KEY[key].name,
                "weighted_cards_per_match": round(avg["cards"], 2),
                "weighted_booking_points_per_match": round(avg["booking_points"], 1),
                "seasons": [
                    {"season": season_label(int(s)), **_basic(sg)}
                    for s, sg in g.groupby("season")
                ],
            }
        )

    finished = api[api["status"] == "FINISHED"]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seasons": [season_label(int(s)) for s in sorted(df["season"].unique())],
        "method": METHOD_NOTES,
        "leagues": leagues,
        "referees": referees,
        "teams": teams,
        "hot_picks": picks,
        "hot_treble": treble,
        "upcoming_fixtures": upcoming,
        "data_quality": {
            "joined_matches": len(joined),
            "unmatched_rows": len(unmatched),
            "appointments_used": appointments_used,
            "appointment_problems": appointment_problems,
            "matches_without_card_data": [
                f"{r.league} {r.date} {r.api_home} v {r.api_away}"
                for r in no_cards.itertuples()
            ],
            # England only: football-data.co.uk names a different referee than the API
            "referee_disagreements": [
                f"{r.league} {r.date} {r.api_home} v {r.api_away}: "
                f"API '{r.referee}', CSV '{r.csv_referee}'"
                for r in joined.itertuples()
                if getattr(r, "csv_referee_agrees", pd.NA) is False
            ],
            "finished_api_matches_without_referee": {
                lg: int(g["referee_id"].isna().sum())
                for lg, g in finished.groupby("league")
            },
        },
    }
