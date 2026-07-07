# Theta Gainers Algo — Trade-Taking / Execution Pipeline Spec

Mined from `strategies/Theta Gainers Algo Development/01 - Main Code (for Developer)/backend/`.
This is the build instruction for replicating the algo's trade-taking logic into a pure-Python
`lib/dummy.py`-style module (notify-only, NO live broker), for the Expiry → Execution section.

**Framing:** the runtime monitor→trigger→enter loop (`strategy/engine.py`) is a skeleton stub
(emits fake premium ticks). But every downstream piece is fully implemented + tested: strike
selection, slicer, SOR, idempotency, pre-trade risk, OMS placement, runtime RMS exit loop,
requote/peg, paper broker. Replication = wire these together + author the trigger loop the stub omits.

## 1. Lifecycle state machine
States (`common/types.py:39`): DRAFT, MONITORING, ENTERING, LIVE, EXITING, CLOSED, EMERGENCY_HALT.
Transitions (`strategy/state_machine.py:7` `_ALLOWED`):
- DRAFT→{MONITORING,CLOSED}; MONITORING→{ENTERING,CLOSED,EMERGENCY_HALT};
- ENTERING→{LIVE,EMERGENCY_HALT,EXITING}; LIVE→{EXITING,EMERGENCY_HALT};
- EXITING→{CLOSED,EMERGENCY_HALT}; EMERGENCY_HALT→{CLOSED}; CLOSED terminal.
- is_active = {MONITORING,ENTERING,LIVE,EXITING}.
Triggers (`strategy/service.py`): create→DRAFT; start→MONITORING; execute_now→LIVE (stub);
exit→EXITING/CLOSED; kill→EMERGENCY_HALT.

## 2. Strike selection
(A) Filter engine `strike_selector/` — 16 filters (`filters.py:74` REGISTRY): DISTANCE_POINTS/PERCENT,
DELTA, PREMIUM_PER_LEG, COMBINED_PREMIUM, PREMIUM_PER_CR_MARGIN, OI_MIN, OI_WALL_BEHIND,
BID_ASK_SPREAD_PCT, MIN_VOLUME, IV_RANK, DAYS_TO_EXPIRY, CUSHION_RATIO, PCR_REGIME, VIX_REGIME, TIME_WINDOW.
- DISTANCE_PERCENT = abs(strike−spot)/spot×100; PREMIUM_PER_CR_MARGIN = (ltp×lot)/margin_per_lot×1e7;
  CUSHION_RATIO = abs(strike−spot)/expected_move; VIX_REGIME calm≤13 panic≥25.
- Pair mode keeps near-symmetric (abs(dc−dp)≤spot×0.01), sorts by combined_premium, top 20.
(B) Deep-OTM recommender `analytics/deep_otm.py` (pure):
- expected_move = spot×(vix/100)/√252 × √dte × safety (1.5 weekly / 2.0 monthly).
- Tiers (cushion ratio): ≥3.0 Tier1 ALMOST_SURE p0.95; ≥2.0 VERY_DEEP 0.90; ≥1.5 BALANCED 0.80; ≥1.0 AGGRESSIVE 0.70.
- OI walls MIN_OI=1e6; wall_confirmation_score, reject if <4; premium/margin require ≥1% (margin≈strike×lot×0.11).

## 3. Entry triggers (IMPLEMENT — only enum-documented today)
trigger_mode ∈ {COMBINED, SEPARATE}. COMBINED: ce_bid+pe_bid ≥ combined_threshold.
SEPARATE: ce_bid≥ce_threshold AND pe_bid≥pe_threshold. On fire: MONITORING→ENTERING→(OMS)→LIVE.
Yield/Cr, regime, time-window are strike-selector filters (applied at pick time).

## 4. Slicing / sizing
qty_units = lots × lot_size. Lots (`config.py:40`): NIFTY 65, SENSEX 20. Freeze: NIFTY 1800, SENSEX 1000.
Iceberg (`slicer.py:25`): chunks ≤ freeze, slice i delay = i×jitter(100ms). 2250@1800→[1800,450].
SOR (`router.py:27` allocate): rank demats by free_margin desc, greedy fill take=min(remaining, free//margin_per_unit).
OMS (`order_manager.py:77` place_leg): SOR→slice→rate-limited submit→peg→aggregate avg_fill, slippage_pct.
Margin heuristics: NIFTY ₹105k/lot, SENSEX ₹145k/lot (service); paper get_margin span10%+exposure5%, hedge_benefit 60%.

## 5. Pre-trade risk (`risk/pretrade.py:29` run_all)
1 lot cap>10; 2 active≥5; 3 daily_loss≥50k; 4 cooling_off; 5 OTR≥100; 6 fat-finger total_premium>500×max(qty);
7 freeze warning; 8 spread>5%→illiquid, oi<10k warn; 9 margin required>available.
Config caps (`config.py:44`): max_lots 10, max_active 5/global 25, daily_loss 50k/global 500k,
circuit 3 errors, cooling 30min, dead_man 120s, mtm_dd_kill 40%, two_person_approval ≥5 lots.

## 6. TP / SL / trailing (`risk/runtime.py:66` risk_loop, 5s tick — port directly)
RiskContext: current_pnl, peak_pnl, effective_sl, lockin_activated, consecutive_errors.
1 errors≥3→CIRCUIT_BREAKER; 2 update peak; 3 pnl≤−effective_sl→SL_HIT; 4 pnl≥target→TARGET_HIT;
5 now_ist≥squareoff(15:15)→TIME_EXIT; 6 dd=(peak−cur)/peak×100≥40→MTM_DRAWDOWN;
7 trailing: if pnl≥trigger, new_sl=pnl−step, ratchet up only; 8 lock-in: pnl≥lockin→effective_sl=0 (breakeven);
9 dead-man hb>120s→DEAD_MAN_SWITCH; 10 reconcile every 30s→POSITION_MISMATCH.
Deep-OTM exit_decision (advisory): captured=(entry−cur)/entry; CLOSE_EXPIRY if expiry-day & ≥0.85 & hr≥14;
BOOK if ≥0.70 & dte≥2; DEFENSIVE_EXIT if spot within 20% of strike or vix_chg>25%; WARN fresh OI; else HOLD.
ExitReason: SL_HIT,TARGET_HIT,TIME_EXIT,MANUAL_EXIT,KILL_SWITCH,DEAD_MAN_SWITCH,CIRCUIT_BREAKER,MTM_DRAWDOWN,POSITION_MISMATCH,BROKER_DOWN.

## 7. Idempotency (`execution/idempotency.py`)
client_ref = nav-{strategy}-{leg}-{slice}-{attempt}-{sha1[:4]}. order_hash = sha256(prev+canonical) chain,
canonical = sorted-keys JSON (no timestamps). Paper broker dedups same client_ref_id → same broker_order_id.

## 8. Notify (`notify/service.py:54`)
Severity: STARTED/ENTERED/TARGET/TIME/MANUAL/SUMMARY=INFO; SL/REJECTED=WARN; KILL/MTM/PARTIAL/BROKER_DOWN=ERROR;
DEAD_MAN/CIRCUIT/MISMATCH/LOSS_CAP=CRITICAL. Routing INFO→[wa,tg]; WARN+email; ERROR+sms; CRITICAL+voice.
All channels no-op-log without creds. Fire at each lifecycle/halt point (must be wired — currently only WS publish).

## 9. Broker boundary (stub these)
ABC `brokers/base.py:97` BrokerClient — only broker surface. place/modify/cancel/get_order, get_quote(s),
get_margin, get_positions, fetch_security_master, login/exchange/refresh, ping. Resolved via `registry.get_broker`.
Paper broker `brokers/paper.py` = ready stub: place_order idempotent, 0.5% synth reject, _simulate_fill
sleeps 0.05–0.5s fills at limit×(1+slip) slip −0.5..+1%. Rate limiter (Redis) → stub no-op.

## REPLICATION PLAN → one pure-Python module
Port verbatim: enums; state_machine; slicer (+freeze consts); SOR allocate; idempotency;
strike selection (pick deep_otm.py — already pure); pre-trade caps (in-memory); runtime RMS risk_loop
(callable-injected — port directly); OMS sizing/aggregation; requote/peg (wait3s/max3/tick0.05/slip1%).
IMPLEMENT trigger (§3). Stub: DummyBroker (deterministic fills), rate-limiter no-op, notify→event list, DB→dataclasses.
Author the missing M5/M6 glue loop: MONITORING poll→trigger→ENTERING→pretrade→place_basket→LIVE+risk_loop;
_halt→exit_fn flatten→EXITING→CLOSED, record final_pnl, append notify event.
Data structures: Instrument, OrderRequest(frozen)/OrderAck/OrderUpdate/Quote/MarginInfo, LegPlanInput/LegFillResult,
Strategy(underlying,expiry,ce/pe_strike,lots,trigger_mode+thresholds,order_type,limit_buffer_pct 2.0,sl/target,
trailing,lockin,squareoff 15:15,state,exit_reason,peak_pnl), RiskContext.
