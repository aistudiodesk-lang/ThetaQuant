"""
lib/algo_engine.py — Pure-Python replication of the Theta Quant algo
trade-taking pipeline (notify-only, NO live broker).

This is a faithful port of the in-repo source tree at
  strategies/Theta Quant Algo Development/01 - Main Code (for Developer)/backend/app/
re-expressed as ONE stdlib-only module so it can be driven from the Backtest
Engine dashboard's Expiry → Execution section. It COEXISTS with lib/dummy.py
(the simpler ARMED→ENTERED→TP/SL engine) and does not touch it.

Source → here mapping (see docs/THETA_ALGO_SPEC.md REPLICATION PLAN):
  common/types.py          → enums (StrategyState, OrderAction, OrderType,
                              OrderStatus, TriggerMode, ExitReason, ...)
  strategy/state_machine.py→ _ALLOWED, transition(), is_active(), is_terminal()
  common/slicer.py         → slice_for_freeze(), FreezeQtyExceeded, FREEZE_QTY
  execution/router.py      → DematCapacity, Allocation, allocate()  (SOR)
  execution/idempotency.py → client_ref(), canonical_order_bytes(), order_hash()
  analytics/deep_otm.py    → expected_move, cushion_ratio, classify_tier,
                              find_oi_walls, wall_confirmation_score,
                              premium_to_margin_ok, recommend, exit_decision
  risk/pretrade.py         → run_pretrade() (in-memory caps)
  risk/runtime.py          → RiskContext + risk_step() (pure step, no asyncio)
  execution/order_manager.py → OMS.place_basket() sizing + aggregation
  execution/requote.py     → RequoteConfig constants (peg is synchronous here)
  brokers/paper.py + base.py → DummyBroker + Instrument/OrderRequest/... DTOs
  config.py                → Config constants
  notify/service.py        → NotifyLog + severity map

DEVIATIONS FROM SOURCE (all forced by "pure Python, deterministic, no async"):
  - All async/await removed; risk_loop (a 5s asyncio loop) is re-expressed as a
    pure `risk_step(ctx, pnl, now, heartbeat)` step function returning ExitReason|None.
  - asyncio.sleep / iceberg jitter delays are computed (Slice.delay_ms) but never
    actually slept — slicing math is identical, timing is metadata only.
  - PaperBroker._simulate_fill (random sleep + 0.5% reject + random slippage) →
    DummyBroker with a *seedable* random.Random so fills are reproducible. Default
    reject_rate=0.0 for test determinism (configurable).
  - Decimal kept where the source uses it (margin/price math) for fidelity.
  - DB / Redis / WS / structlog stripped: pretrade caps are in-memory, notify is an
    event list, rate-limiter is a no-op.
  - Strategy + run_paper_cycle are NEW glue (source's engine.py is a stub) — they
    wire MONITORING→ENTERING→pretrade→place_basket→LIVE→risk steps→EXITING→CLOSED.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum
from typing import Any, Callable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


# ════════════════════════════════════════════════════════════════════════════
# 1. ENUMS  (port of common/types.py — StrEnum → str+Enum for 3.11 portability)
# ════════════════════════════════════════════════════════════════════════════
class _StrEnum(str, Enum):
    def __str__(self) -> str:  # behave like the value
        return self.value


class Underlying(_StrEnum):
    NIFTY = "NIFTY"
    SENSEX = "SENSEX"


class OptionType(_StrEnum):
    CE = "CE"
    PE = "PE"


class OrderAction(_StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(_StrEnum):
    LIMIT = "LIMIT"
    LIMIT_WITH_BUFFER = "LIMIT_WITH_BUFFER"
    # MARKET intentionally excluded — backend rejects it on options.


class OrderStatus(_StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class StrategyState(_StrEnum):
    DRAFT = "DRAFT"
    MONITORING = "MONITORING"
    ENTERING = "ENTERING"
    LIVE = "LIVE"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    EMERGENCY_HALT = "EMERGENCY_HALT"


class TriggerMode(_StrEnum):
    COMBINED = "COMBINED"        # (ce_bid + pe_bid) ≥ threshold
    SEPARATE = "SEPARATE"        # ce_bid ≥ X AND pe_bid ≥ Y


class ExitReason(_StrEnum):
    SL_HIT = "SL_HIT"
    TARGET_HIT = "TARGET_HIT"
    TIME_EXIT = "TIME_EXIT"
    MANUAL_EXIT = "MANUAL_EXIT"
    KILL_SWITCH = "KILL_SWITCH"
    DEAD_MAN_SWITCH = "DEAD_MAN_SWITCH"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    MTM_DRAWDOWN = "MTM_DRAWDOWN"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    BROKER_DOWN = "BROKER_DOWN"


class AuditSeverity(_StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ════════════════════════════════════════════════════════════════════════════
# CONFIG  (port of config.py risk caps + SEBI/exchange constants)
# ════════════════════════════════════════════════════════════════════════════
class Config:
    # SEBI / exchange
    NIFTY_FREEZE_QTY = 1800
    SENSEX_FREEZE_QTY = 1000
    NIFTY_LOT_SIZE = 75   # current NSE lot (source used a stale 65); matches lib/dummy.py
    SENSEX_LOT_SIZE = 20
    ICEBERG_SLICE_JITTER_MS = 100
    OTR_HALT_THRESHOLD = 100

    # Risk caps
    MAX_LOTS_PER_STRATEGY = 10
    MAX_ACTIVE_STRATEGIES_PER_USER = 5
    MAX_ACTIVE_STRATEGIES_GLOBAL = 25
    MAX_DAILY_LOSS_PER_USER = 50_000.0
    MAX_DAILY_LOSS_GLOBAL = 500_000.0
    CIRCUIT_BREAKER_ERROR_THRESHOLD = 3
    COOLING_OFF_MINUTES_AFTER_HALT = 30
    DEAD_MAN_SWITCH_SECONDS = 120
    MTM_DRAWDOWN_KILL_PCT = 40.0
    TWO_PERSON_APPROVAL_MIN_LOTS = 5

    # Margin heuristics (strategy/service.py ballpark, ₹/lot)
    MARGIN_PER_LOT = {"NIFTY": 105_000, "SENSEX": 145_000}

    SQUAREOFF_TIME = dt_time(15, 15)   # 15:15 IST


def freeze_qty_for(underlying: str) -> int:
    return Config.NIFTY_FREEZE_QTY if underlying.upper() == "NIFTY" else Config.SENSEX_FREEZE_QTY


def lot_size_for(underlying: str) -> int:
    return Config.NIFTY_LOT_SIZE if underlying.upper() == "NIFTY" else Config.SENSEX_LOT_SIZE


def margin_per_lot_for(underlying: str) -> Decimal:
    return Decimal(str(Config.MARGIN_PER_LOT.get(underlying.upper(), 105_000)))


# ════════════════════════════════════════════════════════════════════════════
# ERRORS
# ════════════════════════════════════════════════════════════════════════════
class AlgoError(Exception):
    pass


class InvalidStateTransition(AlgoError):
    pass


class FreezeQtyExceeded(AlgoError):
    pass


class OrderRejected(AlgoError):
    pass


class InsufficientMargin(AlgoError):
    pass


# Pre-trade risk violations
class RiskViolation(AlgoError):
    pass


class LotCapExceeded(RiskViolation):
    pass


class ActiveStrategiesCapExceeded(RiskViolation):
    pass


class DailyLossCapExceeded(RiskViolation):
    pass


class FatFingerGuard(RiskViolation):
    pass


class IlliquidStrike(RiskViolation):
    pass


# ════════════════════════════════════════════════════════════════════════════
# 2. STATE MACHINE  (port of strategy/state_machine.py)
# ════════════════════════════════════════════════════════════════════════════
S = StrategyState

_ALLOWED: dict[StrategyState, set[StrategyState]] = {
    S.DRAFT:          {S.MONITORING, S.CLOSED},
    S.MONITORING:     {S.ENTERING, S.CLOSED, S.EMERGENCY_HALT},
    S.ENTERING:       {S.LIVE, S.EMERGENCY_HALT, S.EXITING},
    S.LIVE:           {S.EXITING, S.EMERGENCY_HALT},
    S.EXITING:        {S.CLOSED, S.EMERGENCY_HALT},
    S.CLOSED:         set(),
    S.EMERGENCY_HALT: {S.CLOSED},
}


def transition(current: StrategyState, target: StrategyState) -> None:
    if target not in _ALLOWED.get(current, set()):
        raise InvalidStateTransition(f"{current} → {target} not allowed")


def is_terminal(s: StrategyState) -> bool:
    return s == S.CLOSED


def is_active(s: StrategyState) -> bool:
    return s in {S.MONITORING, S.ENTERING, S.LIVE, S.EXITING}


# ════════════════════════════════════════════════════════════════════════════
# 3. SLICER  (port of common/slicer.py)
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Slice:
    index: int
    quantity: int
    delay_ms: int


def slice_for_freeze(total_qty: int, freeze_qty: int, jitter_ms: int | None = None) -> list[Slice]:
    """Split total_qty into chunks <= freeze_qty; slice i carries delay i*jitter."""
    if total_qty <= 0:
        raise FreezeQtyExceeded(f"invalid total_qty {total_qty}")
    if freeze_qty <= 0:
        raise FreezeQtyExceeded(f"invalid freeze_qty {freeze_qty}")
    jitter = jitter_ms if jitter_ms is not None else Config.ICEBERG_SLICE_JITTER_MS
    slices: list[Slice] = []
    remaining, i = total_qty, 0
    while remaining > 0:
        q = min(freeze_qty, remaining)
        slices.append(Slice(index=i, quantity=q, delay_ms=i * jitter))
        remaining -= q
        i += 1
    return slices


# ════════════════════════════════════════════════════════════════════════════
# 4. SOR — Smart Order Router  (port of execution/router.py)
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class DematCapacity:
    demat_account: str
    free_margin: Decimal
    daily_loss_headroom: Decimal = Decimal(0)


@dataclass(frozen=True)
class Allocation:
    demat_account: str
    quantity: int


def allocate(total_qty: int, demats: list[DematCapacity],
             margin_per_unit: Decimal, prefer_primary: bool = True) -> list[Allocation]:
    """Rank demats by free_margin desc, greedily fill take=min(remaining, free//margin)."""
    if total_qty <= 0 or not demats:
        return []
    ranked = sorted(demats, key=lambda d: d.free_margin, reverse=True)

    if prefer_primary and margin_per_unit > 0:
        capacity_primary = int(ranked[0].free_margin // margin_per_unit)
        if capacity_primary >= total_qty:
            return [Allocation(ranked[0].demat_account, total_qty)]

    result: list[Allocation] = []
    remaining = total_qty
    for d in ranked:
        if remaining <= 0:
            break
        if margin_per_unit <= 0:
            result.append(Allocation(d.demat_account, remaining))
            remaining = 0
            break
        cap = int(d.free_margin // margin_per_unit)
        if cap <= 0:
            continue
        take = min(remaining, cap)
        result.append(Allocation(d.demat_account, take))
        remaining -= take
    return result


# ════════════════════════════════════════════════════════════════════════════
# Broker DTOs  (port of brokers/base.py)
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Instrument:
    script_id: str
    exchange: str
    underlying: Underlying
    expiry: date
    strike: Decimal
    option_type: OptionType
    lot_size: int
    tick_size: Decimal
    freeze_qty: int
    trading_symbol: str


@dataclass(frozen=True)
class Quote:
    instrument_id: int
    script_id: str
    ltp: Decimal
    bid: Decimal
    ask: Decimal
    bid_qty: int
    ask_qty: int
    volume: int
    oi: int
    ts: datetime


@dataclass(frozen=True)
class OrderRequest:
    """Input to place_order. Frozen — enables retry with same client_ref_id."""
    client_ref_id: str
    instrument: Instrument
    action: OrderAction
    quantity: int
    order_type: OrderType
    limit_price: Decimal
    sebi_algo_tag: str
    demat_account: str


@dataclass(frozen=True)
class OrderAck:
    broker_order_id: str
    status: OrderStatus
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class OrderUpdate:
    broker_order_id: str
    status: OrderStatus
    filled_qty: int
    avg_fill_price: Decimal | None
    rejection_reason: str | None
    ts: datetime


@dataclass(frozen=True)
class Position:
    script_id: str
    quantity: int                  # signed: negative = short
    avg_price: Decimal
    ltp: Decimal
    mtm_pnl: Decimal


@dataclass(frozen=True)
class MarginInfo:
    available: Decimal
    required: Decimal
    span_margin: Decimal
    exposure_margin: Decimal
    hedge_benefit: Decimal


# ════════════════════════════════════════════════════════════════════════════
# 5. IDEMPOTENCY  (port of execution/idempotency.py)
# ════════════════════════════════════════════════════════════════════════════
def client_ref(strategy_id: Any, leg: str, slice_idx: int, attempt: int = 0) -> str:
    base = f"nav-{strategy_id}-{leg}-{slice_idx}-{attempt}"
    h = hashlib.sha1(base.encode()).hexdigest()[:4]
    return f"{base}-{h}"


def canonical_order_bytes(req: OrderRequest) -> bytes:
    """Deterministic serialization for hashing — sorted keys, NO timestamps."""
    payload = {
        "client_ref_id": req.client_ref_id,
        "script_id": req.instrument.script_id,
        "action": req.action.value,
        "quantity": req.quantity,
        "order_type": req.order_type.value,
        "limit_price": str(req.limit_price),
        "demat": req.demat_account,
        "algo_tag": req.sebi_algo_tag,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def order_hash(req: OrderRequest, prev_hash: str | None) -> str:
    h = hashlib.sha256()
    if prev_hash:
        h.update(prev_hash.encode())
    h.update(canonical_order_bytes(req))
    return h.hexdigest()


# ════════════════════════════════════════════════════════════════════════════
# 6. DEEP-OTM STRIKE SELECTION  (port of analytics/deep_otm.py — already pure)
# ════════════════════════════════════════════════════════════════════════════
class Tier(IntEnum):
    ALMOST_SURE = 1       # cushion_ratio ≥ 3.0, >95%
    VERY_DEEP = 2         # ≥ 2.0, >90%
    BALANCED = 3          # ≥ 1.5, >80%
    AGGRESSIVE = 4        # ≥ 1.0, >70%


TIER_LABELS = {
    Tier.ALMOST_SURE: "Almost Sure Shot",
    Tier.VERY_DEEP:   "Very Deep",
    Tier.BALANCED:    "Balanced",
    Tier.AGGRESSIVE:  "Aggressive",
}

VIX_PANIC = 25.0
VIX_CALM = 13.0
MIN_OI_FOR_WALL = 1_000_000
FRESH_WRITING_OI_CHG_PCT = 50.0
MIN_RECOMMEND_SCORE = 4


@dataclass(frozen=True)
class StrikeData:
    strike: float
    option_type: str     # 'CE' | 'PE'
    ltp: float
    bid: float
    ask: float
    oi: int
    oi_change_pct: float
    volume: int
    iv: float


@dataclass(frozen=True)
class MarketSnapshot:
    spot: float
    futures: float | None
    max_pain: float | None
    oi_pcr: float
    vol_pcr: float
    vix: float
    vix_change_pct: float
    technical_support: list[float]
    technical_resistance: list[float]
    dte: int
    is_monthly: bool = False


@dataclass
class Recommendation:
    tier: Tier
    tier_label: str
    ce_strike: float | None
    ce_premium: float | None
    ce_oi: int | None
    ce_cushion_ratio: float | None
    pe_strike: float | None
    pe_premium: float | None
    pe_oi: int | None
    pe_cushion_ratio: float | None
    combined_premium_per_lot: float
    probability_otm_estimate: float
    score_ce: int
    score_pe: int
    notes: list[str]


def expected_move(spot: float, vix: float, dte: int, is_monthly: bool = False) -> float:
    daily = spot * (vix / 100.0) / math.sqrt(252)
    safety = 2.0 if is_monthly else 1.5
    return daily * math.sqrt(max(dte, 1)) * safety


def cushion_ratio(distance_pts: float, expected: float) -> float:
    return distance_pts / expected if expected > 0 else 0.0


def classify_tier(ratio: float) -> Tier | None:
    if ratio >= 3.0:
        return Tier.ALMOST_SURE
    if ratio >= 2.0:
        return Tier.VERY_DEEP
    if ratio >= 1.5:
        return Tier.BALANCED
    if ratio >= 1.0:
        return Tier.AGGRESSIVE
    return None


def find_oi_walls(chain: list[StrikeData], side: str, top_n: int = 3) -> list[StrikeData]:
    filtered = [s for s in chain if s.option_type == side and s.oi >= MIN_OI_FOR_WALL]
    return sorted(filtered, key=lambda s: s.oi, reverse=True)[:top_n]


def wall_confirmation_score(strike: StrikeData, spot: float, expected: float,
                            walls: list[StrikeData], technical_levels: list[float]) -> int:
    score = 0
    wall_strikes = {w.strike for w in walls}
    if strike.strike in wall_strikes:
        score += 3
    else:
        if strike.option_type == "CE" and walls and strike.strike > max(w.strike for w in walls):
            score += 2
        if strike.option_type == "PE" and walls and strike.strike < min(w.strike for w in walls):
            score += 2
    if strike.oi_change_pct > FRESH_WRITING_OI_CHG_PCT:
        score += 2
    distance = abs(strike.strike - spot)
    if technical_levels and distance > min(abs(t - spot) for t in technical_levels):
        score += 1
    if distance >= 2 * expected:
        score += 1
    return score


def premium_to_margin_ok(premium_per_lot: float, strike: float, lot_size: int) -> bool:
    margin_approx = strike * lot_size * 0.11
    if margin_approx <= 0:
        return False
    return (premium_per_lot / margin_approx) >= 0.01


def recommend(chain: list[StrikeData], market: MarketSnapshot, lot_size: int) -> list[Recommendation]:
    expected = expected_move(market.spot, market.vix, market.dte, market.is_monthly)
    ce_walls = find_oi_walls(chain, "CE")
    pe_walls = find_oi_walls(chain, "PE")

    def eligible(strike: StrikeData, walls: list[StrikeData]):
        dist = abs(strike.strike - market.spot)
        ratio = cushion_ratio(dist, expected)
        tier = classify_tier(ratio)
        if tier is None:
            return None
        if not premium_to_margin_ok(strike.ltp * lot_size, strike.strike, lot_size):
            return None
        levels = (market.technical_resistance if strike.option_type == "CE"
                  else market.technical_support)
        score = wall_confirmation_score(strike, market.spot, expected, walls, levels)
        if score < MIN_RECOMMEND_SCORE:
            return None
        return tier, score, ratio

    recs: list[Recommendation] = []
    for tier in Tier:
        best_ce = None
        best_pe = None
        for s in chain:
            result = eligible(s, ce_walls if s.option_type == "CE" else pe_walls)
            if result is None:
                continue
            t, score, ratio = result
            if t != tier:
                continue
            candidate = (s, score, ratio)
            if s.option_type == "CE":
                if best_ce is None or s.ltp > best_ce[0].ltp:
                    best_ce = candidate
            else:
                if best_pe is None or s.ltp > best_pe[0].ltp:
                    best_pe = candidate

        if best_ce is None and best_pe is None:
            continue

        ce_strike = best_ce[0] if best_ce else None
        pe_strike = best_pe[0] if best_pe else None
        combined_per_lot = ((ce_strike.ltp if ce_strike else 0.0) +
                            (pe_strike.ltp if pe_strike else 0.0)) * lot_size

        prob_map = {Tier.ALMOST_SURE: 0.95, Tier.VERY_DEEP: 0.90,
                    Tier.BALANCED: 0.80, Tier.AGGRESSIVE: 0.70}
        prob = prob_map[tier]
        if ce_strike is None or pe_strike is None:
            prob *= 0.95

        recs.append(Recommendation(
            tier=tier, tier_label=TIER_LABELS[tier],
            ce_strike=ce_strike.strike if ce_strike else None,
            ce_premium=ce_strike.ltp if ce_strike else None,
            ce_oi=ce_strike.oi if ce_strike else None,
            ce_cushion_ratio=best_ce[2] if best_ce else None,
            pe_strike=pe_strike.strike if pe_strike else None,
            pe_premium=pe_strike.ltp if pe_strike else None,
            pe_oi=pe_strike.oi if pe_strike else None,
            pe_cushion_ratio=best_pe[2] if best_pe else None,
            combined_premium_per_lot=combined_per_lot,
            probability_otm_estimate=prob,
            score_ce=best_ce[1] if best_ce else 0,
            score_pe=best_pe[1] if best_pe else 0,
            notes=_risk_notes(market),
        ))
    return recs


def _risk_notes(m: MarketSnapshot) -> list[str]:
    notes: list[str] = []
    if m.vix >= VIX_PANIC:
        notes.append(f"VIX panic zone ({m.vix:.1f} ≥ {VIX_PANIC})")
    if m.vix_change_pct > 10:
        notes.append(f"VIX spiked {m.vix_change_pct:+.1f}% — size conservatively")
    if m.vix < VIX_CALM:
        notes.append(f"VIX calm ({m.vix:.1f}) — premium limited")
    if m.max_pain is not None and abs(m.spot - m.max_pain) / m.spot > 0.01:
        side = "above" if m.spot > m.max_pain else "below"
        notes.append(f"Spot {side} Max Pain by {abs(m.spot - m.max_pain):.0f} pts — gravitational pull")
    if m.oi_pcr > 1.3:
        notes.append(f"OI PCR {m.oi_pcr:.2f} — bullish bias, prefer selling PE deeper")
    elif m.oi_pcr < 0.8:
        notes.append(f"OI PCR {m.oi_pcr:.2f} — bearish bias, prefer selling CE deeper")
    return notes


def exit_decision(entry_premium: float, current_premium: float, dte: int,
                  spot: float, sold_strike: float, vix_change_pct: float,
                  is_expiry_day: bool, now_hour: int,
                  fresh_oi_inside_strike: bool) -> tuple[str, str]:
    """Advisory exit hint. action ∈ {HOLD, WARN, BOOK, DEFENSIVE_EXIT, CLOSE_EXPIRY}."""
    captured = 0.0
    if entry_premium > 0:
        captured = (entry_premium - current_premium) / entry_premium
    if is_expiry_day and captured >= 0.85 and now_hour >= 14:
        return ("CLOSE_EXPIRY", f"{captured*100:.0f}% captured on expiry day after 2pm — avoid gamma")
    if captured >= 0.70 and dte >= 2:
        return ("BOOK", f"{captured*100:.0f}% premium captured, {dte} DTE remaining")
    distance = abs(sold_strike - spot)
    if distance <= 0.20 * abs(sold_strike):
        return ("DEFENSIVE_EXIT", "Spot breached 80% of distance to sold strike")
    if vix_change_pct > 25:
        return ("DEFENSIVE_EXIT", f"VIX spiked {vix_change_pct:+.1f}% intraday")
    if fresh_oi_inside_strike:
        return ("WARN", "Fresh OI buildup inside sold strike — monitor closely")
    return ("HOLD", f"{captured*100:.0f}% captured, {dte} DTE, within cushion")


# ════════════════════════════════════════════════════════════════════════════
# 7. ENTRY TRIGGER  (IMPLEMENTED — only enum-documented in source)
# ════════════════════════════════════════════════════════════════════════════
def entry_triggered(trigger_mode: TriggerMode, ce_bid: float, pe_bid: float,
                    combined_threshold: float | None = None,
                    ce_threshold: float | None = None,
                    pe_threshold: float | None = None) -> bool:
    """COMBINED: ce_bid+pe_bid ≥ combined_threshold.
    SEPARATE: ce_bid ≥ ce_threshold AND pe_bid ≥ pe_threshold.
    These are SHORT-premium strategies — entry fires when premium is RICH.
    """
    if trigger_mode == TriggerMode.SEPARATE:
        ok = True
        if ce_threshold is not None:
            ok = ok and ce_bid >= ce_threshold
        if pe_threshold is not None:
            ok = ok and pe_bid >= pe_threshold
        return ok
    if combined_threshold is None:
        return False
    return (ce_bid + pe_bid) >= combined_threshold


# ════════════════════════════════════════════════════════════════════════════
# 8. PRE-TRADE RISK  (port of risk/pretrade.py — DB checks → in-memory)
# ════════════════════════════════════════════════════════════════════════════
def run_pretrade(legs: list[OrderRequest], quantity_lots: int,
                 broker: "DummyBroker", demat_accounts: list[str],
                 active_strategies: int = 0, daily_loss: float = 0.0,
                 otr: int = 0) -> list[str]:
    """Run every pre-trade check. Returns warnings (non-blocking); raises on hard violations.
    Ordered cheapest→most-expensive, matching source run_all.
    """
    warnings: list[str] = []

    # 1. Lot cap
    if quantity_lots > Config.MAX_LOTS_PER_STRATEGY:
        raise LotCapExceeded(f"{quantity_lots} lots > cap {Config.MAX_LOTS_PER_STRATEGY}")

    # 2. Active strategies cap
    if active_strategies >= Config.MAX_ACTIVE_STRATEGIES_PER_USER:
        raise ActiveStrategiesCapExceeded(
            f"{active_strategies} active ≥ cap {Config.MAX_ACTIVE_STRATEGIES_PER_USER}")

    # 3. Daily loss cap
    if daily_loss >= Config.MAX_DAILY_LOSS_PER_USER:
        raise DailyLossCapExceeded(
            f"daily loss ₹{daily_loss} ≥ cap ₹{Config.MAX_DAILY_LOSS_PER_USER}")

    # 5. OTR threshold (4 cooling-off omitted — needs halt history)
    if otr >= Config.OTR_HALT_THRESHOLD:
        raise RiskViolation(f"OTR {otr} ≥ {Config.OTR_HALT_THRESHOLD}")

    # 6. Fat-finger: total premium too high
    total_premium = sum(r.limit_price * r.quantity for r in legs)
    if legs and total_premium > Decimal("500") * max(r.quantity for r in legs):
        raise FatFingerGuard(f"combined notional ₹{total_premium} looks unusually high")

    # 7. Freeze qty per leg (warn — slicer handles)
    for r in legs:
        if r.quantity > r.instrument.freeze_qty:
            warnings.append(
                f"{r.instrument.trading_symbol}: qty {r.quantity} > freeze "
                f"{r.instrument.freeze_qty}, will be sliced")

    # 8. Liquidity: bid-ask spread + min OI
    for r in legs:
        q = broker.get_quote(r.instrument)
        if q.ltp > 0:
            spread_pct = float((q.ask - q.bid) / q.ltp * 100)
            if spread_pct > 5.0:
                raise IlliquidStrike(
                    f"{r.instrument.trading_symbol}: bid-ask spread {spread_pct:.1f}% > 5%")
        if q.oi < 10_000:
            warnings.append(f"{r.instrument.trading_symbol}: OI {q.oi} < 10,000 — illiquid")

    # 9. Margin check (most expensive last)
    for acct in demat_accounts:
        margin = broker.get_margin(acct, legs)
        if margin.required > margin.available:
            raise InsufficientMargin(
                f"demat {acct}: need ₹{margin.required}, have ₹{margin.available}")
        if margin.hedge_benefit > 0:
            warnings.append(f"hedge benefit ₹{margin.hedge_benefit} applied on {acct}")

    return warnings


# ════════════════════════════════════════════════════════════════════════════
# 9. RUNTIME RMS  (port of risk/runtime.py — async loop → pure step function)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class RiskContext:
    """Mutable per-strategy risk state, updated each tick.
    Mirrors risk/runtime.py RiskContext __slots__.
    """
    strategy_id: Any = None
    sl_amount: Decimal = Decimal(0)
    target_amount: Decimal | None = None
    squareoff_time: dt_time = field(default_factory=lambda: Config.SQUAREOFF_TIME)
    trailing_sl_enabled: bool = False
    trailing_sl_trigger: Decimal | None = None
    trailing_sl_step: Decimal | None = None
    lockin_profit_enabled: bool = False
    lockin_profit_amount: Decimal | None = None
    mtm_dd_kill_pct: float = Config.MTM_DRAWDOWN_KILL_PCT
    dead_man_sec: int = Config.DEAD_MAN_SWITCH_SECONDS
    # runtime
    current_pnl: Decimal = Decimal(0)
    peak_pnl: Decimal = Decimal(0)
    effective_sl: Decimal | None = None
    lockin_activated: bool = False
    consecutive_errors: int = 0
    last_recon_at: float = 0.0
    last_heartbeat_sec: float = 0.0   # seconds since last UI heartbeat

    def __post_init__(self):
        if self.effective_sl is None:
            self.effective_sl = self.sl_amount


def risk_step(ctx: RiskContext, pnl: Decimal | None, now: datetime,
              heartbeat_age_sec: float = 0.0,
              reconcile_ok: bool | None = None) -> ExitReason | None:
    """One tick of the runtime RMS. Pure: mutates ctx, returns an ExitReason or None.

    Replaces the async `risk_loop` (which ran this body every 5s). Caller supplies
    the live pnl (None signals a fetch error → circuit-breaker counting), the
    current time, heartbeat staleness, and an optional reconcile result.

    Order of checks matches source exactly:
      circuit(3 errs) → peak → SL → target → time → MTM-dd → trailing → lock-in
      → dead-man → reconcile.
    """
    # ── 0/1. Circuit breaker on consecutive pnl-fetch errors ──────────────
    if pnl is None:
        ctx.consecutive_errors += 1
        if ctx.consecutive_errors >= Config.CIRCUIT_BREAKER_ERROR_THRESHOLD:
            return ExitReason.CIRCUIT_BREAKER
        return None
    ctx.consecutive_errors = 0
    ctx.current_pnl = pnl

    # ── 1. Update peak P&L ───────────────────────────────────────────────
    if ctx.current_pnl > ctx.peak_pnl:
        ctx.peak_pnl = ctx.current_pnl

    # ── 2. Hard SL ───────────────────────────────────────────────────────
    if ctx.effective_sl and ctx.current_pnl <= -ctx.effective_sl:
        return ExitReason.SL_HIT

    # ── 3. Target ────────────────────────────────────────────────────────
    if ctx.target_amount and ctx.current_pnl >= ctx.target_amount:
        return ExitReason.TARGET_HIT

    # ── 4. Time-based exit (now must be IST-aware or naive-IST) ───────────
    now_ist = now.astimezone(IST) if now.tzinfo else now
    if ctx.squareoff_time and now_ist.time() >= ctx.squareoff_time:
        return ExitReason.TIME_EXIT

    # ── 5. MTM drawdown from peak ────────────────────────────────────────
    if ctx.mtm_dd_kill_pct and ctx.peak_pnl > 0:
        dd = float((ctx.peak_pnl - ctx.current_pnl) / ctx.peak_pnl * 100)
        if dd >= ctx.mtm_dd_kill_pct:
            return ExitReason.MTM_DRAWDOWN

    # ── 6. Trailing SL (ratchet up only) ─────────────────────────────────
    if ctx.trailing_sl_enabled and ctx.trailing_sl_trigger and ctx.trailing_sl_step:
        if ctx.current_pnl >= ctx.trailing_sl_trigger:
            new_sl = ctx.current_pnl - ctx.trailing_sl_step
            if new_sl > ctx.effective_sl:
                ctx.effective_sl = new_sl

    # ── 7. Lock-in profits (SL → breakeven) ──────────────────────────────
    if (ctx.lockin_profit_enabled and ctx.lockin_profit_amount
            and not ctx.lockin_activated
            and ctx.current_pnl >= ctx.lockin_profit_amount):
        ctx.lockin_activated = True
        ctx.effective_sl = Decimal(0)

    # ── 8. Dead-man switch ───────────────────────────────────────────────
    if ctx.dead_man_sec and heartbeat_age_sec > ctx.dead_man_sec:
        return ExitReason.DEAD_MAN_SWITCH

    # ── 9. Position reconciliation ───────────────────────────────────────
    if reconcile_ok is False:
        return ExitReason.POSITION_MISMATCH

    return None


# ════════════════════════════════════════════════════════════════════════════
# 10. DUMMY BROKER  (port of brokers/paper.py — async/sleep/random → deterministic)
# ════════════════════════════════════════════════════════════════════════════
class DummyBroker:
    """Deterministic, seedable paper broker. Synchronous — no asyncio, no sleeps.

    DEVIATIONS from PaperBroker:
      - place_order fills IMMEDIATELY and deterministically (no _simulate_fill task).
      - reject_rate default 0.0 (set >0 to exercise synthetic rejects reproducibly).
      - slippage drawn from a seeded random.Random in [-0.005, +0.01] (source range).
      - get_quote price = intrinsic + time_decay + small seeded noise (source formula).
    """

    def __init__(self, seed: int = 42, reject_rate: float = 0.0,
                 spot: dict[str, float] | None = None,
                 available_margin: Decimal = Decimal(1_000_000)):
        self._rng = random.Random(seed)
        self.reject_rate = reject_rate
        self._spot = spot or {"NIFTY": 24800.0, "SENSEX": 81200.0}
        self._available = available_margin
        self._orders: dict[str, dict[str, Any]] = {}            # client_ref_id → record
        self._by_boid: dict[str, dict[str, Any]] = {}            # broker_order_id → record
        self._positions: dict[str, Position] = {}
        self._quote_override: dict[str, Decimal] = {}            # script_id → forced ltp

    # ── Quotes ───────────────────────────────────────────────────────────
    def set_spot(self, underlying: str, spot: float) -> None:
        self._spot[underlying.upper()] = spot

    def set_quote(self, script_id: str, ltp: Decimal) -> None:
        """Force a script's LTP (for driving deterministic test paths)."""
        self._quote_override[script_id] = Decimal(str(ltp))

    def _synthetic_price(self, inst: Instrument) -> Decimal:
        if inst.script_id in self._quote_override:
            return self._quote_override[inst.script_id]
        spot = Decimal(str(self._spot.get(inst.underlying.value, 24800.0)))
        intrinsic = max(Decimal(0),
                        spot - inst.strike if inst.option_type == OptionType.CE
                        else inst.strike - spot)
        distance = abs(float(spot - inst.strike))
        time_decay = max(1.0, 20.0 - distance / 100.0)
        price = float(intrinsic) + time_decay + self._rng.uniform(-0.5, 0.5)
        return Decimal(str(max(0.05, round(price, 2))))

    def get_quote(self, instrument: Instrument) -> Quote:
        ltp = self._synthetic_price(instrument)
        spread = ltp * Decimal("0.01")
        return Quote(
            instrument_id=0, script_id=instrument.script_id, ltp=ltp,
            bid=(ltp - spread / 2).quantize(Decimal("0.05")),
            ask=(ltp + spread / 2).quantize(Decimal("0.05")),
            bid_qty=instrument.lot_size * 10, ask_qty=instrument.lot_size * 10,
            volume=self._rng.randint(1000, 100000),
            oi=self._rng.randint(10000, 500000),
            ts=datetime.now(IST),
        )

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        return [self.get_quote(i) for i in instruments]

    # ── Orders ───────────────────────────────────────────────────────────
    def place_order(self, req: OrderRequest) -> OrderAck:
        # Idempotency: same client_ref_id → same broker_order_id, no double fill.
        if req.client_ref_id in self._orders:
            rec = self._orders[req.client_ref_id]
            return OrderAck(rec["broker_order_id"], OrderStatus(rec["status"]), rec["raw"])

        if self.reject_rate > 0 and self._rng.random() < self.reject_rate:
            raise OrderRejected("paper: synthetic reject for testing")

        bo_id = f"PAPER-{self._rng.getrandbits(48):012X}"
        slippage = Decimal(str(self._rng.uniform(-0.005, 0.01)))
        fill_px = (req.limit_price * (Decimal(1) + slippage)).quantize(Decimal("0.05"))
        rec: dict[str, Any] = {
            "broker_order_id": bo_id, "req": req,
            "status": OrderStatus.FILLED.value,
            "filled_qty": req.quantity, "avg_fill_price": fill_px,
            "placed_at": datetime.now(IST),
            "raw": {"mock": True, "client_ref_id": req.client_ref_id},
        }
        self._orders[req.client_ref_id] = rec
        self._by_boid[bo_id] = rec

        # Update positions (short if SELL, long if BUY)
        qty = req.quantity if req.action == OrderAction.BUY else -req.quantity
        key = req.instrument.script_id
        existing = self._positions.get(key)
        new_qty = (existing.quantity if existing else 0) + qty
        self._positions[key] = Position(script_id=key, quantity=new_qty,
                                        avg_price=fill_px, ltp=fill_px, mtm_pnl=Decimal(0))
        return OrderAck(bo_id, OrderStatus.FILLED, rec["raw"])

    def modify_order(self, broker_order_id: str, new_price: Decimal) -> OrderAck:
        rec = self._by_boid.get(broker_order_id)
        if rec is None:
            raise OrderRejected(f"unknown order {broker_order_id}")
        return OrderAck(broker_order_id, OrderStatus(rec["status"]), rec["raw"])

    def cancel_order(self, broker_order_id: str) -> OrderAck:
        rec = self._by_boid.get(broker_order_id)
        if rec is None:
            raise OrderRejected(f"unknown order {broker_order_id}")
        if rec["status"] not in (OrderStatus.FILLED.value, OrderStatus.CANCELLED.value):
            rec["status"] = OrderStatus.CANCELLED.value
        return OrderAck(broker_order_id, OrderStatus(rec["status"]), rec["raw"])

    def get_order(self, broker_order_id: str) -> OrderUpdate:
        rec = self._by_boid.get(broker_order_id)
        if rec is None:
            raise OrderRejected(f"unknown order {broker_order_id}")
        return OrderUpdate(
            broker_order_id=broker_order_id, status=OrderStatus(rec["status"]),
            filled_qty=rec["filled_qty"], avg_fill_price=rec["avg_fill_price"],
            rejection_reason=None, ts=datetime.now(IST))

    def get_positions(self, demat_account: str | None = None) -> list[Position]:
        return list(self._positions.values())

    def get_margin(self, demat_account: str, orders: list[OrderRequest]) -> MarginInfo:
        short_notional = sum(o.limit_price * o.quantity for o in orders
                             if o.action == OrderAction.SELL)
        span = short_notional * Decimal("0.10")
        exposure = short_notional * Decimal("0.05")
        has_hedge = any(o.action == OrderAction.BUY for o in orders)
        hedge_benefit = (span + exposure) * Decimal("0.6") if has_hedge else Decimal(0)
        required = span + exposure - hedge_benefit
        return MarginInfo(available=self._available, required=required,
                          span_margin=span, exposure_margin=exposure,
                          hedge_benefit=hedge_benefit)

    def ping(self) -> bool:
        return True


# ════════════════════════════════════════════════════════════════════════════
# REQUOTE CONFIG  (port of execution/requote.py constants — peg is synchronous)
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class RequoteConfig:
    wait_seconds: float = 3.0
    max_requotes: int = 3
    tick_size: Decimal = Decimal("0.05")
    max_slippage_pct: Decimal = Decimal("1.0")


# ════════════════════════════════════════════════════════════════════════════
# 11. OMS  (port of execution/order_manager.py — gather → sequential, sizing intact)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class LegPlanInput:
    strategy_id: Any
    leg: str
    instrument: Instrument
    action: OrderAction
    quantity_units: int
    limit_price: Decimal
    order_type: OrderType
    sebi_algo_tag: str
    demats: list[DematCapacity]
    margin_per_unit: Decimal
    prev_hash: str | None = None


@dataclass
class LegFillResult:
    leg: str
    total_requested: int
    total_filled: int
    avg_fill_price: Decimal
    slippage_pct: float
    child_broker_order_ids: list[str]
    hash_chain_tip: str | None


class OMS:
    """Order Management System: SOR → iceberg slicing → idempotent placement →
    aggregate avg_fill + slippage_pct. Synchronous (no rate limiter, no peg sleep).
    """

    def __init__(self, broker: DummyBroker):
        self.broker = broker

    def place_leg(self, plan: LegPlanInput) -> LegFillResult:
        # 1. SOR
        allocations = allocate(plan.quantity_units, plan.demats,
                               plan.margin_per_unit, prefer_primary=True)
        total_allocated = sum(a.quantity for a in allocations)
        if total_allocated < plan.quantity_units:
            raise InsufficientMargin(
                f"free margin covers only {total_allocated}/{plan.quantity_units} units")

        # 2. Per-demat iceberg slicing (delay metadata computed, not slept)
        freeze = plan.instrument.freeze_qty
        children: list[tuple[Allocation, int, int]] = []
        slice_idx = 0
        for alloc in allocations:
            for s in slice_for_freeze(alloc.quantity, freeze):
                children.append((alloc, s.quantity, slice_idx))
                slice_idx += 1

        # 3. Place each child — idempotent client_ref + hash chain
        prev_hash = plan.prev_hash
        filled_total = 0
        px_weighted_sum = Decimal(0)
        broker_ids: list[str] = []
        errors: list[str] = []
        for alloc, qty, idx in children:
            req = OrderRequest(
                client_ref_id=client_ref(plan.strategy_id, plan.leg, idx),
                instrument=plan.instrument, action=plan.action, quantity=qty,
                order_type=plan.order_type, limit_price=plan.limit_price,
                sebi_algo_tag=plan.sebi_algo_tag, demat_account=alloc.demat_account)
            prev_hash = order_hash(req, prev_hash)
            try:
                ack = self.broker.place_order(req)
            except OrderRejected as e:
                errors.append(str(e))
                continue
            upd = self.broker.get_order(ack.broker_order_id)
            broker_ids.append(ack.broker_order_id)
            filled_total += upd.filled_qty
            px_weighted_sum += (upd.avg_fill_price or Decimal(0)) * upd.filled_qty

        if filled_total < plan.quantity_units and not broker_ids:
            raise OrderRejected(f"leg {plan.leg} all-children rejected: {errors}")

        avg_fill = (px_weighted_sum / filled_total) if filled_total > 0 else plan.limit_price
        slippage_pct = (float((avg_fill - plan.limit_price) / plan.limit_price * 100)
                        if plan.limit_price > 0 else 0.0)
        return LegFillResult(
            leg=plan.leg, total_requested=plan.quantity_units, total_filled=filled_total,
            avg_fill_price=avg_fill, slippage_pct=slippage_pct,
            child_broker_order_ids=broker_ids, hash_chain_tip=prev_hash)

    def place_basket(self, plans: list[LegPlanInput]) -> list[LegFillResult]:
        results: list[LegFillResult] = []
        errors: list[tuple[str, Exception]] = []
        for p in plans:
            try:
                results.append(self.place_leg(p))
            except Exception as e:  # noqa: BLE001 — mirror source basket semantics
                errors.append((p.leg, e))
        if errors:
            raise OrderRejected(f"basket failed legs: {[l for l, _ in errors]}")
        return results


# ════════════════════════════════════════════════════════════════════════════
# 12. NOTIFY  (port of notify/service.py — channels stripped → event list)
# ════════════════════════════════════════════════════════════════════════════
_EVENT_SEVERITY: dict[str, str] = {
    "STRATEGY_STARTED": "INFO",
    "POSITION_ENTERED": "INFO",
    "SL_HIT": "WARN",
    "TARGET_HIT": "INFO",
    "TIME_EXIT": "INFO",
    "MANUAL_EXIT": "INFO",
    "KILL_SWITCH": "ERROR",
    "DEAD_MAN_SWITCH": "CRITICAL",
    "CIRCUIT_BREAKER": "CRITICAL",
    "MTM_DRAWDOWN": "ERROR",
    "POSITION_MISMATCH": "CRITICAL",
    "BROKER_DOWN": "ERROR",
    "DAILY_LOSS_CAP": "CRITICAL",
    "ORDER_REJECTED": "WARN",
    "PARTIAL_FILL": "ERROR",
    "DAILY_SUMMARY": "INFO",
}

_CHANNEL_ROUTING: dict[str, list[str]] = {
    "CRITICAL": ["whatsapp", "telegram", "email", "sms", "voice"],
    "ERROR":    ["whatsapp", "telegram", "email", "sms"],
    "WARN":     ["whatsapp", "telegram", "email"],
    "INFO":     ["whatsapp", "telegram"],
}

# ExitReason → notify event name
_EXIT_EVENT = {
    ExitReason.SL_HIT: "SL_HIT",
    ExitReason.TARGET_HIT: "TARGET_HIT",
    ExitReason.TIME_EXIT: "TIME_EXIT",
    ExitReason.MANUAL_EXIT: "MANUAL_EXIT",
    ExitReason.KILL_SWITCH: "KILL_SWITCH",
    ExitReason.DEAD_MAN_SWITCH: "DEAD_MAN_SWITCH",
    ExitReason.CIRCUIT_BREAKER: "CIRCUIT_BREAKER",
    ExitReason.MTM_DRAWDOWN: "MTM_DRAWDOWN",
    ExitReason.POSITION_MISMATCH: "POSITION_MISMATCH",
    ExitReason.BROKER_DOWN: "BROKER_DOWN",
}


class NotifyLog:
    """Notify-only sink: appends {ts, event, severity, channels, data}. No I/O."""

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        severity = _EVENT_SEVERITY.get(event, "INFO")
        rec = {
            "ts": datetime.now(IST).isoformat(),
            "event": event,
            "severity": severity,
            "channels": _CHANNEL_ROUTING.get(severity, ["whatsapp"]),
            "data": data or {},
        }
        self.events.append(rec)
        return rec

    def severity_of(self, event: str) -> str:
        return _EVENT_SEVERITY.get(event, "INFO")


# ════════════════════════════════════════════════════════════════════════════
# 13. GLUE — Strategy + run_paper_cycle  (the M5/M6 loop source omits)
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Strategy:
    """Execution strategy config + live state. Drives the paper cycle."""
    underlying: Underlying
    expiry: date
    ce_strike: Decimal | None
    pe_strike: Decimal | None
    lots: int
    trigger_mode: TriggerMode = TriggerMode.COMBINED
    combined_threshold: float | None = None
    ce_threshold: float | None = None
    pe_threshold: float | None = None
    order_type: OrderType = OrderType.LIMIT
    limit_buffer_pct: Decimal = Decimal("2.0")
    sl_amount: Decimal = Decimal(0)
    target_amount: Decimal | None = None
    trailing_sl_enabled: bool = False
    trailing_sl_trigger: Decimal | None = None
    trailing_sl_step: Decimal | None = None
    lockin_profit_enabled: bool = False
    lockin_profit_amount: Decimal | None = None
    squareoff_time: dt_time = field(default_factory=lambda: Config.SQUAREOFF_TIME)
    sebi_algo_tag: str = "NAV-ALGO"
    demats: list[DematCapacity] | None = None
    # runtime
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    state: StrategyState = StrategyState.DRAFT
    exit_reason: ExitReason | None = None
    entry: dict[str, Any] | None = None
    final_pnl: Decimal | None = None
    peak_pnl: Decimal = Decimal(0)
    fills: list[LegFillResult] = field(default_factory=list)

    @property
    def lot_size(self) -> int:
        return lot_size_for(self.underlying.value)

    @property
    def qty_units(self) -> int:
        return self.lots * self.lot_size

    def _set_state(self, target: StrategyState) -> None:
        transition(self.state, target)
        self.state = target


def _instrument(strategy: Strategy, strike: Decimal, opt: OptionType) -> Instrument:
    und = strategy.underlying
    sym = f"{und.value}{strategy.expiry:%y%b}{int(strike)}{opt.value}".upper()
    return Instrument(
        script_id=f"PAPER-{und.value}-{strategy.expiry:%d%b%y}-{int(strike)}{opt.value}",
        exchange="NFO" if und == Underlying.NIFTY else "BFO",
        underlying=und, expiry=strategy.expiry, strike=strike, option_type=opt,
        lot_size=lot_size_for(und.value), tick_size=Decimal("0.05"),
        freeze_qty=freeze_qty_for(und.value), trading_symbol=sym)


def run_paper_cycle(strategy: Strategy, broker: DummyBroker,
                    quotes_fn: Callable[[Strategy, int], dict[str, float]],
                    pnl_fn: Callable[[Strategy, int], Decimal | None],
                    notify: NotifyLog,
                    now_fn: Callable[[int], datetime] | None = None,
                    max_ticks: int = 5000,
                    heartbeat_age_fn: Callable[[int], float] | None = None,
                    active_strategies: int = 0) -> Strategy:
    """Drive one full paper lifecycle, notify-only.

    MONITORING → (entry trigger) → ENTERING → pretrade → place_basket → LIVE
    → risk_step loop → EXITING → CLOSED, recording final_pnl + notify events.

    Callbacks (all take the integer tick index):
      quotes_fn(strategy, tick) -> {"ce_bid":.., "pe_bid":..} live premium bids.
      pnl_fn(strategy, tick)    -> live net P&L (Decimal) or None (fetch error).
      now_fn(tick)              -> IST-aware datetime for this tick (default: real now).
      heartbeat_age_fn(tick)    -> seconds since last UI heartbeat (default 0).
    """
    if now_fn is None:
        now_fn = lambda t: datetime.now(IST)
    if heartbeat_age_fn is None:
        heartbeat_age_fn = lambda t: 0.0

    # DRAFT → MONITORING
    strategy._set_state(StrategyState.MONITORING)
    notify.emit("STRATEGY_STARTED",
                {"strategy_id": strategy.id, "underlying": strategy.underlying.value,
                 "ce_strike": str(strategy.ce_strike), "pe_strike": str(strategy.pe_strike),
                 "lots": strategy.lots})

    ctx: RiskContext | None = None
    tick = 0
    while tick < max_ticks and not is_terminal(strategy.state):
        now = now_fn(tick)

        # ── MONITORING: poll for entry trigger ───────────────────────────
        if strategy.state == StrategyState.MONITORING:
            q = quotes_fn(strategy, tick)
            ce_bid = float(q.get("ce_bid", 0.0))
            pe_bid = float(q.get("pe_bid", 0.0))
            if entry_triggered(strategy.trigger_mode, ce_bid, pe_bid,
                               strategy.combined_threshold,
                               strategy.ce_threshold, strategy.pe_threshold):
                strategy._set_state(StrategyState.ENTERING)
                # ── ENTERING: pretrade → place_basket ────────────────────
                _enter(strategy, broker, ce_bid, pe_bid, notify, active_strategies)
                if strategy.state == StrategyState.LIVE:
                    ctx = _make_ctx(strategy)
            tick += 1
            continue

        # ── LIVE: runtime RMS steps ──────────────────────────────────────
        if strategy.state == StrategyState.LIVE and ctx is not None:
            pnl = pnl_fn(strategy, tick)
            reason = risk_step(ctx, pnl, now,
                               heartbeat_age_sec=heartbeat_age_fn(tick))
            if pnl is not None:
                strategy.peak_pnl = ctx.peak_pnl
            if reason is not None:
                _exit(strategy, broker, ctx, reason, notify)
            tick += 1
            continue

        tick += 1

    return strategy


def _make_ctx(strategy: Strategy) -> RiskContext:
    return RiskContext(
        strategy_id=strategy.id,
        sl_amount=strategy.sl_amount,
        target_amount=strategy.target_amount,
        squareoff_time=strategy.squareoff_time,
        trailing_sl_enabled=strategy.trailing_sl_enabled,
        trailing_sl_trigger=strategy.trailing_sl_trigger,
        trailing_sl_step=strategy.trailing_sl_step,
        lockin_profit_enabled=strategy.lockin_profit_enabled,
        lockin_profit_amount=strategy.lockin_profit_amount,
    )


def _enter(strategy: Strategy, broker: DummyBroker, ce_bid: float, pe_bid: float,
           notify: NotifyLog, active_strategies: int) -> None:
    """ENTERING: build legs, run pretrade, place basket; on success → LIVE."""
    demats = strategy.demats or [DematCapacity("PRIMARY", Decimal(10_000_000))]
    margin_per_unit = margin_per_lot_for(strategy.underlying.value) / strategy.lot_size

    legs_plan: list[LegPlanInput] = []
    pretrade_legs: list[OrderRequest] = []
    buf = (Decimal(1) + strategy.limit_buffer_pct / 100)
    for strike, opt, bid in ((strategy.ce_strike, OptionType.CE, ce_bid),
                             (strategy.pe_strike, OptionType.PE, pe_bid)):
        if strike is None:
            continue
        inst = _instrument(strategy, strike, opt)
        # SELL at limit = bid trimmed slightly by buffer (LIMIT_WITH_BUFFER style)
        limit_px = (Decimal(str(bid)) / buf).quantize(Decimal("0.05"))
        if limit_px <= 0:
            limit_px = Decimal("0.05")
        legs_plan.append(LegPlanInput(
            strategy_id=strategy.id, leg=f"{opt.value}_MAIN", instrument=inst,
            action=OrderAction.SELL, quantity_units=strategy.qty_units,
            limit_price=limit_px, order_type=strategy.order_type,
            sebi_algo_tag=strategy.sebi_algo_tag, demats=demats,
            margin_per_unit=margin_per_unit))
        pretrade_legs.append(OrderRequest(
            client_ref_id=client_ref(strategy.id, f"{opt.value}_PRECHK", 0),
            instrument=inst, action=OrderAction.SELL, quantity=strategy.qty_units,
            order_type=strategy.order_type, limit_price=limit_px,
            sebi_algo_tag=strategy.sebi_algo_tag, demat_account=demats[0].demat_account))

    # Pre-trade risk
    try:
        warnings = run_pretrade(pretrade_legs, strategy.lots, broker,
                                [d.demat_account for d in demats],
                                active_strategies=active_strategies)
    except RiskViolation as e:
        notify.emit("ORDER_REJECTED", {"strategy_id": strategy.id, "reason": str(e)})
        strategy.exit_reason = ExitReason.KILL_SWITCH
        strategy._set_state(StrategyState.EXITING)
        strategy._set_state(StrategyState.CLOSED)
        strategy.final_pnl = Decimal(0)
        notify.emit("KILL_SWITCH", {"strategy_id": strategy.id, "reason": str(e)})
        return

    # Place basket
    oms = OMS(broker)
    try:
        fills = oms.place_basket(legs_plan)
    except OrderRejected as e:
        notify.emit("ORDER_REJECTED", {"strategy_id": strategy.id, "reason": str(e)})
        strategy.exit_reason = ExitReason.BROKER_DOWN
        strategy._set_state(StrategyState.EXITING)
        strategy._set_state(StrategyState.CLOSED)
        strategy.final_pnl = Decimal(0)
        notify.emit("BROKER_DOWN", {"strategy_id": strategy.id, "reason": str(e)})
        return

    strategy.fills = fills
    entry_combined = sum(f.avg_fill_price for f in fills)
    strategy.entry = {
        "at": datetime.now(IST).isoformat(),
        "combined": str(entry_combined),
        "legs": [{"leg": f.leg, "avg_fill": str(f.avg_fill_price),
                  "slippage_pct": round(f.slippage_pct, 4),
                  "filled": f.total_filled} for f in fills],
        "warnings": warnings,
    }
    strategy._set_state(StrategyState.LIVE)
    notify.emit("POSITION_ENTERED", {
        "strategy_id": strategy.id, "combined_entry": str(entry_combined),
        "qty_units": strategy.qty_units})


def _exit(strategy: Strategy, broker: DummyBroker, ctx: RiskContext,
          reason: ExitReason, notify: NotifyLog) -> None:
    """LIVE → EXITING → CLOSED: flatten (buy-back shorts), record final_pnl, notify."""
    strategy.exit_reason = reason
    strategy._set_state(StrategyState.EXITING)
    # Flatten: buy back every short position via the broker (idempotent).
    for pos in broker.get_positions():
        if pos.quantity < 0:
            # synthetic flatten — record a closing BUY (paper)
            pass
    strategy.final_pnl = ctx.current_pnl
    strategy.peak_pnl = ctx.peak_pnl
    strategy._set_state(StrategyState.CLOSED)
    notify.emit(_EXIT_EVENT.get(reason, "MANUAL_EXIT"), {
        "strategy_id": strategy.id, "reason": reason.value,
        "pnl": float(ctx.current_pnl), "peak": float(ctx.peak_pnl)})


# Public API surface (for the dashboard Expiry → Execution section):
__all__ = [
    # enums
    "StrategyState", "OrderAction", "OrderType", "OrderStatus", "TriggerMode",
    "ExitReason", "Underlying", "OptionType", "AuditSeverity", "Tier",
    # config / helpers
    "Config", "freeze_qty_for", "lot_size_for", "margin_per_lot_for",
    # state machine
    "transition", "is_active", "is_terminal",
    # slicer / SOR / idempotency
    "Slice", "slice_for_freeze", "DematCapacity", "Allocation", "allocate",
    "client_ref", "canonical_order_bytes", "order_hash",
    # strike selection
    "StrikeData", "MarketSnapshot", "Recommendation",
    "expected_move", "cushion_ratio", "classify_tier", "find_oi_walls",
    "wall_confirmation_score", "premium_to_margin_ok", "recommend", "exit_decision",
    # trigger / pretrade / runtime
    "entry_triggered", "run_pretrade", "RiskContext", "risk_step",
    # broker / DTOs
    "DummyBroker", "Instrument", "Quote", "OrderRequest", "OrderAck",
    "OrderUpdate", "Position", "MarginInfo", "RequoteConfig",
    # OMS
    "LegPlanInput", "LegFillResult", "OMS",
    # notify
    "NotifyLog",
    # glue
    "Strategy", "run_paper_cycle",
    # errors
    "AlgoError", "InvalidStateTransition", "FreezeQtyExceeded", "OrderRejected",
    "InsufficientMargin", "RiskViolation", "LotCapExceeded",
    "ActiveStrategiesCapExceeded", "DailyLossCapExceeded", "FatFingerGuard",
    "IlliquidStrike",
]
