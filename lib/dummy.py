"""
lib/dummy.py — Execution engine (dummy / paper). Ported feature-set from the
Theta Quant algo, WITHOUT broker order placement.

Lifecycle of an execution strategy:
  ARMED      → monitor live premium for the ENTRY trigger (criteria-based)
  ENTERED    → (dummy fill) monitor TP / SL triggers
  TP_HIT/SL_HIT/CLOSED/CANCELLED

Criteria the user sets (Theta-Gainers style):
  - mode: strangle (CE+PE) | ce | pe
  - distance_pct off spot → strikes computed (grid-rounded away from spot)
    (or explicit ce_strike / pe_strike)
  - trigger_mode: COMBINED (ce+pe ≥ threshold) | SEPARATE (ce≥X AND pe≥Y)
  - threshold expressed as premium (₹) OR yield_per_cr (auto-converts via margin)
  - lots
Exit (set anytime, before or after entry):
  - tp: combined premium ≤ value  (short decayed → take profit)
  - sl: combined premium ≥ value  (short spiked → stop loss)
    OR profit/loss in ₹ (computed from entry vs live)

These are SELL strategies (short premium): entry fires when premium is RICH
(≥ target); TP fires when premium has decayed; SL fires when premium spikes.

Storage: data/dummy_trades.jsonl (append-only; latest record per id wins).
"""
from __future__ import annotations
import json, uuid, math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "dummy_trades.jsonl"

# fallback margin per lot (₹) when not supplied — index option-sell ballpark
MARGIN_PER_LOT = {"NIFTY": 150000, "SENSEX": 160000, "BANKNIFTY": 180000}
GRID = {"NIFTY": 50, "SENSEX": 100, "BANKNIFTY": 100}
LOT = {"NIFTY": 75, "SENSEX": 20, "BANKNIFTY": 35}


def _append(obj):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def _load_raw():
    if not STORE.exists():
        return []
    out = []
    for line in STORE.read_text().splitlines():
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def all_strategies() -> list[dict]:
    by_id = {}
    for r in _load_raw():
        i = r.get("id")
        if not i:
            continue
        if r.get("type") == "delete":
            by_id.pop(i, None)
            continue
        cur = by_id.setdefault(i, {"events": []})
        push = r.pop("events_push", None)
        cur.update(r)
        if push:
            cur.setdefault("events", []).append(push)
    return sorted(by_id.values(), key=lambda x: x.get("created_at", ""), reverse=True)


def _now():
    return datetime.now(IST).isoformat()


# ── Strategy presets — save your 4-5 setups once, re-arm each expiry (E-1/E-0) ──
PRESETS = ROOT / "data" / "dummy_presets.json"
PRESET_KEYS = ("name", "instrument", "mode", "distance_pct", "ce_distance_pct",
               "pe_distance_pct", "lots", "trigger_mode", "combined_threshold",
               "ce_threshold", "pe_threshold", "yield_per_cr", "tp", "sl", "note",
               # scheduling: auto-arm at sched_start, auto-stop at sched_stop (HH:MM IST)
               "sched_start", "sched_stop", "sched_enabled", "sched_last_armed")


def list_presets() -> list[dict]:
    try:
        return json.loads(PRESETS.read_text()) if PRESETS.exists() else []
    except Exception:
        return []


def save_preset(cfg: dict) -> dict:
    rows = list_presets()
    p = {k: cfg.get(k) for k in PRESET_KEYS}
    p["name"] = p.get("name") or f"{(p.get('instrument') or 'NIFTY')} {p.get('mode','strangle')}"
    p["id"] = cfg.get("id") or uuid.uuid4().hex[:8]
    rows = [r for r in rows if r.get("id") != p["id"]]
    rows.append(p)
    PRESETS.parent.mkdir(parents=True, exist_ok=True)
    PRESETS.write_text(json.dumps(rows, indent=2))
    return p


def delete_preset(pid: str) -> bool:
    rows = list_presets()
    new = [r for r in rows if r.get("id") != pid]
    if len(new) == len(rows):
        return False
    PRESETS.write_text(json.dumps(new, indent=2))
    return True


def get_preset(pid: str) -> dict | None:
    return next((r for r in list_presets() if r.get("id") == pid), None)


def set_schedule(pid: str, start=None, stop=None, enabled=None) -> dict | None:
    """Set/clear a preset's auto-start/stop schedule (HH:MM IST)."""
    rows = list_presets()
    hit = None
    for r in rows:
        if r.get("id") == pid:
            if start is not None: r["sched_start"] = start or None
            if stop is not None: r["sched_stop"] = stop or None
            if enabled is not None: r["sched_enabled"] = bool(enabled)
            hit = r
    if hit:
        PRESETS.write_text(json.dumps(rows, indent=2))
    return hit


def set_runtime(pid: str, **kv) -> bool:
    """Patch runtime fields on a preset (e.g. sched_last_armed) without touching config."""
    rows = list_presets()
    hit = False
    for r in rows:
        if r.get("id") == pid:
            r.update(kv); hit = True
    if hit:
        PRESETS.write_text(json.dumps(rows, indent=2))
    return hit


def presets_due_to_start(now_hhmm: str, today: str) -> list[dict]:
    """Enabled presets whose start time has arrived today and haven't armed yet today."""
    out = []
    for p in list_presets():
        if not p.get("sched_enabled"):
            continue
        s = p.get("sched_start"); e = p.get("sched_stop")
        if not s or p.get("sched_last_armed") == today:
            continue
        if now_hhmm >= s and (not e or now_hhmm < e):
            out.append(p)
    return out


def strategies_due_to_stop(now_hhmm: str) -> list[dict]:
    """Live (ARMED/ENTERED) scheduled strategies whose stop time has passed."""
    return [s for s in all_strategies()
            if s.get("status") in ("ARMED", "ENTERED") and s.get("sched_stop")
            and now_hhmm >= s["sched_stop"]]


def grid_strikes(instrument: str, spot: float, distance_pct: float, mode: str,
                 ce_distance_pct: float = None, pe_distance_pct: float = None) -> dict:
    """Strikes away from spot, rounded to grid AWAY from spot. CE and PE can be
    DIFFERENT distances (e.g. CE 3% / PE 2%); each falls back to distance_pct."""
    g = GRID.get(instrument.upper(), 50)
    ce_d = ce_distance_pct if ce_distance_pct is not None else distance_pct
    pe_d = pe_distance_pct if pe_distance_pct is not None else distance_pct
    ce = pe = None
    if mode in ("strangle", "ce") and ce_d is not None:
        ce = int(math.ceil(spot * (1 + ce_d / 100) / g) * g)
    if mode in ("strangle", "pe") and pe_d is not None:
        pe = int(math.floor(spot * (1 - pe_d / 100) / g) * g)
    return {"ce_strike": ce, "pe_strike": pe}


def yield_per_cr(combined: float, instrument: str, margin_per_lot: float = None) -> float | None:
    """₹/Cr of margin from a per-lot combined premium."""
    if not combined:
        return None
    inst = instrument.upper()
    m = margin_per_lot or MARGIN_PER_LOT.get(inst, 150000)
    lot = LOT.get(inst, 75)
    return round(combined * lot / m * 1e7)


def premium_for_yield(target_per_cr: float, instrument: str, margin_per_lot: float = None) -> float | None:
    """Inverse: combined premium (₹/share) that yields target_per_cr."""
    if not target_per_cr:
        return None
    inst = instrument.upper()
    m = margin_per_lot or MARGIN_PER_LOT.get(inst, 150000)
    lot = LOT.get(inst, 75)
    return round(target_per_cr * m / lot / 1e7, 2)


def create(cfg: dict) -> dict:
    inst = (cfg.get("instrument") or "NIFTY").upper()
    rec = {
        "id": uuid.uuid4().hex[:10], "created_at": _now(),
        "name": cfg.get("name") or f"{inst} {cfg.get('mode','strangle')}",
        "instrument": inst, "mode": cfg.get("mode", "strangle"),
        "distance_pct": cfg.get("distance_pct"),
        "ce_distance_pct": cfg.get("ce_distance_pct"), "pe_distance_pct": cfg.get("pe_distance_pct"),
        "ce_strike": cfg.get("ce_strike"), "pe_strike": cfg.get("pe_strike"),
        "lots": int(cfg.get("lots") or 1),
        "trigger_mode": (cfg.get("trigger_mode") or "COMBINED").upper(),
        "combined_threshold": cfg.get("combined_threshold"),
        "ce_threshold": cfg.get("ce_threshold"), "pe_threshold": cfg.get("pe_threshold"),
        "yield_per_cr": cfg.get("yield_per_cr"),
        "tp": cfg.get("tp"), "sl": cfg.get("sl"),       # {mode, value}
        "note": cfg.get("note", ""),
        "sched_stop": cfg.get("sched_stop"), "sched_preset": cfg.get("sched_preset"),
        "status": "ARMED", "entry": None, "exit": None,
        "events": [{"at": _now(), "what": "armed"}],
    }
    _append(rec)
    return rec


def update(strategy_id: str, patch: dict) -> None:
    _append({"id": strategy_id, **patch})


def set_exit_levels(strategy_id: str, tp: dict = None, sl: dict = None) -> None:
    p = {"id": strategy_id}
    if tp is not None: p["tp"] = tp
    if sl is not None: p["sl"] = sl
    _append(p)


def cancel(strategy_id: str) -> None:
    _append({"id": strategy_id, "status": "CANCELLED", "closed_at": _now()})


def delete(strategy_id: str) -> None:
    _append({"id": strategy_id, "type": "delete"})


# Fill-timing learning feed — "this premium got sold at this time" for the backtest
# loop (analyses/900_learning_loop). Local-only (data/ is gitignored). IST timestamps.
FILLS = ROOT / "data" / "fill_timing.jsonl"


def _log_fill(strategy_id: str, ce: float, pe: float, combined: float) -> None:
    """Append a fill record so actual sell-timing can be compared to the backtested
    optimum (the 30-40% premium-timing leak, analyses 027/028)."""
    try:
        s = next((x for x in all_strategies() if x.get("id") == strategy_id), None) or {}
        now = datetime.now(IST)
        rec = {"ts": now.isoformat(), "time_ist": now.strftime("%H:%M"),
               "date": now.strftime("%Y-%m-%d"), "strategy_id": strategy_id,
               "name": s.get("name"), "instrument": s.get("instrument"),
               "ce_strike": s.get("ce_strike"), "pe_strike": s.get("pe_strike"),
               "lots": s.get("lots"), "ce": ce, "pe": pe, "combined": combined,
               "source": "dummy_fill"}
        FILLS.parent.mkdir(parents=True, exist_ok=True)
        with FILLS.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def mark_entered(strategy_id: str, ce: float, pe: float, combined: float) -> None:
    _append({"id": strategy_id, "status": "ENTERED",
             "entry": {"at": _now(), "ce": ce, "pe": pe, "combined": combined},
             "events_push": {"at": _now(), "what": "ENTRY level reached"}})
    _log_fill(strategy_id, ce, pe, combined)


def mark_exit(strategy_id: str, kind: str, combined: float) -> None:
    _append({"id": strategy_id, "status": f"{kind}_HIT",
             "exit": {"at": _now(), "type": kind, "combined": combined},
             "events_push": {"at": _now(), "what": f"{kind} level reached"}})


# ── Monitor ──────────────────────────────────────────────────────────────────
def _legs_premium(s: dict, ltp_lookup) -> dict:
    """Return {ce, pe, combined} live premium for a strategy's strikes."""
    inst = s["instrument"]
    ce = ltp_lookup(inst, s.get("ce_strike"), "CE") if s.get("ce_strike") else 0.0
    pe = ltp_lookup(inst, s.get("pe_strike"), "PE") if s.get("pe_strike") else 0.0
    ce = ce or 0.0; pe = pe or 0.0
    return {"ce": ce, "pe": pe, "combined": round(ce + pe, 2)}


def _entry_hit(s: dict, prem: dict) -> bool:
    if s["trigger_mode"] == "SEPARATE":
        ok = True
        if s.get("ce_strike") and s.get("ce_threshold") is not None:
            ok = ok and prem["ce"] >= s["ce_threshold"]
        if s.get("pe_strike") and s.get("pe_threshold") is not None:
            ok = ok and prem["pe"] >= s["pe_threshold"]
        return ok
    thr = s.get("combined_threshold")
    if thr is None and s.get("yield_per_cr"):
        thr = premium_for_yield(s["yield_per_cr"], s["instrument"])
    return thr is not None and prem["combined"] >= thr


def _exit_hit(s: dict, prem: dict):
    """Return 'TP' | 'SL' | None for an ENTERED strategy."""
    entry = (s.get("entry") or {}).get("combined")
    lots_qty = s["lots"] * LOT.get(s["instrument"], 75)
    for kind, lvl in (("TP", s.get("tp")), ("SL", s.get("sl"))):
        if not lvl:
            continue
        mode = lvl.get("mode"); val = lvl.get("value")
        if val is None:
            continue
        if mode == "premium":               # combined premium level
            if kind == "TP" and prem["combined"] <= val: return "TP"
            if kind == "SL" and prem["combined"] >= val: return "SL"
        elif mode in ("profit", "loss") and entry is not None:
            pnl = (entry - prem["combined"]) * lots_qty   # short: decay = profit
            if kind == "TP" and pnl >= val: return "TP"
            if kind == "SL" and pnl <= -abs(val): return "SL"
    return None


def check(ltp_lookup) -> list[dict]:
    """Advance all ARMED/ENTERED strategies; return list of fired events."""
    fired = []
    for s in all_strategies():
        st = s.get("status")
        if st not in ("ARMED", "ENTERED"):
            continue
        prem = _legs_premium(s, ltp_lookup)
        if prem["combined"] <= 0:
            continue
        if st == "ARMED" and _entry_hit(s, prem):
            mark_entered(s["id"], prem["ce"], prem["pe"], prem["combined"])
            fired.append({**s, "status": "ENTERED", "fired": "ENTRY", "premium": prem})
        elif st == "ENTERED":
            kind = _exit_hit(s, prem)
            if kind:
                mark_exit(s["id"], kind, prem["combined"])
                fired.append({**s, "status": f"{kind}_HIT", "fired": kind, "premium": prem})
    return fired
