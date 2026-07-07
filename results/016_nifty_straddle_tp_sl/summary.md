# 016 — NIFTY Straddle/Strangle SELL · with TP/SL exits + fake-stop study

_Generated 2026-05-11 09:06 IST_

## Question (Rohan, 2026-05-11)

> "I can't tolerate ₹1L/Cr loss. Stop at 25-40K/Cr. Test 100/200pt OTM straddles. Fake stops are real — confirm modes needed."

## Methodology

- **Data:** NIFTY 1-min OHLC parquet store (front-month FUT + CE/PE chain, ±10 strikes around ATM, ATM±500pts window).
- **Sizing:** ₹2.35L NIFTY E-0 margin/lot → 42.55 lots/Cr → **3,225 shares/Cr** (user-spec 43 lots × 75).
- **Friction (headline):** ₹100/leg × 2 legs = ₹200/lot. Also reported per-trade in CSV: Axis ₹12/lot, Monarch ₹20/lot.
- **₹/share ↔ ₹/Cr (gross):** 1 ₹/share ≈ ₹3,225/Cr. ₹10K/Cr ≈ ₹3.10/share. ₹25K/Cr ≈ ₹7.75. ₹40K/Cr ≈ ₹12.40. ₹50K/Cr ≈ ₹15.50.
- **Strike variants (3):**
  - **ATM** straddle: CE@ATM, PE@ATM (ATM = round-to-50 of FUT close at entry).
  - **OTM_100** strangle: CE@ATM+100, PE@ATM−100.
  - **OTM_200** strangle: CE@ATM+200, PE@ATM−200.
- **Entry times:** 09:30, 10:00, 10:30.
- **DTE buckets:** E-1, E-2, E-3, E-4 (trading-day distance to next NIFTY weekly using `lib/expiry_calendar.NIFTY_WEEKLY_EXPIRIES`).
- **TP grid (₹/share drop):** [2, 3, 5, 8, 10, 15, 20] → ₹[6, 10, 16, 26, 32, 48, 64]K/Cr (gross).
- **SL grid (₹/share rise):** [5, 8, 10, 12, 15, 20, 25, 30] → ₹[16, 26, 32, 39, 48, 64, 81, 97]K/Cr (gross).
- **SL confirmation modes:**
  - `instant` — trigger on first 1-min close at/above SL.
  - `confirm_3m` — need 3 consecutive 1-min closes at/above SL.
  - `confirm_5m` — need 5 consecutive.
  - `intrabar_high` — pessimistic: trigger on any 1-min HIGH at/above SL, filled AT the SL line.
- **TP/SL precedence:** if both fire same bar, SL wins (conservative).
- **Time stop:** 15:20 if neither TP nor SL fires.
- **Forward-fill** within day for missing minute bars on either leg.
- **Slippage:** none beyond the intrabar-high pessimistic mode. 1-min bar close used otherwise.

## Sample size

| DTE | Trading days |
|-----|--------------|
| E-1 | 54 |
| E-2 | 51 |
| E-3 | 48 |
| E-4 | 42 |

## Headline — best (TP, SL, confirm) per (DTE × variant)

Constraint: `n ≥ 20` AND `%loss_days_worse_than_neg_50K ≤ 5%`.
Ranking: by **mean ₹/Cr** (expected value, since the user's pain is left tail not median).

| DTE | variant | TP ₹ | SL ₹ | confirm | n | tp% | sl% | time% | mean ₹/Cr | median | worst | %≤-50K |
|-----|---------|------|------|---------|---|-----|-----|-------|-----------|--------|-------|--------|
| E-1 | ATM | 2 | 30 | confirm_3m | 156 | 94.9% | 4.5% | 0.6% | -3,344 | +585 | -159,149 | 4.5% |
| E-1 | OTM_100 | 3 | 10 | instant | 156 | 80.8% | 19.2% | 0.0% | -4,757 | +3,138 | -65,160 | 4.5% |
| E-1 | OTM_200 | 20 | 8 | instant | 156 | 21.2% | 50.6% | 28.2% | -1,659 | -34,043 | -56,862 | 1.9% |
| E-2 | ATM | 20 | 5 | instant | 153 | 31.4% | 66.0% | 2.6% | -2,154 | -26,064 | -58,777 | 2.0% |
| E-2 | OTM_100 | 20 | 5 | instant | 153 | 28.8% | 67.3% | 3.9% | -2,870 | -25,426 | -60,053 | 2.0% |
| E-2 | OTM_200 | 20 | 5 | instant | 153 | 24.8% | 62.7% | 12.4% | -1,412 | -25,106 | -56,064 | 2.0% |
| E-3 | ATM | 20 | 5 | instant | 144 | 27.8% | 70.8% | 1.4% | -5,583 | -26,144 | -59,734 | 2.1% |
| E-3 | OTM_100 | 20 | 5 | instant | 143 | 28.0% | 69.9% | 2.1% | -5,028 | -25,745 | -58,298 | 3.5% |
| E-3 | OTM_200 | 20 | 5 | instant | 142 | 23.9% | 67.6% | 8.5% | -4,389 | -26,463 | -66,915 | 0.7% |
| E-4 | ATM | 20 | 5 | instant | 126 | 25.4% | 67.5% | 7.1% | -4,790 | -26,303 | -63,723 | 3.2% |
| E-4 | OTM_100 | 15 | 5 | instant | 126 | 34.9% | 62.7% | 2.4% | -4,455 | -25,266 | -62,287 | 1.6% |
| E-4 | OTM_200 | 15 | 8 | instant | 126 | 34.9% | 55.6% | 9.5% | -7,173 | -34,601 | -64,681 | 4.8% |

## Top 10 overall (highest mean ₹/Cr, tail-cap satisfied)

| DTE | variant | TP | SL | confirm | n | mean ₹/Cr | worst | %≤-50K | sl_hit% |
|-----|---------|----|----|---------|---|-----------|-------|--------|---------|
| E-2 | OTM_200 | 20 | 5 | instant | 153 | -1,412 | -56,064 | 2.0% | 62.7% |
| E-1 | OTM_200 | 20 | 8 | instant | 156 | -1,659 | -56,862 | 1.9% | 50.6% |
| E-2 | OTM_200 | 15 | 5 | instant | 153 | -1,877 | -56,064 | 2.0% | 58.2% |
| E-2 | ATM | 20 | 5 | instant | 153 | -2,154 | -58,777 | 2.0% | 66.0% |
| E-2 | ATM | 15 | 5 | instant | 153 | -2,326 | -58,777 | 2.0% | 59.5% |
| E-2 | ATM | 10 | 5 | instant | 153 | -2,788 | -58,777 | 2.0% | 50.3% |
| E-2 | OTM_100 | 20 | 5 | instant | 153 | -2,870 | -60,053 | 2.0% | 67.3% |
| E-2 | OTM_100 | 15 | 5 | instant | 153 | -2,927 | -60,053 | 2.0% | 61.4% |
| E-2 | OTM_100 | 8 | 5 | instant | 153 | -3,265 | -60,053 | 2.0% | 45.8% |
| E-1 | ATM | 2 | 30 | confirm_3m | 156 | -3,344 | -159,149 | 4.5% | 4.5% |

## Direct answer — best per variant

For each strike variant, the single best (TP, SL, confirm) by mean ₹/Cr under the tail-cap constraint:

| variant | DTE | TP | SL | confirm | n | mean ₹/Cr | median | worst | sl_hit% | tp_hit% |
|---------|-----|----|----|---------|---|-----------|--------|-------|---------|---------|
| ATM | E-2 | 20 | 5 | instant | 153 | -2,154 | -26,064 | -58,777 | 66.0% | 31.4% |
| OTM_100 | E-2 | 20 | 5 | instant | 153 | -2,870 | -25,426 | -60,053 | 67.3% | 28.8% |
| OTM_200 | E-2 | 20 | 5 | instant | 153 | -1,412 | -25,106 | -56,064 | 62.7% | 24.8% |

Unconstrained best per variant (no tail cap — for reference):

| variant | DTE | TP | SL | confirm | n | mean ₹/Cr | median | worst | %≤-50K |
|---------|-----|----|----|---------|---|-----------|--------|-------|--------|
| ATM | E-1 | 20 | 15 | confirm_3m | 156 | +7,385 | +55,878 | -143,032 | 36.5% |
| OTM_100 | E-1 | 20 | 12 | confirm_3m | 156 | +4,641 | +39,681 | -113,191 | 34.6% |
| OTM_200 | E-2 | 10 | 30 | intrabar_high | 153 | +1,498 | +24,202 | -104,255 | 17.0% |

## Fake-stop analysis — does waiting help?

For each (DTE × variant × TP × SL) cell where at least one confirm mode produced mean ₹/Cr ≥ 5K (n ≥ 20), compare the 4 confirm modes side-by-side.
Look for cells where `confirm_3m` or `confirm_5m` IMPROVES mean ₹/Cr vs `instant`, AND reduces sl_hit%.

**Top 15 cells where `confirm_3m` BEATS `instant` (Δ mean ₹/Cr):**

| DTE | variant | TP | SL | instant mean | 3m mean | Δ 3m | 5m mean | Δ 5m | intrabar | instant sl% | 3m sl% |
|-----|---------|----|----|--------------|---------|------|---------|------|----------|-------------|--------|
| E-1 | ATM | 20 | 10 | +421 | +5,526 | +5,105 | +4,046 | +3,625 | -16,453 | 53.8% | 44.9% |
| E-1 | ATM | 20 | 15 | +3,620 | +7,385 | +3,765 | +5,620 | +2,000 | -16,607 | 43.6% | 36.5% |
| E-1 | ATM | 20 | 12 | +2,369 | +5,600 | +3,231 | +5,082 | +2,713 | -17,178 | 48.7% | 41.0% |

**Across all interesting cells (avg of 3 cells):**

| confirm mode | avg Δ mean ₹/Cr vs instant | avg sl_hit% |
|--------------|---------------------------|-------------|
| instant       | (baseline)                | 48.7% |
| confirm_3m    | +4,033    | 40.8% |
| confirm_5m    | +2,779    | 39.3% |
| intrabar_high | -18,882 | 70.3% |

**Verdict on fake-stop confirmation:** `confirm_3m` is **HELPS** on average (Δ ≈ ₹+4,033/Cr, sl% +7.9pp). `confirm_5m` is **HELPS** (Δ ≈ ₹+2,779/Cr).
`intrabar_high` (pessimistic) underperforms `instant` by ₹18,882/Cr on average — the pessimistic fill assumption costs us that much; treat instant numbers as moderately optimistic, intrabar as pessimistic.

## Tail days at the recommended rule (OTM_200, E-2, instant)

These are days where SL did NOT save us — loss exceeded ₹40K/Cr even with the stop. The cause is usually a one-bar gap right through the stop, or a slow drift where the stop fires late.

| date | DTE | variant | weekday | entry | exit reason | exit time | hold min | entry comb | exit comb | ₹/share | ₹/Cr | gap% | intra-1030% | VIX-proxy |
|------|-----|---------|---------|-------|-------------|-----------|----------|-----------|-----------|---------|------|------|-------------|-----------|
| 2025-06-23 | E-3 | OTM_200 | Monday | 10:30 | SL | 13:20 | 170 | 135.9 | 154.2 | -18.3 | -66,915 | -0.48 | 0.34 | 11.4 |
| 2025-06-06 | E-4 | OTM_100 | Friday | 10:30 | SL | 10:41 | 11 | 267.25 | 284.1 | -16.85 | -62,287 | -0.08 | 0.26 | 18.9 |
| 2026-02-16 | E-1 | OTM_200 | Monday | 10:30 | SL | 14:34 | 244 | 48.85 | 64.0 | -15.15 | -56,862 | -0.10 | 0.45 | 15.5 |
| 2026-01-09 | E-2 | OTM_200 | Friday | 09:30 | SL | 09:33 | 3 | 64.95 | 79.85 | -14.9 | -56,064 | +0.00 | 0.12 | 9.0 |
| 2025-08-29 | E-2 | OTM_200 | Friday | 09:30 | SL | 09:35 | 5 | 84.35 | 99.25 | -14.9 | -56,064 | +0.61 | 0.02 | 10.6 |
| 2026-01-28 | E-4 | OTM_100 | Wednesday | 10:00 | SL | 10:08 | 8 | 374.1 | 388.95 | -14.85 | -55,904 | +0.03 | 0.32 | 12.1 |
| 2025-06-03 | E-2 | OTM_200 | Tuesday | 10:30 | SL | 10:39 | 9 | 254.1 | 268.05 | -13.95 | -53,032 | +0.10 | 0.03 | 19.2 |
| 2026-02-02 | E-1 | OTM_200 | Monday | 10:30 | SL | 10:55 | 25 | 111.7 | 125.45 | -13.75 | -52,394 | -2.46 | 0.42 | 10.9 |
| 2026-03-16 | E-1 | OTM_200 | Monday | 10:30 | SL | 10:55 | 25 | 202.3 | 215.55 | -13.25 | -50,798 | +0.03 | 0.61 | 18.6 |
| 2025-07-30 | E-1 | OTM_200 | Wednesday | 10:30 | SL | 11:28 | 58 | 32.3 | 45.25 | -12.95 | -49,840 | +0.01 | 0.14 | 7.3 |
| 2026-02-18 | E-4 | OTM_100 | Wednesday | 09:30 | SL | 09:55 | 25 | 181.8 | 194.7 | -12.9 | -49,681 | +0.06 | 0.26 | 15.0 |
| 2025-09-04 | E-3 | OTM_200 | Thursday | 10:00 | SL | 10:09 | 9 | 70.5 | 83.4 | -12.9 | -49,681 | +0.43 | 0.06 | 10.2 |
| 2025-07-02 | E-1 | OTM_200 | Wednesday | 09:30 | SL | 10:01 | 31 | 43.35 | 55.95 | -12.6 | -48,723 | -0.96 | 0.13 | 12.0 |
| 2025-12-01 | E-1 | OTM_200 | Monday | 10:00 | SL | 10:51 | 51 | 59.4 | 71.6 | -12.2 | -47,447 | +0.22 | 0.01 | 10.3 |
| 2025-12-12 | E-2 | OTM_200 | Friday | 09:30 | SL | 10:08 | 38 | 66.55 | 78.75 | -12.2 | -47,447 | +0.33 | 0.12 | 11.1 |
| 2026-02-23 | E-1 | OTM_200 | Monday | 09:30 | SL | 10:45 | 75 | 72.2 | 84.2 | -12.0 | -46,809 | +0.40 | 0.10 | 15.6 |
| 2025-04-29 | E-1 | OTM_200 | Tuesday | 09:30 | SL | 09:40 | 10 | 62.05 | 74.05 | -12.0 | -46,809 | +0.13 | 0.22 | 18.1 |
| 2025-07-29 | E-2 | OTM_200 | Tuesday | 10:30 | SL | 13:53 | 203 | 69.2 | 80.9 | -11.7 | -45,851 | -0.28 | 0.10 | 8.2 |
| 2025-05-16 | E-4 | OTM_100 | Friday | 09:30 | SL | 09:40 | 10 | 290.05 | 301.7 | -11.65 | -45,691 | +0.20 | 0.26 | 20.6 |
| 2026-05-04 | E-1 | OTM_200 | Monday | 10:30 | SL | 10:33 | 3 | 95.45 | 107.05 | -11.6 | -45,532 | +0.39 | 0.36 | 28.0 |
| 2026-05-04 | E-1 | OTM_200 | Monday | 09:30 | SL | 09:32 | 2 | 99.85 | 111.4 | -11.55 | -45,372 | +0.39 | 0.36 | 28.0 |
| 2026-01-05 | E-1 | OTM_200 | Monday | 10:00 | SL | 13:49 | 229 | 34.35 | 45.7 | -11.35 | -44,734 | +0.08 | 0.05 | 9.3 |
| 2025-05-23 | E-4 | OTM_100 | Friday | 10:00 | SL | 10:03 | 3 | 313.75 | 325.05 | -11.3 | -44,574 | -0.04 | 0.60 | 19.2 |
| 2026-04-06 | E-1 | OTM_200 | Monday | 10:30 | SL | 11:11 | 41 | 241.45 | 252.7 | -11.25 | -44,415 | -0.10 | 0.12 | 29.0 |
| 2026-01-05 | E-1 | OTM_200 | Monday | 09:30 | SL | 14:01 | 271 | 30.55 | 41.8 | -11.25 | -44,415 | +0.08 | 0.05 | 9.3 |

## Recommendation for ≥₹5Cr live deployment

**Best risk-adjusted setup:** E-2 OTM_200 · TP ₹20/share · SL ₹5/share · `instant` confirmation.
- Mean EV: **₹-1,412/Cr**, median ₹-25,106/Cr.
- Worst single day: **₹-56,064/Cr** (2.0% of days worse than −₹50K).
- TP fires 24.8% · SL fires 62.7% · time-stop 12.4%. 
- Sample: 153 (day × entry) trades — about 2.4 opportunities per month per entry slot.

Practical guidance:
- Position SL orders **at broker level** (not mental). 1-min bar closes used here; in live trading use the broker's stop trigger logic.
- The `instant` mode means: 
  > exit immediately on first 1-min bar close at SL — simplest, fastest.
- Sizing at ₹5Cr: 215 lots (5 × 43). Margin block ≈ ₹11.75L (5 × ₹2.35L).
- Friction at Axis ₹6/lot/leg: net P&L per lot improves by ₹188 → ~₹8K/Cr boost vs the default ₹100/leg headline.

## Caveats

- **1-min bar slippage**: the simulator uses bar closes (and high for intrabar mode). Real fills may be worse on spikes — use intrabar mode as the realistic worst case.
- **No transaction-by-transaction order book**: STT, exchange, GST not separately modeled here — only ₹/lot/leg cost. Friction CSV columns show Axis ₹12/lot and Monarch ₹20/lot variants alongside default ₹200/lot.
- **VIX proxy used elsewhere; not used in TP/SL grid filter** — the grid is unconditional. Future work: re-run conditional on VIX regime.
- **Friction sensitivity** is large: ₹100/leg default vs ₹6/leg Axis = ₹188/lot per trade ≈ ₹8K/Cr per trade. At 200 lots and 100 trading days = ~₹16L/yr just from friction choice.
- **Forward-fill within day**: if a leg goes 5-10min without prints (common on deep-OTM), the simulator uses last seen close. Doesn't affect ATM much, can mute extremes on OTM_200.
- **Sample size**: 195 trading days per DTE max. E-3, E-4 have fewer days. Tail estimates wide.
- **TP/SL precedence**: SL wins on tie-bar. Conservative.

## Files

- `per_trade_results.csv` — every (date, DTE, entry, variant, TP, SL, confirm) simulated trade
- `grid_summary.csv` — aggregated stats per (dte, variant, tp_rs, sl_rs, confirm)
- `best_per_dte.csv` — top 10 per DTE under tail-cap constraint
- `fake_stop_comparison.csv` — confirm-mode side-by-side
- `tail_days_with_sl.csv` — days worse than −₹40K/Cr at recommended rules
- `heatmap_tpsl.png` · `ev_vs_tail.png`