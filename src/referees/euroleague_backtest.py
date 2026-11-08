"""Walk-forward check of the EuroLeague player picks: each week is predicted only from earlier games.

For every week: fit the player model and the referee figures on games before that Monday, project
every player who played that week (bets on players who don't play are void), pick the top
N_PICKS bets exactly as the dashboard does (the real crew is used, as it is known by game day),
and score them. All over-line chances are also collected for a calibration table ("the model
said 60%: how often did it happen?") and compared with the typical-player chances.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pandas as pd

from referees.euroleague_model import (
    MARKETS,
    candidates,
    crew_adjustment,
    fit,
    game_players,
    pick,
    pick_chance,
)
from referees.euroleague_stats import referee_table

ACTUAL = {
    "fouls_committed": "fouls_committed",
    "fouls_drawn": "fouls_drawn",
    "ftm": "ftm",
}
PERIODS = {
    "2025/26": ("2025-09-01", "2026-07-01"),
    "2026/27 so far": ("2026-07-01", "2027-07-01"),
}
BINS = [(0, 20), (20, 35), (35, 50), (50, 65), (65, 80), (80, 101)]


def _mondays(start: str, end: str):
    d = date.fromisoformat(start)
    d -= timedelta(days=d.weekday())
    while d.isoformat() < end:
        yield d
        d += timedelta(days=7)


def hit(bet: dict, actual: float) -> bool:
    return actual > bet["line"] if bet["selection"] == "over" else actual < bet["line"]


def run(
    games: pd.DataFrame, players: pd.DataFrame, today: str | None = None, progress=print
) -> dict:
    today = today or date.today().isoformat()
    actual = players.set_index(["season", "game", "player_id"])
    picks_out, calib = [], []
    for label, (start, end) in PERIODS.items():
        end = min(end, today)
        for monday in _mondays(start, end):
            wk_start, wk_end = (
                monday.isoformat(),
                (monday + timedelta(days=7)).isoformat(),
            )
            week = games[
                (games["date"] >= wk_start)
                & (games["date"] < wk_end)
                & (games["date"] < today)
            ]
            if week.empty:
                continue
            before_g = games[games["date"] < wk_start]
            keys = set(zip(before_g["season"], before_g["game"], strict=True))
            before_p = players[
                [
                    k in keys
                    for k in zip(players["season"], players["game"], strict=True)
                ]
            ]
            f = fit(games, players, wk_start)
            refs, *_ = referee_table(before_g, before_p)
            entries = []
            for game in week.to_dict("records"):
                crew = crew_adjustment(game["referees"], refs)
                played = set(
                    players[
                        (players["season"] == game["season"])
                        & (players["game"] == game["game"])
                    ]["player_id"]
                )
                projs = game_players(f, game, crew, played)
                entries.append((game, crew, projs))
                for proj in projs:
                    row = actual.loc[(game["season"], game["game"], proj["player_id"])]
                    for c in candidates(proj, f.league_ft_pct):
                        if c["selection"] != "over":
                            continue
                        calib.append(
                            {
                                "period": label,
                                "market": c["market"],
                                "line": c["line"],
                                "p": c["p"],
                                "typical": c["typical"],
                                "won": float(row[ACTUAL[c["market"]]]) > c["line"],
                            }
                        )
            for pk in pick(entries, f.league_ft_pct):
                g, proj, bet = pk["game"], pk["proj"], pk["bet"]
                value = float(
                    actual.loc[(g["season"], g["game"], proj["player_id"])][
                        ACTUAL[bet["market"]]
                    ]
                )
                picks_out.append(
                    {
                        "period": label,
                        "week": wk_start,
                        "player": proj["player"],
                        "market": bet["market"],
                        "selection": bet["selection"],
                        "line": bet["line"],
                        "p": bet["p"],
                        "typical": bet["typical"],
                        "actual": value,
                        "won": hit(bet, value),
                    }
                )
        progress(f"  {label}: {sum(p['period'] == label for p in picks_out)} picks")
    return summarise(picks_out, calib)


def summarise(picks: list[dict], calib: list[dict]) -> dict:
    record = {}
    for label in PERIODS:
        ps = [p for p in picks if p["period"] == label]
        if not ps:
            continue
        won = sum(p["won"] for p in ps)
        record[label] = {
            "picks": len(ps),
            "won": won,
            "hit_rate_pct": round(100 * won / len(ps), 1),
            "avg_model_pct": round(100 * sum(p["p"] for p in ps) / len(ps), 1),
            "avg_corrected_pct": round(
                100 * sum(pick_chance(p["p"]) for p in ps) / len(ps), 1
            ),
            "avg_typical_pct": round(100 * sum(p["typical"] for p in ps) / len(ps), 1),
            "break_even_odds": round(len(ps) / won, 2) if won else None,
            "by_market": {
                m: {
                    "picks": sum(p["market"] == m for p in ps),
                    "won": sum(p["won"] for p in ps if p["market"] == m),
                }
                for m in MARKETS
            },
        }
    cal = pd.DataFrame(calib)
    bins = []
    if not cal.empty:
        for lo, hi in BINS:
            x = cal[(cal["p"] * 100 >= lo) & (cal["p"] * 100 < hi)]
            if len(x):
                bins.append(
                    {
                        "bin": f"{lo}-{min(hi, 100)}%",
                        "predictions": len(x),
                        "predicted_pct": round(100 * float(x["p"].mean()), 1),
                        "observed_pct": round(100 * float(x["won"].mean()), 1),
                    }
                )

    def logloss(col):
        p = cal[col].clip(1e-6, 1 - 1e-6)
        return float(
            -(
                cal["won"] * p.map(math.log) + (~cal["won"]) * (1 - p).map(math.log)
            ).mean()
        )

    scores = (
        {
            "predictions": len(cal),
            "log_loss_model": round(logloss("p"), 4),
            "log_loss_typical": round(logloss("typical"), 4),
            "brier_model": round(float(((cal["p"] - cal["won"]) ** 2).mean()), 4),
            "brier_typical": round(
                float(((cal["typical"] - cal["won"]) ** 2).mean()), 4
            ),
        }
        if not cal.empty
        else {}
    )
    return {
        "pick_record": record,
        "calibration": bins,
        "scores": scores,
        "picks": picks,
    }


def write(result: dict, json_path, md_path) -> None:
    json_path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    lines = [
        "# EuroLeague player picks: walk-forward backtest",
        "",
        "Each week is predicted only from earlier games; bets on players who did not play are void.",
        "",
        "## Pick record (5 picks per week)",
        "",
        "| Period | Picks | Won | Hit rate | Model said (raw) | Shown (corrected) | Typical player | Break-even odds |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, r in result["pick_record"].items():
        lines.append(
            f"| {label} | {r['picks']} | {r['won']} | {r['hit_rate_pct']}% | {r['avg_model_pct']}% | "
            f"{r['avg_corrected_pct']}% | {r['avg_typical_pct']}% | {r['break_even_odds']} |"
        )
    s = result["scores"]
    if s:
        lines += [
            "",
            f"## All over-line chances ({s['predictions']} predictions)",
            "",
            "| Method | Log loss (lower is better) | Brier (lower is better) |",
            "|---|---|---|",
            f"| Model | {s['log_loss_model']} | {s['brier_model']} |",
            f"| Typical player in the role | {s['log_loss_typical']} | {s['brier_typical']} |",
            "",
            "## Calibration",
            "",
            "| Model chance | Predictions | Predicted | Happened |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {b['bin']} | {b['predictions']} | {b['predicted_pct']}% | {b['observed_pct']}% |"
            for b in result["calibration"]
        ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
