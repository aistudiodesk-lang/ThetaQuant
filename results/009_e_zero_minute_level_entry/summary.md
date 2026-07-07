# 009 — E-0 Minute-Level Entry Sweep (NIFTY)

Two questions:
1. Was 9:20-9:45 entry at 2.5%+ OTM → 100% success in last 1 year?
2. What's the absolute best minute to enter for max premium?

Sample: **48 NIFTY weekly E-0 days** (2025-04-17 → 2026-04-21).
Distances tested: [2.5, 3.0, 4.0, 5.0] % OTM (symmetric).
Entry minutes tested: 9:15-10:30 (every 1 min).
Hold: to 15:25 expiry close. Friction: real Axis (₹6/lot).

## Rohan's claim verification: 9:20-9:45 entry × ≥2.5% OTM

| distance % | events | worthless % | win % (net positive) | avg net ₹/lot | worst net ₹/lot |
|---|---|---|---|---|---|
| 2.5 | 1248 | 100.0 | 100.0 | 264 | 53 |
| 3.0 | 1248 | 100.0 | 100.0 | 167 | 33 |
| 4.0 | 1248 | 100.0 | 100.0 | 95 | 20 |
| 5.0 | 1248 | 100.0 | 100.0 | 67 | 11 |


## Best entry minute per distance (top 3 by avg net)

| dist % | entry | days | median entry ₹ | avg net ₹/lot | worst ₹/lot | worthless % |
|---|---|---|---|---|---|---|
| 2.5 | 09:15 | 48 | 3.3 | **374** | 53 | 100.0 |
| 2.5 | 09:17 | 48 | 3.2 | **312** | 53 | 100.0 |
| 2.5 | 09:16 | 48 | 3.0 | **312** | 49 | 100.0 |
| 3.0 | 09:15 | 48 | 2.38 | **231** | 40 | 100.0 |
| 3.0 | 09:16 | 48 | 2.33 | **200** | 36 | 100.0 |
| 3.0 | 09:17 | 48 | 2.33 | **199** | 40 | 100.0 |
| 4.0 | 09:15 | 48 | 1.78 | **125** | 30 | 100.0 |
| 4.0 | 09:16 | 48 | 1.78 | **114** | 27 | 100.0 |
| 4.0 | 09:17 | 48 | 1.75 | **110** | 27 | 100.0 |
| 5.0 | 09:15 | 48 | 1.55 | **88** | 17 | 100.0 |
| 5.0 | 09:16 | 48 | 1.52 | **81** | 20 | 100.0 |
| 5.0 | 09:17 | 48 | 1.48 | **79** | 17 | 100.0 |


## Charts
- `premium_decay_chart.png` — how avg entry premium fades by minute
- `net_pnl_chart.png` — avg net P&L per lot by minute

## Files
- `minute_grid.csv` — full per-event data
- `minute_distance_grid.csv` — aggregate (entry × distance)
- `claim_check_9_20_to_9_45.csv` — verification table
- `best_entry_per_distance.csv` — top 3 minutes per distance
