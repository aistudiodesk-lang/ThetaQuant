"""
lib/playbook.py — single source of truth for STRATEGY_LIVE §9W rules.

Used by:
  - dashboard/server.py    → /api/playbook/* JSON for web UI
  - scripts/telegram_bot.py → markdown messages for chat

Both surfaces must call the SAME functions here. Web renders cards/tables,
Telegram renders text. The logic itself is shared.

Backtest results baked in:
  Source: STRATEGY_LIVE.md §9W (locked 2026-06-04)
  Backtests: analyses/018-024 on 56 NIFTY + 54 SENSEX E-0 days
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# ─── Instrument config ─────────────────────────────────────────────────────────
INSTRUMENT_CFG = {
    "NIFTY":     {"grid": 50,  "lot": 75, "lots_per_cr": 43, "median_morning_range": 0.51},
    "SENSEX":    {"grid": 100, "lot": 20, "lots_per_cr": 40, "median_morning_range": 0.85},
    "BANKNIFTY": {"grid": 100, "lot": 15, "lots_per_cr": 67, "median_morning_range": 0.70},
}


# ─── Regime classification (per analysis 025) ──────────────────────────────────
def classify_regime(snapshot: dict) -> str:
    """Bucket today into: calm_green | normal | moderate | high_risk.

    Used by Tier 1 distance recommender (analysis 025).
    """
    vix = snapshot.get("vix") or 0
    max_gap = 0.0; max_range = 0.0; max_move = 0.0
    for inst in ("NIFTY", "SENSEX"):
        d = snapshot.get(inst, {})
        gap = abs(d.get("gap_pct") or 0)
        rng = d.get("pre_range_pct") or d.get("day_range_pct") or 0
        mv  = abs(d.get("pre_move_pct") or d.get("change_pct") or 0)
        max_gap = max(max_gap, gap); max_range = max(max_range, rng); max_move = max(max_move, mv)
    if max_gap > 0.7 or max_range > 1.0 or vix > 18:
        return "high_risk"
    if max_gap > 0.4 or max_range > 0.7 or vix > 15:
        return "moderate"
    if max_gap <= 0.3 and max_range <= 0.5 and vix <= 13:
        return "calm_green"
    return "normal"


# ─── Tier 1 distance lookup (locked from analysis 025, 119-day backtest) ──────
# Rule: 100% win rate in backtest at this distance + 0.25% contingency on high_risk.
# Floor: never closer than 2.0% on Tier 1.
TIER1_DISTANCE = {
    "calm_green": {"NIFTY": 2.0,  "SENSEX": 2.0},
    "normal":     {"NIFTY": 2.0,  "SENSEX": 2.0},
    "moderate":   {"NIFTY": 2.0,  "SENSEX": 2.0},
    "high_risk":  {"NIFTY": 2.25, "SENSEX": 2.25},
}

# Expected premium per regime × distance (from analysis 025, ₹/Cr median)
TIER1_EXPECTED_PREMIUM = {
    "calm_green": {"NIFTY": {2.0: 6530,  2.25: 5805}, "SENSEX": {2.0: 3560, 2.25: 3100}},
    "normal":     {"NIFTY": {2.0: 7901,  2.25: 7256}, "SENSEX": {2.0: 4560, 2.25: 3880}},
    "moderate":   {"NIFTY": {2.0: 10562, 2.25: 9030}, "SENSEX": {2.0: 5120, 2.25: 4440}},
    "high_risk":  {"NIFTY": {2.0: 16125, 2.25: 12739},"SENSEX": {2.0: 6420, 2.25: 5520}},
}


def tier1_distance(regime: str, instrument: str) -> float:
    """Returns the safe Tier 1 distance (% OTM) for given regime + instrument."""
    return TIER1_DISTANCE.get(regime, {}).get(instrument, 2.25)


def tier1_expected_premium(regime: str, instrument: str) -> float:
    """Returns expected median premium ₹/Cr at the recommended distance."""
    dist = tier1_distance(regime, instrument)
    return TIER1_EXPECTED_PREMIUM.get(regime, {}).get(instrument, {}).get(dist, 0)


# ─── Deep OTM entry timing (analysis 027, locked 11-Jun-2026) ──────────────────
# Settlement-captured premium by entry time. NIFTY calm_green: 10:00 (0 breach,
# beats 09:30). Everything else: 09:25-09:35. PLUS universal spike-limit overlay.
TIER1_ENTRY_TIME = {
    "calm_green": {"NIFTY": "10:00",       "SENSEX": "09:25-09:35"},
    "normal":     {"NIFTY": "09:25-09:35", "SENSEX": "09:25-09:35"},
    "moderate":   {"NIFTY": "09:25-09:35", "SENSEX": "09:25-09:35"},
    "high_risk":  {"NIFTY": "09:25-09:35", "SENSEX": "09:25-09:35"},
}
SPIKE_LIMIT_RULE = ("Split entry: ~65% at the entry window; immediately place standing "
                    "SELL LIMITs on the SAME strikes at 1.3x your fill for the other ~35%. "
                    "Spike fills occur on ~14% of days at ~1.7x capture (analysis 027). "
                    "Unfilled limits -> market at 11:30.")


def tier1_entry_time(regime: str, instrument: str) -> str:
    return TIER1_ENTRY_TIME.get(regime, {}).get(instrument, "09:25-09:35")


# ─── Hard exclusions (Layer 1) ─────────────────────────────────────────────────
def hard_exclusions(snapshot: dict) -> list[str]:
    """Check the 7-flag STOP test. Returns list of flags that fired."""
    excl = []
    vix = snapshot.get("vix") or 0
    if vix > 19:
        excl.append(f"VIX {vix} > 19")
    for inst in ("NIFTY", "SENSEX"):
        d = snapshot.get(inst, {})
        gap = d.get("gap_pct")
        if gap is not None and abs(gap) > 0.7:
            excl.append(f"{inst} gap {gap}% > 0.7%")
        rng = d.get("pre_range_pct") or d.get("day_range_pct")
        if rng is not None and rng > 1.0:
            excl.append(f"{inst} range {rng}% > 1.0%")
    return excl


# ─── Tier definitions ──────────────────────────────────────────────────────────
# Each entry: { id, label, instrument, otm_pct, entry_time, range_max,
#               premium_floor_per_cr, exit, mean_pcr, win_pct, worst_pcr, star }
TIER_SETUPS = [
    # ★ STAR
    {"id":"sensex_1_0_1000", "label":"SENSEX 1.0% OTM @ 10:00 ★ STAR", "instrument":"SENSEX",
     "tier":3, "otm_pct":1.0, "entry_time":"10:00", "range_max":0.7, "premium_floor_per_cr":20000,
     "exit":"HOLD", "mean_pcr":47000, "win_pct":100, "worst_pcr":20000, "star":True},
    # Tier 3 — NIFTY 0.7%
    {"id":"nifty_0_7_1030", "label":"NIFTY 0.7% OTM @ 10:30", "instrument":"NIFTY",
     "tier":3, "otm_pct":0.7, "entry_time":"10:30", "range_max":0.4, "premium_floor_per_cr":30000,
     "exit":"HOLD", "mean_pcr":45000, "win_pct":100, "worst_pcr":32000, "star":False},
    {"id":"nifty_0_5_1030", "label":"NIFTY 0.5% OTM @ 10:30 (tight)", "instrument":"NIFTY",
     "tier":3, "otm_pct":0.5, "entry_time":"10:30", "range_max":0.4, "premium_floor_per_cr":40000,
     "exit":"HOLD", "mean_pcr":53000, "win_pct":92, "worst_pcr":-43000, "star":False},
    {"id":"sensex_0_7_1130", "label":"SENSEX 0.7% OTM @ 11:30", "instrument":"SENSEX",
     "tier":3, "otm_pct":0.7, "entry_time":"11:30", "range_max":0.8, "premium_floor_per_cr":30000,
     "exit":"HOLD", "mean_pcr":42000, "win_pct":100, "worst_pcr":4000, "star":False},
    # Tier 2 — NIFTY
    {"id":"nifty_1_25_1130", "label":"NIFTY 1.25% OTM @ 11:30", "instrument":"NIFTY",
     "tier":2, "otm_pct":1.25, "entry_time":"11:30", "range_max":0.7, "premium_floor_per_cr":15000,
     "exit":"HOLD", "mean_pcr":21000, "win_pct":100, "worst_pcr":14000, "star":False},
    {"id":"nifty_1_5_1100", "label":"NIFTY 1.5% OTM @ 11:00", "instrument":"NIFTY",
     "tier":2, "otm_pct":1.5, "entry_time":"11:00", "range_max":0.7, "premium_floor_per_cr":12500,
     "exit":"HOLD", "mean_pcr":17000, "win_pct":100, "worst_pcr":12000, "star":False},
    {"id":"nifty_2_0_0945", "label":"NIFTY 2.0% OTM @ 09:45", "instrument":"NIFTY",
     "tier":2, "otm_pct":2.0, "entry_time":"09:45", "range_max":1.0, "premium_floor_per_cr":8000,
     "exit":"HOLD", "mean_pcr":16000, "win_pct":100, "worst_pcr":8000, "star":False},
    # Tier 2 — SENSEX
    {"id":"sensex_1_25_1000", "label":"SENSEX 1.25% OTM @ 10:00", "instrument":"SENSEX",
     "tier":2, "otm_pct":1.25, "entry_time":"10:00", "range_max":0.8, "premium_floor_per_cr":15000,
     "exit":"HOLD", "mean_pcr":32000, "win_pct":100, "worst_pcr":400, "star":False},
    {"id":"sensex_1_5_1000", "label":"SENSEX 1.5% OTM @ 10:00", "instrument":"SENSEX",
     "tier":2, "otm_pct":1.5, "entry_time":"10:00", "range_max":1.0, "premium_floor_per_cr":12500,
     "exit":"HOLD", "mean_pcr":30000, "win_pct":100, "worst_pcr":1000, "star":False},
    {"id":"sensex_2_0_0945", "label":"SENSEX 2.0% OTM @ 09:45", "instrument":"SENSEX",
     "tier":2, "otm_pct":2.0, "entry_time":"09:45", "range_max":1.0, "premium_floor_per_cr":8000,
     "exit":"HOLD", "mean_pcr":17000, "win_pct":93, "worst_pcr":-200, "star":False},
    # Tier 4 — midday KICKER v2 (analysis 034; supersedes 033's rare-gate recipe).
    # Trades EVERY NIFTY expiry — no range gate. Instead the STRIKES ADAPT to the day:
    # distance = 1.0 × the range-so-far at 12:00 (clamped 0.3–1.5%), so a volatile
    # morning sells wider automatically. TP = buy back at 40% premium capture; SL 2×
    # entry (fill at market, not the level); else carry to 15:20. Backtest (56 days):
    # +₹24,138/Cr mean · 84% win · TP hits 82% · p5 −₹87k · worst −₹204k (inside the
    # −₹3.5L/Cr cap) · 4/5 quarters positive. Worst days = calm-noon strikes then a
    # violent afternoon → the SL is NOT optional. SENSEX: every variant decayed to
    # negative in the latest quarter — SKIP the kicker on SENSEX (034).
    {"id":"nifty_kicker_v2_1200", "label":"NIFTY KICKER v2 @ 12:00 · strikes = 1.0× range-so-far (0.3–1.5%)",
     "instrument":"NIFTY", "tier":4, "otm_pct":1.0, "entry_time":"12:00", "range_max":9.9,
     "premium_floor_per_cr":10000,
     "exit":"TP 40% capture → SQUARE OFF · SL 2× (market) · else carry to 15:20 · SIZE ₹1.5–2 Cr (book-level zero-loss standard)",
     "mean_pcr":24138, "win_pct":84, "worst_pcr":-204196, "star":False},
]


def qualifying_tiers(snapshot: dict) -> list[dict]:
    """Return tier setups that pass current regime."""
    excl = hard_exclusions(snapshot)
    if excl:
        return []  # only Tier 1 today
    out = []
    for setup in TIER_SETUPS:
        inst = setup["instrument"]
        d = snapshot.get(inst, {})
        rng = d.get("pre_range_pct") or d.get("day_range_pct") or 0
        qualifies = rng <= setup["range_max"]
        out.append({**setup,
                    "qualifies": qualifies,
                    "reason_passed": (f"pre-range {rng}% ≤ {setup['range_max']}%"
                                      if qualifies else
                                      f"pre-range {rng}% > {setup['range_max']}% — wait")})
    return out


# ─── Strike + premium math ─────────────────────────────────────────────────────
def nearest_strike(spot: float, otm_pct: float, side: str, grid: int) -> int:
    """Round to nearest strike. PE goes below spot, CE goes above."""
    if side == "PE":
        target = spot * (1 - otm_pct / 100)
    else:
        target = spot * (1 + otm_pct / 100)
    return int(round(target / grid) * grid)


def premium_to_per_cr(combined_premium: float, instrument: str) -> float:
    """Convert ₹/share combined to ₹/Cr deployed."""
    cfg = INSTRUMENT_CFG.get(instrument, INSTRUMENT_CFG["NIFTY"])
    return combined_premium * cfg["lot"] * cfg["lots_per_cr"]


# ─── Yellow/Red triggers ───────────────────────────────────────────────────────
def compute_triggers(instrument: str, entry_spot: float,
                     pe_strike: int, ce_strike: int,
                     pe_entry: float = None, ce_entry: float = None,
                     pre_entry_high: float = None) -> dict:
    """Compute all Yellow/Red/PT levels for a position.

    Returns dict with: yellow_pe_spot, yellow_ce_spot, red_pe_spot, red_ce_spot,
                       red_strike_pe, red_strike_ce, big_move_pts, profit_take_combined
    """
    pe_buffer = entry_spot - pe_strike
    ce_buffer = ce_strike - entry_spot
    cfg = INSTRUMENT_CFG.get(instrument, INSTRUMENT_CFG["NIFTY"])

    # 0.4% directional move (Yellow's "big move" condition)
    big_move_pts = round(entry_spot * 0.4 / 100)

    out = {
        "instrument": instrument,
        "entry_spot": entry_spot,
        "pe_strike": pe_strike, "ce_strike": ce_strike,
        "pe_buffer_pts": int(pe_buffer), "ce_buffer_pts": int(ce_buffer),
        "big_move_pts": big_move_pts,
        # Yellow: spot eats 50% of buffer (PE side or CE side)
        "yellow_pe_spot": round(entry_spot - 0.5 * pe_buffer),
        "yellow_ce_spot": (round(pre_entry_high * 1.001)
                           if pre_entry_high else
                           round(entry_spot + 0.5 * ce_buffer)),
        # Red: spot eats 85% of buffer (or crosses strike)
        "red_pe_spot": round(entry_spot - 0.85 * pe_buffer),
        "red_ce_spot": round(entry_spot + 0.85 * ce_buffer),
        "red_strike_pe": pe_strike,
        "red_strike_ce": ce_strike,
        "lot_size": cfg["lot"],
        "lots_per_cr": cfg["lots_per_cr"],
    }

    if pe_entry is not None and ce_entry is not None:
        combined_entry = pe_entry + ce_entry
        out["combined_entry"] = round(combined_entry, 2)
        out["combined_per_cr"] = round(premium_to_per_cr(combined_entry, instrument), 0)
        # Profit-take at 30% of entry premium remaining (= 70% decay captured)
        out["profit_take_combined"] = round(combined_entry * 0.30, 2)
        out["profit_take_decay_pct"] = 70

    return out


# ─── Expiry calendar ───────────────────────────────────────────────────────────
def expiring_today() -> list[str]:
    """Return list of instruments whose expiry is today.

    Rules:
      Tuesday weekly → NIFTY
      Thursday weekly → SENSEX
      Last Tuesday of month → NIFTY (monthly) + BANKNIFTY (monthly)
    """
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parent.parent))
    from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES
    today = datetime.now(IST).date()
    insts = []
    if today in NIFTY_WEEKLY_EXPIRIES:
        insts.append("NIFTY")
    if today in SENSEX_WEEKLY_EXPIRIES:
        insts.append("SENSEX")
    # Last Tuesday = also BANKNIFTY monthly
    if today.weekday() == 1:  # Tuesday
        next_tuesday = today + timedelta(days=7)
        if next_tuesday.month != today.month:
            if "BANKNIFTY" not in insts:
                insts.append("BANKNIFTY")
    return insts


def next_expiries() -> dict:
    """Returns {instrument: (date, days_away)} for next expiry per instrument."""
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parent.parent))
    from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES
    today = datetime.now(IST).date()
    out = {}
    nn = next((e for e in NIFTY_WEEKLY_EXPIRIES if e >= today), None)
    ns = next((e for e in SENSEX_WEEKLY_EXPIRIES if e >= today), None)
    if nn: out["NIFTY"] = (nn, (nn - today).days)
    if ns: out["SENSEX"] = (ns, (ns - today).days)
    return out


# ─── Premium floors by tier (for rec output) ───────────────────────────────────
TIER_PREMIUM_FLOORS = {
    1: {"label": "Tier 1 (Deep OTM ≥2.5%)", "floor_per_cr": 4000, "ideal_per_cr": 5000},
    2: {"label": "Tier 2 (Mid 1.25-2%)", "floor_per_cr": 12500, "ideal_per_cr": 20000},
    3: {"label": "Tier 3 (Near 0.5-1%)", "floor_per_cr": 20000, "ideal_per_cr": 35000},
}


# ─── Sizing constraints ───────────────────────────────────────────────────────
TIER_SIZING = {
    1: {"max_pct_book": 100, "label": "main book (per §2)"},
    2: {"max_pct_book": 30,  "label": "≤30% of book"},
    3: {"max_pct_book": 15,  "label": "≤15% of book"},
}
