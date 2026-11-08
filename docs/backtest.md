# Card model backtest

Generated 2026-11-08 by `uv run referees backtest`. Walk-forward: each week is predicted from matches before that week only.

**Chosen settings** (best log score on 2025-07-01 to 2026-07-01): half-life 540 days, referee prior 80.0, team prior 80.0.

## Holdout: 2026-07-01 to 2026-11-08 (not used for tuning)

| Method | Matches | Log score ↑ | Brier 3.5 ↓ | Brier 4.5 ↓ | Brier 5.5 ↓ | MAE ↓ |
|---|---|---|---|---|---|---|
| Model (tuned) | 763 | -2.1458 | 0.2309 | 0.2284 | 0.183 | 1.701 |
| Current dashboard method | 763 | -2.1517 | 0.2318 | 0.2283 | 0.1838 | 1.709 |
| League average only | 763 | -2.1639 | 0.2372 | 0.2334 | 0.1867 | 1.722 |

## Tuning season (in-sample for the settings)

| Method | Matches | Log score ↑ | Brier 3.5 ↓ | Brier 4.5 ↓ | Brier 5.5 ↓ | MAE ↓ |
|---|---|---|---|---|---|---|
| Model (tuned) | 2833 | -2.1703 | 0.228 | 0.2318 | 0.1836 | 1.743 |

## Calibration of P(over 4.5 cards), both periods

| Predicted P(over 4.5) | Matches | Predicted | Observed |
|---|---|---|---|
| 0-20% | 19 | 18.4% | 5.3% |
| 20-35% | 984 | 30.3% | 28.5% |
| 35-50% | 1666 | 41.4% | 42.0% |
| 50-65% | 698 | 56.7% | 54.6% |
| 65-80% | 228 | 69.1% | 63.2% |
| 80-100% | 1 | 81.0% | 0.0% |

## Hot Prediction rule (5 picks per week, chosen from earlier data only)

| Period | Picks | Won | Hit rate | Model said | Typical match | Break-even odds |
|---|---|---|---|---|---|---|
| 2026/27 so far | 65 | 48 | 73.8% | 64.9% | 49.0% | 1.35 |
| 2025/26 | 201 | 142 | 70.6% | 65.1% | 48.0% | 1.42 |

## Safe treble (3 legs, combined fair odds about 2.00, one per week)

| Period | Trebles | Won | Win rate | Legs won | Model said | Typical match | Break-even odds |
|---|---|---|---|---|---|---|---|
| 2026/27 so far | 12 | 7 | 58.3% | 86.1% | 48.2% | 24.8% | 1.71 |
| 2025/26 | 40 | 20 | 50.0% | 82.5% | 47.9% | 23.5% | 2.0 |
