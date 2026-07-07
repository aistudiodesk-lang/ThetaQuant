# 007 — Realistic Broker Cost + Final Winner

## Cost model used (Rohan's session 2026-04-23 input)

| Component | Rate / value |
|---|---|
| Brokerage (Axis) | ₹6 / lot / transaction |
| Brokerage (Monarch) | ₹10 / lot / transaction |
| STT (options sell) | 0.10% of sell premium |
| Exchange charges | ~0.053% of premium turnover |
| SEBI | ~₹10 / crore (negligible) |
| GST | 18% on (brokerage + exchange + SEBI) |
| Stamp duty | 0.003% on BUY side only — not applicable when selling and letting expire |
| Funding | max ₹600 / Cr of margin / event (applied conservatively) |
| Trade pattern | SELL-only when option expires worthless; SELL+BUY when squared off intra-day |

Per-lot friction at 2.5% OTM E-1 (combined premium ≈ ₹7.50 / share):
- **Axis no square-off: ~₹26 / lot** (was modelling ₹400 placeholder)
- **Axis with square-off: ~₹40 / lot**
- **Monarch no square-off: ~₹35 / lot**
- **Monarch with square-off: ~₹60 / lot**
- Plus ₹10.91 / lot funding (conservative)

Square-off only applies on days where the option does NOT expire worthless. At 2.5%+ OTM E-1 in the sample, that's 0 days — so friction is uniform-low.

## Top viable configurations (real cost, breach ≤ 5%)

| event | dist % | broker | days | avg friction ₹/lot | win % | avg net ₹/lot | worst ₹/lot | pf mean ₹/event | pf worst ₹/event | ann % on ₹1Cr | breach % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| E-1 | 2.0 | Axis | 46 | 32.1 | 95.7 | 1,718 | -2,726 | ₹94,516 | ₹-149,950 | **43.48%** | 4.3% |
| E-1 | 2.0 | Monarch | 46 | 42.0 | 95.7 | 1,709 | -2,745 | ₹93,975 | ₹-150,989 | **43.23%** | 4.3% |
| E-1 | 2.5 | Axis | 46 | 29.1 | 100.0 | 1,208 | 153 | ₹66,419 | ₹8,433 | **30.55%** | 0.0% |
| E-1 | 2.5 | Monarch | 46 | 38.6 | 100.0 | 1,198 | 144 | ₹65,900 | ₹7,914 | **30.31%** | 0.0% |
| E-1 | 3.0 | Axis | 46 | 27.9 | 100.0 | 830 | 120 | ₹45,644 | ₹6,624 | **21.0%** | 0.0% |
| E-1 | 3.0 | Monarch | 46 | 37.3 | 100.0 | 820 | 111 | ₹45,125 | ₹6,105 | **20.76%** | 0.0% |
| E-0 | 2.0 | Axis | 48 | 27.7 | 97.9 | 504 | -776 | ₹27,743 | ₹-42,663 | **13.32%** | 2.1% |
| E-0 | 2.0 | Monarch | 48 | 37.5 | 97.9 | 495 | -795 | ₹27,203 | ₹-43,701 | **13.06%** | 2.1% |
| E-1 | 4.0 | Axis | 46 | 26.7 | 100.0 | 462 | 63 | ₹25,394 | ₹3,444 | **11.68%** | 0.0% |
| E-1 | 4.0 | Monarch | 46 | 36.1 | 100.0 | 452 | 53 | ₹24,875 | ₹2,925 | **11.44%** | 0.0% |
| E-1 | 5.0 | Axis | 46 | 26.1 | 100.0 | 294 | 47 | ₹16,182 | ₹2,567 | **7.44%** | 0.0% |
| E-1 | 5.0 | Monarch | 46 | 35.6 | 100.0 | 285 | 37 | ₹15,663 | ₹2,048 | **7.2%** | 0.0% |


## 🏆 Winner

**E-1 · 2.0% OTM · Axis broker**

- Sell NIFTY CE+PE ~2.0% from spot at 10:00 IST on E-1 days (Mon → Tue expiry, or Wed → Thu legacy expiry).
- Hold both legs to expiry close (15:25 next day). Don't square off.
- Run **55 lots per ₹1 Cr capital** (uniform sizing).
- Expected: **43.48% annualized on ₹1Cr**, win rate 95.7%, average ₹94,516 per event, worst event ₹-149,950, **4.3% breach rate of your ₹7K/Cr cap.**
- Real all-in friction works out to ~₹32 per lot per event (vs my ₹400 placeholder — 14× over-estimate).

## Why this beats the placeholder analysis

Earlier (006) headline at ₹100/lot placeholder said E-1 · 2.5% OTM · 29% annualized.
With your **real costs (~₹26/lot Axis), the same configuration produces ~30.6%**.

The deeper distances (3-4%) also become unambiguously profitable at real cost — they were marginal at placeholder friction.

## Caveats

- 46 E-1 days is ~1 year of data. Scale annualization with caution; cross-validation across years is the next step (2024 data needed).
- Funding cost of ₹10.91/lot is applied to every event conservatively. If it actually only fires occasionally, returns are slightly higher.
- Assumes uniform lot sizing. Laddering across distances may improve worst-case further.
- STT on assignment (0.125% of intrinsic) would apply if any leg expires ITM. None did in the 2.5%+ OTM sample; if it ever happens it's a meaningful event-level loss spike.
- Doesn't model bid-ask slippage on entry — minute-bar close used as fill price.

## Files
- `realistic_winners.csv` — full event × distance × broker matrix
- `expected_pnl_by_distance.png` — avg per-lot net by distance and broker
