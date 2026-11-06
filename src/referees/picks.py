"""Hot predictions: the 2-3 card bets where the model differs most from a typical match.

A bet that is merely "likely" (over 1.5 cards) is priced short by every bookmaker. What can
beat a bookmaker's standard price is a match where the referee and the two teams push the
chances well away from the league norm. So, for each upcoming fixture in the window:

1. Every modelled bet is a candidate: match total over/under 3.5 / 4.5 / 5.5 and each side
   over/under 1.5 / 2.5 cards.
2. Keep candidates the model rates at least MIN_PROB likely.
3. Score = model chance - chance in a typical match of that league (league-only model).
4. Best candidate per fixture, then the top N fixtures; fixtures with a known referee first.

The same rule is backtested in backtest.py (`hot_picks`), so its track record is shown too.
These are estimates, not certainties: the user should check the bookmaker's odds against the
"minimum odds" (fair odds + MARGIN) before betting.
"""

from __future__ import annotations

import math

MIN_PROB = 0.55
MARGIN = 0.05  # ask for at least 5% better than fair odds
WINDOW_DAYS = 7
N_PICKS = 3
MATCH_LINES = [3.5, 4.5, 5.5]
SIDE_LINES = [1.5, 2.5]


def _k(line: float) -> str:
    return str(line).replace(".", "_")


def candidates(model_p: dict, base_p: dict) -> list[dict]:
    """All bets for one fixture with the model's and the typical match's chance (0-1).

    `model_p` / `base_p` are CardModel.predict() outputs (probabilities as 0-1).
    """
    out = []
    for line in MATCH_LINES:
        p, b = model_p[f"prob_over_{_k(line)}"], base_p[f"prob_over_{_k(line)}"]
        out.append(
            {
                "market": "match_cards",
                "selection": "over",
                "line": line,
                "p": p,
                "base": b,
            }
        )
        out.append(
            {
                "market": "match_cards",
                "selection": "under",
                "line": line,
                "p": 1 - p,
                "base": 1 - b,
            }
        )
    for side in ("home", "away"):
        for line in SIDE_LINES:
            p, b = (
                model_p[f"{side}_prob_over_{_k(line)}"],
                base_p[f"{side}_prob_over_{_k(line)}"],
            )
            out.append(
                {
                    "market": f"{side}_cards",
                    "selection": "over",
                    "line": line,
                    "p": p,
                    "base": b,
                }
            )
            out.append(
                {
                    "market": f"{side}_cards",
                    "selection": "under",
                    "line": line,
                    "p": 1 - p,
                    "base": 1 - b,
                }
            )
    return out


def best_candidate(
    model_p: dict, base_p: dict, min_prob: float = MIN_PROB
) -> dict | None:
    eligible = [c for c in candidates(model_p, base_p) if c["p"] >= min_prob]
    if not eligible:
        return None
    best = max(eligible, key=lambda c: c["p"] - c["base"])
    return {**best, "score": best["p"] - best["base"]}


def is_hit(pick: dict, home_cards: float, away_cards: float) -> bool:
    actual = {
        "match_cards": home_cards + away_cards,
        "home_cards": home_cards,
        "away_cards": away_cards,
    }[pick["market"]]
    return (
        actual > pick["line"] if pick["selection"] == "over" else actual < pick["line"]
    )


def bet_label(pick: dict, home: str, away: str) -> str:
    who = {"match_cards": "Match", "home_cards": home, "away_cards": away}[
        pick["market"]
    ]
    return f"{who}: {pick['selection']} {pick['line']} cards"


def min_odds(p: float, margin: float = MARGIN) -> float:
    """Smallest decimal odds worth taking: fair odds (1/p) plus the margin, rounded up."""
    return math.ceil(100 * (1 + margin) / p) / 100
