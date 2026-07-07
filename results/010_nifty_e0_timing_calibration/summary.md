# 010 — NIFTY E-0 Timing Calibration

Sample: **48 NIFTY weekly E-0 days** (2025-04-22 → 2026-04-28).
Goal: when does combined CE+PE premium at 2.5% OTM peak — and what predicts it?

## Regime classification

A day is classified as:
- **calm**: peak premium ≤ 105% of 9:30 premium (or peaks before 10:00) — i.e. theta wins from open
- **vega**: peak premium > 110% of 9:30 AND occurs after 10:00 — IV expansion drove a later peak
- **borderline**: in-between

```
regime
borderline    36
vega           9
calm           3
```

## Average premium path by regime

| Regime | n | 9:15 | 9:20 | 9:30 | 9:45 | 10:00 | 10:30 | 11:00 | 11:30 | 12:00 | Peak (avg) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **calm** | 3 | 3.38 | 4.22 | 4.93 | 4.1 | 3.42 | 2.78 | 3.03 | 2.58 | 2.05 | 5.08 |
| **vega** | 9 | 3.29 | 2.87 | 2.72 | 3.77 | 3.74 | 3.33 | 3.37 | 3.69 | 3.12 | 5.38 |
| **borderline** | 36 | 6.81 | 4.79 | 4.47 | 4.08 | 3.55 | 2.92 | 2.5 | 2.3 | 1.92 | 7.45 |

## Signals predicting vega regime

| Signal | n where TRUE | n where FALSE | vega% TRUE | vega% FALSE | discrimination |
|---|---|---|---|---|---|
| |f30_drift| > 0.5% | 22 | 26 | 18.2% | 19.2% | **-1.0** |
| |gap_pct| > 0.4 | 29 | 19 | 17.2% | 21.1% | **-3.8** |
| |f15_drift| > 0.3% | 34 | 14 | 14.7% | 28.6% | **-13.9** |
| |gap_pct| > 0.7 | 14 | 34 | 7.1% | 23.5% | **-16.4** |


## Calibrated rule (extracted from data above)

(Refine threshold combinations after reviewing regime_signal_table.csv)

## Files
- `per_day.csv` — every E-0 day's signals + premium path + regime classification
- `regime_signal_table.csv` — signal discrimination
- `avg_premium_by_minute_per_regime.csv` — premium curve by regime
- `premium_path_by_regime.png` — visual of the premium curves
