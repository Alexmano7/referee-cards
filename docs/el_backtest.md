# EuroLeague player picks: walk-forward backtest

Each week is predicted only from earlier games; bets on players who did not play are void.

## Pick record (5 picks per week)

| Period | Picks | Won | Hit rate | Model said (raw) | Shown (corrected) | Typical player | Break-even odds |
|---|---|---|---|---|---|---|---|
| 2025/26 | 157 | 103 | 65.6% | 70.6% | 65.5% | 27.7% | 1.52 |
| 2026/27 so far | 27 | 18 | 66.7% | 72.4% | 66.8% | 35.8% | 1.5 |

## All over-line chances (115423 predictions)

| Method | Log loss (lower is better) | Brier (lower is better) |
|---|---|---|
| Model | 0.4461 | 0.1448 |
| Typical player in the role | 0.4882 | 0.1596 |

## Calibration

| Model chance | Predictions | Predicted | Happened |
|---|---|---|---|
| 0-20% | 57149 | 7.5% | 8.6% |
| 20-35% | 21687 | 27.1% | 25.8% |
| 35-50% | 15207 | 42.1% | 41.1% |
| 50-65% | 11309 | 57.0% | 55.5% |
| 65-80% | 7736 | 71.8% | 71.5% |
| 80-100% | 2335 | 86.0% | 85.3% |
