#!/usr/bin/env python3.11
"""
scripts/test_algo_engine.py — full paper-cycle test for lib/algo_engine.

Drives: arm a NIFTY strangle in MONITORING → feed quotes that cross the entry
trigger → ENTERING → pretrade → place_basket → LIVE → feed a pnl path that hits
target → TARGET exit → CLOSED. Asserts state transitions + notify events fired.
Also covers TIME_EXIT, the state machine, slicer, SOR, idempotency, deep-otm.

Run:  python3.11 scripts/test_algo_engine.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time as dt_time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import algo_engine as ae
from lib.algo_engine import (
    Strategy, StrategyState, TriggerMode, OptionType, OrderAction, OrderType,
    ExitReason, Underlying, DummyBroker, NotifyLog, run_paper_cycle,
    RiskContext, risk_step, slice_for_freeze, allocate, DematCapacity,
    client_ref, order_hash, OrderRequest, Instrument, entry_triggered,
)

IST = ae.IST
PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ════════════════════════════════════════════════════════════════════════════
def test_state_machine():
    print("\n[state machine]")
    ae.transition(StrategyState.DRAFT, StrategyState.MONITORING)  # ok
    bad = False
    try:
        ae.transition(StrategyState.LIVE, StrategyState.MONITORING)
    except ae.InvalidStateTransition:
        bad = True
    check("illegal LIVE→MONITORING raises", bad)
    check("is_active(LIVE)", ae.is_active(StrategyState.LIVE))
    check("is_terminal(CLOSED)", ae.is_terminal(StrategyState.CLOSED))


def test_slicer():
    print("\n[slicer]")
    sl = slice_for_freeze(2250, 1800)
    check("2250@1800 → [1800,450]", [s.quantity for s in sl] == [1800, 450],
          str([s.quantity for s in sl]))
    check("slice delays cumulative", [s.delay_ms for s in sl] == [0, 100])


def test_sor():
    print("\n[SOR]")
    demats = [DematCapacity("A", Decimal(500_000)), DematCapacity("B", Decimal(1_500_000))]
    alloc = allocate(12, demats, Decimal(100_000))
    # B ranks first (1.5M → 15-unit capacity), covers all 12 → single allocation
    check("prefer_primary single demat", len(alloc) == 1 and alloc[0].demat_account == "B",
          str(alloc))
    alloc2 = allocate(25, demats, Decimal(100_000))  # B=15, A=5 → split (capacity-capped at 20)
    check("greedy split across demats", sum(a.quantity for a in alloc2) == 20, str(alloc2))


def test_idempotency():
    print("\n[idempotency]")
    cr = client_ref(7, "CE_MAIN", 0)
    check("client_ref format", cr.startswith("nav-7-CE_MAIN-0-0-") and len(cr.split("-")) == 6, cr)
    inst = Instrument("S1", "NFO", Underlying.NIFTY, date(2026, 6, 23), Decimal(25000),
                      OptionType.CE, 65, Decimal("0.05"), 1800, "N1")
    req = OrderRequest(cr, inst, OrderAction.SELL, 65, OrderType.LIMIT,
                       Decimal("10.0"), "TAG", "PRIMARY")
    h1 = order_hash(req, None)
    h2 = order_hash(req, None)
    check("order_hash deterministic (no timestamps)", h1 == h2)
    h3 = order_hash(req, h1)
    check("hash chain advances", h3 != h1 and len(h3) == 64)


def test_deep_otm():
    print("\n[deep-otm]")
    em = ae.expected_move(24800, 14.0, 4, False)
    check("expected_move positive", em > 0, str(em))
    check("classify_tier 3.5→ALMOST_SURE", ae.classify_tier(3.5) == ae.Tier.ALMOST_SURE)
    check("classify_tier 0.5→None", ae.classify_tier(0.5) is None)
    act, _ = ae.exit_decision(100, 10, 3, 24800, 25500, 0, False, 11, False)
    check("exit_decision 90% captured → BOOK", act == "BOOK", act)


def test_trigger():
    print("\n[entry trigger]")
    check("COMBINED fires at threshold",
          entry_triggered(TriggerMode.COMBINED, 6.0, 7.0, combined_threshold=12.0))
    check("COMBINED below threshold no-fire",
          not entry_triggered(TriggerMode.COMBINED, 3.0, 4.0, combined_threshold=12.0))
    check("SEPARATE both legs required",
          entry_triggered(TriggerMode.SEPARATE, 8.0, 9.0, ce_threshold=7.0, pe_threshold=7.0))
    check("SEPARATE one leg short no-fire",
          not entry_triggered(TriggerMode.SEPARATE, 8.0, 5.0, ce_threshold=7.0, pe_threshold=7.0))


def test_risk_step():
    print("\n[risk_step]")
    now = datetime(2026, 6, 18, 11, 0, tzinfo=IST)
    # circuit breaker on 3 consecutive None pnl
    ctx = RiskContext(sl_amount=Decimal(50000), target_amount=Decimal(20000))
    r = [risk_step(ctx, None, now) for _ in range(3)][-1]
    check("circuit breaker after 3 errors", r == ExitReason.CIRCUIT_BREAKER, str(r))
    # SL
    ctx = RiskContext(sl_amount=Decimal(50000))
    check("SL hit", risk_step(ctx, Decimal(-60000), now) == ExitReason.SL_HIT)
    # target
    ctx = RiskContext(sl_amount=Decimal(50000), target_amount=Decimal(20000))
    check("target hit", risk_step(ctx, Decimal(25000), now) == ExitReason.TARGET_HIT)
    # time exit
    ctx = RiskContext(sl_amount=Decimal(50000))
    late = datetime(2026, 6, 18, 15, 20, tzinfo=IST)
    check("time exit after 15:15", risk_step(ctx, Decimal(100), late) == ExitReason.TIME_EXIT)
    # MTM drawdown
    ctx = RiskContext(sl_amount=Decimal(500000))
    risk_step(ctx, Decimal(10000), now)  # peak=10000
    check("MTM drawdown 40%+", risk_step(ctx, Decimal(5000), now) == ExitReason.MTM_DRAWDOWN)
    # dead-man
    ctx = RiskContext(sl_amount=Decimal(500000))
    check("dead-man switch >120s",
          risk_step(ctx, Decimal(100), now, heartbeat_age_sec=200) == ExitReason.DEAD_MAN_SWITCH)
    # trailing ratchet
    ctx = RiskContext(sl_amount=Decimal(5000), trailing_sl_enabled=True,
                      trailing_sl_trigger=Decimal(10000), trailing_sl_step=Decimal(3000))
    risk_step(ctx, Decimal(15000), now)
    check("trailing SL ratchets up", ctx.effective_sl == Decimal(12000), str(ctx.effective_sl))
    # lock-in
    ctx = RiskContext(sl_amount=Decimal(5000), lockin_profit_enabled=True,
                      lockin_profit_amount=Decimal(8000))
    risk_step(ctx, Decimal(9000), now)
    check("lock-in → breakeven SL", ctx.lockin_activated and ctx.effective_sl == Decimal(0))


def test_broker_idempotent():
    print("\n[DummyBroker]")
    b = DummyBroker(seed=1)
    inst = Instrument("X1", "NFO", Underlying.NIFTY, date(2026, 6, 23), Decimal(25500),
                      OptionType.CE, 65, Decimal("0.05"), 1800, "N1")
    cr = client_ref("s1", "CE_MAIN", 0)
    req = OrderRequest(cr, inst, OrderAction.SELL, 65, OrderType.LIMIT,
                       Decimal("10.0"), "TAG", "PRIMARY")
    a1 = b.place_order(req)
    a2 = b.place_order(req)  # same client_ref → same broker order, no double fill
    check("idempotent place_order", a1.broker_order_id == a2.broker_order_id)
    check("single short position", len(b.get_positions()) == 1 and
          b.get_positions()[0].quantity == -65)


def test_full_cycle_target():
    print("\n[full paper cycle → TARGET]")
    strat = Strategy(
        underlying=Underlying.NIFTY, expiry=date(2026, 6, 23),
        ce_strike=Decimal(25500), pe_strike=Decimal(24000), lots=2,
        trigger_mode=TriggerMode.COMBINED, combined_threshold=12.0,
        order_type=OrderType.LIMIT_WITH_BUFFER,
        sl_amount=Decimal(40000), target_amount=Decimal(15000),
    )
    broker = DummyBroker(seed=5)
    notify = NotifyLog()

    # Quotes: stay below 12 for first 2 ticks, then cross.
    def quotes_fn(s, t):
        if t < 2:
            return {"ce_bid": 3.0, "pe_bid": 4.0}     # 7 < 12, no entry
        return {"ce_bid": 7.0, "pe_bid": 6.5}         # 13.5 ≥ 12, fire

    # PnL: ramp up to hit target after entry.
    def pnl_fn(s, t):
        return Decimal(min(2000 * t, 16000))

    # Fixed pre-squareoff time so TIME_EXIT doesn't pre-empt.
    now_fn = lambda t: datetime(2026, 6, 18, 11, 0, tzinfo=IST)

    out = run_paper_cycle(strat, broker, quotes_fn, pnl_fn, notify,
                          now_fn=now_fn, max_ticks=50)

    events = [e["event"] for e in notify.events]
    check("ended CLOSED", out.state == StrategyState.CLOSED, str(out.state))
    check("exit_reason TARGET_HIT", out.exit_reason == ExitReason.TARGET_HIT, str(out.exit_reason))
    check("entry recorded", out.entry is not None and len(out.entry["legs"]) == 2)
    check("STRATEGY_STARTED fired", "STRATEGY_STARTED" in events)
    check("POSITION_ENTERED fired", "POSITION_ENTERED" in events)
    check("TARGET_HIT fired", "TARGET_HIT" in events)
    check("final_pnl >= target", out.final_pnl is not None and out.final_pnl >= Decimal(15000),
          str(out.final_pnl))
    # qty sizing: 2 lots × 75 = 150 units per leg
    check("leg sizing 150 units", all(f.total_filled == 150 for f in out.fills),
          str([f.total_filled for f in out.fills]))
    # severity routing
    sev = {e["event"]: e["severity"] for e in notify.events}
    check("POSITION_ENTERED severity INFO", sev["POSITION_ENTERED"] == "INFO")


def test_full_cycle_time_exit():
    print("\n[full paper cycle → TIME_EXIT]")
    strat = Strategy(
        underlying=Underlying.SENSEX, expiry=date(2026, 6, 19),
        ce_strike=Decimal(82000), pe_strike=Decimal(80000), lots=1,
        trigger_mode=TriggerMode.SEPARATE, ce_threshold=5.0, pe_threshold=5.0,
        sl_amount=Decimal(40000), target_amount=Decimal(99999999),  # unreachable
    )
    broker = DummyBroker(seed=9)
    notify = NotifyLog()
    quotes_fn = lambda s, t: {"ce_bid": 6.0, "pe_bid": 6.0}   # fires immediately
    pnl_fn = lambda s, t: Decimal(500)                         # small, never target/SL
    # Tick 0,1 before squareoff; tick >=2 after 15:15 → TIME_EXIT
    def now_fn(t):
        hh, mm = (11, 0) if t < 2 else (15, 20)
        return datetime(2026, 6, 18, hh, mm, tzinfo=IST)

    out = run_paper_cycle(strat, broker, quotes_fn, pnl_fn, notify, now_fn=now_fn, max_ticks=50)
    events = [e["event"] for e in notify.events]
    check("ended CLOSED", out.state == StrategyState.CLOSED, str(out.state))
    check("exit_reason TIME_EXIT", out.exit_reason == ExitReason.TIME_EXIT, str(out.exit_reason))
    check("TIME_EXIT fired", "TIME_EXIT" in events)
    check("SEPARATE entry sized 20 units", all(f.total_filled == 20 for f in out.fills),
          str([f.total_filled for f in out.fills]))


def test_pretrade_lot_cap():
    print("\n[pretrade lot cap]")
    strat = Strategy(
        underlying=Underlying.NIFTY, expiry=date(2026, 6, 23),
        ce_strike=Decimal(25500), pe_strike=Decimal(24000), lots=15,  # > 10 cap
        trigger_mode=TriggerMode.COMBINED, combined_threshold=10.0,
    )
    broker = DummyBroker(seed=3)
    notify = NotifyLog()
    quotes_fn = lambda s, t: {"ce_bid": 6.0, "pe_bid": 6.0}
    pnl_fn = lambda s, t: Decimal(0)
    now_fn = lambda t: datetime(2026, 6, 18, 11, 0, tzinfo=IST)
    out = run_paper_cycle(strat, broker, quotes_fn, pnl_fn, notify, now_fn=now_fn, max_ticks=10)
    events = [e["event"] for e in notify.events]
    check("lot cap → KILL_SWITCH closed", out.state == StrategyState.CLOSED and
          "KILL_SWITCH" in events, str(events))


if __name__ == "__main__":
    test_state_machine()
    test_slicer()
    test_sor()
    test_idempotency()
    test_deep_otm()
    test_trigger()
    test_risk_step()
    test_broker_idempotent()
    test_full_cycle_target()
    test_full_cycle_time_exit()
    test_pretrade_lot_cap()

    print(f"\n{'='*48}\n  {PASS} passed, {FAIL} failed\n{'='*48}")
    sys.exit(1 if FAIL else 0)
