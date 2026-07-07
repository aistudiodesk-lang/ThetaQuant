# ThetaDesk — Consolidated Strategy-Logic Spec

_Implementation-ready spec mined from three sources, organised per strategy. This is the single
reference that drives the ThetaDesk build. Every rule is cited to its source file:line._

**Sources mined**
- **A — Theta Gainers Algo** (FastAPI+React, EXPIRY trading): `strategies/Theta Gainers Algo Development/01 - Main Code (for Developer)/backend/app/...`
- **B — Covered Call Analyzer** (Streamlit+SQLite): `Covered Call Analyzer/src/...`, `Covered Call Analyzer/docs/STRATEGIES.md`, `.env`
- **C — This project's ported libs + locked docs**: `lib/playbook.py`, `lib/covered_call.py`, `lib/dummy.py`, `lib/holdings.py`, `lib/journal.py`, `lib/expiry_calendar.py`, `STRATEGY_LIVE.md`, `THETA_GAINERS_BRAIN_DUMP.md`, `SESSION_STATE.md`.

**Strategy coverage at a glance**

| # | Strategy | Status in repo |
|---|---|---|
| 1 | Expiry deep-OTM (the algo) | Tier-1 logic DONE in `lib/playbook.py`; algo-grade SL/trailing/notify spec mined from source A (not yet ported) |
| 2 | Index monthly OTM | **TODO — none found.** Repo is weekly-only. |
| 3 | Index long (6–12mo strangles / single leg) | **TODO — none found.** Only the E-0 intraday "lottery harvest" long sleeve exists. |
| 4 | Covered Calls — Against Investment (S1) | DONE (eligibility+buckets) in `lib/covered_call.py` + `lib/holdings.py`; monitoring TODO |
| 5 | Regular OTM buy-write (S2A) | PARTIAL — payoff+buckets in `lib/covered_call.py`; screener/strike-picker in source B only |
| 6 | ITM theta (S2B) | PARTIAL — bucket+payoff in `lib/covered_call.py`; no screener |
| 7 | Commodity | **TODO — none found.** Brent is a regime *filter*, not a strategy. |

---

## Conventions used throughout

- **E-0** = expiry day (NIFTY Tue, SENSEX Thu). **E-1** = trading day before. Use `lib/expiry_calendar.py` helpers — never weekday inference.
- **₹/Cr** = premium captured per ₹1 Cr of margin deployed. NIFTY: ₹1/share ≈ ₹3,225/Cr; ₹10/share ≈ ₹32,250/Cr (`THETA_GAINERS_BRAIN_DUMP.md:29`).
- **lots/Cr**: NIFTY 43, SENSEX 40, BANKNIFTY 67 (`lib/playbook.py:23-25`). Lot sizes NIFTY 75, SENSEX 20, BANKNIFTY 15 (same lines; note brain-dump flags NIFTY may be 65 — verify per trade, `THETA_GAINERS_BRAIN_DUMP.md:22,335`).
- **Buffer** = distance in points from entry spot to a short strike (`lib/playbook.py:205-206`).
- "Source A" / "Source B" / "Source C" tags map to the three codebases above.

---

# 1. EXPIRY DEEP-OTM (the workhorse algo)

Naked far-OTM short strangles on weekly expiry, theta seller, hold-to-expiry. Locked rulebook is
`STRATEGY_LIVE.md` §1, §2, §9O, §9S–§9W; ported to `lib/playbook.py`. Source A is the algo-grade
execution engine whose SL/trailing/notification layer is **not yet** in this repo.

### 1.1 Strike suggestions / analysis

**Tier 1 (deep, ≥2.0% OTM) — locked, ported to `lib/playbook.py`:**

- **Regime classifier** `classify_regime(snapshot)` → `calm_green | normal | moderate | high_risk` (`lib/playbook.py:30-49`):
  - `high_risk` if max_gap > 0.7% OR max_range > 1.0% OR VIX > 18
  - `moderate` if max_gap > 0.4% OR max_range > 0.7% OR VIX > 15
  - `calm_green` if max_gap ≤ 0.3% AND max_range ≤ 0.5% AND VIX ≤ 13
  - else `normal`
- **Tier-1 distance lookup** `TIER1_DISTANCE` (`lib/playbook.py:55-60`): 2.0% for calm/normal/moderate; **2.25%** on `high_risk`. Both instruments. **Floor: never closer than 2.0% on Tier 1** (100% win across 119 E-0 days, analysis 025).
- **Entry-time lookup** `TIER1_ENTRY_TIME` (`lib/playbook.py:85-90`): NIFTY calm_green → **10:00**; everything else → **09:25–09:35**.
- **Spike-limit overlay** `SPIKE_LIMIT_RULE` (`lib/playbook.py:91-94`): fill ~65% at the window; place standing SELL LIMITs on the SAME strikes at **1.3× your fill** for the other ~35%; unfilled → market at 11:30. (Spikes fill ~14% of days at ~1.7× capture, analysis 027.)
- **Strike rounding** `nearest_strike(spot, otm_pct, side, grid)` (`lib/playbook.py:180-186`): PE = round(spot·(1−otm/100)/grid)·grid; CE = round(spot·(1+otm/100)/grid)·grid. Grids: NIFTY 50, SENSEX 100, BANKNIFTY 100.

**Tier 2 / Tier 3 framework (near & mid OTM, §9W) — locked, ported to `TIER_SETUPS` (`lib/playbook.py:122-157`):**

- **Hard exclusions (Layer 1)** `hard_exclusions(snapshot)` (`lib/playbook.py:102-116`): VIX > 19, OR any instrument |gap| > 0.7%, OR pre-range > 1.0% → **Tier 1 only today**. (`STRATEGY_LIVE.md:722-736` adds: major news 24hr, |pre-move 9:15→10:30| > 0.7%, Brent 24hr > ±3%, yesterday day-range > 1.5%, VIX rising > 1.5pt — these extra flags are in the doc but NOT in code yet → TODO.)
- **Tier qualification** `qualifying_tiers(snapshot)` (`lib/playbook.py:160-176`): each setup qualifies iff instrument pre-range ≤ its `range_max`.
- **The ★ STAR trade**: SENSEX 1.0% OTM @ 10:00, pre-range ≤ 0.7%, premium ≥ ₹20K/Cr, HOLD → +₹47K/Cr mean, 100% win (`lib/playbook.py:124-126`, `STRATEGY_LIVE.md:760`).
- Full per-instrument Tier 2/3 tables (OTM × best-entry × pre-range cap × premium floor × backtest win/worst): `STRATEGY_LIVE.md:744-781`; encoded as `TIER_SETUPS` rows with `otm_pct, entry_time, range_max, premium_floor_per_cr, win_pct, worst_pcr` (`lib/playbook.py:122-157`).

**Source-A deep-OTM analytics (richer; NOT in this repo — port target):** `backend/app/analytics/deep_otm.py`
- **Tier by cushion ratio**: ALMOST_SURE ≥3.0 (95%), VERY_DEEP ≥2.0 (90%), BALANCED ≥1.5 (80%), AGGRESSIVE ≥1.0 (70%) (`deep_otm.py:23-35`).
- **Expected move**: `daily = spot·(VIX/100)/sqrt(252)`; `EM = daily·sqrt(max(dte,1))·mult`, mult = 2.0 monthly / 1.5 weekly (`deep_otm.py:91-100`). `cushion_ratio = distance_pts / EM` (`deep_otm.py:103-104`).
- **OI walls**: top-3 strikes per side with OI ≥ 1,000,000 (`MIN_OI_FOR_WALL`, `deep_otm.py:115-118`).
- **Wall-confirmation score** (recommend if ≥ `MIN_RECOMMEND_SCORE`=4): +3 strike is a top-3 wall, +2 beyond a wall, +2 OI_change% > 50 (fresh writing), +1 beyond technical S/R, +1 distance ≥ 2× EM (`deep_otm.py:121-152`).
- **PCR / max-pain bias**: PCR > 1.3 → bullish (sell PE deeper); PCR < 0.8 → bearish (sell CE deeper); else neutral (`deep_otm.py:240-255`, mirrored `filters.py:251-260`).
- **VIX regime**: calm ≤ 13, panic ≥ 25 (`deep_otm.py:37-38`, `filters.py:263-272`).
- **Premium-to-margin gate**: `margin ≈ strike·lot·0.11`; require `premium/margin ≥ 1%` (`deep_otm.py:155-159`).

### 1.2 Targets (sizing + return)

**Capital pattern** (`STRATEGY_LIVE.md:7-21`): E-1 advance 5–7%, E-0 T1 ~85%, T2 ~8%, T3 ~2%.

**Sizing caps** `TIER_SIZING` (`lib/playbook.py:295-299`) + §9W.7 (`STRATEGY_LIVE.md:841-851`):
- Tier 1: up to 100% of book. Tier 2: ≤ 30%. Tier 3: ≤ 15%. Volatile-recovery 0.5%@12:00: ≤ 5%.
- **Universal worst-case rule**: size any Tier 2/3 leg so `worst_pcr × deployed_Cr ≤ ₹1L absolute`. (e.g. Tier 3 worst −₹109K/Cr → max ~₹91L deployed.)

**Return targets / premium floors:**
- `TIER_PREMIUM_FLOORS` (`lib/playbook.py:287-291`): T1 floor ₹4K/Cr (ideal ₹5K); T2 floor ₹12.5K/Cr (ideal ₹20K); T3 floor ₹20K/Cr (ideal ₹35K).
- Per-setup floors live in `TIER_SETUPS[*].premium_floor_per_cr` (`lib/playbook.py:122-157`).
- Expected premium per regime×distance `TIER1_EXPECTED_PREMIUM` (`lib/playbook.py:63-68`).
- **E-1 advance floor** (`THETA_GAINERS_BRAIN_DUMP.md:48`): combined ≥ ₹7,500/Cr (ideal ₹10K) at ≥3.5% OTM after 14:45, else SKIP.
- **E-0 Bucket A floors** (`THETA_GAINERS_BRAIN_DUMP.md:68-71`): ≥₹4K/Cr minimum, ₹5K standard, ₹6K full-size trigger.
- Annual yield expectation, Tier 2+3 sleeves @ ₹100Cr: ~₹34 Cr/yr (`STRATEGY_LIVE.md:962-973`).
- Source-A risk caps (port target): max 10 lots/strategy, 5 active/user, ₹50K daily-loss/user, ₹500K global (`config.py:44-55`).

### 1.3 Entry levels / when to sell

- **E-1 advance**: 14:45 IST or later ONLY (news-risk window before), ≥3.5% OTM, bias-adjusted by max-pain/gap/PCR (`STRATEGY_LIVE.md` §9O, `THETA_GAINERS_BRAIN_DUMP.md:43-58`).
- **E-0 Bucket A**: 09:17–09:22 optimal (analysis 009 — 100% worthless ≥2.5% OTM); Tier-1 timing per `tier1_entry_time()` (`lib/playbook.py:97-98`).
- **Tier 2/3 decision algorithm (Layer 7)**: 10-step morning flow — snapshot 09:30 → pre-context 10:30 → hard-exclusion gate → STAR first → Tier-3 secondary → Tier-3 deepest → volatile-recovery → Tier 2 → Tier 1 base → monitoring (`STRATEGY_LIVE.md:854-895`).
- **Entry trigger modes (Source A + ported `lib/dummy.py`):**
  - **COMBINED**: fire when `(ce + pe) ≥ combined_threshold` (`dummy.py:184-187`; A: `schemas.py:46-48`).
  - **SEPARATE**: fire when `ce ≥ ce_threshold AND pe ≥ pe_threshold` (`dummy.py:177-183`; A: `schemas.py:49-51`).
  - Threshold may be given as premium (₹) OR as `yield_per_cr`, auto-converted via `premium_for_yield()` (`dummy.py:103-110,185-186`).

### 1.4 Monitoring of a live/taken trade

**Yellow / Red bands — locked (§9W.5), ported to `compute_triggers()` (`lib/playbook.py:196-240`):**
- **Yellow PE**: spot ≤ `entry_spot − 0.5·pe_buffer` AND 30-min net move ≤ −0.4% (`big_move_pts = 0.4%·spot`). **Yellow CE**: spot ≥ pre-entry-high break (`pre_entry_high·1.001`) or `entry_spot + 0.5·ce_buffer` AND +0.4% 30-min move (`lib/playbook.py:209-222`, `STRATEGY_LIVE.md:789-807`).
  - **Action: close ONLY the losing leg at market. Keep the other.** Never leg out otherwise (`STRATEGY_LIVE.md:806`).
- **Red**: spot reaches `entry_spot ± 0.85·buffer` OR crosses INTO the strike intraday → **close BOTH legs, no re-entry that side today** (`lib/playbook.py:223-227`, `STRATEGY_LIVE.md:809-812`).
- **Profit-take**: `profit_take_combined = 0.30 × combined_entry` (close when premium decays to 30% = 70% captured) (`lib/playbook.py:236-238`). §9W.6 note: prefer **PT_60 over PT_70** on filtered SENSEX 0.7% (`STRATEGY_LIVE.md:816-837`).
- **Time stops**: default HOLD to 15:25; volatile-recovery 0.5%@12:00 → hard **T_1400** close (`STRATEGY_LIVE.md:830`).
- **Live-trigger surface**: `/api/monitor/status` renders Y/O/R per open journal trade, 20s poll (`SESSION_STATE.md:9`). Open trades come from `lib/journal.py:open_trades()`.

**Dummy/paper execution loop (ported, `lib/dummy.py`):** lifecycle ARMED → ENTERED → TP_HIT/SL_HIT/CLOSED/CANCELLED. `check(ltp_lookup)` advances all strategies (`dummy.py:210-228`). Exit `_exit_hit` (`dummy.py:190-207`): TP when combined ≤ value (or pnl ≥ target); SL when combined ≥ value (or pnl ≤ −|val|). Short P&L = `(entry − live)·qty`.

**Source-A risk runtime (port target — algo-grade, `backend/app/risk/runtime.py`):** 5-sec loop (`:191`) checking, in order: peak-PnL track → hard SL (`pnl ≤ −effective_sl`) → target (`pnl ≥ target`) → time-exit (default 15:15 IST) → MTM drawdown kill (dd% of peak ≥ 40%) → **trailing SL** (when `pnl ≥ trigger`, raise SL to `pnl − step`, upward-only) → **lock-in** (when `pnl ≥ lockin_amount`, move SL to breakeven) → dead-man switch (heartbeat > 120s) → position reconciliation (every 30s; mismatch → exit) → emit P&L tick. State machine: DRAFT→MONITORING→ENTERING→LIVE→EXITING→CLOSED (+EMERGENCY_HALT) (`state_machine.py:7-15`). Pre-trade RMS (`pretrade.py:29-107`): lot cap, active-strategy cap, daily-loss cap, cooling-off, OTR, fat-finger (`total_premium > ₹500·max_qty`), freeze-qty, bid-ask spread > 5% hard-reject, OI < 10K warn, margin check.

### 1.5 Notifications

- Bot scheduled sends (IST weekdays): 08:30 pre-market, 09:20 regime, 09:30+11:00 strike recs (expiry-day index only), 12:00–15:00 hourly premium, 15:30 day-end; position monitoring every 60s in market hours (`SESSION_STATE.md:44-48`).
- Yellow fire → alert "close losing leg"; Red fire → alert "close both" (driven by `compute_triggers`).
- **Source-A event→severity→channel map (port target):** event severities (`notify/service.py:34-51`): SL_HIT/ORDER_REJECTED=WARN; KILL_SWITCH/MTM_DRAWDOWN/BROKER_DOWN/PARTIAL_FILL=ERROR; DEAD_MAN_SWITCH/CIRCUIT_BREAKER/POSITION_MISMATCH/DAILY_LOSS_CAP=CRITICAL; the rest INFO. Channel routing (`service.py:27-32`): CRITICAL→whatsapp+telegram+email+sms+voice; ERROR→…+sms; WARN→whatsapp+telegram+email; INFO→whatsapp+telegram. Max 3 retries (`channels.py:138`).

---

# 2. INDEX MONTHLY OTM

**Strike suggestions / Targets / Entry / Monitoring / Notifications — TODO, NONE FOUND.**

The entire repo is weekly-expiry (NIFTY Tue, SENSEX Thu). The only monthly reference is the calendar
notation that the last Tuesday of a month is also BANKNIFTY monthly expiry (`lib/playbook.py:262-267`)
— a calendar fact, not a strategy. No distance %, entry, sizing, or target rules exist for monthly OTM.

**Build note:** Source-A `expected_move()` already takes an `is_monthly` flag (2.0× vs 1.5× safety
multiplier, `deep_otm.py:91-100`) — the natural hook when this strategy is specced. Decide
distance/entry/floors before building; nothing to port.

---

# 3. INDEX LONG (6–12 month strangles / single leg)

**Strike suggestions / Targets / Entry / Monitoring / Notifications — TODO, NONE FOUND.**

The framework is short-premium only; "NO long premium" is an absolute prohibition except one sleeve
(`THETA_GAINERS_BRAIN_DUMP.md:143`). The only long position anywhere is the **E-0 lottery harvest**:
buy 4–5% OTM far-out options intraday for rare 5×–10× spikes, ≤₹5 Cr notional across 5–8 strikes,
E-0 only (`THETA_GAINERS_BRAIN_DUMP.md:91-96`, analysis 014). That is intraday, not 6–12 month.

No 6–12 month strangle/single-leg sizing, strike, delta, or target rules exist. Full greenfield.

---

# 4. COVERED CALLS — AGAINST INVESTMENT (S1)

Sell deep/mid-OTM calls (or cash-secured puts) against existing equity + futures holdings; must NOT go
ITM. Ported math: `lib/covered_call.py` (`against_investment`). Holdings/coverage: `lib/holdings.py`.
Canonical rules: Source-B `Covered Call Analyzer/docs/STRATEGIES.md` + `src/scoring/*`.

### 4.1 Strike suggestions / analysis

**Buckets** `CONFIG["against_investment"]` (`lib/covered_call.py:17-22`), mirroring Source-B `.env`:

| Bucket | Min OTM | Delta cap | Monthly-yield floor | Score cap (B) |
|---|---|---|---|---|
| DEEP | 10.0% | ≤ 0.10 | 1.2%/mo | 20 |
| MID | 6.0% | ≤ 0.25 | 3.5%/mo | 40 |

A strike enters a bucket only if **all three** (OTM, delta, yield) qualify; else SKIP (Source-B
`strikes.py:119-131`, `docs/STRATEGIES.md:22-29`). Allocation: 90% book DEEP / 10% MID (`STRATEGIES.md:15-16`).
Source-B `.env` knobs: `DEEP_OTM_PCT=0.10, DEEP_YIELD_FLOOR_PCT=1.2, DEEP_DELTA_MAX=0.10, MID_OTM_PCT=0.06, MID_YIELD_FLOOR_PCT=3.5, MID_DELTA_MAX=0.25, IV_PCT_FLOOR=40` (`config.py:40-48`).

**Eligibility verdict** `cc_eligibility(snap, leg)` → GREEN/YELLOW/RED/REJECT (`lib/covered_call.py:49-92`):
- **CE hard vetoes (→ REJECT)**: confirmed/fresh breakout; weekly RSI > 70 OR daily RSI > 75; MACD bullish on both D+W; within 3% of 52w high. Source-B adds recent breakaway gap-up (`eligibility.py:25-41`, `STRATEGIES.md:39-43`). _Note: ported `lib/covered_call.py` omits the gap-up veto → port gap → TODO._
- **PE hard vetoes**: weekly RSI < 30; MACD bearish on both D+W. Source-B also vetoes bearish trend (`eligibility.py:49-54`).
- **Soft verdict** — ported version uses a simple risk score (`lib/covered_call.py:78-91`): `delta·350` capped 35 + trend points (bullish 25 / weak_bullish 14 / sideways 6 / weak_bearish 2) + 4 if IV < 40; verdict GREEN ≤20, YELLOW ≤40, else RED.
- **Source-B verdict (richer, bias-delta based)**: CE GREEN if `bear≥40 AND bull≤25`, YELLOW if `bear≥25 AND bull≤35`, else RED (PE inverted). Bias scores built from RSI(d/w), MACD(d/w), trend, breakout, gap, divergence, %-off-high (`eligibility.py:44-61`, `snapshot.py:60-99`). _Porting the full bias model is a refinement → PARTIAL._

**Strike ranking (composite risk score, lower=safer) — Source B `scoring/strikes.py:88-115`:** delta `min(|δ|·350,35)` + ATR-σ distance (<1.5σ=20,<2.0=12,<2.5=6,<3.0=2) + technical bias (≤25) + OI-wall-absent (+8) + IV<25 (+4), capped 100. `pick_best_per_bucket()` returns best DEEP + MID plus fallbacks closest to target OTM (`strikes.py:136-161`). _Not yet ported → PARTIAL._

**Yield + margin math (ported):** `monthly_yield_pct(premium, margin, dte) = (premium·100/margin)·(30/dte)` (`lib/covered_call.py:36-40`); `distance_pct(strike, spot)` (`:43-46`). Margin fallback = `strike·lot·0.18` (Source-B `strikes.py:74`, `margins.py:14`).

### 4.2 Targets (sizing + return)

- **Return floors**: DEEP ≥ 1.2%/mo on margin; MID ≥ 3.5%/mo (above table).
- **Coverage / item-wise sizing** — `lib/holdings.py` reads the master workbook
  `"Full strategy Reporting - including strategy notes.xlsx"`:
  - `load_holdings()` (sheet **"Selling Plan"**, `holdings.py:38-94`): per stock — lot size, total equity, futures, **Total F+E** held, **Actual Qty sold** (coverage), **Remaining Qty** (uncovered), current rate, 52wk high, `% Distance from Current Price`, **Strike to Sell**. Derived: `coverage_pct = sold/total·100`, `uncovered_qty = total − sold`, `pct_off_high = (cur−h52)/h52·100`.
  - `load_stockwise()` (sheet **"Stockwise"**, header row 2, `holdings.py:97-136`): authoritative per-underlying P&L — CE/PE sold qty+value, Options Net, Future Qty/Value, **Net current p/l**, Margin Used, **% Return**.
  - `load_futures_m2m()` (sheet **"Futures Holding"**, `holdings.py:139-181`): per future — orig buy vs live, daily/monthly M2M, qty, margin.
  - `portfolio_summary()` (`holdings.py:184-193`): n_stocks, total_value, total_qty, ce_sold_qty, uncovered_qty, coverage_pct.
- **Qty sizing rule (Source B)**: `qty_planned = lots_each × lot_size` (`page_s1.py:587`); coverage tracked per-symbol against `Total F+E`.

### 4.3 Entry levels / when to sell

Sell when eligibility passes (no veto) AND a strike qualifies for a bucket AND meets its yield floor.
GREEN = strong bearish bias (ideal); YELLOW = mild (acceptable, watch); RED/REJECT = do not sell this
name this expiry. PE-selling (cash-secured, names happy to own at strike) uses inverted rules
(`STRATEGIES.md:17-18,54`). No specific entry-time-of-day rule (monthly/positional, not intraday).

### 4.4 Monitoring of a live/taken trade

**"Never ITM" tripwires — Source-B `docs/STRATEGIES.md:68-84` (PLANNED v0.3, NOT YET CODED → TODO):**

| Tripwire | Yellow | Red (force decision) |
|---|---|---|
| Spot buffer eaten | > 40% | > 60% |
| Delta drift since entry | > 0.12 | > 0.18 |
| RSI daily | crosses 60 | crosses 70 |
| MACD daily | fresh bullish cross | histogram expanding 3 sessions |
| Trendline | tags overhead trendline | breaks above with volume |
| Breakout | testing N-day high | closes above with volume |
| Gap | gap-up < 1% | gap-up > 1% or unfilled |
| Sector | +2% in 2 sessions | +3% or sector breakout |

**Two yellows on the same position = treat as red.** Red opens a decision card: cost-to-close + three
roll candidates (up / out / up-and-out with their deltas/premiums/net-credit) + "hold & watch" with a
defined invalidation level (`STRATEGIES.md:81-84`). **Square-off / roll: roll up / out / up-and-out on
red; otherwise hold to expiry.** Take-profit levels: none defined in source (CC is hold-to-decay).

### 4.5 Notifications

Source-B notifications are PLANNED v0.3, not implemented: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env
stubs only (`config.py:34-35`, `.env`). Planned fires: two-yellow on a position; any red; **cross-strategy
conflict** ("name in both S1 held + S2 breaking out → your CC may be at risk", `STRATEGIES.md:181-188`).
In this repo, this would flow through `lib/journal.py` open trades + the same Y/R monitor surface as expiry. → TODO.

---

# 5. REGULAR OTM BUY-WRITE (S2A)

Buy futures (or equity) on strong momentum + sell an OTM CE; gain on premium AND the future if it
rises. Ported bucket+payoff: `lib/covered_call.py` (`regular_otm`). Screener/strike-picker: Source B
only (documented as "S2 Momentum Buy-Write", which the analyzer does not split into A/B — the A/B
split is THIS project's taxonomy in `lib/covered_call.py:23-32`).

### 5.1 Strike suggestions / analysis

- **Bucket** `regular_otm.OTM` (`lib/covered_call.py:25`): otm_pct **2.0%**, delta_max **0.45**, yield_floor **2.0%/mo**. Intent: "gain on call premium AND the future if it rises" (`:26`).
- **Momentum screener (Source B `scoring/momentum.py:12-102`)** — score /100: Trend (bullish 25 / weak_bullish 14 / sideways 6 / weak_bearish 2 / bearish 0) + RSI (daily sweet-spot 55–75 = 11, weekly 55–80 = 9, tiers below) + MACD (daily fresh-bull 11 / expanding 8 / fading 3; weekly 9/6/2) + confirmed breakout (15, testing 6) + %-off-52wk-high (>−5% =10, >−10% =7, >−20% =3) + fundamentals (ROCE>25 +4 / >15 +2, +EPS growth +2, rev>5% +2, PEG 0–1.5 +2). Penalties: bearish RSI divergence −10, recent failed breakout −12.
- **Pass filters (all true)** (`momentum.py:93-99`, `STRATEGIES.md:134-140`): trend bullish/weak_bullish; daily RSI ∈ [50,78]; weekly RSI ≥ 50; MACD daily not bearish; no bearish divergence; no recent failed breakout. Scans full NFO universe (~214 names), 10 workers, cached 15 min (`STRATEGIES.md:142-143`).
- **Strike auto-picker (PLANNED, Source B `STRATEGIES.md:145-159`)**: `expected_move = entry·(1 + α·momentum_score/100)` (α≈0.5); choose K s.t. K ≤ expected_move, `(K−F)+premium ≥ 3.5% of margin`, **delta(K) ≥ 0.55**, premium not unfairly cheap (IV vs HV). _Not coded → TODO._
- **Payoff (ported)** `payoff_buy_write(F, K, P, spot, lots, lot)` (`lib/covered_call.py:95-109`): `Net(S)=qty·[(S−F)+P−max(0,S−K)]`; max_profit `(K−F+P)·qty` if S≥K; breakeven `F−P`. `payoff_curve()` gives the chart (`:112-122`). Log-normal P(profit)/P(max-profit) in Source-B `scoring/payoff.py:22-86`.

### 5.2 Targets

≥ **3.5%/month on total margin** (futures span + CE margin); max **3–4 stock basket** (concentration);
all squared off by expiry; max **2-month carry** on futures worst-case (`STRATEGIES.md:110-115`).
Bucket yield floor (ported) 2.0%/mo (`lib/covered_call.py:25`). Item-wise sizing = futures lots per
name within the 3–4 basket; no per-Cr table in source.

### 5.3 Entry levels / when to sell

Enter when a name **passes all momentum filters** (§5.1) and a CE strike meets the auto-picker
constraints. Then buy the future + sell the CE simultaneously (buy-write).

### 5.4 Monitoring

**Momentum-failure tripwires — Source-B `STRATEGIES.md:161-172` (PLANNED v0.3 → TODO):**

| Tripwire | Yellow | Red |
|---|---|---|
| MACD histogram | flattens | turns negative (daily) |
| RSI | breaks below 50 | bearish divergence confirmed |
| Trendline | tests rising support | breaks rising trendline w/ volume |
| Breakout level | revisits | closes back below |
| Gap | gap-down < 1% (filled) | gap-down > 1% / unfilled |
| Sector | flat 5 sessions | breaking down |
| Drawdown on futures | −1.5% | −2.5% |
| Time vs price | DTE<7 & stock ≥2% below K | DTE<5 & stock ≥3% below K |

**Hard rule**: square off by expiry day, except a "carry futures" flag (≤2-month) only if (a) momentum
still bullish AND (b) next-month CE re-sold against the carried future (`STRATEGIES.md:174-178`).
Take-profit: capped at K by design; no separate TP level.

### 5.5 Notifications

Same as S1 (PLANNED): tripwire yellows/reds + cross-strategy conflict alert. → TODO.

---

# 6. ITM THETA (S2B)

Buy futures + sell an **ITM** CE to harvest time value as an income asset. THIS project's variant
(`lib/covered_call.py` `itm_theta`); Source-B folds this into "S2 buy-write" (its S2 is described as
"sell ITM-by-expiry CE", `STRATEGIES.md:88-105`) and does not maintain a separate screener for it.

### 6.1 Strike suggestions / analysis

- **Bucket** `itm_theta.ITM` (`lib/covered_call.py:28-32`): otm_pct **−3.0%** (i.e. 3% in-the-money), **delta_min 0.55**, yield_floor **3.5%/mo**. Intent: "eat the time value as an income asset."
- **Payoff** reuses `payoff_buy_write()` (`lib/covered_call.py:95-109`); `is_itm_target = strike ≤ fut_entry` flags ITM-by-design (`:108`). Same `Net(S)` formula; because K < F, the position is capped immediately and lives on theta + the (F−K) intrinsic cushion.
- Screener: none specific to ITM; reuse S2A momentum pass-filters when also long-momentum. → PARTIAL/TODO.

### 6.2 Targets

≥ 3.5%/month on margin (bucket floor, `lib/covered_call.py:30`); aligns with Source-B S2 target
(`STRATEGIES.md:112`). Basket / carry rules as S2A.

### 6.3 Entry levels / when to sell

Enter when intrinsic + time value gives ≥3.5%/mo on margin and delta ≥ 0.55 (deep enough to be
income-stable). No coded auto-trigger → TODO.

### 6.4 Monitoring

No ITM-specific tripwire table in source. Use S2A momentum-failure tripwires (§5.4) for the downside
(the only real risk is the future falling below breakeven `F−P`). Square off by expiry. → TODO.

### 6.5 Notifications

Same PLANNED S2 set. → TODO.

---

# 7. COMMODITY

**Strike suggestions / Targets / Entry / Monitoring / Notifications — TODO, NONE FOUND.**

No commodity options/futures strategy exists anywhere in the three sources. **Brent crude appears only
as a regime-exclusion FILTER** for the index strategies — skip/Tier-1-only when Brent 24hr move > ±3%
(`STRATEGY_LIVE.md` §9W.2 / §9U.2, `THETA_GAINERS_BRAIN_DUMP.md:155`, `TIER_PLAYBOOK_PRINTABLE.md`). That is
an input, not a tradable strategy. Full greenfield if commodity trading is ever added.

---

# IMPLEMENTATION MAP

DONE = working in a cited lib. PARTIAL = some logic ported, richer version in source not yet brought
over (port target). TODO = not built (greenfield or planned-only in source).

| Strategy | Strike suggestions / analysis | Targets (sizing + return) | Monitoring (Y/O/R, SL/TP) | Notifications |
|---|---|---|---|---|
| **1. Expiry deep-OTM** | **DONE** — `lib/playbook.py` (regime, TIER1_DISTANCE, TIER_SETUPS, nearest_strike, hard_exclusions). PARTIAL: cushion-ratio/OI-wall/PCR analytics live only in Source-A `analytics/deep_otm.py` (port target). | **DONE** — `lib/playbook.py` (TIER_SIZING, TIER_PREMIUM_FLOORS, TIER1_EXPECTED_PREMIUM); floors in `STRATEGY_LIVE.md` §9W. | **DONE** — `lib/playbook.compute_triggers` (Y/R/PT) + `lib/dummy.py` paper loop + `/api/monitor/status`. PARTIAL: trailing-SL/lock-in/dead-man/recon are Source-A `risk/runtime.py` (port target). | **PARTIAL** — bot schedule + Y/R alerts (`SESSION_STATE.md:44-48`). Severity→channel matrix is Source-A `notify/service.py` (port target). |
| **2. Index monthly OTM** | **TODO** (none found) | **TODO** | **TODO** | **TODO** |
| **3. Index long 6–12mo** | **TODO** (none found) | **TODO** | **TODO** | **TODO** |
| **4. CC — Against Investment (S1)** | **DONE** — `lib/covered_call.py` (buckets, cc_eligibility, yield). PARTIAL: full bias-delta verdict + strike ranking + gap-up veto in Source-B `scoring/` (port target). | **DONE** — `lib/holdings.py` (Selling Plan / Stockwise / Futures Holding, coverage, qty). | **TODO** — tripwire table planned in Source-B `docs/STRATEGIES.md:68-84`, not coded; would reuse journal + Y/R surface. | **TODO** — planned only (telegram stubs in Source-B `.env`). |
| **5. Regular OTM buy-write (S2A)** | **PARTIAL** — bucket + `payoff_buy_write` in `lib/covered_call.py`; momentum screener + auto-strike-picker in Source-B `scoring/momentum.py` + `STRATEGIES.md:145-159` (not ported). | **PARTIAL** — bucket yield floor in `lib/covered_call.py`; 3.5%/mo + 3–4 basket + 2-mo carry in `STRATEGIES.md:110-115` (rule, not enforced). | **TODO** — momentum-failure tripwires planned in `STRATEGIES.md:161-172`. | **TODO** — planned only. |
| **6. ITM theta (S2B)** | **PARTIAL** — bucket + payoff in `lib/covered_call.py` (`itm_theta`); no dedicated screener. | **PARTIAL** — bucket floor 3.5%/mo in `lib/covered_call.py`. | **TODO** — reuse S2A tripwires; no ITM-specific table. | **TODO** — planned only. |
| **7. Commodity** | **TODO** (none found) | **TODO** | **TODO** | **TODO** |
