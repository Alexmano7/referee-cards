"""Walk-forward backtest of the card model, settings tuning, and comparison with baselines.

For every week in the test period, the model is fitted on matches played *before* that
week only, then predicts that week's matches (with their actual referee, as the dashboard
would have from the referee lookup). Nothing from the future leaks in.

- Tuning period: the 2025/26 season, used to choose the model settings.
- Holdout period: 2026/27 so far, never used for tuning, to report honest accuracy.

Scores per method:
- log score: mean log-probability given to the actual card count (higher is better)
- Brier score for over 3.5 / 4.5 / 5.5: mean squared error of the probability (lower is better)
- MAE: mean absolute difference between expected and actual cards
- calibration for over 4.5: predicted vs observed rate in probability bins
"""

from __future__ import annotations

import itertools
import json
from datetime import date

import numpy as np
import pandas as pd

from referees.config import PROJECT_ROOT
from referees.model import (
    MATCH_LINES,
    MAX_CARDS,
    PARAMS_FILE,
    _k,
    fit,
    log_score,
    nb_pmf_array,
    prob_over,
    side_observations,
)
from referees.stats import (
    _adjusted,
    add_match_metrics,
    league_averages,
    team_factors,
)

RESULTS_FILE = PROJECT_ROOT / "docs" / "backtest.json"
REPORT_FILE = PROJECT_ROOT / "docs" / "backtest.md"
TUNE_PERIOD = (date(2025, 7, 1), date(2026, 7, 1))
GRID = {
    # First run (120-730 days, k 5-40) picked the edge (120 d, k 40, k 40), so the grid
    # was widened toward shorter half-lives and stronger evening-out.
    "half_life_days": [60, 90, 120, 180, 365],
    "k_referee": [20.0, 40.0, 80.0, 160.0],
    "k_team": [20.0, 40.0, 80.0, 160.0],
}
CALIBRATION_BINS = [0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0]


def prepare(joined: pd.DataFrame) -> pd.DataFrame:
    cards = ["home_yellows", "away_yellows", "home_reds", "away_reds"]
    played = joined[~joined[cards].isna().any(axis=1)].astype(dict.fromkeys(cards, int))
    df = add_match_metrics(played).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def _weeks(df: pd.DataFrame, start: date, end: date):
    test = df[(df["date"] >= start) & (df["date"] < end)]
    week = pd.to_datetime(test["date"]).dt.to_period("W-SUN").dt.start_time.dt.date
    yield from test.groupby(week)


def _model_predictions(df, obs, start, end, params) -> list[dict]:
    out = []
    for wk, g in _weeks(df, start, end):
        m = fit(obs, as_of=wk, **params)
        for r in g.itertuples():
            p = m.predict(r.league, r.api_home, r.api_away, r.referee_id)
            out.append(
                {
                    "y": int(r.cards),
                    "mu": p["expected_cards"],
                    "dist": p["distribution"],
                }
            )
    return out


def _current_method_predictions(df, obs, start, end) -> list[dict]:
    """The dashboard's original method: referee adjusted rate x average team factor."""
    out = []
    for wk, g in _weeks(df, start, end):
        train = df[df["date"] < wk]
        season = wk.year if wk.month >= 7 else wk.year - 1
        tr = add_match_metrics(train.drop(columns=["weight"]), season)
        lavg = league_averages(tr)
        factors = team_factors(tr, lavg)
        phi = fit(obs, as_of=wk, k_referee=1e9, k_team=1e9).phi_match  # dispersion only
        rates = {}
        for rid, rg in tr[tr["referee_id"].notna()].groupby("referee_id"):
            rates[int(rid)] = _adjusted(rg, lavg)["adjusted_cards_per_match"]
        for r in g.itertuples():
            if r.league not in lavg:
                continue
            base = (
                rates.get(int(r.referee_id), lavg[r.league]["cards"])
                if pd.notna(r.referee_id)
                else lavg[r.league]["cards"]
            )
            hf = factors.get((r.league, r.api_home), {"cards": 1.0})["cards"]
            af = factors.get((r.league, r.api_away), {"cards": 1.0})["cards"]
            mu = base * (hf + af) / 2
            out.append(
                {
                    "y": int(r.cards),
                    "mu": mu,
                    "dist": nb_pmf_array(mu, phi.get(r.league, 0.0), MAX_CARDS),
                }
            )
    return out


def hot_pick_record(df, obs, start, end, params, n_picks: int = 3) -> dict:
    """Backtest of the Hot Prediction rule (picks.py): each week, the top picks are chosen
    from earlier data only, then checked against what happened."""
    from referees.picks import best_candidate, is_hit

    picks = []
    typical_params = {**params, "k_referee": 1e9, "k_team": 1e9}
    for wk, g in _weeks(df, start, end):
        m = fit(obs, as_of=wk, **params)
        typical = fit(obs, as_of=wk, **typical_params)
        week = []
        for r in g.itertuples():
            ref = r.referee_id if pd.notna(r.referee_id) else None
            c = best_candidate(
                m.predict(r.league, r.api_home, r.api_away, ref),
                typical.predict(r.league, r.api_home, r.api_away, None),
            )
            if c:
                known = ref is not None and int(ref) in m.referee
                week.append((known, c["score"], c, r))
        week.sort(key=lambda x: (x[0], x[1]), reverse=True)
        for _, _, c, r in week[:n_picks]:
            picks.append(
                {
                    "p": c["p"],
                    "base": c["base"],
                    "hit": is_hit(c, r.home_cards, r.away_cards),
                }
            )
    if not picks:
        return {"picks": 0}
    hits = sum(p["hit"] for p in picks)
    rate = hits / len(picks)
    return {
        "picks": len(picks),
        "hits": int(hits),
        "hit_rate_pct": round(100 * rate, 1),
        "avg_model_pct": round(100 * float(np.mean([p["p"] for p in picks])), 1),
        "avg_typical_pct": round(100 * float(np.mean([p["base"] for p in picks])), 1),
        "break_even_odds": round(1 / rate, 2) if rate else None,
    }


def treble_record(df, obs, start, end, params) -> dict:
    """Backtest of the safe treble (picks.best_treble): one treble per week from earlier
    data only; it wins when all three legs win."""
    from referees.picks import best_treble, is_hit, treble_legs

    results = []
    typical_params = {**params, "k_referee": 1e9, "k_team": 1e9}
    for wk, g in _weeks(df, start, end):
        m = fit(obs, as_of=wk, **params)
        typical = fit(obs, as_of=wk, **typical_params)
        pool, rows = [], {}
        for r in g.itertuples():
            ref = r.referee_id if pd.notna(r.referee_id) else None
            legs = treble_legs(
                m.predict(r.league, r.api_home, r.api_away, ref),
                typical.predict(r.league, r.api_home, r.api_away, None),
            )
            pool.append((r.Index, ref is not None and int(ref) in m.referee, legs))
            rows[r.Index] = r
        chosen = best_treble(pool)
        if not chosen:
            continue
        p = float(np.prod([leg["p"] for _, leg in chosen]))
        base = float(np.prod([leg["base"] for _, leg in chosen]))
        won = all(
            is_hit(leg, rows[i].home_cards, rows[i].away_cards) for i, leg in chosen
        )
        legs_won = sum(
            is_hit(leg, rows[i].home_cards, rows[i].away_cards) for i, leg in chosen
        )
        results.append({"p": p, "base": base, "won": won, "legs_won": legs_won})
    if not results:
        return {"trebles": 0}
    wins = sum(r["won"] for r in results)
    rate = wins / len(results)
    return {
        "trebles": len(results),
        "won": int(wins),
        "win_rate_pct": round(100 * rate, 1),
        "legs_won_pct": round(
            100 * sum(r["legs_won"] for r in results) / (3 * len(results)), 1
        ),
        "avg_model_pct": round(100 * float(np.mean([r["p"] for r in results])), 1),
        "avg_typical_pct": round(100 * float(np.mean([r["base"] for r in results])), 1),
        "break_even_odds": round(1 / rate, 2) if rate else None,
    }


def score(preds: list[dict]) -> dict:
    if not preds:
        return {"matches": 0}
    y = np.array([p["y"] for p in preds])
    out = {
        "matches": len(preds),
        "log_score": round(
            float(np.mean([log_score(p["dist"], p["y"]) for p in preds])), 4
        ),
        "mae": round(float(np.mean(np.abs(y - np.array([p["mu"] for p in preds])))), 3),
    }
    for line in MATCH_LINES:
        prob = np.array([prob_over(p["dist"], line) for p in preds])
        out[f"brier_over_{_k(line)}"] = round(
            float(np.mean((prob - (y > line)) ** 2)), 4
        )
    return out


def calibration(preds: list[dict], line: float = 4.5) -> list[dict]:
    prob = np.array([prob_over(p["dist"], line) for p in preds])
    hit = np.array([p["y"] > line for p in preds], dtype=float)
    rows = []
    for lo, hi in itertools.pairwise(CALIBRATION_BINS):
        mask = (prob >= lo) & (prob < hi if hi < 1 else prob <= hi)
        if mask.sum():
            rows.append(
                {
                    "bin": f"{int(lo * 100)}-{int(hi * 100)}%",
                    "matches": int(mask.sum()),
                    "predicted_pct": round(100 * float(prob[mask].mean()), 1),
                    "observed_pct": round(100 * float(hit[mask].mean()), 1),
                }
            )
    return rows


def run(joined: pd.DataFrame, today: date | None = None, progress=print) -> dict:
    today = today or date.today()
    df = prepare(joined)
    obs = side_observations(df)
    holdout = (TUNE_PERIOD[1], today)

    progress("Tuning settings on 2025/26 ...")
    grid_results = []
    for hl, kr, kt in itertools.product(*GRID.values()):
        params = {"half_life_days": hl, "k_referee": kr, "k_team": kt}
        s = score(_model_predictions(df, obs, *TUNE_PERIOD, params))
        grid_results.append({**params, **s})
        progress(
            f"  half-life {hl:>3} d, k_ref {kr:>4}, k_team {kt:>4}: log score {s['log_score']}, Brier 4.5 {s['brier_over_4_5']}"
        )
    best = max(grid_results, key=lambda r: r["log_score"])
    best_params = {k: best[k] for k in GRID}

    progress("Scoring on 2026/27 so far (not used for tuning) ...")
    model_hold = _model_predictions(df, obs, *holdout, best_params)
    league_only = _model_predictions(
        df, obs, *holdout, {**best_params, "k_referee": 1e9, "k_team": 1e9}
    )
    current = _current_method_predictions(df, obs, *holdout)
    model_tune = _model_predictions(df, obs, *TUNE_PERIOD, best_params)

    results = {
        "generated": str(today),
        "tune_period": [str(d) for d in TUNE_PERIOD],
        "holdout_period": [str(d) for d in holdout],
        "best_params": best_params,
        "grid": grid_results,
        "holdout": {
            "Model (tuned)": score(model_hold),
            "Current dashboard method": score(current),
            "League average only": score(league_only),
        },
        "tuning_season": {"Model (tuned)": score(model_tune)},
        "calibration_over_4_5": calibration(model_tune + model_hold),
        "treble": {
            "2026/27 so far": treble_record(df, obs, *holdout, best_params),
            "2025/26": treble_record(df, obs, *TUNE_PERIOD, best_params),
        },
        "hot_picks": {
            "2026/27 so far": hot_pick_record(df, obs, *holdout, best_params),
            "2025/26": hot_pick_record(df, obs, *TUNE_PERIOD, best_params),
        },
    }
    return results


def save(results: dict) -> None:
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=1), encoding="utf-8")
    PARAMS_FILE.write_text(
        json.dumps(results["best_params"], indent=1) + "\n", encoding="utf-8"
    )
    REPORT_FILE.write_text(render_markdown(results), encoding="utf-8")


def render_markdown(r: dict) -> str:
    def table(rows: dict) -> str:
        head = "| Method | Matches | Log score ↑ | Brier 3.5 ↓ | Brier 4.5 ↓ | Brier 5.5 ↓ | MAE ↓ |\n|---|---|---|---|---|---|---|\n"
        return head + "".join(
            f"| {name} | {s['matches']} | {s['log_score']} | {s['brier_over_3_5']} | {s['brier_over_4_5']} | {s['brier_over_5_5']} | {s['mae']} |\n"
            for name, s in rows.items()
        )

    cal = (
        "| Predicted P(over 4.5) | Matches | Predicted | Observed |\n|---|---|---|---|\n"
        + "".join(
            f"| {c['bin']} | {c['matches']} | {c['predicted_pct']}% | {c['observed_pct']}% |\n"
            for c in r["calibration_over_4_5"]
        )
    )
    bp = r["best_params"]
    return (
        "# Card model backtest\n\n"
        f"Generated {r['generated']} by `uv run referees backtest`. Walk-forward: each week is predicted "
        "from matches before that week only.\n\n"
        f"**Chosen settings** (best log score on {r['tune_period'][0]} to {r['tune_period'][1]}): "
        f"half-life {bp['half_life_days']} days, referee prior {bp['k_referee']}, team prior {bp['k_team']}.\n\n"
        f"## Holdout: {r['holdout_period'][0]} to {r['holdout_period'][1]} (not used for tuning)\n\n"
        + table(r["holdout"])
        + "\n## Tuning season (in-sample for the settings)\n\n"
        + table(r["tuning_season"])
        + "\n## Calibration of P(over 4.5 cards), both periods\n\n"
        + cal
        + "\n## Hot Prediction rule (3 picks per week, chosen from earlier data only)\n\n"
        + "| Period | Picks | Won | Hit rate | Model said | Typical match | Break-even odds |\n"
        + "|---|---|---|---|---|---|---|\n"
        + "".join(
            f"| {k} | {v.get('picks')} | {v.get('hits')} | {v.get('hit_rate_pct')}% "
            f"| {v.get('avg_model_pct')}% | {v.get('avg_typical_pct')}% | {v.get('break_even_odds')} |\n"
            for k, v in r.get("hot_picks", {}).items()
        )
        + "\n## Safe treble (3 legs, combined fair odds about 2.00, one per week)\n\n"
        + "| Period | Trebles | Won | Win rate | Legs won | Model said | Typical match | Break-even odds |\n"
        + "|---|---|---|---|---|---|---|---|\n"
        + "".join(
            f"| {k} | {v.get('trebles')} | {v.get('won')} | {v.get('win_rate_pct')}% | {v.get('legs_won_pct')}% "
            f"| {v.get('avg_model_pct')}% | {v.get('avg_typical_pct')}% | {v.get('break_even_odds')} |\n"
            for k, v in r.get("treble", {}).items()
        )
    )
