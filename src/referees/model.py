"""Probability model for cards: referee, team and opponent effects estimated together.

For each side of each match (two observations per match):

    expected cards = base[league, venue] x referee x receives[team] x draws[opponent]

- Fitted by weighted Poisson maximum likelihood with a Gamma prior (mean 1) on every
  referee/team factor: the prior acts like `k` expected cards of "average" evidence, so
  factors with little data stay close to 1 (the same idea as the shrinkage in stats.py).
- Recency: an observation `age` days old has weight 0.5 ** (age / half_life_days).
- Card counts vary more than Poisson, so probabilities use a negative binomial with a
  per-league over-dispersion `phi` (variance = mu + phi * mu^2) estimated from the data.

No libraries beyond numpy/pandas. All factors are multiplicative, so each update is a
closed-form ratio (iterative proportional fitting), which converges quickly.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from referees.config import PROJECT_ROOT

PARAMS_FILE = PROJECT_ROOT / "config" / "model_params.json"
DEFAULT_PARAMS = {"half_life_days": 365, "k_referee": 15.0, "k_team": 15.0}
MATCH_LINES = [3.5, 4.5, 5.5]
TEAM_LINES = [1.5, 2.5]
MAX_CARDS = 15  # distributions are computed for 0..MAX_CARDS


def load_params(path: Path = PARAMS_FILE) -> dict:
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        return {k: saved.get(k, v) for k, v in DEFAULT_PARAMS.items()}
    return dict(DEFAULT_PARAMS)


@dataclass
class CardModel:
    base: dict[tuple[str, str], float]
    referee: dict[int, float]
    receives: dict[tuple[str, str], float]
    draws: dict[tuple[str, str], float]
    phi_match: dict[str, float]
    phi_side: dict[str, float]
    params: dict = field(default_factory=dict)

    # --- expected cards -------------------------------------------------------------
    def side_mean(
        self, league: str, venue: str, team: str, opponent: str, referee_id
    ) -> float:
        base = self.base.get((league, venue))
        if base is None:  # league never seen: average of all bases
            base = float(np.mean(list(self.base.values())))
        ref = self.referee.get(int(referee_id), 1.0) if _known(referee_id) else 1.0
        return (
            base
            * ref
            * self.receives.get((league, team), 1.0)
            * self.draws.get((league, opponent), 1.0)
        )

    def predict(self, league: str, home: str, away: str, referee_id=None) -> dict:
        mu_h = self.side_mean(league, "home", home, away, referee_id)
        mu_a = self.side_mean(league, "away", away, home, referee_id)
        mu = mu_h + mu_a
        phi_m = self.phi_match.get(league, 0.0)
        phi_s = self.phi_side.get(league, 0.0)
        dist = nb_pmf_array(mu, phi_m, MAX_CARDS)
        out = {
            "expected_cards": mu,
            "expected_home_cards": mu_h,
            "expected_away_cards": mu_a,
            "distribution": dist,
        }
        for line in MATCH_LINES:
            out[f"prob_over_{_k(line)}"] = prob_over(dist, line)
        for side, m in (("home", mu_h), ("away", mu_a)):
            d = nb_pmf_array(m, phi_s, MAX_CARDS)
            for line in TEAM_LINES:
                out[f"{side}_prob_over_{_k(line)}"] = prob_over(d, line)
        return out


def _k(line: float) -> str:
    return str(line).replace(".", "_")


def _known(x) -> bool:
    return (
        x is not None
        and not (isinstance(x, float) and math.isnan(x))
        and not pd.isna(x)
    )


# --- negative binomial -------------------------------------------------------------


def nb_pmf_array(mu: float, phi: float, kmax: int) -> list[float]:
    """P(X = 0..kmax-1) and P(X >= kmax) in the last slot. phi = 0 means Poisson."""
    ks = np.arange(kmax)
    if mu <= 0:
        p = np.zeros(kmax + 1)
        p[0] = 1.0
        return p.tolist()
    if phi <= 1e-9:
        logp = ks * math.log(mu) - mu - np.array([math.lgamma(k + 1) for k in ks])
    else:
        r = 1.0 / phi
        logp = (
            np.array(
                [math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) for k in ks]
            )
            + r * math.log(r / (r + mu))
            + ks * math.log(mu / (r + mu))
        )
    p = np.exp(logp)
    return [*p.tolist(), max(0.0, 1.0 - float(p.sum()))]


def prob_over(dist: list[float], line: float) -> float:
    """P(X > line) for a half line such as 4.5."""
    return float(sum(dist[math.floor(line) + 1 :]))


def log_score(dist: list[float], y: int) -> float:
    """Log of the probability given to what happened (higher is better)."""
    p = dist[y] if y < len(dist) - 1 else dist[-1]
    return math.log(max(p, 1e-12))


# --- fitting -----------------------------------------------------------------------


def side_observations(df: pd.DataFrame) -> pd.DataFrame:
    """Two rows per match (home side, away side) from add_match_metrics output."""
    cols = ["league", "date", "referee_id"]
    home = df[cols].assign(
        venue="home",
        team=df["api_home"],
        opponent=df["api_away"],
        cards=df["home_cards"],
        match_id=df.index,
    )
    away = df[cols].assign(
        venue="away",
        team=df["api_away"],
        opponent=df["api_home"],
        cards=df["away_cards"],
        match_id=df.index,
    )
    return pd.concat([home, away], ignore_index=True)


def fit(
    obs: pd.DataFrame,
    as_of: date,
    half_life_days: float = DEFAULT_PARAMS["half_life_days"],
    k_referee: float = DEFAULT_PARAMS["k_referee"],
    k_team: float = DEFAULT_PARAMS["k_team"],
    iterations: int = 30,
) -> CardModel:
    obs = obs[pd.to_datetime(obs["date"]) < pd.Timestamp(as_of)]
    obs = obs[obs["cards"].notna()]
    age = (pd.Timestamp(as_of) - pd.to_datetime(obs["date"])).dt.days.to_numpy(
        dtype=float
    )
    w = 0.5 ** (age / half_life_days)
    y = obs["cards"].to_numpy(dtype=float)

    b_idx, b_keys = pd.factorize(pd.Index(list(zip(obs["league"], obs["venue"]))))
    t_idx, t_keys = pd.factorize(
        pd.Index(list(zip(obs["league"], obs["team"]))).append(
            pd.Index(list(zip(obs["league"], obs["opponent"])))
        )
    )
    n = len(obs)
    team_idx, opp_idx = t_idx[:n], t_idx[n:]
    has_ref = obs["referee_id"].notna().to_numpy()
    r_idx, r_keys = pd.factorize(obs["referee_id"].fillna(-1).astype(int))

    base = np.bincount(b_idx, w * y, len(b_keys)) / np.bincount(b_idx, w, len(b_keys))
    ref = np.ones(len(r_keys))
    recv = np.ones(len(t_keys))
    draw = np.ones(len(t_keys))
    ref_mask_w = w * has_ref

    for _ in range(iterations):
        mu = base[b_idx] * ref[r_idx] * recv[team_idx] * draw[opp_idx]
        base *= np.bincount(b_idx, w * y, len(b_keys)) / np.bincount(
            b_idx, w * mu, len(b_keys)
        )
        mu = base[b_idx] * ref[r_idx] * recv[team_idx] * draw[opp_idx]
        ref = _gamma_update(ref, r_idx, ref_mask_w, y, mu, k_referee)
        mu = base[b_idx] * ref[r_idx] * recv[team_idx] * draw[opp_idx]
        recv = _gamma_update(recv, team_idx, w, y, mu, k_team)
        mu = base[b_idx] * ref[r_idx] * recv[team_idx] * draw[opp_idx]
        draw = _gamma_update(draw, opp_idx, w, y, mu, k_team)

    referee = {int(k): float(v) for k, v in zip(r_keys, ref) if int(k) != -1}
    mu = base[b_idx] * ref[r_idx] * recv[team_idx] * draw[opp_idx]
    phi_side, phi_match = {}, {}
    obs = obs.assign(_mu=mu, _w=w)
    for league, g in obs.groupby("league"):
        phi_side[league] = _dispersion(
            g["cards"].to_numpy(float),
            g["_mu"].to_numpy(float),
            g["_w"].to_numpy(float),
        )
        m = g.groupby("match_id").agg(
            y=("cards", "sum"), mu=("_mu", "sum"), w=("_w", "first")
        )
        phi_match[league] = _dispersion(
            m["y"].to_numpy(float), m["mu"].to_numpy(float), m["w"].to_numpy(float)
        )
    return CardModel(
        base={tuple(k): float(v) for k, v in zip(b_keys, base)},
        referee=referee,
        receives={tuple(k): float(v) for k, v in zip(t_keys, recv)},
        draws={tuple(k): float(v) for k, v in zip(t_keys, draw)},
        phi_match=phi_match,
        phi_side=phi_side,
        params={
            "half_life_days": half_life_days,
            "k_referee": k_referee,
            "k_team": k_team,
            "as_of": str(as_of),
        },
    )


def _gamma_update(factor, idx, w, y, mu, k):
    """factor_j <- (sum w*y + k) / (sum w*mu/factor_j + k): Poisson-Gamma posterior mean."""
    n = len(factor)
    observed = np.bincount(idx, w * y, n)
    expected_without = np.bincount(idx, w * mu, n) / factor
    return (observed + k) / (expected_without + k)


def _dispersion(y: np.ndarray, mu: np.ndarray, w: np.ndarray) -> float:
    """Method-of-moments phi in var = mu + phi*mu^2 (0 = Poisson)."""
    num = float(np.sum(w * ((y - mu) ** 2 - mu)))
    den = float(np.sum(w * mu**2))
    return max(0.0, num / den) if den > 0 else 0.0
