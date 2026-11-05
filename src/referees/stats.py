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

from referees.config import LEAGUES_BY_KEY, current_season_start_year, season_label

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
    "projection": "referee adjusted rate x average of the two teams' card factors "
    "(team factor = team's adjusted cards-per-match / league average)",
    "england_second_yellow": "football-data.co.uk: in English files a second-yellow dismissal "
    "counts as 1 red only; in Spain/Germany/France it counts as 1 yellow "
    "+ 1 red. Compare Premier League numbers with care across leagues.",
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
    df["cards"] = df["yellows"] + df["reds"]
    df["booking_points"] = YELLOW_POINTS * df["yellows"] + RED_POINTS * df["reds"]
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
                "league average (referee not appointed yet)"
                if ref_id is None
                else "league average (referee has no history)"
            )
        fixtures.append(
            {
                "league": r.league,
                "date": str(r.date),
                "utc_kickoff": r.utc_kickoff,
                "matchday": None if pd.isna(r.matchday) else int(r.matchday),
                "home": r.api_home,
                "away": r.api_away,
                "referee_id": ref_id,
                "referee": r.referee if isinstance(r.referee, str) else None,
                "projected_cards": round(base_c * (hf["cards"] + af["cards"]) / 2, 2),
                "projected_booking_points": round(
                    base_bp * (hf["booking_points"] + af["booking_points"]) / 2, 1
                ),
                "basis": basis,
            }
        )
    return fixtures


def build_report(
    joined: pd.DataFrame,
    api: pd.DataFrame,
    unmatched: pd.DataFrame,
    today: date | None = None,
) -> dict:
    """Everything that goes into data/referees.json."""
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
    future = api[
        api["status"].isin(["SCHEDULED", "TIMED"])
        & (pd.to_datetime(api["date"]) >= pd.Timestamp(today))
    ]
    future = future.sort_values("utc_kickoff")
    factors = team_factors(df, league_avg)
    all_projected = project_fixtures(future, rates, league_avg, factors)
    horizon = str(today + timedelta(days=UPCOMING_DAYS))
    upcoming = [f for f in all_projected if f["date"] <= horizon]

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
        "upcoming_fixtures": upcoming,
        "data_quality": {
            "joined_matches": len(joined),
            "unmatched_rows": len(unmatched),
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
