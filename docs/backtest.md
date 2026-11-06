# Card model backtest

Generated 2026-11-07 by `uv run referees backtest`. Walk-forward: each week is predicted from matches before that week only.

**Chosen settings** (best log score on 2025-07-01 to 2026-07-01): half-life 180 days, referee prior 80.0, team prior 40.0.

## Holdout: 2026-07-01 to 2026-11-07 (not used for tuning)

| Method | Matches | Log score ↑ | Brier 3.5 ↓ | Brier 4.5 ↓ | Brier 5.5 ↓ | MAE ↓ |
|---|---|---|---|---|---|---|
| Model (tuned) | 352 | -2.1021 | 0.2336 | 0.23 | 0.1868 | 1.632 |
| Current dashboard method | 352 | -2.1139 | 0.2347 | 0.2308 | 0.1879 | 1.652 |
| League average only | 352 | -2.1239 | 0.2386 | 0.2336 | 0.1908 | 1.654 |

## Tuning season (in-sample for the settings)

| Method | Matches | Log score ↑ | Brier 3.5 ↓ | Brier 4.5 ↓ | Brier 5.5 ↓ | MAE ↓ |
|---|---|---|---|---|---|---|
| Model (tuned) | 1372 | -2.1576 | 0.2376 | 0.2333 | 0.1731 | 1.715 |

## Calibration of P(over 4.5 cards), both periods

| Predicted P(over 4.5) | Matches | Predicted | Observed |
|---|---|---|---|
| 0-20% | 7 | 18.7% | 14.3% |
| 20-35% | 560 | 30.1% | 30.7% |
| 35-50% | 934 | 41.5% | 41.6% |
| 50-65% | 216 | 54.8% | 54.2% |
| 65-80% | 7 | 67.6% | 42.9% |

## Hot Prediction rule (3 picks per week, chosen from earlier data only)

| Period | Picks | Won | Hit rate | Model said | Typical match | Break-even odds |
|---|---|---|---|---|---|---|
| 2026/27 so far | 33 | 22 | 66.7% | 64.4% | 51.5% | 1.5 |
| 2025/26 | 111 | 82 | 73.9% | 66.3% | 49.6% | 1.35 |

## Safe treble (3 legs, combined fair odds about 2.00, one per week)

| Period | Trebles | Won | Win rate | Legs won | Model said | Typical match | Break-even odds |
|---|---|---|---|---|---|---|---|
| 2026/27 so far | 9 | 4 | 44.4% | 74.1% | 47.8% | 28.9% | 2.25 |
| 2025/26 | 37 | 18 | 48.6% | 80.2% | 48.0% | 25.6% | 2.06 |
