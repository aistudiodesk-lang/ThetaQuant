# EXPIRY DAY PLAYBOOK
## When to sell short strangles closer than 2.5% OTM

*Print pages 1-3. Pages 4+ are backtest reference only.*

---

# PAGE 1 — MORNING DECISION (do this every expiry day at 10:30 IST)

## Step 1: Look at the morning before doing anything

Fill in:

```
Time check:           ___ : ___ AM
SENSEX spot:          ____________
SENSEX 9:15 high:     ____________
SENSEX 9:15 low:      ____________
NIFTY spot:           ____________
NIFTY 9:15 high:      ____________
NIFTY 9:15 low:       ____________
India VIX:            ______
```

## Step 2: STOP if ANY of these is true

If yes to any → **do only deep OTM (≥2.5%) today, nothing closer.**

| ☐ | Test | If YES → STOP |
|---|---|---|
| ☐ | Is there RBI, Fed, Budget, election or war news today/tomorrow? | YES → STOP |
| ☐ | Did SENSEX/NIFTY gap more than 0.7% (or 500/200 points) at open? | YES → STOP |
| ☐ | Has spot moved more than 0.7% from open by 10:30? | YES → STOP |
| ☐ | Is SENSEX 9:15-10:30 high-low range > 750 points? | YES → STOP |
| ☐ | Is NIFTY 9:15-10:30 high-low range > 240 points? | YES → STOP |
| ☐ | Is VIX above 19, OR has it jumped up by more than 1.5 today? | YES → STOP |
| ☐ | Did Brent crude move more than 3% in 24 hours? | YES → STOP |

**ALL clear?** → Go to Page 2.

---

# PAGE 2 — WHICH TRADE TO DO TODAY

Try the trades in order. **Stop at the FIRST one whose conditions are met.** Skip the rest.

## ⭐ THE BEST TRADE: SENSEX 1.0% OTM at 10:00 IST

### When to do this
- All Page 1 checks passed
- SENSEX 9:15-10:30 range ≤ 525 points (= 0.7% of 75,000)
- Combined premium for both legs ≥ ₹25/share (= ₹20K per Cr deployed)
- Time is 10:00 ± 15 minutes

### Example
SENSEX spot = 75,000

```
   SELL 74,250 PE  (1.0% below)  @ ₹14
   SELL 75,750 CE  (1.0% above)  @ ₹12
   ───────────────────────────────────────
   Combined premium: ₹26/share  →  ₹20.8K per Cr ✓
```

### How to manage
- **HOLD until 15:25** (don't touch unless Yellow/Red exit fires — see Page 3)
- At close, both legs settle worthless → keep all the premium

### Backtest record (14 sample days)
- Won 14 out of 14 (100%)
- Best day: ₹47K/Cr
- Worst day: still positive +₹20K/Cr

### Size
Up to **15% of your book** on this trade.

---

## TRADE B: NIFTY 0.7% OTM at 10:30 IST

### When
- All Page 1 checks passed
- NIFTY 9:15-10:30 range ≤ 96 points (= 0.4% of 24,000)
- |Pre-move| ≤ 96 points
- Combined premium ≥ ₹10/share (= ₹30K per Cr)

### Example
NIFTY spot = 24,000

```
   SELL 23,850 PE  (0.7% below)  @ ₹6
   SELL 24,150 CE  (0.7% above)  @ ₹5
   ───────────────────────────────────────
   Combined premium: ₹11/share  →  ₹35.5K per Cr ✓
```

### How to manage
- **HOLD until 15:25**

### Backtest record (9 sample days)
- Won 9 out of 9 (100%)
- Best day: ₹70K/Cr
- Worst day: still positive +₹32K/Cr

### Size
Up to **15% of your book**.

---

## TRADE C: SENSEX 0.7% OTM at 11:30 IST

### When
- All Page 1 checks passed
- SENSEX 9:15-11:30 range ≤ 600 points (= 0.8% of 75,000)
- Combined premium ≥ ₹38/share (= ₹30K per Cr)
- Time is 11:30 ± 15 minutes

### Example
SENSEX spot = 75,000

```
   SELL 74,475 PE  (0.7% below)  @ ₹22
   SELL 75,525 CE  (0.7% above)  @ ₹18
   ───────────────────────────────────────
   Combined premium: ₹40/share  →  ₹32K per Cr ✓
```

### How to manage
- **HOLD until 15:25**

### Backtest record (6 sample days)
- Won 6 out of 6 (100%)
- Worst day: +₹4K/Cr (still positive)

---

## TRADE D: SENSEX 0.5% OTM "Volatile-Recovery" at 12:00 IST

### When
- This is your **fallback if morning was choppy**
- SENSEX gapped down or had range 0.5%-1.0% by 10:30
- BUT by 12:00, market has calmed: 9:15-12:00 range ≤ 525 points
- Combined premium still ≥ ₹50/share (= ₹40K/Cr)

### Example
SENSEX morning was choppy, settled to 74,800 by 12:00

```
   SELL 74,425 PE  (0.5% below)  @ ₹30
   SELL 75,175 CE  (0.5% above)  @ ₹22
   ───────────────────────────────────────
   Combined premium: ₹52/share  →  ₹41.6K per Cr ✓
```

### How to manage
- **HARD EXIT AT 14:00 IST** regardless of P&L
- Set a phone alarm for 14:00. When it rings, square off both legs at market.
- Don't wait for "just 30 more minutes." The last 90 minutes of gamma is dangerous.

### Size
Maximum **5% of book** (this is experimental / aggressive).

### Backtest record (5 sample days)
- Won 4 out of 5 (80%)
- Worst day: −₹2.5K/Cr (essentially safe)

---

## TIER 2 (1.25%-2.0% OTM): Always safe-ish, lower yield

If above 4 trades don't qualify, fall back to these. All have HOLD-to-expiry exits.

| Setup | Enter at | Min premium | Notes |
|---|---|---|---|
| **SENSEX 1.25% OTM** | 10:00 | ₹19/share | Excellent: +₹32K/Cr mean |
| **SENSEX 1.5% OTM** | 10:00 | ₹16/share | Solid: +₹30K/Cr |
| **NIFTY 1.5% OTM** | 11:00 | ₹4/share | +₹17K/Cr |
| **NIFTY 2.0% OTM** | 09:45 | ₹2.5/share | Always-safe, +₹16K/Cr |

---

# PAGE 3 — INTRADAY EXITS (Yellow & Red)

**Every active trade from Page 2 needs these monitors. Set phone alerts at entry.**

## YELLOW EXIT — close losing leg only

This fires when the trade has gone against you AND a "real move" is confirmed.

### Worked example — Trade A (SENSEX 1.0% OTM):
You sold at 10:00 with SENSEX at 75,000:
- SELL 74,250 PE @ ₹14
- SELL 75,750 CE @ ₹12

**Set these two alerts on your phone/TradingView:**

```
Alert 1 (PE side):
   SENSEX touches 74,625
   (this is halfway between entry 75,000 and your 74,250 PE strike)

Alert 2 (CE side):
   SENSEX touches 9:15-10:30 high + 75 points
   Example: if morning high was 75,150, alert at 75,225
```

### When alert 1 (PE side) fires, ALSO check:
- Has SENSEX dropped 300+ points in the last 30 minutes?

If YES to both → **Yellow PE has fired.**
- **Action: BUY BACK the 74,250 PE only** (close that leg at market)
- **Keep the 75,750 CE running** (it's now even safer)
- Do NOT roll, do NOT touch the CE
- Walk away from the screen

If only alert fired but no 300-pt drop → just keep watching, don't act.

### When alert 2 (CE side) fires, ALSO check:
- Has SENSEX risen 300+ points in the last 30 minutes?

If YES → **Yellow CE has fired.**
- **Action: BUY BACK the 75,750 CE only**
- Keep the 74,250 PE running

### For NIFTY trades the levels are:
- Use 96 points instead of 300 (NIFTY is smaller)

---

## RED EXIT — close BOTH legs immediately

This is the emergency stop. No thinking. Just close.

### Worked example — Trade A continuation:
You sold at 10:00 with SENSEX 75,000, strikes 74,250 PE / 75,750 CE.

**Red alerts:**

```
Alert R1 (PE side red):
   SENSEX touches 74,362
   (this is 85% of the way from 75,000 to 74,250)

Alert R2 (CE side red):
   SENSEX touches 75,637
   (85% of the way from 75,000 to 75,750)

Alert R3 (catastrophic):
   SENSEX touches 74,250 or 75,750 (your strikes)
```

### When any of these fires:
**Buy back BOTH legs at market immediately.**

No analysis. No "let me see if it bounces." Close both.

Then: **no re-entry on that side today.** You can still do other tiers if conditions allow, but not this strangle.

---

## PROFIT BOOK — when does theta give you a quick win?

If by 13:00 IST, your combined premium has dropped to about **₹10/share total** (or whatever 30% of your entry was):
- Consider closing both legs and locking in the profit
- Especially useful if Friday afternoon news is approaching

### Worked example:
You sold combined at ₹26/share at 10:00. By 13:00, market is calm and combined LTP is ₹8/share.
- **You can buy back both for ₹8 = profit of ₹18/share = locked in ~70% of max possible**
- Or hold the last 2.5 hours for the remaining ₹8 (which you might NOT get if pin breaks)
- **Recommended:** book profit at PT level for any trade where you went in close to 0.5% OTM

For deeper trades (≥1.0% OTM): HOLD to expiry is usually better than PT.

---

# PAGE 4 — SIZING & ANNUAL EXPECTATION

## How much to put per trade

| Trade type | Max % of total book |
|---|---|
| Star Trade (SENSEX 1.0%) | up to **15%** |
| NIFTY 0.7% | up to **15%** |
| SENSEX 0.7% | up to **15%** |
| NIFTY/SENSEX 0.5% (tight conditions) | up to **15%** |
| Volatile-Recovery 0.5% @ 12:00 | up to **5%** |
| Tier 2 (1.25-2.0%) | up to **30%** |
| Tier 1 deep OTM (≥2.5%) | rest of book |

## Universal sizing rule
For any leg you're about to sell, ask yourself:
**"If the worst day backtest happens today, how much will I lose?"**

If it's more than ₹1 lakh absolute → **size down**.

Quick rule of thumb at ₹100Cr book:
- Tier 3 worst day was ≈ ₹110K/Cr → can deploy up to ₹0.9 Cr per leg safely.
- Tier 2 worst day was ≈ ₹20K/Cr → can deploy up to ₹5 Cr per leg.

## Expected yearly P&L from these sleeves alone

| Tier | Trades/year (both indices) | Avg per trade | Annual at ₹100Cr |
|---|---|---|---|
| 3A (0.5%) | ~8 | +₹55K/Cr | ₹4.4 Cr |
| 3B (0.7%) | ~15 | +₹43K/Cr | ₹6.5 Cr |
| 3C (1.0% star) | ~21 | +₹40K/Cr | ₹8.4 Cr |
| 2A (1.25%) | ~28 | +₹30K/Cr | ₹8.4 Cr |
| 2B (1.5%) | ~26 | +₹25K/Cr | ₹6.5 Cr |
| **TOTAL TIER 2+3** | | | **~₹34 Cr/yr** |

*Plus your existing Tier 1 deep OTM book on the remaining days.*

---

# PAGES 5+ — BACKTEST REFERENCE (do not need to print)

## Why pre-entry range matters more than premium

The 10 worst NIFTY days in 119-day sample all had pre-entry range > 0.45%. Every single one. The 0.4% filter catches all 10.

Worst NIFTY days were:
- 2025-04-17: pre-range 0.51%, day-range exploded to 2.32%, -₹747K/Cr
- 2025-05-15: pre-range 0.61%, day-range 2.21%, -₹452K/Cr
- 2026-01-20: pre-range 0.54%, day-range 1.49%, -₹340K/Cr
- 2025-09-02: pre-range 0.46%, day-range 0.90%, -₹313K/Cr

Pre-range >0.4% = morning chop = afternoon explosion = filtered out.

## Backtest sample sizes

| Instrument | E-0 days in data | Time range |
|---|---|---|
| NIFTY | 61 days | Apr 2025 - Jun 2026 |
| SENSEX | 58 days | Apr 2025 - Jun 2026 |

## Why SENSEX needs different filters than NIFTY

| Metric | NIFTY | SENSEX |
|---|---|---|
| Median morning range (9:15-10:30) | 0.51% | 0.85% |
| 75th %ile | 0.62% | 1.05% |
| Why | Better liquidity, more institutional | More retail, wider spreads |

So NIFTY filter is range ≤ 0.4% (catches normal NIFTY days).
SENSEX filter is range ≤ 0.7-0.8% (catches normal SENSEX days).

## Why HOLD beats Profit-Take on filtered trades

When the entry filter is tight enough, 100% win rate means PT gives away upside.

Example: SENSEX 1.0% filtered:
- HOLD: ₹47K/Cr mean, 100% win
- PT_70 (lock at 30% of entry left): ₹36K/Cr mean
- Premium often bounces and never reaches PT level

PT only helps when filter is loose (more uncertain outcomes).

## Why YELLOW beats Stop-Loss on premium

A 50% jump in premium might happen 2-3 times a day on close OTM strikes (vega chop). If you exit on that, you lose every time you'd have been fine.

The Yellow signal (spot moved 50% of buffer AND 30-min directional move) ensures you only exit on REAL threats. It has 0% false negatives — when Yellow doesn't fire, the strike never went ITM in our 119-day sample.

## Critical assumptions
1. Brokers fill within ₹1-2 of LTP for Yellow exits (reasonable for index options)
2. ₹10/lot friction (Axis/Monarch real cost, per analysis 007)
3. No slippage on entry (you use limit orders or wait for fills)
4. VIX data is from NSE real-time feed
5. Calendar days exclude declared market holidays

---

**Last updated:** 2026-06-04
**Source:** STRATEGY_LIVE §9W (committed)
**Backtests:** analyses/018-024
