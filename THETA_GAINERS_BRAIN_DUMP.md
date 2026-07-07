# THETA GAINERS — BRAIN DUMP (self-contained reference)

_Last refreshed: 2026-05-11 (post-live-experiment session)._

**Purpose.** A single document with everything an outside LLM (ChatGPT, Gemini, Claude.ai) needs to give Rohan Shah useful advice on his Indian index options short-premium strategy, without access to his repo or backtest data. Paste this into a chat and the assistant has full context.

---

## 0. Trader profile

- **Name:** Rohan Shah · Navin Group (Mumbai)
- **Capital:** ~₹100 Cr deployable per week
- **Instruments:** NIFTY 50 (Tuesday weekly expiry) + SENSEX (Thursday weekly expiry). NSE / BSE options.
- **Style:** Naked far-OTM short strangles / strangle-like spreads, single-day to 1-day-overnight holding. Theta seller. NEVER long premium.
- **Brokers used:** Axis Direct (₹6 / lot round-trip) and Monarch Networth Capital (₹10 / lot round-trip). Multiple demats per broker (A-001, A-002, M-001, M-002, etc.) for capital distribution.
- **Communication style preference:** terse, data-driven, numbers first. No fluff. No restating obvious things. Honest verdicts even when uncomfortable.

## 1. Capital + sizing math (memorise these)

| Item | NIFTY | SENSEX |
|---|---|---|
| Lot size (verify before each trade — SEBI changed lot sizes during 2024-2026) | 75 (was 75, possibly now 65 — confirm with broker) | 20 |
| Weekly expiry day | Tuesday | Thursday |
| E-0 margin per lot (sold OTM, short) | ~₹2.35 L | ~₹2.5 L |
| Lots per ₹1 Cr of margin | ~43 | ~40 |
| Shares per ₹1 Cr (= lots × lot_size) | ~3,225 | ~800 |
| Reference: per-Cr ₹/share multiplier | × 3,225 | × 800 |

**Per-Cr translation (NIFTY):** ₹1/share of premium captured = ₹3,225/Cr gross. ₹10/share = ₹32,250/Cr.

**Margin block at his typical full deployment ≈ ₹100 Cr book** = ~4,300 NIFTY lots OR ~4,000 SENSEX lots OR a mix.

**Friction (real, after broker-cost analysis 007):**
- Axis: ~₹6 / lot / leg round-trip → ~₹12/lot total for a CE+PE pair → ~₹516/Cr for a strangle.
- Monarch: ~₹10/lot/leg → ~₹20/lot total → ~₹860/Cr.
- STT + exchange + GST on sell-only side of an expiring-worthless trade: minimal (~₹200-400/Cr).
- **TOTAL effective friction at scale: ~₹700-1,200/Cr per trade event.** Placeholder ₹400/lot used in earlier analyses was 14× over-estimate.

## 2. The CANONICAL playbook (the only thing he should trade)

Reference: `STRATEGY_LIVE.md §1, §2, §9O, §9S, §9T`.

### 2A. E-1 advance tier — 10% of capital · deepest OTM · longest carry

- **What:** sell short strangle the trading day BEFORE expiry (Monday for NIFTY, Wednesday for SENSEX).
- **Timing:** ONLY **14:45 IST or later**. Before 14:45 = news-risk window, do NOT enter.
- **Distance:** minimum **3.5% OTM both legs** (was 3% — got whipsawed on 6-May war-pause news).
- **Premium floor:** combined premium × shares/Cr ≥ **₹7,500/Cr** for E-1 carry, ideal ≥ ₹10,000/Cr. If at 3.5%+ distance the premium is below ₹7,500/Cr after 14:45 → SKIP.
- **Size:** ~10% of capital deployable (~₹10 Cr).
- **Bias adjustment per market regime:**
  - BEARISH (max-pain below spot / gap-down / PCR<0.7) → PE further (4%+), CE at 3.5%.
  - BULLISH (max-pain above spot / gap-up / PCR>1.2) → CE further (4%+), PE at 3.5%.
  - NEUTRAL → both at 3.5%+.
- **VIX overlay (widen distance on high vol):**
  - VIX 16-18 → +0.25% both sides
  - VIX 18-22 → +0.5%
  - VIX ≥22 → +1.0% both sides
- **Hold:** to expiry next day 15:25, no intraday adjustments unless rule triggers.

### 2B. E-0 day Bucket A (the WORKHORSE) — 70% of capital

- **What:** sell short strangle on expiry morning, aim for both legs worthless by 15:25 close.
- **Timing:** **09:17-09:22 IST** is optimal entry minute (verified in analysis 009 — 100% worthless rate AND 100% net-positive across 1,248 sims at ≥2.5% OTM). 09:15 captures most premium but slippage risk; 09:17-09:22 keeps ~80% of edge with clean fills.
- **Distance:**
  - Regime LOW-VOL (VIX <16, range <0.5%, no events): 2.5% OTM both sides.
  - Regime MODERATE: 3.0%.
  - Regime HIGH (VIX >18, news pending, intraday > 1%): 3.5-4.0%.
- **Premium floors (per-Cr, after sizing × shares/Cr):**
  - Combined ≥ **₹4,000/Cr** = minimum (don't bother below this).
  - Combined ≥ **₹5,000/Cr** = standard target.
  - Combined ≥ **₹6,000/Cr** = full-quantity trigger (deploy max planned size).
- **Hold:** to 15:25 expiry. SL line at ~150 NIFTY pts / 500 SENSEX pts from strike on adverse-direction leg.
- **Verified result:** analysis 006/007 found **2.5% OTM E-1 carry → E-0 settlement on NIFTY = 30.6% annualized, 100% win rate, 0 cap-breaches in 46-day sample, mean +₹62K/Cr per event**. Strictly dominates every other tested strategy.

### 2C. Mid Risk tier — 15% of capital · closer OTM

- **What:** strangles 2.0-2.5% OTM (closer than Bucket A, more premium, more risk).
- **Timing:** same as Bucket A (09:17-09:22) OR add at 12:00 after morning theta-drift.
- **Distance:** 2.0-2.5% OTM. NEVER below 2.0% on regime grading rules.
- **Premium target:** 2-3× Bucket A premium per leg.
- **Hold:** to 15:25 with stop-loss if either leg loses 0.5-1% adverse direction.

### 2D. High Risk B1 — 12-15% of capital · ATM-ish strangle · CLOSE BY 12:00 IST

- **What:** ATM ±1% strangle on E-0 morning. Pure gamma harvest, very risky.
- **Timing:** enter 09:17-09:30 maximum, **HARD CLOSE BY 12:00 IST** regardless of P&L.
- **Distance:** 1.0-1.5% OTM both sides.
- **Why close at 12:00:** gamma compresses post-noon, pin-risk concentrates. Don't hold ATM into power hour.
- **Size:** smallest of the tiers (12-15% of capital).

### 2E. Lottery harvest (separate tier — buy side, optional)

- **What:** LONG far-OTM (4-5% out) on E-0 to capture rare 5×-10× spikes from manipulator-driven moves.
- **Per analysis 014:** 75% of E-0 days have ≥1 spike to a 4-5% strike. The other 25% (like 7-May-2026) lose the small premium paid.
- **Sizing:** ≤₹5 Cr notional spread across 5-8 strikes. Net loss days = bounded.

### 2F. Tier 3 (NEAR OTM 0.5-1%) — backtest-validated framework (locked 4-June-2026, §9W)

**Source:** Analyses 018-024 on 56 NIFTY + 54 SENSEX E-0 days.

**Hard exclusions first (skip if any true):**
- News in 24hr (RBI/Fed/budget/election/war)
- |Gap| > 0.7%, |pre-move| > 0.7%, pre-range > 1%
- VIX > 19 or rising > 1.5pt
- Brent > ±3% move

**Entry rules per (instrument × OTM × entry time):**

| Setup | Filter | Exit | Backtest result |
|---|---|---|---|
| ★ **SENSEX 1.0% @ 10:00** | pre-range ≤0.7%, prem ≥₹20K/Cr | HOLD | +₹47K/Cr, 100% win, worst +₹20K |
| NIFTY 0.7% @ 10:30 | pre-range ≤0.4%, prem ≥₹30K/Cr | HOLD | +₹45K/Cr, 100% win, worst +₹32K |
| NIFTY 0.5% @ 10:30 | pre-range ≤0.4%, prem ≥₹40K/Cr | HOLD | +₹53K/Cr, 92% win, worst −₹43K |
| SENSEX 0.7% @ 11:30 | pre-range ≤0.8%, prem ≥₹30K/Cr | HOLD | +₹42K/Cr, 100% win, worst +₹4K |
| SENSEX 0.5% @ 11:00 | pre-range ≤0.5%, prem ≥₹40K/Cr | HOLD or PT_80 | +₹57K/Cr, 75% win |
| **Volatile-recovery 0.5% @ 12:00** | range settled by 12:00 to ≤0.5% NIFTY/0.7% SENSEX, prem ≥₹40K | **T_1400 hard close** | +₹17K/Cr, 80% win, worst −₹2.5K |

**Yellow override (analyses 019-021):**
- PE yellow: spot ≤ entry − 50% of pe_buffer AND 30-min net move ≤ −0.4%
- CE yellow: spot ≥ pre-entry high break AND 30-min net move ≥ +0.4%
- Action: close LOSING leg only at market

**Red (mechanical close both legs):**
- Spot reaches 85% of buffer OR crosses into strike intraday

### 2G. Tier 2 (MID OTM 1.25-2%) — backtest-validated (§9W)

| Setup | Filter | Exit | Backtest |
|---|---|---|---|
| SENSEX 1.25% @ 10:00 | pre-range ≤0.8%, prem ≥₹15K | HOLD | +₹32K/Cr, 100% win |
| SENSEX 1.5% @ 10:00 | pre-range ≤1.0%, prem ≥₹12.5K | HOLD | +₹30K/Cr, 100% win |
| NIFTY 1.25% @ 11:30 | pre-range ≤0.7%, prem ≥₹15K | HOLD | +₹21K/Cr, 100% win |
| NIFTY 1.5% @ 11:00 | pre-range ≤0.7%, prem ≥₹12.5K | HOLD | +₹17K/Cr, 100% win |
| NIFTY 2.0% @ 09:45 | pre-range ≤1.0%, prem ≥₹8K | HOLD | +₹16K/Cr, 100% win |

**Sizing rules:** Tier 2 ≤30% of book · Tier 3 ≤15% of book · Volatile-recovery ≤5%.

**Annual yield (Tier 2+3 sleeves at ₹100Cr book): ~₹34 Cr/yr** in addition to Tier 1 main book.

## 3. ABSOLUTE PROHIBITIONS (the don'ts)

1. **NO ATM short straddles / near-ATM strangles.** Analysis 016 (388K simulations) proved: no setting achieves positive mean EV AND tail-loss ≤₹100K/Cr. §9U marked ABANDONED on 2026-05-11 after live test confirmed structural negative skew. See section 6.6 below.
2. **NO long premium** except the small lottery harvest sleeve.
3. **NO E-1 entry between 09:15 and 14:45** — news-risk window. The 6-May SENSEX trade taught this rule.
4. **NO entry on a regime-violation day** (see section 5).
5. **NO carrying mid-risk or high-risk positions overnight.** Bucket A only.
6. **NO closing one leg of a short strangle and leaving the other naked** ("legging out" trap — 2026-05-11 lesson, cost ~₹19K live).
7. **NO limit orders on the LOSING leg** when SL fires — limits don't fill on continuation moves; you chase at worse price.
8. **NO skipping a tradable day** at the per-Cr floors above — never miss yield on a calm regime.

## 4. Pre-trade REGIME FILTER (apply before every entry)

If ANY of these flags red, **SKIP** or **deploy at half size with caution**:

| Filter | Threshold | What "violated" looks like |
|---|---|---|
| NIFTY/SENSEX |open gap| | > 0.5% | Bigger gap = continuation-trend likely, theta-strategy struggles |
| India VIX level | > 17 | Elevated implied vol → realized vol expectations up |
| VIX day-change | > +1 pt | Rising vega works against short premium |
| Intraday range by 10:30 | > 1.0% | Trending early = continuation in afternoon |
| Brent crude move 24hr | > ±3% | Oil shock transmits to INR + equities |
| News events 24 hrs (RBI, Fed, election, war, earnings of index heavyweights) | Any pending/breaking | Binary outcomes whipsaw OTM strikes |
| Recent shock (1-2 days) | Active news cycle | Markets in price-discovery mode |
| Max-pain vs spot | > 1% apart and moving away | Pin failing |
| Wall OI strength on nearest strikes | < 2M lots or breached | No "magnet" holding strikes |

### Regime → distance adjustment shortcuts

| Day type | Bucket A distance | E-1 advance distance |
|---|---|---|
| All-LOW signals | 2.5% | 3.5% |
| Mixed-MODERATE | 3.0% | 3.75-4.0% |
| Multi-HIGH (VIX>18 + gap+news) | 3.5-4.0% | 4.5%+ or SKIP |

## 5. Backtest analyses summary (the receipts)

Reference: `FINDINGS_LOG.md` (one line per analysis). Detailed scripts in `analyses/`, results CSVs in `results/`.

### Headline winners (anchored)

| ID | What it tested | Verdict |
|---|---|---|
| **006** | Portfolio-scale 55 lots/Cr × E-1 + E-0 sample, friction sweep | **NIFTY E-1 2.5% OTM hold-to-expiry = 29% ann, 100% win rate, 0 cap breaches.** Backup: 3% = 19% ann, 0 breaches. 2% = 42% ann but 4.3% breach rate. |
| **007** | Real broker-cost model (Axis ₹6/lot, Monarch ₹10/lot, STT/exchange/GST) | Real friction ~₹29/lot vs ₹400 placeholder. **FINAL: NIFTY E-1 2.5% OTM Axis = 30.6% ann, 100% wins, worst event +₹8.4K.** All 46 events profitable. |
| **008** | E-0 time × distance × condition grid | **09:30 entry beats 10:00 by ~10%.** 09:30 × 2.5% = ₹325 avg/lot, 100% worthless. |
| **009** | E-0 minute-level entry sweep (9:15-10:30 × distances) | **Sweet spot 9:17-9:22.** Best minute = 9:15 (open) for premium capture; 9:17-9:22 has cleanest fills with ~80% of open's edge. ALL 1,248 sims at ≥2.5% OTM in 9:20-9:45 window: 100% worthless + 100% net-positive. |
| **015** | NIFTY ATM straddle by DTE (E-1 to E-4) | **Reframe: ATM straddle yield is ₹15-50K/Cr median, NOT ₹5-7K.** But mean turns NEGATIVE on E-3/E-4 due to fat-left-tail. Best defensive rule E-2 09:30 → 13:00 with VIX+gap filter: 77% wins, +₹40K/Cr median, but worst day −₹370K/Cr (still scary). |
| **016** | NIFTY straddle/strangle SELL with TP/SL grid + fake-stop confirm modes (388K sims) | **NO setting achieves mean EV ≥ +₹5K/Cr AND worst-day ≥ −₹100K/Cr.** Tight SL = friction churn destroys edge. Loose SL = gaps punch through. confirm_3m beats instant by avg +₹4K/Cr (fake stops are real). Verdict: SHORT STRADDLE IS NOT VIABLE at user's risk tolerance. |

### Side findings (useful for daily reasoning)

| ID | Finding |
|---|---|
| 001-003 | 3% OTM intraday close-of-day strangle barely breaks even before friction. Filters reduce loss-days by cutting trades, not winning more. |
| 004 | At 4-5% OTM E-1, premium is tiny (₹2.4-3.3 combined). 100% worthless rate. Friction at placeholder ₹400/lot made it negative; real friction (~₹29/lot) flips it positive. |
| 005 | E-0 deep OTM (4-5%): 100% worthless rate, ₹40-100/lot gross premium. MAE breach rate cleanest in dataset. |
| 010-012 | NIFTY vs SENSEX behavior, 100Cr full-year simulation. |
| 013 | Fake spike detector — manipulator-driven moves vs real moves. |
| 014 | Deep OTM manipulation: 75% of expiry days have ≥1 spike, exploitable on the BUY side (lottery harvest tier). |

## 6. Live trade records (the calibration data points)

### 6.1 — 2026-04-28 (TUE NIFTY E-0) — FIRST live v2.0 execution
- Setup: 5 positions, 12 legs, multi-tier across Axis + Monarch.
- Result: **all worthless. Net ₹3.88 lakh (~0.39% on margin, ~98% annualized).**
- Spot drifted 24,049 → 23,985 close (calm).
- Theta path matched backtest: ₹176K @ 12:13, ₹407K @ 15:07, ~₹438K gross at expiry.
- Friction ~₹50K (11.4% tax — much smaller than placeholder).
- **Lessons:** (a) Mid Risk 23800 PE at 0.98% OTM violated v2.0 ≥2.0% rule — won today but rule stands. (b) "Skip today, wait for 5-May" advice was wrong — late-day deployment on calm days is +EV. (c) 9:17-9:22 entry would have captured ~₹6L vs ₹3.9L (+50%).

### 6.2 — 2026-05-07 (THU SENSEX E-0) — Multi-bucket ₹176 Cr
- Setup: 5 portfolios × Bucket A Deep + High Risk B1 strangle + Mid Risk + 80,500 CE 6-May carry + Lottery harvest.
- Result: **Net ₹4.17 Lakh (~0.24% on margin, ~59% ann).**
- Pin held at 78,000, closed 77,888 (−0.09%).
- Bucket A delivered as designed: ₹3.06L across 6,648 lots short. Zero defensive cuts.
- **Lessons:** (a) Mid Risk Monarch was clean; earlier intra-day −₹20K read was Sensibull display artifact (avg shown as 0 after close). (b) High Risk B1 entered post-12:00 (violated §9T rule) — worked because pin held but DON'T generalize. (c) Lottery harvest -₹5K = no-spike day (in the 25% bucket per analysis 014). (d) Position shifting during day cost ~₹15-25K — needs friction budget rule. (e) MTM swings −₹158K → +₹3.77L close — when pin holds, recovery is brutal in seller's favor.

### 6.3 — 2026-05-06 (WED SENSEX E-1) — The trade that taught §9O
- Entered 79,500 CE / 75,000 PE at 14:00 (3% OTM each).
- War-pause news at ~15:00 spiked SENSEX +1.4%, CE went ₹6 → ₹30+.
- Cut all PE + 30% CE; carried 70% CE overnight.
- 7-May open +0.43% gap up to 78,294; CE 79,500 buffer dropped to 1.54%.
- Squared at ~₹5.10 vs entry ₹6.25 = breakeven on carry, saved ~₹70K vs prior-day mark.
- **Root cause:** entered during news-risk window 14:00 + distance only 3% (now floor is 3.5%).
- **Rule installed:** §9O E-1 timing 14:45+ only, distance ≥3.5%.

### 6.4 — 2026-05-11 (MON NIFTY E-1) — ATM straddle experimental failure (DON'T REPEAT)
- Setup: NIFTY 23,950 ATM straddle SELL, 10 lots, for 12-May expiry. Entered ~09:40.
- **Regime conditions at entry — three flags red:**
  - Gap −0.85% (vs 0.5% threshold)
  - VIX 18.84 with +2.0pt day-change (vs 17 / +1pt thresholds)
  - Iran-Trump geopolitical news overnight + Brent +4.32% to $105.70 (vs no-news threshold)
- **Execution mistake:** instead of holding both legs and stopping on combined premium, I closed CE leg at ₹59 (taking the easy decay win), then set limit BUY on PE at ₹140 which never filled, then capitulated buy at ₹156.
- **Net: −₹25/share combined = ~₹19K gross loss = ~₹83K/Cr equivalent.** Matches 016 backtest tail-day prediction.
- **Lessons:** (1) Three regime flags red = mandatory skip. (2) Don't leg out of short straddles. (3) Limits on losing leg = worst trap. (4) Bucket A 2.5-3% OTM is strictly dominant. The straddle play is now permanently §9U-ABANDONED.

### 6.5 — 2026-05-11 actual Bucket A trade (the right one)
- Setup: 4-position deep-OTM strangle for 12-May NIFTY expiry, Monarch broker.
  - SELL −5,980 × 22,700 PE @ ₹1.20 (4.96% OTM)
  - SELL −7,020 × 22,800 PE @ ₹1.20 (4.55% OTM)
  - SELL −2,990 × 24,900 CE @ ₹1.75 (4.34% OTM)
  - SELL −15,535 × 25,000 CE @ ₹1.95 (4.76% OTM)
- Total premium collected: ~₹51,061. Max profit at expiry: +₹51K.
- Asymmetric: CE-heavy (more 25,000 CE) reflecting expected upside-cap on a gap-down regime.
- Sized at ~₹10 Cr margin block → ~₹5K/Cr max profit if all expire worthless = modest yield for the size but very safe.
- Status at 18:14 IST: total P&L +₹7,628 (MTM unbooked).

### 6.6 — The straddle abandonment decision
After 016 backtest + the 2026-05-11 live experiment, Rohan decided:
- §9U short straddle is permanently abandoned at any size.
- Default answer to "should I do a straddle today?" = **NO**, do not even check the filter conditions.
- The active core stays §1 (E-1 advance 10%) + §2 (E-0 three-tier).

## 7. Daily decision tree

```
Q1: What day is it relative to weekly expiry?
    │
    ├─ E-0 (NIFTY Tue / SENSEX Thu): → step Q2
    ├─ E-1 (NIFTY Mon / SENSEX Wed): → step Q3
    ├─ E-2 or earlier: → no trade, observe regime
    └─ Holiday: → skip
    
Q2 (E-0 day): Check regime filters at 09:15
    │
    ├─ All clear: deploy full Bucket A 2.5% OTM at 09:17-09:22
    │             + Mid Risk 2.0-2.5%
    │             + High Risk B1 1.0-1.5% (CLOSE BY 12:00)
    │             + Lottery harvest (separate sleeve)
    │
    ├─ Moderate (1-2 flags amber): deploy at 3.0% OTM, skip High Risk B1
    │
    └─ Multi-HIGH (2+ flags red): SKIP. Don't trade. Re-assess after lunch.
    
Q3 (E-1 day): Wait until 14:45 IST
    │
    ├─ Before 14:45: NO ENTRY. Watch news, observe regime drift.
    │
    └─ At 14:45+: check regime + premium floor
        │
        ├─ Combined per-Cr ≥ ₹7,500 at 3.5%+ OTM: enter advance tier (10% capital)
        │   bias-adjusted per max-pain / gap direction
        │
        └─ Floor not met OR multi-HIGH regime: SKIP, plan for E-0 only next day
```

## 8. Cardinal lessons (the rules you must internalize)

1. **Premium ≥ floor before distance.** A 5% OTM with ₹1.5 combined premium is USELESS — don't bother. A 2.5% with ₹6/share is gold. Always check per-Cr floor.
2. **Late entry on calm days is +EV.** 14:45+ for E-1 isn't a missed-opportunity sin; it's the right rule. 28-Apr lesson.
3. **Theta beats delta on E-0.** Even small adverse spot moves get crushed by theta as 15:25 approaches. Hold to expiry on Bucket A — don't trade-manage on noise.
4. **When pin holds, recovery is brutal in seller's favor.** Don't panic-cover at MTM lows on calm days. 7-May went −₹158K @ 12:43 → +₹377K close.
5. **Three regime flags = mandatory skip.** Doesn't matter how juicy premium looks. 11-May straddle: 3 flags red, lost ₹19K.
6. **Don't leg out of short strangles.** Closing the winning leg removes your gamma hedge.
7. **Limit orders on losing legs are traps.** They don't fill on continuation moves.
8. **Real friction is ~₹29/lot, not ₹400.** Don't reject thin-premium trades using over-estimated friction.
9. **Lottery harvest at ₹0.05-0.25 LTPs is broker-cost-toxic** (200-400% of premium). Sizing rules need recalibration.
10. **Backtest mean turning negative despite high win-rate = fat-left-tail problem.** Win-rate alone is wrong metric for short-premium. Always check mean/median/p25/worst.

## 9. Data store + tooling

### 9.1 Data
- `data/parquet/instrument={NIFTY,SENSEX}/year=YYYY/month=MM/*.parquet` — minute OHLC + OI for spot, FUT, all CE/PE strikes within ±5% spot for nearest + next weekly expiry. ~2 MB/day total. Source: Kite Connect historical_data() API, 3 req/sec rate limit.
- `data/kite_ingest_log.parquet` — tracks (instrument, trade_date, n_rows, ingested_at) for dedup.
- `data/dashboard_snapshots/YYYY-MM-DD.json` — daily snapshot of positions + market context + MTM analysis, persisted from the FastAPI dashboard.

### 9.2 Daily routine
- 09:00 IST: run `python3 scripts/kite_login.py` (Kite tokens expire ~6 AM IST). Paste callback URL.
- **After login auto-fires** (added 2026-05-11): `scripts/post_login_sync.py` runs in background:
  1. `run_kite_ingest.py --days 14` — backfill any missed days, idempotent
  2. POST to dashboard `/api/snapshot/save` — preserves positions, refreshes market context
  3. Verification log of last 7 trading days
- Log: `results/post_login_sync.log`. Verify: `tail -30 results/post_login_sync.log`.

### 9.3 Cron jobs (launchd plists)
- `com.rohanshah.morning-check.plist` — 09:30 IST weekdays: notification if yesterday's data missing or session expired.
- `com.rohanshah.kite-ingest.plist` — 16:30 IST weekdays: fallback ingest (depends on valid session at 16:30).

### 9.4 Dashboard (FastAPI on :8000)
- `/api/snapshot` — live NIFTY/SENSEX/VIX
- `/api/chain/{instrument}` — full option chain with LTPs
- `/api/recommend/{instrument}?bias=auto` — asymmetric strangle recommendation
- `/api/snapshot/save` (POST) — persist daily snapshot
- `/api/position-analysis` (POST) — live MTM + per-leg verdict
- `/recommend/{instrument}` (HTML) — 3-tier strategy UI with editable strikes
- `/report` (HTML) — Live + Historical positions view

### 9.5 Analysis scripts
- Naming convention: `analyses/NNN_<slug>.py` with `@params` block at top, writes to `results/NNN_<slug>/`.
- All analyses bucket-tested with friction sensitivity (₹40, ₹100, ₹200, ₹400/lot variants).
- DTE definition: trading-day count to next weekly expiry, NIFTY weekly switch (Thu→Tue) on 2025-09-02 handled via `lib/expiry_calendar.NIFTY_WEEKLY_EXPIRIES` hardcoded list — do NOT use weekday inference.

## 10. Open questions / next research

1. **Verify NIFTY lot size for 2026.** SEBI has been adjusting lot sizes 2024-2026. Today's Sensibull screenshot showed quantities cleanly divisible by 65 (not 75). Confirm with broker before each sizing calculation.
2. **Crude oil regime overlay** — today's 11-May was triggered by Brent +4.32%. Add Brent-shock filter to regime grader.
3. **PCR threshold validation** — 0.7 / 1.2 thresholds for bullish/bearish bias are heuristic. Backtest needed.
4. **Multi-portfolio order management** — manual orchestration across 5+ brokers/demats. Consider Kite Connect order API integration for the Axis demat at least.
5. **Sensibull-display-artifact reconciliation** — 7-May misread of 76,700 PE −₹20K. Build a positions-reconciliation tool that matches broker-confirmation against Sensibull.
6. **Holiday-aware DTE** — current `trading_days_until_expiry` treats Mon-Fri as trading; doesn't subtract holidays. Minor impact on bucketing but should be tightened.

## 11. Glossary

- **E-0:** expiry day (Tuesday for NIFTY, Thursday for SENSEX)
- **E-1, E-2, etc.:** trading days BEFORE expiry
- **OTM:** out-of-the-money
- **ATM:** at-the-money
- **MTM:** mark-to-market (unbooked P&L)
- **IST:** Indian Standard Time (UTC+5:30)
- **VIX / India VIX:** NSE volatility index, NIFTY 30-day implied vol
- **Max-pain:** the strike where summed CE+PE OI is maximum; market often pins here on E-0
- **PCR:** Put-Call Ratio (volume or OI)
- **Bucket A:** core deep-OTM E-0 strangle (Section 2B above)
- **Mid Risk / High Risk B1:** tier nomenclature for closer-OTM E-0 strangles
- **Pin:** spot settling at / near a strike on E-0 (driven by max-pain / dealer hedging dynamics)
- **Leg out:** closing one side of a strangle while keeping the other open (BAD — removes gamma hedge)
- **Confirm_3m:** stop-loss only fires when 3 consecutive 1-min bars close at/above SL (filters fake wicks)

## 12. Repo file index (where to find what)

| File | Purpose |
|---|---|
| `CLAUDE.md` | Backtest engine context + protocol for Claude Code sessions |
| `STRATEGY_LIVE.md` | The canonical rulebook (sections 1-9). Read sections 1, 2, 9O, 9R, 9S, 9T, 9U. |
| `OPERATIONS_MANUAL.md` | Full strategy + infrastructure reference |
| `DAILY_DATA_ROUTINE.md` | Single-page kite_login routine + auto-sync (post-2026-05-11) |
| `FINDINGS_LOG.md` | Append-only one-line summary of every analysis + live trade |
| `THETADESK_HANDBOOK.md` | Web dashboard reference (UI, endpoints, snapshots) |
| `analyses/0NN_*.py` | Parameterized analysis scripts |
| `results/0NN_*/summary.md` | Human-readable analysis output |
| `lib/expiry_calendar.py` | HARDCODED NIFTY+SENSEX weekly expiry dates 2025-2026 — use these, not weekday checks |
| `lib/kite_live.py`, `lib/kite_historical.py` | Kite Connect wrappers |
| `dashboard/server.py` | FastAPI dashboard (~2,300 lines) |
| `scripts/kite_login.py` | Daily Kite token refresh |
| `scripts/post_login_sync.py` | Auto-sync wrapper fired after every login |

---

## How to use this file with an external chat

Paste this entire document at the start of a fresh chat with ChatGPT / Gemini / Claude.ai and prefix with:

> I'm Rohan Shah. The document below is a brain-dump of my Indian index options strategy + backtest findings + live trade history. Use it as the basis for any advice — don't make up numbers. If you need data I haven't included, ask me. Be terse and data-driven. No fluff. Honest verdicts even when uncomfortable.

Then ask your question.

---

_Generated by Claude Code 2026-05-11 — refresh manually after each significant analysis or live trade._
