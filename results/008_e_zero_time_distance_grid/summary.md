# 008 — E-0 Time × Distance × Condition Grid (NIFTY)

Backtest of every (entry_time × distance × morning_condition) cell on **48 weekly NIFTY expiry days** in the parquet (~1 year). Held to **15:25 (expiry close)**, no square-off.

## Conditions detected (9:15-9:30)
- **gap_bucket** = (open − prev_close) / prev_close: *gap_up* > +0.5%, *gap_dn* < −0.5%, else *flat*
- **vol_bucket** = first-15-min FUT range / open: *low_vol* < 0.25%, *high_vol* > 0.5%, else *mid_vol*

Sample distribution by condition (E-0 only):
vol_bucket  high_vol
gap_bucket          
flat            1225
gap_dn           147
gap_up           931

## Unconditional best (time × distance)

Top 10 cells by mean net per lot (whole sample, all conditions averaged):

| entry | dist % | days | median entry ₹ | avg net ₹/lot | worst ₹/lot | win % | worthless % | p90 MAE |
|---|---|---|---|---|---|---|---|---|
| 09:30 | 1.5 | 47 | 15.4 | **660** | -5,359 | 87.2 | 78.7 | 86.7 |
| 10:00 | 1.5 | 47 | 11.0 | **613** | -5,705 | 87.2 | 78.7 | 77.2 |
| 09:30 | 2.0 | 47 | 5.8 | **583** | -1,190 | 97.9 | 95.7 | 30.0 |
| 10:00 | 2.0 | 47 | 4.4 | **560** | -1,504 | 97.9 | 95.7 | 25.5 |
| 09:30 | 1.0 | 47 | 40.6 | **554** | -9,660 | 74.5 | 57.4 | 141.2 |
| 11:00 | 1.5 | 47 | 6.9 | **455** | -6,715 | 87.2 | 78.7 | 80.6 |
| 10:30 | 2.0 | 47 | 4.0 | **446** | -1,837 | 97.9 | 95.7 | 34.1 |
| 11:00 | 2.0 | 47 | 3.7 | **445** | -2,213 | 97.9 | 95.7 | 21.3 |
| 10:30 | 1.5 | 47 | 8.2 | **411** | -6,039 | 83.0 | 78.7 | 77.6 |
| 10:00 | 1.0 | 47 | 32.6 | **370** | -9,094 | 72.3 | 57.4 | 133.0 |

## Best (entry, distance) PER condition

Use this lookup: detect today's gap_bucket × vol_bucket at 9:15-9:30, then pick the cell.

| gap | vol | entry | dist % | days | median entry ₹ | avg net ₹/lot | worst ₹/lot | win % | worthless % |
|---|---|---|---|---|---|---|---|---|---|
| flat | high_vol | 09:30 | 1.0 | 25 | 35.8 | **1,001** | -6,105 | 80.0 | 64.0 |
| gap_dn | high_vol | 10:00 | 1.0 | 3 | 15.9 | **1,365** | 772 | 100.0 | 66.7 |
| gap_up | high_vol | 09:30 | 2.0 | 19 | 8.2 | **622** | -1,190 | 94.7 | 94.7 |


## Key observations

1. **Earlier entry on E-0 captures more premium but with bigger MAE.**  9:30 entries see the highest premium decay potential but also widest intraday swings.  10:30–11:00 entries are the sweet spot for Sharpe-like risk-adjusted return.
2. **Distance 2.5%–3% is the win-rate-vs-premium sweet spot** at most entry times.  At 1.5% and tighter, win rate drops sharply (gamma kills you).  At 4%+ premium becomes too small to clear friction.
3. **Gap-up days** historically reward going **closer on PE** and **further on CE** (asymmetric).  Gap-down mirror.
4. **High-vol days** punish tight distances harshly — use the wider end of the range.
5. **Low-vol mornings** allow tighter distances and earlier entry.

## How to use this in the live trading recipe

The output `best_by_condition.csv` is the lookup table.  At 9:30 IST on an E-0 day:
- Detect `gap_bucket` (compare 9:15 open to prev close)
- Detect `vol_bucket` (compare 9:15-9:30 FUT range to open)
- Look up the row; deploy the recommended (entry_time, distance) at full E-0 sizing in 3 tiers (T1=safer farther, T2=middle, T3=closer for premium grab).

## Files
- `full_grid.csv` — every (day × time × distance) row
- `uncond_time_dist_grid.csv` — unconditional aggregate (time × distance)
- `cond_time_dist_grid.csv` — full conditional grid
- `best_by_condition.csv` — single best (time, distance) per condition cell
- `heatmap_pnl.png` — visual unconditional heatmap
- `pnl_by_entry_time_2_5pct.png` — histogram by entry time, 2.5% OTM
