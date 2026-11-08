"""Hot predictions: the (up to) 5 card bets where the model differs most from a typical match.

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
N_PICKS = 5  # raised from 3 at the user's request (2026-11-08)
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


# --- safe treble: 3 legs from 3 matches, combined fair odds of about 2.00 ---------------

TREBLE_TARGET = 2.0
TREBLE_BAND = (1.85, 2.15)
TREBLE_MIN_LEG_PROB = 0.60
TREBLE_LEGS_PER_MATCH = 3


def treble_legs(model_p: dict, base_p: dict) -> list[dict]:
    """A match's best few safe legs: model chance >= 60% and above a typical match."""
    legs = [
        {**c, "score": c["p"] - c["base"]}
        for c in candidates(model_p, base_p)
        if c["p"] >= TREBLE_MIN_LEG_PROB and c["p"] > c["base"]
    ]
    legs.sort(key=lambda c: c["score"], reverse=True)
    return legs[:TREBLE_LEGS_PER_MATCH]


def best_treble(
    matches: list[tuple[object, bool, list[dict]]],
) -> list[tuple[object, dict]] | None:
    """Choose 3 legs from 3 different matches with combined fair odds in TREBLE_BAND.

    `matches` holds (match id, referee known, legs). At a fixed total of about 2.00 every
    treble has about a 50% model chance, so the choice is the one the model rates most above
    a typical match: the highest product of p / typical chance (closest to 2.00 on a tie).
    Matches with a known referee are used first; others only if that finds nothing.
    """
    from itertools import combinations, product

    def search(pool):
        best, best_key = None, None
        for trio in combinations(pool, 3):
            for legs in product(*(m[2] for m in trio)):
                p = legs[0]["p"] * legs[1]["p"] * legs[2]["p"]
                odds = 1 / p
                if not (TREBLE_BAND[0] <= odds <= TREBLE_BAND[1]):
                    continue
                ratio = p / (legs[0]["base"] * legs[1]["base"] * legs[2]["base"])
                key = (ratio, -abs(odds - TREBLE_TARGET))
                if best_key is None or key > best_key:
                    best, best_key = list(zip((m[0] for m in trio), legs)), key
        return best

    with_legs = [m for m in matches if m[2]]
    known = [m for m in with_legs if m[1]]
    return (search(known) if len(known) >= 3 else None) or search(with_legs)
