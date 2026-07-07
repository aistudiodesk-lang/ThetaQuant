# Manipulation Harvest Strategy — Algo Specification

**Strategy name:** `manipulation_harvest_v1`
**Target instrument:** SENSEX weekly options (Thursday E-0 only)
**Status:** Specified 2026-05-05, ready for coding/backtest
**Empirical basis:** Analysis 014 — 212 detected spikes across 40 of 53 SENSEX E-0 days (1-yr sample)

---

## 1. Strategy thesis

Indian weekly SENSEX options exhibit **systematic late-day premium manipulation** on deep-OTM strikes. Big institutional desks sweep illiquid books between 14:30-15:25 IST on expiry days, briefly spiking premiums 5-140× before collapse. The strategy harvests this manipulation via three layered plays:

- **Play D (LONG):** Buy small basket of deep-OTM strikes at low premium pre-window → take profit on spike
- **Play A/C (SHORT):** Pre-placed SELL LIMITs at 10-15× LTP catch spike fills passively
- **Margin recycling:** Close 20% of existing deep-OTM shorts at near-zero LTP to free capital

**Expected EV:** +₹15-20K per Thursday at ₹10-15K capital outlay (~₹8-12 LAKH/year over ~50 SENSEX expiries).

---

## 2. Pre-conditions (entry filters)

| Condition | Check | Action if fail |
|---|---|---|
| Today is SENSEX weekly E-0 | `is_e0(today, "SENSEX")` from `lib/expiry_calendar.py` | SKIP — strategy only on SENSEX Thursdays |
| Time is 14:00-14:30 IST | for Phase 1 entry | Wait or skip if past |
| Kite session live | `lib/kite_live.py` smoke test | Login first |
| SENSEX spot available | `get_spot()` returns valid | Abort, retry |

---

## 3. State machine

```
                  ┌──────────────────┐
                  │  PHASE_0 (init)  │
                  │  9:15 - 14:00    │
                  └────────┬─────────┘
                           │ at 14:00 IST
                           ▼
                  ┌──────────────────────┐
                  │ PHASE_1 (PREP)       │
                  │ 14:00 - 14:30        │
                  │ - Close 20% shorts   │
                  │ - Buy Play D basket  │
                  └────────┬─────────────┘
                           │ at 14:30 IST
                           ▼
                  ┌────────────────────────┐
                  │ PHASE_2 (DEPLOY)       │
                  │ 14:30 - 15:00          │
                  │ - Place SELL LIMITs    │
                  │ - Place TAKE-PROFITs   │
                  │   on Play D longs      │
                  └────────┬───────────────┘
                           │ at 15:00 IST
                           ▼
                  ┌────────────────────────┐
                  │ PHASE_3 (CATCH)        │
                  │ 15:00 - 15:25          │
                  │ - Monitor for spikes   │
                  │ - Orders fill passively│
                  │ - Manual override OK   │
                  └────────┬───────────────┘
                           │ at 15:25 IST
                           ▼
                  ┌────────────────────────┐
                  │ PHASE_4 (SETTLE)       │
                  │ All open longs settle  │
                  │ Unfilled limits cancel │
                  │ Booked P&L = day result│
                  └────────────────────────┘
```

---

## 4. Phase-by-phase rules

### PHASE 1: PREP (14:00-14:30 IST)

#### 1A. Margin recycling — close 20% of existing deep-OTM shorts

```
For each existing SHORT position where:
  - Same expiry as today (SENSEX 0DTE)
  - Distance from spot >= 2.5% OTM
  - Current LTP <= 0.10

  Close 20% of position quantity at LTP+₹0.05 limit (best-effort)
  → frees margin for Plays D and A/C
```

**Skip rule:** if no qualifying positions, skip 1A.

#### 1B. Play D — BUY deep-OTM basket

```
Inputs:
  spot = get_spot("SENSEX")
  budget_buy = 12_500   # ₹10-15K (config: 12.5K mid)
  n_strikes = 5
  per_strike_budget = budget_buy / n_strikes  # ₹2,500 each

Strike selection:
  candidates = [
    (CE, +4.5%),  # most-manipulated CE side
    (CE, +5.0%),
    (CE, +5.5%),
    (PE, -4.5%),  # most-manipulated PE side
    (PE, -5.0%),
    (PE, -5.5%),
  ]
  
  # Pick 5 of 6: balance CE/PE based on max-pain
  if max_pain > spot:                # bullish bias → spot will drift up → favor CE side
    select = [CE+4.5, CE+5.0, CE+5.5, PE-5.0, PE-5.5]
  elif max_pain < spot:              # bearish bias → favor PE side
    select = [CE+5.0, CE+5.5, PE-4.5, PE-5.0, PE-5.5]
  else:
    select = [CE+4.5, CE+5.0, CE+5.5, PE-4.5, PE-5.0]   # symmetric default

For each (side, dist) in select:
  strike = round(spot * (1 + dist/100) / 100) * 100   # SENSEX grid 100
  ltp = get_ltp(strike, side)
  if ltp > 0.50: skip strike   # too expensive — not in the manipulation zone
  if ltp < 0.05: skip strike   # already below quote tick
  
  # OI filter — only buy strikes with manipulable OI
  oi = get_oi(strike, side)
  if oi > 20_00_000: skip strike   # 20 lakh — too liquid to manipulate
  
  qty_lots = floor(per_strike_budget / (ltp * SENSEX_LOT_SIZE))   # SENSEX_LOT_SIZE = 20
  qty_lots = min(qty_lots, 100)   # cap per strike
  qty_lots = max(qty_lots, 5)     # minimum
  
  PLACE BUY LIMIT @ ltp+₹0.05 (slightly above ask for fill)
```

#### 1C. Wait / monitor till 14:30

Confirm fills. Re-place at LTP+₹0.10 if any unfilled by 14:25.

---

### PHASE 2: DEPLOY (14:30-15:00 IST)

#### 2A. Place SELL LIMITs (Play A/C — passive spike catchers)

```
sell_strike_zone = 3.0% to 5.5% OTM
multiplier = 12   # default; range 10-15 acceptable

candidates = strikes in [3.0%, 5.5%] OTM (CE and PE), with LTP between [0.05, 0.50]

For each strike:
  ltp = get_ltp(strike, side)
  oi = get_oi(strike, side)
  
  # Filters
  if oi > 30_00_000: skip   # too liquid — won't get manipulated
  if ltp < 0.05: skip       # below quote tick
  if ltp > 0.50: skip       # too rich — not in target zone
  
  # Sizing — be conservative; these are SHORT POSITIONS that could go ITM rarely
  qty_lots = 20             # standard slot per strike
  
  # Optional: if user already has SHORT position on this strike, scale up qty_lots
  # because it's just adding to existing exposure
  
  sell_limit_price = ltp * multiplier
  PLACE SELL LIMIT @ sell_limit_price  GTC-FOR-DAY
```

#### 2B. Place TAKE-PROFIT LIMITs on Play D longs

```
For each Play D LONG position from Phase 1:
  buy_avg = position.avg_buy_price
  tp_multiplier = 10   # default; range 8-15
  tp_price = buy_avg * tp_multiplier
  
  PLACE SELL LIMIT @ tp_price (close the long position)  GTC-FOR-DAY
```

---

### PHASE 3: CATCH (15:00-15:25 IST)

#### 3A. Active monitoring (alert-only, orders are auto-fill)

```
Every 30 seconds, poll all deep-OTM strikes (3-6% OTM, both CE and PE):
  current_ltp[strike] = get_ltp(strike, side)
  
  # Detect spike
  if current_ltp >= 5 * baseline_at_14:00:
    log_alert("SPIKE on {strike}{side}: {baseline}→{current_ltp} ({multiple}x)")
    
  # Detect collapse (post-spike)
  if was_spiked[strike] and current_ltp < spike_high * 0.5:
    log_alert("COLLAPSE on {strike}{side}: spike done")
```

**No order changes during Phase 3 unless manual override.** Pre-placed limits do the work.

#### 3B. Manual override window

If real-time spike detected on a strike where you DON'T already have a sell limit:
- Operator (Rohan) can place a fresh SELL LIMIT at peak-1 tick
- Algo records the manual trade for journal

---

### PHASE 4: SETTLE (after 15:25 IST)

```
At 15:25:
  Cancel all unfilled GTC-FOR-DAY orders
  Compute P&L:
    play_d_pnl = sum(filled_TP - buy_avg) * lot_size for each Play D long
    play_a_pnl = sum(sell_fill_price * lot_size) for each filled SELL LIMIT
    margin_recycle_pnl = -sum(close_price * 20% qty * lot_size) for closed shorts
                        + sum(original_avg_sell - close_price * 20%qty * lot_size)
    total_pnl = play_d_pnl + play_a_pnl + margin_recycle_pnl

Log to results/manipulation_harvest_log.csv:
  date, day_of_week, n_play_d_longs, n_play_d_filled_at_tp, n_sell_limits_placed,
  n_sell_limits_filled, capital_used, gross_pnl, net_pnl
```

---

## 5. Risk management

### Hard rules (non-negotiable)

| Rule | Enforcement |
|---|---|
| **No SL on deep-OTM positions** (long or short) | Skip all premium-based stops; only spot-based |
| **Max budget per Thursday** = ₹15,000 (Play D buys) | Cap on order placement; reject if exceeded |
| **Max sell-limit qty per strike** = 50 lots (= 1000 contracts SENSEX) | Don't oversize illiquid strikes |
| **Skip if SENSEX spot moves > 1.5% during day** | Strategy assumes pin behavior; high vol = abort |
| **Skip if VIX > 22** | High vol regime = manipulators have better targets |
| **No trades after 15:20** | Settlement risk; no new positions in last 5 min |

### Spot-based emergency exit

```
If at any point during 14:00-15:25:
  abs(current_spot - day_open) / day_open > 1.0%:
    cancel all open orders
    close all Play D longs at market
    log "SPOT_EMERGENCY_EXIT" event
```

(In our 1-yr sample, 0% of E-0 days had >1.0% spot move during 14:00-15:25; this is a tail safeguard only.)

---

## 6. Configuration parameters

```yaml
# manipulation_harvest_v1.yaml
strategy:
  name: manipulation_harvest_v1
  instrument: SENSEX
  active_day: THURSDAY  # weekly expiry
  
phase_1:
  start_time: "14:00"
  end_time: "14:30"
  
  margin_recycle:
    enabled: true
    qualifying_distance_pct: 2.5   # >= this OTM
    qualifying_max_ltp: 0.10
    close_pct: 0.20                # close 20% of qty
  
  play_d_buy:
    enabled: true
    budget_inr: 12500              # ₹10-15K range; default 12.5K
    n_strikes: 5
    distance_zones:                # 4.5-5.5% OTM both sides
      - { side: CE, dist_pct: 4.5 }
      - { side: CE, dist_pct: 5.0 }
      - { side: CE, dist_pct: 5.5 }
      - { side: PE, dist_pct: -4.5 }
      - { side: PE, dist_pct: -5.0 }
      - { side: PE, dist_pct: -5.5 }
    max_ltp_filter: 0.50           # don't buy if too expensive
    max_oi_filter: 2000000         # 20 lakh — must be illiquid
    qty_per_strike_min: 5          # lots
    qty_per_strike_max: 100        # lots
    limit_price_offset: 0.05       # buy at ask+0.05

phase_2:
  start_time: "14:30"
  end_time: "15:00"
  
  sell_limits:
    enabled: true
    multiplier: 12                 # default 12× LTP; range 10-15
    distance_zone: [3.0, 5.5]      # OTM range
    max_ltp_filter: 0.50
    max_oi_filter: 3000000         # 30 lakh
    qty_per_strike: 20             # lots default
  
  play_d_take_profit:
    enabled: true
    tp_multiplier: 10              # default 10× buy avg; range 8-15

phase_3:
  start_time: "15:00"
  end_time: "15:25"
  
  spike_detector:
    poll_interval_sec: 30
    spike_threshold_x: 5.0         # alert when LTP >= 5× baseline
    baseline_time: "14:00"
  
  manual_override: enabled

phase_4:
  cleanup_time: "15:25"
  cancel_unfilled: true
  log_to: "results/manipulation_harvest_log.csv"

risk:
  max_daily_capital: 15000
  spot_emergency_threshold_pct: 1.0
  vix_skip_threshold: 22
  max_qty_per_strike: 50           # lots
  no_new_orders_after: "15:20"
```

---

## 7. Pseudo-code skeleton

```python
# scripts/run_manipulation_harvest.py

from datetime import datetime, time
from lib.kite_live import _kite, get_spot
from lib.expiry_calendar import is_e0, is_market_holiday
import yaml

config = yaml.safe_load(open("config/manipulation_harvest_v1.yaml"))


def main():
    today = date.today()
    if not is_e0(today, "SENSEX"): return abort("Not SENSEX expiry day")
    if is_market_holiday(today): return abort("Holiday")
    
    state = "PHASE_0"
    play_d_longs = []
    sell_limit_orders = []
    margin_recycled = []
    
    while True:
        now = datetime.now(IST).time()
        
        if state == "PHASE_0" and now >= time(14, 0):
            state = "PHASE_1"
            
        elif state == "PHASE_1" and now < time(14, 30):
            margin_recycled = run_margin_recycle(config)
            play_d_longs = run_play_d_buy(config)
        
        elif state == "PHASE_1" and now >= time(14, 30):
            state = "PHASE_2"
            
        elif state == "PHASE_2" and now < time(15, 0):
            sell_limit_orders = run_sell_limits(config)
            place_take_profits_on_play_d(play_d_longs, config)
        
        elif state == "PHASE_2" and now >= time(15, 0):
            state = "PHASE_3"
            
        elif state == "PHASE_3" and now < time(15, 25):
            run_spike_monitor(config)   # alerts only
            check_emergency_exit(config)  # spot-based safeguard
        
        elif state == "PHASE_3" and now >= time(15, 25):
            state = "PHASE_4"
            cleanup_unfilled_orders()
            log_pnl()
            break
        
        sleep(30)


def run_play_d_buy(config):
    spot = get_spot("SENSEX")
    max_pain = compute_max_pain("SENSEX")
    
    # Adjust strike selection based on max-pain bias
    if max_pain > spot * 1.001:
        select = [(CE, 4.5), (CE, 5.0), (CE, 5.5), (PE, -5.0), (PE, -5.5)]
    elif max_pain < spot * 0.999:
        select = [(CE, 5.0), (CE, 5.5), (PE, -4.5), (PE, -5.0), (PE, -5.5)]
    else:
        select = [(CE, 4.5), (CE, 5.0), (CE, 5.5), (PE, -4.5), (PE, -5.0)]
    
    longs = []
    per_strike_budget = config['phase_1']['play_d_buy']['budget_inr'] / 5
    
    for side, dist_pct in select:
        strike = round(spot * (1 + dist_pct/100) / 100) * 100
        ltp = get_ltp("SENSEX", strike, side, expiry=today)
        oi = get_oi("SENSEX", strike, side, expiry=today)
        
        if not (0.05 <= ltp <= 0.50): continue
        if oi > 2_000_000: continue
        
        qty_lots = min(100, max(5, int(per_strike_budget / (ltp * 20))))
        order_id = place_buy_limit(strike, side, qty_lots, ltp + 0.05)
        longs.append({"strike": strike, "side": side, "qty": qty_lots, "order_id": order_id, "buy_avg": ltp + 0.05})
    
    return longs


def run_sell_limits(config):
    spot = get_spot("SENSEX")
    multiplier = config['phase_2']['sell_limits']['multiplier']
    orders = []
    
    for dist in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5]:
        for side, sign in [("CE", 1), ("PE", -1)]:
            strike = round(spot * (1 + sign * dist/100) / 100) * 100
            ltp = get_ltp("SENSEX", strike, side, expiry=today)
            oi = get_oi("SENSEX", strike, side, expiry=today)
            
            if not (0.05 <= ltp <= 0.50): continue
            if oi > 3_000_000: continue
            
            sell_limit_price = round(ltp * multiplier, 1)   # nearest tick
            qty_lots = 20
            order_id = place_sell_limit(strike, side, qty_lots, sell_limit_price)
            orders.append({"strike": strike, "side": side, "qty": qty_lots, "limit": sell_limit_price, "order_id": order_id})
    
    return orders


def check_emergency_exit(config):
    spot = get_spot("SENSEX")
    open_price = get_open("SENSEX")
    move_pct = abs(spot - open_price) / open_price * 100
    
    if move_pct > config['risk']['spot_emergency_threshold_pct']:
        cancel_all_orders()
        market_close_all_play_d_longs()
        log_event("SPOT_EMERGENCY_EXIT")
```

---

## 8. Backtest validation plan

Before going live, validate against historical SENSEX data (2025-04-28 → 2026-04-30, 53 E-0 days):

1. **Replay each Thursday**: simulate Phase 1 buys, Phase 2 limits, Phase 3 spikes
2. **Use actual minute-bar prices** for fill simulation
3. **Compute per-event P&L** assuming realistic fill (limit hits when bar high >= limit price)
4. **Aggregate** to validate ₹15-20K/Thursday EV claim
5. **Flag any losses > ₹15K** — investigate why (spike didn't materialize or spot moved)

Expected metrics:
- Win rate: 60-70% (Thursdays with at least one spike fill)
- Median per Thursday: +₹10-12K
- Mean per Thursday: +₹18-22K (right-skewed due to multi-strike raid days)
- Worst Thursday: -₹15K (full Play D budget loss, no spikes hit)
- Annual: +₹8-12 LAKH

---

## 9. Files / dependencies needed

```
scripts/run_manipulation_harvest.py      # main runner
config/manipulation_harvest_v1.yaml      # config (above)
lib/kite_live.py                         # already exists — adds get_oi(), place_buy_limit(), etc.
lib/expiry_calendar.py                   # already exists
analyses/014_deep_otm_manipulation.py    # historical detection (already done)
analyses/015_backtest_manipulation_harvest.py    # NEW — backtest the algo above
results/manipulation_harvest_log.csv     # daily P&L journal
results/manipulation_harvest_alerts.log  # spike alerts journal
```

---

## 10. Operational checklist (for go-live)

- [ ] Code `scripts/run_manipulation_harvest.py` per pseudo-code above
- [ ] Add `place_buy_limit()`, `place_sell_limit()`, `cancel_order()` wrappers in `lib/kite_live.py`
- [ ] Test harness with paper-trade mode (orders printed, not placed)
- [ ] Backtest analysis 015 — validate ₹15-20K/Thu EV
- [ ] Manual run on first 2-3 Thursdays — verify fills, log carefully
- [ ] Then enable auto-run via launchd plist (Thursdays 13:55 IST start)
- [ ] Add to morning-check cron: alert if Thursday algo failed to start

---

## 11. Known risks & limitations

1. **Sample size:** 1 year of data, ~50 SENSEX expiries. Patterns may shift.
2. **Spike detection lag:** Limit orders catch 70-80% of spikes; some flash spikes may slip past.
3. **Over-saturation:** If too many traders adopt this strategy, spikes attenuate.
4. **Regulatory risk:** SEBI may intervene if manipulation becomes too obvious. Strategy effectiveness could decline.
5. **Liquidity:** During raids, your fills may be at-or-below your limit (favorable). During quiet days, no harm.
6. **Friction:** Each round-trip costs ~₹0.05-0.10 in slippage + brokerage. Already factored in EV.

---

## 12. Version history

- **v1.0** (2026-05-05): Initial spec from manual play observed via analysis 014. Empirically grounded in 212 detected spikes.
- **v1.1** (planned): After 4-6 Thursdays of live data — refine multipliers, OI thresholds, strike selection.
- **v2.0** (planned): Add NIFTY trial if NIFTY spike rate increases (currently too rare).

---

*This spec is the canonical reference for coding the algo. All parameter choices are empirically grounded in `analyses/014_deep_otm_manipulation.py`. Update spec when backtest 015 lands.*
