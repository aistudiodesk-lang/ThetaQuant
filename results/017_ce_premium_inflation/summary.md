# 017 — CE premium inflation pattern after 9:30 on NIFTY E-0 days

_Generated 2026-05-12 16:10 IST_

## Question

> How often does CE premium INCREASE between 9:30 and 11:00 on E-0 days despite spot falling? What conditions cause it?

## Sample
- 55 NIFTY E-0 days in parquet store (2025-04 to 2026-05).

## Pattern frequency

| Pattern | n | % of days |
|---------|---|-----------|
| flat | 19 | 34.5% |
| normal-down | 17 | 30.9% |
| normal-up | 16 | 29.1% |
| confused-up (theta>delta) | 3 | 5.5% |

## Average stats per pattern

| Pattern | n | avg spot Δ% | avg ATM CE Δ% | avg 3%OTM CE Δ% | avg VIX-proxy | avg |gap|% | avg range to 10:30 |
|---------|---|-------------|---------------|------------------|----------------|------------|---------------------|
| confused-up (theta>delta) | 3 | +0.14% | -26.1% | -45.7% | 13.12 | -0.26% | 0.67% |
| flat | 19 | -0.00% | -32.5% | -20.4% | 11.26 | +0.13% | 0.55% |
| normal-down | 17 | -0.31% | -58.4% | -3.2% | 15.27 | -0.13% | 0.64% |
| normal-up | 16 | +0.33% | +109.0% | -29.7% | 15.08 | -0.03% | 0.55% |

## Top INFLATION days (biggest ATM CE rise on a down-spot morning)

## Findings (mechanistic)


## How to use this in live trading

- **Don't enter B1 CE shorts during 9:30-11:00 on high-VIX-proxy days** — premium likely still inflating.
- **Best entry window for B1 CE on E-0**: 12:30-13:00 (post inflation, pre afternoon collapse).
- **9:17-9:22 entry for Bucket A (deep OTM, ≥2.5%)**: still optimal — deep OTM legs less affected by the vega re-expansion; you capture the open premium then ride through.
- **If you must enter B1 CE in the morning** — size HALF and tolerate MTM drawdown to 12:00.

## Files

- `per_day_results.csv` — every E-0 day with all timing slices and pattern label
- `pattern_summary.csv` — counts + avg stats per pattern
- `inflation_days.csv` — only INFLATION-pattern days, ranked by ATM CE %Δ
- `inflation_scatter.png` — VIX-proxy vs ATM CE %Δ scatter, color-coded by pattern