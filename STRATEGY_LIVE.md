# Rohan's Live Strangle Strategy — v2.0 (locked 2026-04-28)

**v2.0 supersedes v1.0.** New capital pattern: small E-1 advance + large E-0 in 3 tiers. Backtested on 46 NIFTY E-1 days + 47 NIFTY E-0 days (Apr 2025 → Apr 2026). Real broker cost basis: Axis ₹6/lot, sell-and-let-expire.

---

## 0. Capital pattern (full margin always deployed)

| Slot | Allocation | When entered | When closed |
|---|---|---|---|
| **E-1 advance** | **5–7%** of available margin | Day-before-expiry, 10:00 IST | Held to E-0 close 15:25 |
| **E-0 T1 (safe)** | **~85%** | E-0 day, 09:45–10:00 | E-0 close 15:25 |
| **E-0 T2 (medium)** | **~8%** | E-0 day, 09:30–09:45 | E-0 close 15:25 |
| **E-0 T3 (premium-grab)** | **~2%** | E-0 day, 09:30 | E-0 close 15:25 |
| **TOTAL** | **~100%** | | |

Per ₹1Cr capital (55-lot total at ₹1.8L margin):
- E-1 advance ≈ **3 lots** (5% × 55 = 2.75)
- E-0 T1 ≈ **47 lots** (85%)
- E-0 T2 ≈ **4 lots** (8%)
- E-0 T3 ≈ **1 lot** (2%)

---

## 1. The E-1 advance tier (small, deepest, longest carry)

| | |
|---|---|
| Distance | **3.5% OTM both sides** |
| Entry time | **10:00–10:15 IST** |
| Hold | To expiry close 15:25 next day |
| Sample stats (3.5% E-1) | avg ₹622 net/lot, worst ~−₹100, **100% worthless** |
| Why so small | Backtest shows E-1 gives lower per-lot ₹ than E-0 (which has gamma+theta). Used as a head-start tier. |

---

## 2. The E-0 three-tier deployment (the workhorse — 95% of capital)

### Default distances (flat-gap, normal vol day)

| Tier | Distance (default) | Entry time | Per-lot expected (₹) |
|---|---|---|---|
| **T1** (~85%) | **3.0% / 3.0%** | **09:18–09:22** | avg ₹201, worst +₹36, **100% worthless** |
| **T2** (~8%) | **2.5% / 2.5%** | **09:17–09:20** | avg ₹313, worst +₹53, **100% worthless** |
| **T3** (~2%) | **2.0% / 2.0%** | **09:20** | avg ₹600+, worst −₹1,150, **95.7% worthless** |

> **Why 9:17-9:22 entry on E-0:** minute-level backtest (009) shows premium decays nearly monotonically from open to noon. **9:15 is mathematically optimal** (avg ₹374/lot at 2.5% OTM) but execution risk (wide spreads, thin liquidity in first 60 sec) costs ~10-15% in slippage. **9:17-9:22 keeps ~80-85% of the open-bar premium** with much cleaner fills. After 9:30 you've already lost ~30% of available edge. **Verified: every minute 9:15-10:30 × every distance ≥2.5% OTM × every E-0 day in 47-day sample → 100% worthless.**

### Condition overlays for E-0 (read at 9:15–9:30)

#### Gap direction (vs prev close)

| Gap | T1 CE / PE | T2 CE / PE | T3 CE / PE | Entry shift |
|---|---|---|---|---|
| **Flat** (-0.5% to +0.5%) | 3.0% / 3.0% | 2.5% / 2.5% | **2.0% / 2.0%** | use defaults |
| **Gap up +0.5% to +1%** | 3.5% / 2.5% | 3.0% / 2.0% | 2.5% / 1.5% | T2/T3 enter 09:30 sharp |
| **Gap up > +1%** | 4.0% / 3.0% | 3.5% / 2.5% | 3.0% / 2.0% | Halve T3 size; T1 entry 10:00–10:15 |
| **Gap down −0.5% to −1%** | 2.5% / 3.5% | 2.0% / 3.0% | 1.5% / 2.5% | T2/T3 enter 09:30 sharp |
| **Gap down < −1%** | 3.0% / 4.0% | 2.5% / 3.5% | 2.0% / 3.0% | Halve T3; T1 entry 10:00–10:15 |

Logic: short-strangle pain = continuation in gap direction. Push the at-risk side farther; reclaim premium on the cushioned side.

> **Sample evidence:** in 19 gap-up E-0 days, **2.5% / 2.5% × 09:30 stayed 100% worthless** (worst +₹82). So even on gap-up days the symmetric 2.5% OTM is safe — the asymmetric rule above is principled inference, not cell-validated.

#### INDIA VIX

| VIX | Adjustment |
|---|---|
| **< 13** | Tighten 0.25% (low vol — push T3 to 1.5% OTM for premium) |
| **13–16** | No change (default regime) |
| **16–18** | Add **+0.25%** to T1, T2 |
| **18–22** | Add **+0.5%** to T1, T2; **skip T3** (replace with extra T1 lots); delay T1 entry to 10:30 |
| **> 22** | Add **+1.0%** to T1, T2; **skip T3 + halve T2**; delay T1 entry to 11:00 |

#### Premium fatness (read combined CE+PE at 2.5% OTM at 9:30)

| Combined @ 2.5% (₹/share) | Action |
|---|---|
| **< ₹2** | Premium thin (low IV). Tighten 0.5% on all tiers — better to grab what's there. |
| **₹2–₹6** | Default regime. |
| **₹6–₹15** | Elevated IV. Add +0.25% to all. |
| **> ₹15** | High IV / news priced in. Add +0.5% to all; halve T3. |

#### Major scheduled event (RBI, FOMC, CPI, Budget, results)

If event is **today** OR **between now and tomorrow's expiry**:
- Add **+1.0%** to all tiers
- **Skip T3 entirely**; redeploy that 2% margin into T1
- Delay T1 entry to 10:30+

---

## 3. Strike rounding

```
CE_strike = round(spot × (1 + dist_pct/100) ÷ 50) × 50
PE_strike = round(spot × (1 − dist_pct/100) ÷ 50) × 50
```

NIFTY grid = ₹50, lot = 65. SENSEX grid = ₹100, lot = 20 — NOT YET BACKTESTED.

---

## 4. Hold rule (don't deviate)

- **All 4 positions held to expiry close (15:25 same day for E-0 trades; next-day 15:25 for E-1 advance).**
- 100% of T1+T2 (and 96% of T3) historically expired worthless.
- Intraday wobbles are NORMAL — sample shows 90th-pct MAE of ₹2-30/share on E-0; the strategy's edge is "wait it out".

---

## 5. Kill-switches (only fire on real emergency)

Per-tier, combined CE+PE adverse from entry:

| Tier | Threshold (₹/share) | Per-lot ₹ loss when triggered |
|---|---|---|
| E-1 advance | **₹6** | ~₹390/lot |
| E-0 T1 | **₹4** | ~₹260/lot |
| E-0 T2 | **₹4** | ~₹260/lot |
| E-0 T3 | **₹3** | ~₹195/lot |

**Hard portfolio kill (exit ALL positions, no questions):**
- NIFTY moves > **±1.5% intraday** from 9:15 open
- Trading halt
- Surprise major news mid-session
- Cumulative portfolio loss > **₹35K/Cr**

---

## 6. Days to trade

| Day | Status |
|---|---|
| **Mon** (E-1 to Tue weekly) | ✅ enter E-1 advance tier |
| **Tue** (NIFTY weekly E-0) | ✅ enter E-0 T1 + T2 + T3 |
| **Wed** (E-1 to Thu legacy, if exists) | ✅ enter E-1 advance |
| **Thu** (legacy weekly E-0, if exists) | ✅ enter E-0 T1 + T2 + T3 |
| Other (Fri, mid-week non-cycle) | ⏸ no trade — DTE 5+ premium too thin |

**SENSEX:** strategy logic mirrors but data not yet ingested. Don't run live till backtested.

---

## 7. Output template — when Rohan says "give me expiry levels for [date]"

I will respond with this exact format:

```
TRADE CARD — NIFTY · target expiry <date> (<dow>)

─── E-1 ADVANCE (entered <prev_day> at 10:00) ───
  CE 3.5% OTM → strike #####     PE 3.5% OTM → strike #####
  Sizing: 3 lots/Cr (5% margin)
  Status: HELD into E-0 / TO BE ENTERED <prev_day>

─── E-0 CONDITIONS DETECTED (today, 9:15-9:30) ───
  Spot @ 9:15:        ₹S
  Gap vs prev close:  +X.XX%       → overlay: <which>
  INDIA VIX:          XX.X         → overlay: <which>
  Premium @ 2.5%:     ₹X.X / share → overlay: <which>
  Events today:       none / <list>

─── E-0 TRADE PLAN (after overlays) ───
  T1 (47 lots/Cr): CE X.X% / PE X.X%   strikes ##### / #####   enter 09:45-10:00
  T2 ( 4 lots/Cr): CE X.X% / PE X.X%   strikes ##### / #####   enter 09:30-09:45
  T3 ( 1 lot /Cr): CE X.X% / PE X.X%   strikes ##### / #####   enter 09:30

─── EXPECTED P&L (median basis, per Cr) ───
  E-1 advance: ~₹X    T1: ~₹X    T2: ~₹X    T3: ~₹X
  TOTAL: ~₹X gross / ~₹X net (after Axis ~₹29/lot friction)

─── KILL-SWITCHES ───
  E-1: ₹6 combined adverse  ·  T1 / T2: ₹4  ·  T3: ₹3
  Portfolio: NIFTY ±1.5% OR ₹35K/Cr loss → exit ALL

PLAN: hold all 4 positions to 15:25 today.
```

---

## 8. Caveats and known gaps

1. **Backtest = 1 year** (47 E-0 days, 46 E-1 days). Cross-validation on 2024 still pending data ingest.
2. **Asymmetric distances under gap** are principled, not individually backtested. The symmetric 2.5% on gap-up days survived 19/19 in sample, so the asymmetric rule is conservative — real numbers may be even safer.
3. **Vol-bucket conditioning** is currently impotent — E-0 days are essentially all "high_vol" (first-15-min range > 0.5%). Don't condition on vol_bucket separately.
4. **Sample is sparse for gap-down days** (only 3 in 47 E-0 days). Default to symmetric defaults if gap-down — don't trust the cell.
5. **SENSEX support** awaits data ingest. NIFTY-only for live.
6. **Funding cost ₹600/Cr** conservatively applied to every event in the cost model. Real returns slightly higher if funding rare.
7. **Don't tighten stops** on intraday wobbles. The whole edge of this strategy is conviction in expiry-day decay; panic exits destroy it.

---

## 9. Live execution lessons (2026-04-28)

First live full-deployment day: ~₹100 cr margin, all strikes expired worthless, ~₹3.88 L net P&L (0.39% on margin). Lessons to embed:

### 9A. Time-decayed risk thresholds

A position's risk depends on **distance × remaining-time-to-expiry**, not distance alone. Use this matrix when flagging positions during the day:

| Distance \ Time-to-expiry | > 4 hrs | 1-4 hrs | < 1 hr | < 15 min |
|---|---|---|---|---|
| < 0.5% | 🔴 | 🔴 | 🟡 | 🟢 (LTP < ₹0.50) |
| 0.5–1.0% | 🔴 | 🟡 | 🟢 | 🟢 |
| 1.0–1.5% | 🟡 | 🟢 | 🟢 | 🟢 |
| 1.5–2.0% | 🟢 | 🟢 | 🟢 | 🟢 |
| > 2.0% | 🟢 | 🟢 | 🟢 | 🟢 |

🔴 = exit immediately. 🟡 = monitor closely, exit if adverse. 🟢 = let ride to expiry.

### 9B. Late-entry harm/help

If you must enter after 11:00 AM IST (missed 9:17-9:22 window), apply these adjustments:

| Entry time | Margin to deploy | Distance floor |
|---|---|---|
| 9:17–9:22 (optimal) | up to 95% | strategy defaults |
| 9:30–10:00 | up to 90% | strategy defaults |
| 10:00–11:00 | 70-80% | +0.25% on all tiers |
| 11:00–12:30 | 50-60% | +0.5% on all tiers, skip T3 |
| 12:30–14:00 | 25-30% | +0.5% on all, skip T3 |
| 14:00+ | 0-15% | only T1 at 3.5%+ OTM, scalper mode |

Reason: per-lot premium decays ~30% per hour-late after 9:30. By 12:00 you've already lost half the available edge. Reducing margin compensates for both the lower per-lot reward AND the inability to "read the day" (vol regime not fully observable past 11:00).

### 9C. Don't break the 2.0% T3 floor

On 2026-04-28, Mid Risk position placed 23800 PE at 0.98% OTM. It worked — booked ₹83,655. But:
- Backtest shows 1% OTM has only 57% worthless rate over 47 days.
- Today was a calm, bearish-drifting day with low realised volatility — favourable conditions.
- A single win does NOT validate the rule break. Sample of 1.
- **The 2.0% floor stays. Even when premium is tempting, don't go closer.**

If you want more premium, scale UP qty at 2.0-2.5% OTM rather than going closer. Risk-adjusted return is higher.

### 9D. Real friction stays small at scale

Live execution showed friction = **~11.4% of gross premium captured**. This is the true cost of capital deployment. Should be embedded in any future P&L projections.

| Component | % of gross |
|---|---|
| Brokerage + STT + GST + exchange + funding | ~11–13% |
| Implied "strategy edge over friction" at 2.5%-3% OTM | ~85% |
| Net keep | ~88% |

### 9E. Post-trade observation: pin behavior on E-0

Spot pinned around 24,000 (within ₹50) all afternoon. This is consistent with max-pain pinning — strikes with highest OI clustered around 24,000 in today's data. Future enhancement: **track max-pain OI distribution intraday and use as entry overlay** (skip days where max-pain is far from current spot, indicating potential for big moves).

### 9F. Limit-order fill reality on E-0 morning (live observation 2026-04-28)

**Theoretical optimal entry = 9:15-9:22.** Live experience showed limit orders at LTP have **only ~50% fill rate** in this window because bid-ask spreads on far-OTM weeklies are wide at open. Rohan placed 24400 CE @ ₹3 + 23750 PE @ ₹2.5 at 9:22 — neither filled cleanly until 9:25-9:35.

**Refined practical rule (E-0 entry timing):**

| Time | Bid-ask state | Fill prob @ LTP limit | Premium captured (vs 9:15 baseline) |
|---|---|---|---|
| 9:15-9:17 | very wide | ~30% | 100% (highest) |
| 9:17-9:22 | wide | ~50% | ~95% |
| **9:25-9:35** | **medium-tight** | **~90%** | **~85%** ⭐ |
| 9:35-9:50 | tight | ~98% | ~70% |
| 9:50-10:30 | tight | 99%+ | ~50-60% |

**Recommended order placement protocol:**
1. Watch chain at 9:15-9:25, observe spread tightening.
2. At 9:25 IST: place CE leg first at LTP (limit). If unfilled in 60 sec, drop ₹0.10. Repeat 3-4× until filled.
3. Once CE leg fills, immediately place PE leg same way. Do NOT place both simultaneously — if CE fills + PE hangs, you have unhedged short.
4. Max time budget: 9:25-9:40. If unfilled by 9:40, switch to market order or skip the strike.

**Why 9:25-9:35 beats 9:17-9:22 in practice:**
- Open-bell volatility (9:15-9:22) creates erratic bid-ask quoting.
- Real two-way flow develops by 9:25 — MMs tighten spreads.
- PE premium often spikes BACK UP between 9:25-9:35 due to (a) MM repricing, (b) vega bump from realised vol, (c) hedger flow. Sample 28-Apr-2026: 23750 PE went 2.00 (9:25) → 2.30 (9:30) without 13-pt spot move.
- CE premium also has a brief peak around 9:25-9:30 as spot reaches first-hour high before pulling back.

### 9G. The "theta-beats-delta" phenomenon on E-0 (confirmed live)

Live observation 28-Apr-2026, 24400 CE:
- 9:30 → 10:00: spot RALLIED +49 pts (24,109 → 24,158) but CE DROPPED -4% (2.85 → 2.75)
- 10:00 → 10:30: spot ~flat but CE dropped -33% (2.75 → 1.85)

**This is the strategy's edge.** Even when spot moves against your CE short, E-0 morning theta + IV crush eats premium faster than delta builds intrinsic — UNLESS spot moves >0.5% in <30 min. Conviction in "wait it out" is rational.

### 9H. Limit-order pricing rule (resolves the "what limit to set" question)

For E-0 entry at 9:20-9:25, set limits relative to the LTP at the time of placement:

| Drift detected (9:15-9:19 spot move) | CE leg limit | PE leg limit | Rationale |
|---|---|---|---|
| **Rally** (spot UP > +0.1%) | **LTP exactly** (no hike) | **LTP + ₹0.05-0.10** (5-10% above) | CE racing AGAINST gravity — fill fast or miss. PE has multiple oscillation peaks — patient limit catches them. |
| **Fall** (spot DOWN > -0.1%) | **LTP + ₹0.05-0.10** | **LTP exactly** | Mirror image. |
| **Flat** (within ±0.1%) | **LTP + ₹0.05** | **LTP + ₹0.05** | Symmetric — both sides offer briefly. |

**Time-budget protocol for limit fills:**
- Place at 9:22.
- Unfilled by **+5 min**? Drop your limit by ₹0.05 on the at-risk leg.
- Unfilled by **+10 min**? Place market order on that leg, OR abort and skip.
- Don't wait past 9:35 — premium decay accelerates exponentially after.

**Live evidence (28-Apr-2026 trade):** Rohan placed 24700 CE @ 0.85 + 23400 PE @ 0.85 at 9:22. Spot drifted UP +0.27% in first 15 min.
- CE side: limit 0.85 had only 1-min fill windows at 9:20, 9:21, 9:26, 9:31-32. Filled around 9:31-9:32 by luck. After 9:32, never returned to 0.85.
- PE side: limit 0.85 had sustained 5+ min windows at 9:29-9:34, 9:46-9:49, 9:56-10:00. Easy fill.
- Correct rule would have been: CE @ 0.80 (LTP, no hike — would have filled 9:22 immediately) + PE @ 0.85 (kept the +0.05 hike — would have filled 9:30).

### 9I. Asymmetric premium behavior on E-0 (put skew dominance)

A counterintuitive but PERSISTENT pattern in Indian index options on E-0:

**On rally days (spot drifting UP through morning):**
- CE premium decays FASTER than delta predicts (theta + IV cool-off + long-unwind selling).
- PE premium is STICKY — oscillates in a narrow band, sometimes EVEN GOES UP during a rally.
- Reason: put skew structurally elevates PE IV; institutional hedgers keep buying puts; max-pain pin behavior.

**Live evidence (28-Apr-2026, 9:55 → 10:30):**
- Spot: 24,138 → 24,167 (+29 pts rally)
- 24700 CE: 0.65 → 0.60 (**-8%**)
- 23400 PE: 0.80 → 0.90 (**+12%**) — went UP while spot was rallying

**Implication for strategy:**

1. PE side gives MORE premium per unit of risk on rally days. Same on CE side for fall days.
2. The v2.0 gap-direction overlay (CE further on rally, PE closer) captures this — but the rule applies even WITHIN-DAY when you observe drift after 9:30.
3. If you missed entering at 9:22 and intraday drift is clear by 10:00, the PE side likely has a "second chance" sell window (sticky premium) — CE side is gone.
4. Don't try to "improve" your CE entry by chasing it down — once theta+IV-crush starts on CE, it doesn't pause.

**Mirror on fall days:** PE is the "racing" leg, CE is the "sticky" leg. Same logic flipped.

### 9J. Calendar of the auto-ingest cron (live infrastructure)

`scripts/com.rohanshah.kite-ingest.plist` is loaded as a launchd job:
- Runs Mon-Fri at **16:30 IST** (after market close)
- Calls `scripts/run_kite_ingest.py --days 2` (catches yesterday + today, dedupes via log)
- Pre-flight check verifies Kite session is live; fails LOUDLY (with action message) if expired
- Logs to `results/kite_ingest_{stdout,stderr}.log`
- Each day adds ~1 MB to parquet store; ~250-500 MB/year all-in

**Daily routine for Rohan:**
- Morning ~9:00 IST: run `python3 scripts/kite_login.py` (Kite tokens expire ~6 AM daily)
- Trading: ask "give me expiry levels" (uses live data via `lib/kite_live.py`)
- Evening: cron runs automatically at 16:30, no action needed

**Management commands:**
```bash
launchctl unload  scripts/com.rohanshah.kite-ingest.plist     # disable
launchctl load -w scripts/com.rohanshah.kite-ingest.plist     # re-enable
launchctl print gui/$(id -u)/com.rohanshah.kite-ingest        # status
```

### 9O. E-1 RULES (post-7-May-2026 calibration)

After the 6-May SENSEX E-1 incident (entered 79500 CE / 75000 PE at 14:00, war-stop news spike pushed spot +1.4% in minutes, strikes went from 3% → 1.5-1.8% away), Rohan locked these E-1 rules:

**Timing:**
- **DO NOT enter E-1 between 9:15 and 14:45** — news-risk window (geopolitical/policy shocks whipsaw 3% strikes).
- **Optimal entry: 14:45-15:15** — news mostly digested, theta accelerated, only 45 min residual surprise risk.
- 15:15-15:25: only if per-Cr ≥ ₹7,500 floor met.
- After 15:25: SKIP. Go to E-0 next day.

**Distance + premium floor (overnight carry):**
- **Min distance: 3.5% OTM both legs** (was 3% — too close, got whipsawed).
- **Premium floor: per-Cr ≥ ₹7,500** combined CE+PE × shares-per-Cr at E-0 margin.
- **Premium ideal: per-Cr ≥ ₹10,000.**
- If neither floor met at 3.5%+ distance after 14:45 → SKIP E-1 entirely.
- Strikes at 5% with ₹1.5 combined premium are USELESS — don't bother.

**Margin reference:** always use **E-0 margin** (not E-1) for sizing math:
- SENSEX E-0 deep OTM: 40 lots/Cr (~₹2.5L per lot)
- NIFTY E-0 deep OTM: 43 lots/Cr (~₹2.35L per lot)
- E-1 margin is 1.5-1.7× lower but E-0 margin is charged from next morning anyway.

**Asymmetric distance per bias** — CE and PE need NOT be at same distance:
- BEARISH bias (max-pain < spot, gap-down, PCR < 0.7): PE further (4%+), CE at 3.5% floor.
- BULLISH bias (max-pain > spot, gap-up, PCR > 1.2): CE further (4%+), PE at 3.5% floor.
- NEUTRAL: both at 3.5%+.
- VIX overlay: ELEVATED 16-18 → +0.25% both sides; HIGH 18-22 → +0.5%; EXTREME ≥22 → +1%.

**Encoded in `dashboard/server.py`:** `_pick_asymmetric_strangle()` and verdict logic in `_trade_verdict()`. UI endpoint: `/api/recommend/{instrument}?bias=auto`.

### 9T. CANONICAL RULEBOOK — Navin Group Expiry Day SOP (locked 7-May-2026)

**Source:** `Expiry Day Index Trading Strategy (2).docx` in project root. This **supersedes** all prior tier definitions. Every recommendation in this dashboard derives from this document.

**Capital split:** 95% Bucket A (Deep OTM strangles) + 5% Bucket B (Slightly Risky).

#### Bucket A — Deep OTM Strangles (95% capital)
| Param | Rule |
|---|---|
| Strike distance | **≥ 2.5% from ATM, ideally 3%+** |
| On riskier days | push further OTM |
| Premium target | **≥ ₹5,000/Cr ideal** · **₹4,000/Cr min** (escalate below) |
| Full quantity trigger | combined premium **≥ ₹6,000/Cr** between 9:15-10:30 → fire entire quantity in one shot |
| Entry window NIFTY | 9:15-9:40 primary · hard cutoff 12:00 |
| Entry window SENSEX | put limits, wait. Premium often climbs to ₹11-12. Hard cutoff 12:00 |
| **SENSEX secondary** | **11:00-12:00 — premium can spike with no spot move; take remaining alloc** |
| SL trigger | spot within **0.5%** of strike (NIFTY 150 pts, SENSEX 500 pts) |
| **SL execution** | **NO automatic SL ever** — manipulators trigger fake SLs. Manual only, confirm spike is real (matches spot, lasts >few mins). |
| Discretionary squareoff | spot moved >1% from entry, OR within 1% of strikes (rethink), OR within 0.5% (hard close) |

#### Bucket B — Slightly Risky (5% capital)

##### B1 — ATM Straddle (opportunistic, only on calm days)
| Param | Rule |
|---|---|
| Trigger | Market NOT trending one-way AND NOT unusually volatile (confirm via OI/tech/news) |
| Entry | 9:45-10:15 |
| Premium target | > **₹50,000/Cr** |
| SL | 10% above combined premium (per leg) — exit FULL position if either hits |
| Profit book | ₹15-20K/Cr |
| Hard close | **12:00 PM max** |

##### B2 — Mid-Deep Range Strangle (DEFAULT B-bucket play)
| Param | Rule |
|---|---|
| Strike | delta-neutral, decent premium, mid-far OTM, low SL-hit probability |
| Premium target | **₹10,000-20,000/Cr** |
| Profit book | ₹2-5K/Cr or trail SL |
| SL | manual 2.5× combined entry premium + dealer alert at OTM-distance threshold |
| Hold | can hold to expiry if strikes far enough |

#### Universal Rules
- **No B1 / high-risk on volatile days** — gap-ups, major events, VIX spikes
- Book profits early when achieved — no greed
- Every trade in Sensibull immediately
- Every trade in Google Sheet

#### Daily Workflow
| Time | Action |
|---|---|
| Pre-market | Review fundamentals/news/VIX/OI/global cues. Decide safe-to-trade + risk level |
| 9:15-10:30 | Deploy Bucket A; take B1/B2 between 9:45-10:15 |
| 10:30-11:30 | Monitor B-bucket, book/exit. Redeploy freed capital to A |
| **By 12:00 PM** | **ALL B-bucket positions MUST be closed.** Only A remains into close |

#### Harvest Strategy (post-12:00, separate from Bucket A/B)
- SENSEX manipulator pattern: 2:45-3:30 PM window
- Manipulators buy deep OTM low-OI strikes to spike prices, then sell
- Buy 4-5 deep OTM ₹0.05-0.10 strikes, sell-limits at ₹1+ via GTT
- Backtest research in progress (`research/strike_spike_harvester_v1.md`)

#### Encoded in `dashboard/server.py`:
- `TIER_DEFS`: A=95% / B2=5%
- `PREM_PER_CR_E0_MIN/TARGET/FULL_QTY` constants = 4000/5000/6000
- `SL_DISTANCE_PTS` = {NIFTY: 150, SENSEX: 500}
- Verdict logic: SENSEX secondary window 11:00-12:00 with premium-override-fire rule
- Position analysis: SL trigger flag when spot within 0.5%

### 9S. The actual tier structure (corrected 7-May session)

Rohan's CONFIRMED tier-by-capital structure for E-0 main shot:

| Tier | Distance OTM (E-0 non-event) | Capital allocation | Per-Cr target | Hit rate | Notes |
|---|---|---|---|---|---|
| **LOW (Ultra-Safe)** | **2.5%+** (floor) | ~80% | ₹3-5K | 96%+ | The workhorse |
| **MID** | **~1.0%** | **up to 10%** | ₹10-15K | 75-85% | High premium, designed for MTM volatility |
| **HIGH / Lottery harvest BUY** | 4-6% OTM | small (₹10-15K outlay) | n/a (×10 multiplier on hit) | 1+ spike on 75% of expiry days | 14:00-15:25 only |
| **Deep OTM SELL** (the giant 80K CE / 76K PE) | 2.0-2.5% | additional ~5-10% via existing strangle | ₹3-4K | 92-96% | Often layered on top of Low Risk |

**Why MID risk is taken even though it MTM-swings:**
- Per-Cr captured is ₹12-15K vs ₹3-5K for Low — 3-4× the premium
- Bounded to 10% capital → max realized loss << gains from Low+Mid combined
- Hit rate ~75-85% means net positive EV over many days
- The intraday volatility is the COST of the higher premium, not a defect

**Wrong framing to avoid:** treating the MID strikes as "concentration risk" because they're closer to spot. They're a separate sized bucket. The 10% cap is the safety mechanism.

**Encoding in dashboard:**
- `dashboard/server.py` `tier_target_dist`: T2 base = 1.0% (was 2.0% — wrong)
- T2 capital pct: 10% (was 12% — close, fine)
- Hit rate floor for T2: 0.80 (not 0.92)

### 9R. NEVER skip a trade on a tradable day — premium floors

**Rule (Rohan, 7-May session):** On any E-0 (or E-1 within window), THERE IS NO "NO-TRADE" OPTION. Skipping = losing free money. Always take SOMETHING — adjust the strikes/sizing, never zero out.

**Hierarchy of premium targets per ₹1 Cr (E-0 margin):**

| Outcome | Per-Cr captured | Action |
|---|---|---|
| 🎯 **Optimal** | **≥ 90% of pre-spot-moved peak** | Hit if you placed limits in 9:17-9:35 sweet spot. Premium near intraday peak. |
| ✅ **Ideal** | **₹5,000/Cr** | Standard main shot — T1 (ultra-safe 2.5%+) at decent premium |
| 🟡 **Acceptable** | **₹3,000/Cr (FLOOR)** | Bare minimum. Below this = took bad strikes or too late entry |
| 🔴 **Below floor** | < ₹3,000/Cr | NEVER. Adjust: tighter strike, more lots, or BOTH. Don't deploy at this premium. |

**Plus kicker:** layer **T2 (closer ~2%) + T3 (closer ~1.5%)** on top of T1 for additional premium. Total all-in per-Cr ≈ ₹6-10K combining T1+T2+T3.

**If T1 at 2.5%+ doesn't hit ₹3K floor:**
1. First try slightly closer (2.3-2.4% — still ultra-safe on low-vol days)
2. If still below → push capital harder (more lots) on T2 closer strikes
3. Never skip the day

**Verdict logic:** the dashboard's `_trade_verdict()` should NEVER output "NO_TRADE" or "SKIP" on a tradable E-0/E-1 day. Worst-case verdict = "MARGINAL" with adjusted-sizing instructions.

### 9Q. "Ultra-safe" is an EARNED label, not a template

**Rule (Rohan, 7-May session):** Don't paste "ultra-safe" on a strike just because it's at 2.5% OTM. Real ultra-safe = **"almost zero chance of expiring ITM" + "reasonable premium captured"**. The 2.5% is the MINIMUM band; on volatile days / event days / news days / IV-rising days, push wider.

**How to assess regime each day (do this BEFORE recommending strikes):**

| Signal | LOW-VOL day | MODERATE | HIGH-VOL / event day |
|---|---|---|---|
| VIX level | <16 | 16-18 | >18 |
| VIX trend | falling | flat | rising |
| Day's range so far | <0.5% | 0.5-1.0% | >1.0% |
| Wall OI strength | giant 4M+ both sides | mid 2-4M | thin <2M or recently breached |
| Max-pain vs spot | within 0.3% | 0.3-1% | >1% or spot moving away |
| Event calendar | nothing | minor | Fed/RBI/Budget/Election/War-news/earnings |
| Recent shock (1-2 days) | quiet | one news event digested | active news cycle |

**Rule: ultra-safe distance = 2.5% × (1 + volatility_adjustment)**
- All-LOW signals → 2.5% IS ultra-safe (don't widen unnecessarily — you give up real premium)
- Mostly-MODERATE → 3.0%
- Multiple HIGH signals → 3.5-4.0%+

**Reasonable premium floor:** target combined CE+PE × shares-per-Cr ≥ ₹3,000-4,000 for E-0 same-day, ≥ ₹7,500 for E-1 carry. If "ultra-safe" distance gives premium way below this, push slightly closer (e.g. 2.5% on safe day) rather than 4% with ₹1.5 premium.

**System hookup needed:** add a `regime_score()` function to `dashboard/server.py` that grades today's volatility, then dynamically sets T1 distance floor between 2.5% (low-vol) and 4% (high-vol). Until coded: assess manually each session before quoting strikes.

### 9P. Lesson 6/7-May (the trade that taught the rule)

- **What happened:** Wed 6-May, took 79500 CE / 75000 PE (~3% OTM each side) at ~14:00. War-pause news at ~15:00 spiked SENSEX +1.4%. CE went from ~₹6 → ₹30+ at close. PE breakeven. Cut all PE + 30% CE; carried 70% CE overnight. Thursday 7-May expiry day, spot opened +0.43% gap up at 78,294. CE 79,500 buffer dropped from 3% to 1.54%.
- **Root cause:** Wrong TIMING (entered during news-risk window 14:00) + wrong DISTANCE (3% too close).
- **Not a system fault** — the dashboard had recommended 79,300/75,000 at the same time, identical exposure to the same risk. Framework needed updating, not just the trade.
- **Fixes applied:** Section 9O above (timing window 14:45+, distance ≥3.5%, per-Cr floor enforcement, asymmetric bias support).
- **Tool added:** `/api/recommend/{instrument}?bias=auto` returns asymmetric strangle with combined premium + per-Cr captured + sizing per E-0 margin.

### 9U. SHORT STRADDLE / NEAR-ATM STRANGLE — when (rare) and how (strict)

**STATUS — 2026-05-11: ABANDONED by Rohan.** After the live test confirmed the backtest verdict (regime-filter violation → ~₹83K/Cr loss on 10 lots) and exposed the leg-out trap, Rohan decided the straddle play is not worth the cognitive overhead at any allowed size. The rules below are retained as **archived reference** — if reconsidered in future, every filter must pass unanimously AND user must explicitly opt-in per-trade. Default answer to "should I do a straddle today?" is **NO, do not even check the filters**.

The active core stays: §1 (E-1 advance 10% deep OTM) + §2 (E-0 three-tier: Bucket A 70% deep OTM + High Risk B1 + Mid Risk) per §9S/9T canonical structure. That's the entire playbook.

---

**Archived ruleset (use only if §9U is reactivated):** Backed by analysis 016 (388K simulations: NO setting in entire grid achieves mean EV ≥ +₹5K/Cr AND worst-day ≥ −₹100K/Cr) + live data point 2026-05-11 (entered during regime-violation, paid ~₹19K tuition on 10-lot test).

The Bucket A 2.5-3% OTM E-1 short (analyses 006/007) strictly dominates this trade in every metric — hit rate, mean EV, max drawdown. Default answer to "should I do a straddle?" is **NO**. The rules below define the narrow case where a small experimental position is permissible.

#### 9U.1 — DTE: E-1 only

- **E-1 (1 trading day to NIFTY weekly expiry)** = the only DTE with marginal positive mean EV in backtest.
- **E-0 (expiry day) = PROHIBITED.** Gamma compresses faster than theta in the final 6 hours. Late-day ATM straddle SELL is picking nickels in front of a steamroller: ₹113K/Cr max payoff vs ₹274-597K/Cr loss on a 100-200pt news move. Bad geometry.
- **E-2, E-3, E-4 = PROHIBITED.** Negative mean EV in 016 backtest, worst-day −₹434K to −₹853K/Cr.
- **NIFTY only.** SENSEX not backtested for this — do not extrapolate.

#### 9U.2 — Regime filter (ALL must be true to even consider entry)

| Condition | Threshold | If violated |
|---|---|---|
| NIFTY |gap| at open | ≤ 0.5% | **SKIP** — gap days defeat the SL (3/3 tail-loss days in 016 had gap > 0.5%) |
| India VIX level | ≤ 17.0 | **SKIP** — premium inflated but realized vol also higher; SL fires |
| India VIX day-change | ≤ +1.0pt | **SKIP** — rising vega works against short straddle |
| NIFTY intraday range by 10:30 | ≤ 0.5% | **SKIP** — early trending = continuation |
| News calendar 24hr | NOTHING pending | **SKIP** — RBI, Fed, geopolitical hot-zone, earnings of index heavyweights |
| Brent crude move 24hr | ≤ ±3% | **SKIP** — oil shocks transmit to INR + equities |
| Bucket A position | NOT yet open | **SKIP this** — never compete with core capital trade |

**The 2026-05-11 case study:** gap −0.85% (red), VIX 18.84 + rising 2.0pt (red), Iran-Trump geopolitical headline (red). **THREE regime flags red.** Trade should never have been placed. Live loss of ~₹19K on 10 lots = ~₹83K/Cr equivalent = textbook tail-day cost. The strategy didn't fail; the filter was overridden.

If even ONE flag is red → SKIP. No exceptions, no "but the premium looks juicy."

#### 9U.3 — Entry timing (when, if regime is clear)

- **DO NOT enter 09:15-09:59.** Open noise + IV inflation + false moves.
- **Sweet spot: 10:00-10:30.** Gap (if any small one existed) has settled, IV has softened from open print, theta runway = ~5 hours.
- **Acceptable: 11:00-12:00.** If 10:00 read was unclear. Less theta but cleaner price discovery.
- **Hard cutoff: 12:30.** Past this, not enough theta runway to clear friction + slippage.
- **Skip the day if you missed the window.** Don't force an entry at 13:30 because "I had time only now."

#### 9U.4 — Strike selection

- **ATM straddle** — round NIFTY spot at entry minute to nearest 50. Both legs at this strike.
- **NOT slightly-OTM strangles (100/200 pt apart).** Analysis 016 showed OTM_100/OTM_200 added nothing on EV; you give up theta without gaining tail protection.
- **NOT staggered strikes.** One strike, both legs.

#### 9U.5 — Sizing

| Phase | Cap |
|---|---|
| Initial trial (first 10 trades) | ≤ ₹1 Cr deployed (10 lots NIFTY ≈ ₹2.35L margin) |
| If win-rate ≥ 60% AND mean EV ≥ +₹3K/Cr after 10 trades | ≤ ₹3 Cr |
| Lifetime cap on this strategy | ≤ ₹5 Cr |
| **Never** | full book / overlap with Bucket A capital |

**Position is a SLEEVE.** Loss of ₹50-100K on any single trade must be absorbable without affecting core P&L narrative.

#### 9U.6 — TP / SL framework (PROPORTIONAL, not absolute)

The backtest used fixed ₹/share thresholds (TP=₹15, SL=₹15). Real-world: premium varies with VIX. Convert to **percentage of entry combined premium:**

| Trigger | Level | Rationale |
|---|---|---|
| **Take-profit** | combined ≤ entry − 10% | Captures realistic 1-day theta + small mean-reversion. ₹40-60K/Cr profit at typical entry premiums. |
| **Stop-loss** | combined ≥ entry + 12% | Wider than backtest's literal ₹15 because VIX-elevated entries need vol-room. ₹50-70K/Cr design loss. |
| **SL confirmation** | 3 consecutive 1-min closes at/above SL | 016 backtest: confirm_3m beats instant by +₹4K/Cr; cuts SL-hit rate ~8pp by filtering fake wicks. |
| **Time stop** | 14:00 IST hard exit | Past 14:00, gamma > theta as expiry tomorrow approaches. Don't hold into 15:25 hoping for pin. |

**Example at entry combined = ₹150:**
- TP = ₹135 (limit buy on both legs)
- SL = ₹168 (manual watch; trigger requires 3-min confirm above 168)
- Time exit = 14:00 market close on both legs

**Example at entry combined = ₹200 (high-VIX day — should not have entered, but if you did):**
- TP = ₹180
- SL = ₹224
- Time exit = 14:00

#### 9U.7 — Exit discipline (the lesson from 2026-05-11)

**HARD RULES — no exceptions:**

1. **SL triggers on COMBINED premium**, not on either leg individually.
2. **When SL fires, close BOTH legs at MARKET, simultaneously.** Never close just one.
3. **NO legging out.** A short straddle is hedged against gamma in both directions while both legs are alive. Close ONE leg = remaining leg becomes a naked directional bet.
4. **NO limit orders on the losing leg.** Limits don't fill, market keeps moving against you, you chase at worse price. (The 2026-05-11 mistake: closed CE at ₹59, set PE limit at ₹140, PE ran to ₹156 before fill — cost ~₹10-12/share recoverable juice.)
5. **If you must adjust one leg, close the LOSING leg first.** Counterintuitive but correct: cap your downside risk before locking in the winning side.
6. **Once both legs closed, NO re-entry that day.** Take the data point, walk away.

#### 9U.8 — Mid-trade events

| Event | Action |
|---|---|
| NIFTY moves 0.5% in 30 min | Watch carefully, don't auto-close. Wait for 3-min SL confirm. |
| VIX jumps +1pt mid-trade | Mentally widen SL trigger by +1% of entry premium (vega hedge). |
| Breaking news headline | Close immediately at market regardless of combined level. Accept the loss. |
| One leg falls 40%+ fast | **DO NOT take the easy win.** The remaining short is now naked. Either close both, or hold both. |
| Brent crude jumps > 2% intraday | Close at next reasonable level. Crude shocks mean continued trend. |

#### 9U.9 — Post-trade logging

Every trade attempted (skipped, won, lost) gets logged to `FINDINGS_LOG.md` with:
- Entry combined, exit combined, time held
- ₹/Cr P&L gross + net
- Regime conditions at entry (gap %, VIX, intraday range, news headline)
- Lesson if any

**Strategy review triggers — ABANDON the sleeve if:**
- 5 consecutive losses, OR
- After 10 trades: mean EV < +₹0/Cr (after friction), OR
- Single-trade loss > ₹150K/Cr.

#### 9U.10 — Honest summary

This strategy exists because the backtest left a narrow ≤ 1.3% probability of acceptable-EV-with-bounded-tail outcome. The sleeve allows live calibration of that thin edge. It is **not** a yield generator — your Bucket A (2.5-3% OTM E-1, mean +₹62K/Cr, 100% wins) already serves that role. The straddle sleeve is for measurement: does the backtest's mean +₹3K/Cr survive live execution, or is it an illusion?

**If you find yourself thinking "the straddle looks great today, let's go bigger" — STOP.** That's the sleeve trying to become a strategy. It can't.

### 9V. DEEP OTM (3-5%) INTRADAY BEHAVIOR ON E-0 (locked 14-May-2026)

Rohan flagged repeatedly: "PE rises when spot rises, CE rises when spot falls, BOTH rise to a certain time period regardless of movement." I (Claude) kept projecting smooth theta curves from 9:30, was wrong on intraday timing. Ran 54 SENSEX E-0 days from parquet store. **Rohan's observation is empirically correct.**

**KEY EMPIRICAL FACTS (SENSEX E-0, 4-5% OTM strikes):**

| Time vs 9:30 | 3% OTM combined | 4% OTM combined | 5% OTM combined | % days combined HIGHER |
|---|---|---|---|---|
| 10:00 | −1.3% | −1.9% | −4.2% | **44% / 37% / 31%** |
| 10:30 | −4.4% | −4.9% | −3.9% | 44% / 37% / 29% |
| 11:00 | −12.0% | −10.2% | **−2.9%** | 33% / 39% / 31% |
| 12:00 | −18.0% | −12.7% | −17.0% | 33% / 35% / 31% |
| **12:30** | — | **−19.4%** | **−22.0%** | (decay starts here) |
| 13:00 | −30.9% | −25.0% | −26.1% | (real decay zone) |
| 14:00 | — | −36.4% | −33.4% | (heavy decay) |

**Peak distribution (when does combined premium PEAK during the day):**
- 3% OTM: 31% of days peak at 9:30 (immediate decay) · **69% peak AFTER 9:30**
- 4% OTM: 22% of days peak at 9:30 · **52% peak AFTER 9:30**
- 5% OTM: 15% of days peak at 9:30 · **42% peak AFTER 9:30**

**"Both sides simultaneously up vs 9:30" (the user's specific observation):**
- 3% OTM: 13-20% of mornings have CE+PE both higher 10:00-11:00
- 4% OTM: 13-24% of mornings have BOTH higher
- 5% OTM: 12-23% of mornings have BOTH higher

**MECHANISM (structural, not noise):**
1. **Morning IV expansion (vega)** — pre-pin uncertainty inflates IV on both sides 9:30-11:00. Vega is positive on both CE and PE (a +1pp IV move adds value to both).
2. **MM gamma re-hedging** — option writers buy back delta during morning chop, lifting LTPs on both sides.
3. **Bid-ask spread widening on E-0 morning** — thin OTM liquidity, single trades print at the ask, LTPs appear elevated vs mid-fair-value.
4. **Skew dynamics** — PE IV is structurally higher than CE IV (volatility skew). When spot moves AGAINST PE, PE-side IV rises, partially offsetting delta loss. Same mechanism creates the "PE up when spot up" phenomenon on 1-in-3 days.
5. **Tiny absolute base** — at deep OTM, combined premium is ₹2-3. A normal ₹0.30 spread/vega move = 10-15% in % terms. Looks dramatic, isn't economically meaningful.

**RULES (operating):**

1. **After 9:17-9:22 Bucket A entry, DO NOT WATCH SCREEN until 13:00.** Distance (≥2.5% OTM) is the safety. Morning premium oscillation is noise.

2. **NEVER project smooth theta decay for morning hours.** If asked, say: "Premium will chop ±15-30% till 12:30, then real decay starts. Don't time-manage on morning MTM."

3. **Combined MTM showing 10-30% adverse at 10:00-11:30 = STATISTICAL NORMAL on 30-44% of days.** This is not a signal to act.

4. **Both sides rising simultaneously vs 9:30 = expected.** Happens on 1 in 5 mornings.

5. **For closer-tier (Mid Risk / B1) entries** — defer to **12:30-13:00 window** (where decay window opens) instead of 10:30-11:30 (still chop zone). The "9:17-9:22 best entry" only applies to deep OTM Bucket A (which uses distance, not premium curve, as its safety).

6. **The only intraday signal that matters: distance % from spot.** If spot breaks toward your strike (e.g., 23,000 PE on NIFTY with spot 23,400 — only 1.7% buffer), THEN act. Premium % moves alone are uninformative.

**WHY I (CLAUDE) KEPT MISSING THIS — self-correction:**

1. Defaulted to Black-Scholes theta intuition (monotonic decay from open) instead of empirical chop pattern.
2. Conflated analysis 009's "9:17-9:22 = 100% worthless at 2.5%+ OTM at EXPIRY" with "premium decays smoothly from 9:30." Those are different claims.
3. Didn't run cross-cutting SENSEX-specific analysis. Applied NIFTY learnings (analysis 017 CE inflation) to SENSEX without verifying.
4. Didn't internalize Rohan's repeated observations across sessions as priors.

**Going forward: any morning projection for Bucket A MUST cite §9V chop band, not Black-Scholes theta.**

### 9W. TIER 2 / TIER 3 FRAMEWORK — NEAR & MID OTM (locked 2026-06-04)

**Source backtests:** Analyses 018-024 on 56 NIFTY + 54 SENSEX E-0 days (2025-04 → 2026-06).

This section governs short positions at strikes **0.5%-2.0% OTM** on E-0 (expiry day). Tier 1 (deep OTM ≥2.5%) is unchanged — see §1 and §2.

---

#### 9W.1 — Tier definitions

| Tier | OTM range | Use case | Yield target |
|---|---|---|---|
| **Tier 1** | ≥ 2.5% | Workhorse (existing §1, §2) | ₹5-50K/Cr (varies by depth) |
| **Tier 2** (mid OTM) | 1.25% – 2.0% | Higher yield on calm days | ≥ ₹12.5K/Cr |
| **Tier 3** (near OTM) | 0.5% – 1.0% | Maximum yield on tight-filter days | ≥ ₹20-40K/Cr |

---

#### 9W.2 — Layer 1: Hard exclusions (skip Tier 2 AND Tier 3 entirely)

If ANY of these is true, **only Tier 1 today**:

| Flag | Trigger |
|---|---|
| Major news within 24hr | RBI / Fed / Budget / Election / War / Index-heavyweight earnings |
| Pre-entry gap at open | \| gap % \| > 0.7% |
| Pre-entry net move (9:15 → 10:30) | \| move \| > 0.7% |
| Pre-entry range (9:15 → 10:30) | > 1.0% |
| India VIX | > 19, OR rising > 1.5 pt intraday |
| Brent crude 24hr move | > ±3% |
| Yesterday's day-range | > 1.5% (regime carryover) |

ANY red → Tier 1 deep OTM only. No exceptions.

---

#### 9W.3 — Layer 2: Tier 3 (near OTM 0.5-1%) — entry rules per instrument

**Instrument-specific filters** (NIFTY has ~half the morning chop of SENSEX — median pre-range NIFTY 0.51% vs SENSEX 0.85%):

##### NIFTY Tier 3 — entry rules

| OTM | Best entry | Pre-range ≤ | Premium ≥ | \|Pre-move\| ≤ | Exit | Backtest n | Mean ₹/Cr | Win | Worst |
|---|---|---|---|---|---|---|---|---|---|
| **0.5%** | 10:30 | **0.4%** | ₹40K/Cr | 0.4% | HOLD | 12 | +₹53K | 92% | −₹43K |
| **0.5% volatile-recovery** | **12:00** | range 0.4-0.5% by 12:00 | ₹40K/Cr | n/a | HOLD | 5 | +₹45K | 80% | **−₹7.6K** ✓ |
| **0.7%** | 10:30 | **0.4%** | ₹30K/Cr | 0.4% | HOLD | 9 | **+₹45K** | **100%** | **+₹32K** ✓✓ |
| **1.0%** | **11:00-12:00** | **0.7%** | ₹20K/Cr | 0.7% | HOLD | 5-6 | +₹25-32K | 100% | +₹20-22K ✓✓ |

##### SENSEX Tier 3 — entry rules

| OTM | Best entry | Pre-range ≤ | Premium ≥ | Exit | Backtest n | Mean ₹/Cr | Win | Worst |
|---|---|---|---|---|---|---|---|---|
| **0.5%** | 11:00 | **0.5%** | ₹40K/Cr | HOLD | 4 | +₹57K | 75% | −₹109K |
| **0.5% volatile-recovery** | **12:00** | range 0.5-0.7% by 12:00 | ₹40K/Cr | **T_1400** | 5 | +₹17K | 80% | **−₹2.5K** ✓ |
| **0.7%** | **11:30** | **0.8%** | ₹30K/Cr | HOLD | 6 | **+₹42K** | **100%** | **+₹4K** ✓✓ |
| **1.0% ★ STAR TRADE** | **10:00** | **0.7%** | ₹20K/Cr | **HOLD** | **14** | **+₹47K** | **100%** | **+₹20K** ✓✓ |

---

#### 9W.4 — Layer 3: Tier 2 (mid OTM 1.25-2%) — entry rules

##### NIFTY Tier 2

| OTM | Best entry | Pre-range ≤ | Premium ≥ | Exit | n | Mean ₹/Cr | Win | Worst |
|---|---|---|---|---|---|---|---|---|
| 1.25% | 11:30 | 0.7% | ₹15K | HOLD | 7 | +₹21K | 100% | +₹14K ✓ |
| 1.5% | 11:00 | 0.7% | ₹12.5K | HOLD | 10 | +₹17K | 100% | +₹12K ✓ |
| 1.75% | 09:45 | 1.0% | ₹10K | HOLD | many | +₹13K | 100% | +₹10K ✓ |
| 2.0% | 09:45 | 1.0% | ₹8K | HOLD | 36 | +₹16K | 100% | +₹8K ✓ |

##### SENSEX Tier 2

| OTM | Best entry | Pre-range ≤ | Premium ≥ | Exit | n | Mean ₹/Cr | Win | Worst |
|---|---|---|---|---|---|---|---|---|
| 1.25% | 10:00 | 0.8% | ₹15K | HOLD | 11 | **+₹32K** | 100% | +₹400 ✓ |
| 1.5% | 10:00 | 1.0% | ₹12.5K | HOLD | 17 | +₹30K | 100% | +₹1K ✓ |
| 2.0% | 09:45 | 1.0% | ₹8K | HOLD | 14 | +₹17K | 93% | −₹200 ✓ |

---

#### 9W.5 — Layer 4: Yellow override (intraday safety net, all tiers)

Source: Analyses 019-021. Even with tight entry filter, monitor spot through the day.

**Yellow signal definitions (apply per leg):**

| Signal | PE side trigger | CE side trigger |
|---|---|---|
| S1_BUFFER_50 | Spot reaches `entry_spot − 0.5 × pe_buffer` | Spot reaches `entry_spot + 0.5 × ce_buffer` |
| S2_RANGE_BREAK | Spot drops below `pre_entry_low × 0.999` | Spot rises above `pre_entry_high × 1.001` |
| S9_BIG_MOVE_30 | 30-min net move ≤ −0.4% (≈ −96 NIFTY pts / −300 SENSEX pts) | 30-min net move ≥ +0.4% |

**Yellow rules per instrument × side:**

| Instrument | Side | OTM | Yellow rule (AND) | Fire rate | P(ITM\|fired) | P(ITM\|not fired) |
|---|---|---|---|---|---|---|
| NIFTY | PE | 0.7% | S1_BUFFER_50 + S9_BIG_MOVE_30 | 20% | 58% | 0% |
| NIFTY | CE | 0.7% | S1_BUFFER_50 + S9_BIG_MOVE_30 | 21% | 54% | 0% |
| SENSEX | PE | 0.7% | S1_BUFFER_50 + S9_BIG_MOVE_30 | 33% | 42% | 0% |
| SENSEX | CE | 0.7% | S2_RANGE_BREAK + S9_BIG_MOVE_30 | 48% | 18% | 0% |

**Action on Yellow fire: close ONLY the losing leg at market. Keep other leg.**
NOT both. NOT roll cascade. Just close the threatened leg.

**RED (mechanical close, no negotiation):**
- Spot reaches `entry_spot − 0.85 × pe_buffer` (or CE equivalent)
- OR spot crosses INTO strike intraday
- Action: close BOTH legs at market, no re-entry that side today

---

#### 9W.6 — Layer 5: Profit-take + time stops (per analysis 022, 023)

**Profit-take notation:** `PT_X` = close when combined ≤ `(1−X/100) × entry_combined`. So PT_70 = wait until premium drops to 30% of entry.

**Recommended exit per tier:**

| Tier | OTM | Recommended exit | Rationale |
|---|---|---|---|
| 3A | 0.5% | HOLD or PT_80 | Premium too high to give up, but PT_80 caps tail |
| 3B | 0.7% | HOLD | Filter is strict enough that worst day is positive |
| **3C ★** | **1.0%** | **HOLD** | **100% win in sample — never give up the bird** |
| 2A | 1.25% | HOLD | Same |
| 2B | 1.5% | HOLD | Same |
| 1 | ≥2.0% | HOLD | Same |
| **3A volatile-recovery** | **0.5% @ 12:00** | **T_1400 hard close** | **Cap gamma exposure to 90 min only** |

**PT_X analysis (when applicable):** PT_60 dominates PT_70 (your prior rule) on filtered SENSEX 0.7%:
- PT_60: +₹20K mean, 84% win
- PT_70 (your rule): +₹15K mean, 72% win
- HOLD: +₹27K mean, 72% win — but only viable on filtered days

If you want PT discipline, use **PT_60 not PT_70**. Premium often bounces between 30-40% before reaching 30% (PT_70), so PT_60 catches the wins PT_70 misses.

---

#### 9W.7 — Layer 6: Sizing per tier

| Tier | Max % of capital | Reason |
|---|---|---|
| Tier 1 | up to 100% (per §2) | Existing main book |
| Tier 2 | ≤ 30% of book | Yield boost, low risk |
| Tier 3 | ≤ 15% of book | Higher risk, smaller sleeve |
| Tier 3 volatile-recovery (0.5% @ 12:00) | ≤ 5% of book | Experimental, smallest |

**Worst-case loss exposure rule (universal):** any single Tier 2/3 leg sized such that worst-day from backtest × deployed-Cr ≤ ₹1L absolute. At ₹100Cr book and Tier 3 worst ₹−109K/Cr, max ₹91L deployed = ~9 lots NIFTY / ~36 lots SENSEX per leg.

---

#### 9W.8 — Layer 7: Decision algorithm (apply every morning)

```
Step 1 — 09:30 IST snapshot:
   spot_NIFTY, spot_SENSEX, VIX, gap%, news calendar, Brent

Step 2 — At 10:30 IST: capture pre-entry context
   pre_range_pct (9:15-10:30 high-low / open)
   pre_move_pct (10:30 spot vs 9:15 open)

Step 3 — HARD EXCLUSION check (Layer 1):
   ANY red flag → only Tier 1 today, EXIT this algorithm

Step 4 — Try TIER 3 STAR TRADE first (SENSEX 1.0% OTM @ 10:00):
   IF (pre_range ≤ 0.7%) AND (combined premium ≥ ₹20K/Cr at 0.7% strikes):
     → DEPLOY SENSEX 1.0% short strangle, HOLD to expiry
   ELSE skip to Step 5

Step 5 — Try TIER 3 secondary (0.7% OTM):
   FOR NIFTY @ 10:30 with pre_range ≤ 0.4%, prem ≥ ₹30K/Cr → DEPLOY HOLD
   FOR SENSEX @ 11:30 with pre_range ≤ 0.8%, prem ≥ ₹30K/Cr → DEPLOY HOLD

Step 6 — Try TIER 3 deepest (0.5% OTM):
   FOR NIFTY @ 10:30 with pre_range ≤ 0.4%, prem ≥ ₹40K/Cr → DEPLOY HOLD
   FOR SENSEX @ 11:00 with pre_range ≤ 0.5%, prem ≥ ₹40K/Cr → DEPLOY HOLD or PT_80

Step 7 — Volatile-morning recovery for 0.5% OTM:
   IF (10:30 filter failed BUT pre_range improved by 12:00 to ≤0.7% SENSEX or ≤0.5% NIFTY):
     → DEPLOY 0.5% OTM at 12:00 with HARD T_1400 EXIT (small size, ≤5% book)

Step 8 — Try TIER 2 (1.25-2.0% OTM):
   FOR NIFTY @ 11:00-11:30 entries at 1.25-1.5% OTM, pre_range ≤ 0.7%, prem floors per table
   FOR SENSEX @ 10:00 entries at 1.25-1.5% OTM, pre_range ≤ 0.8%

Step 9 — Tier 1 base (always run if not already deployed):
   Per existing §1 and §2 deep OTM playbook

Step 10 — Post-deployment monitoring:
   Set Yellow GTT alerts at S1_BUFFER_50 spot levels per leg
   If Yellow fires: close LOSING leg only at market
   If RED fires: close BOTH legs at market
```

---

#### 9W.9 — PRINTABLE QUICK-REFERENCE CARD

```
╔══════════════════════════════════════════════════════════════════════╗
║         TIER 2/3 PLAYBOOK — E-0 NEAR & MID OTM (§9W locked)          ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  ★ STAR TRADE: SENSEX 1.0% OTM at 10:00 IST                          ║
║    Filter: pre_range ≤0.7%, premium ≥₹20K/Cr                         ║
║    Exit:   HOLD to 15:25                                             ║
║    Expected: +₹47K/Cr mean, 100% win (14 days/yr)                    ║
║                                                                      ║
║  ▶ NIFTY 0.7% OTM at 10:30:                                          ║
║    Filter: pre_range ≤0.4%, premium ≥₹30K/Cr, |gap| ≤0.4%            ║
║    Exit:   HOLD                                                      ║
║    Expected: +₹45K/Cr, 100% win, worst +₹32K (9 days/yr)             ║
║                                                                      ║
║  ▶ NIFTY 0.5% OTM at 10:30 (tighter):                                ║
║    Filter: pre_range ≤0.4%, premium ≥₹40K/Cr                         ║
║    Exit:   HOLD                                                      ║
║    Expected: +₹53K/Cr, 92% win, worst −₹43K (12 days/yr)             ║
║                                                                      ║
║  ▶ SENSEX 0.7% OTM at 11:30:                                         ║
║    Filter: pre_range ≤0.8%, premium ≥₹30K/Cr                         ║
║    Exit:   HOLD                                                      ║
║    Expected: +₹42K/Cr, 100% win, worst +₹4K (6 days/yr)              ║
║                                                                      ║
║  ▶ VOLATILE-MORNING RECOVERY (0.5% OTM @ 12:00):                     ║
║    Use only if 10:30 was 0.5-0.7% range, settled by 12:00            ║
║    Filter: pre_range ≤0.5% NIFTY/0.7% SENSEX by 12:00, prem ≥₹40K    ║
║    Exit:   T_1400 hard close (90-min gamma window)                   ║
║    Size:   ≤5% of book                                               ║
║                                                                      ║
║  ▶ TIER 2 (1.25-2.0% OTM): 09:45-10:00 entry, HOLD                   ║
║    Premium floors: 1.25%→₹15K, 1.5%→₹12.5K, 2.0%→₹8K                 ║
║                                                                      ║
║──────────────── HARD EXCLUSIONS (Tier 1 only) ──────────────────────║
║  • News in 24hr (RBI/Fed/budget/election/war)                        ║
║  • |Gap| > 0.7%                                                      ║
║  • Pre-range > 1.0% by 10:30                                         ║
║  • VIX > 19 OR rising > 1.5pt                                        ║
║  • Brent move > ±3%                                                  ║
║                                                                      ║
║──────────────── INTRADAY YELLOW OVERRIDE ───────────────────────────║
║  Yellow PE: (spot ≤ entry − 50% buffer) AND (30-min move ≤ −0.4%)   ║
║  Yellow CE: (spot ≥ pre-entry high break) AND (30-min move ≥ +0.4%) ║
║  Action: close LOSING leg only at market, keep other                 ║
║                                                                      ║
║  Red: spot reaches 85% of buffer OR crosses into strike              ║
║  Action: close BOTH legs at market, no re-entry that side            ║
║                                                                      ║
║──────────────── SIZING ─────────────────────────────────────────────║
║  Tier 1: per §2 (main book)                                          ║
║  Tier 2: ≤30% of book                                                ║
║  Tier 3: ≤15% of book                                                ║
║  Volatile-recovery: ≤5% of book                                      ║
║  Universal: any leg sized so worst_pcr × deployed_Cr ≤ ₹1L absolute  ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

#### 9W.10 — Annual yield expectation at ₹100Cr book

| Tier | Days/yr (NIFTY+SENSEX) | Mean ₹/Cr | Total ₹/yr |
|---|---|---|---|
| 3A (0.5% OTM) | ~8 | +₹55K | ₹4.4 Cr |
| 3B (0.7% OTM) | ~15 | +₹43K | ₹6.5 Cr |
| 3C (1.0% OTM) | ~21 | +₹40K | ₹8.4 Cr |
| 2A (1.25% OTM) | ~28 | +₹30K | ₹8.4 Cr |
| 2B (1.5% OTM) | ~26 | +₹25K | ₹6.5 Cr |
| **Tier 2+3 sleeves** | | | **~₹34 Cr/yr** |

Plus existing Tier 1 main book on remaining days.

---

#### 9W.11 — Open research items

1. **VIX-based dynamic premium floor** — current ₹30K/Cr floor at 0.7% OTM doesn't account for VIX regime. On VIX 22 days the floor might need to be ₹40K. Future analysis.
2. **Per-day-of-week analysis** — Tuesday NIFTY vs Wednesday SENSEX patterns might differ. Future.
3. **Wall-defense modeling** — when CE/PE OI cluster within 0.5% of spot, the defense should explicitly count as a Layer 1 enabler.
4. **Multi-leg laddering** within Tier 3 — today's data shows you ladder 3-4 strikes per tier. Backtest whether laddered entries have different EV than single-strike at center.

---

*This document is the canonical reference. When Rohan asks "give me expiry levels [for date X]", respond using the template in Section 7 above, with conditions read from live data (or last-available proxy with explicit warning).*
