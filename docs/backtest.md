# Card model backtest

Generated 2026-11-07 by `uv run referees backtest`. Walk-forward: each week is predicted from matches before that week only.

**Chosen settings** (best log score on 2025-07-01 to 2026-07-01): half-life 365 days, referee prior 80.0, team prior 80.0.

## Holdout: 2026-07-01 to 2026-11-07 (not used for tuning)

| Method | Matches | Log score ↑ | Brier 3.5 ↓ | Brier 4.5 ↓ | Brier 5.5 ↓ | MAE ↓ |
|---|---|---|---|---|---|---|
| Model (tuned) | 763 | -2.1475 | 0.2316 | 0.2292 | 0.1836 | 1.704 |
| Current dashboard method | 763 | -2.1532 | 0.2326 | 0.2292 | 0.1843 | 1.714 |
| League average only | 763 | -2.1634 | 0.2371 | 0.2333 | 0.1867 | 1.721 |

## Tuning season (in-sample for the settings)

| Method | Matches | Log score ↑ | Brier 3.5 ↓ | Brier 4.5 ↓ | Brier 5.5 ↓ | MAE ↓ |
|---|---|---|---|---|---|---|
| Model (tuned) | 2833 | -2.1715 | 0.2285 | 0.2322 | 0.184 | 1.746 |

## Calibration of P(over 4.5 cards), both periods

| Predicted P(over 4.5) | Matches | Predicted | Observed |
|---|---|---|---|
| 0-20% | 10 | 18.7% | 10.0% |
| 20-35% | 1010 | 30.5% | 28.7% |
| 35-50% | 1665 | 41.3% | 41.7% |
| 50-65% | 734 | 56.9% | 55.6% |
| 65-80% | 176 | 68.9% | 63.6% |
| 80-100% | 1 | 80.7% | 0.0% |

## Hot Prediction rule (3 picks per week, chosen from earlier data only)

| Period | Picks | Won | Hit rate | Model said | Typical match | Break-even odds |
|---|---|---|---|---|---|---|
| 2026/27 so far | 39 | 26 | 66.7% | 65.0% | 49.8% | 1.5 |
| 2025/26 | 121 | 82 | 67.8% | 64.9% | 48.0% | 1.48 |

## Safe treble (3 legs, combined fair odds about 2.00, one per week)

| Period | Trebles | Won | Win rate | Legs won | Model said | Typical match | Break-even odds |
|---|---|---|---|---|---|---|---|
| 2026/27 so far | 12 | 5 | 41.7% | 80.6% | 47.7% | 26.9% | 2.4 |
| 2025/26 | 40 | 20 | 50.0% | 80.8% | 47.8% | 25.3% | 2.0 |
