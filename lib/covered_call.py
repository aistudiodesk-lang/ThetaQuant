"""
lib/covered_call.py — pure covered-call analytics ported from the Covered Call
Analyzer (Streamlit tool). No DB / no Streamlit — just the math, so any tab can
call it. Three sub-strategies:

  against_investment : sell deep/mid-OTM calls (or puts) vs existing holdings (S1)
  regular_otm        : buy futures + sell OTM call, want it to expire/turn ITM (S2)
  itm_theta          : buy futures + sell ITM call to harvest time value (S3)

Buckets/floors mirror the analyzer's .env knobs.
"""
from __future__ import annotations
import math

# ── Tunables (from analyzer .env) ───────────────────────────────────────────
CONFIG = {
    "against_investment": {
        "label": "Against Investment",
        "buckets": {"DEEP": {"otm_pct": 10.0, "delta_max": 0.10, "yield_floor_pm": 1.2},
                    "MID":  {"otm_pct": 6.0,  "delta_max": 0.25, "yield_floor_pm": 3.5}},
        "intent": "Sell OTM calls/puts against holdings — must NOT go ITM.",
    },
    "regular_otm": {
        "label": "Regular OTM (buy-write)",
        "buckets": {"OTM": {"otm_pct": 2.0, "delta_max": 0.45, "yield_floor_pm": 2.0}},
        "intent": "Buy futures + sell OTM call — gain on call premium AND the future if it rises.",
    },
    "itm_theta": {
        "label": "ITM (theta harvest)",
        "buckets": {"ITM": {"otm_pct": -3.0, "delta_min": 0.55, "yield_floor_pm": 3.5}},
        "intent": "Buy futures + sell ITM call — eat the time value as an income asset.",
    },
}


def strike_step(price: float) -> int:
    """Typical NSE option strike interval by underlying price."""
    if price < 250: return 5
    if price < 500: return 10
    if price < 1000: return 20
    if price < 2500: return 25 if price < 1500 else 50
    if price < 5000: return 50
    return 100


def suggest_levels(current: float, high52: float, lot: float, uncovered_qty: float,
                   mode: str = "against_investment") -> dict:
    """Selling-plan suggestion per holding: DEEP & MID strikes (rounded to the
    underlying's strike step), lots sized to the uncovered qty, margin, eligibility
    (from 52w-off-high), and the monthly-yield FLOOR each bucket must clear."""
    cfg = CONFIG.get(mode, CONFIG["against_investment"])
    step = strike_step(current or 100)
    off_high = round((current - high52) / high52 * 100, 1) if (current and high52) else None
    out = {"current": current, "high52": high52, "pct_off_high": off_high,
           "lot": lot, "uncovered_qty": uncovered_qty,
           "lots_to_cover": int(uncovered_qty // lot) if (lot and uncovered_qty) else 0,
           "buckets": []}
    for name, b in cfg["buckets"].items():
        dist = b["otm_pct"]
        raw = (current or 0) * (1 + dist / 100)
        strike = int(round(raw / step) * step) if step else round(raw)
        margin = strike * (lot or 0) * 0.18
        snap = {"pct_off_high": off_high, "delta": b.get("delta_max", 0.1)}
        elig = cc_eligibility(snap, "CE")
        out["buckets"].append({
            "bucket": name, "distance_pct": dist, "strike": strike,
            "margin_per_lot": round(margin),
            "yield_floor_pm": b.get("yield_floor_pm"),
            "eligibility": elig["verdict"], "score": elig["score"], "reasons": elig["reasons"],
        })
    return out


def monthly_yield_pct(premium: float, margin: float, dte: int) -> float | None:
    """(premium×lot collected / margin) annualised to a month."""
    if not margin or not premium:
        return None
    return round((premium * 100.0 / margin) * (30.0 / max(dte, 1)), 3)


def distance_pct(strike: float, spot: float) -> float | None:
    if not spot:
        return None
    return round((strike - spot) / spot * 100.0, 2)


def cc_eligibility(snap: dict, leg: str = "CE") -> dict:
    """GREEN/YELLOW/RED/REJECT verdict for selling a CE (or PE) against a holding.
    snap: {rsi_d, rsi_w, macd_d, macd_w, trend, breakout, pct_off_high, gap}.
    Mirrors the analyzer's hard vetoes + soft scoring (lower = safer)."""
    reasons = []
    r_d = snap.get("rsi_d"); r_w = snap.get("rsi_w")
    macd_d = (snap.get("macd_d") or "").lower(); macd_w = (snap.get("macd_w") or "").lower()
    off_high = snap.get("pct_off_high")
    breakout = (snap.get("breakout") or "").lower()
    up = leg.upper() == "CE"   # CE selling fears upside; PE selling fears downside

    # hard vetoes (REJECT)
    if up:
        if breakout in ("confirmed", "fresh"):
            reasons.append("confirmed breakout")
        if (r_w or 0) > 70 or (r_d or 0) > 75:
            reasons.append("RSI overbought")
        if "bull" in macd_d and "bull" in macd_w:
            reasons.append("MACD bullish both TFs")
        if off_high is not None and abs(off_high) <= 3:
            reasons.append("within 3% of 52w high")
    else:
        if (r_w or 100) < 30:
            reasons.append("weekly RSI oversold")
        if "bear" in macd_d and "bear" in macd_w:
            reasons.append("MACD bearish both TFs")
    if reasons:
        return {"verdict": "REJECT", "reasons": reasons, "score": 100}

    # soft score (0 safe → 100 risky)
    score = 0.0
    delta = abs(snap.get("delta") or 0)
    score += min(35, delta * 350)
    trend = (snap.get("trend") or "").lower()
    if up:
        score += {"bullish": 25, "weak_bullish": 14, "sideways": 6, "weak_bearish": 2}.get(trend, 0)
    else:
        score += {"bearish": 25, "weak_bearish": 14, "sideways": 6, "weak_bullish": 2}.get(trend, 0)
    iv = snap.get("iv")
    if iv is not None and iv < 40:
        score += 4
    score = round(min(score, 100), 1)
    verdict = "GREEN" if score <= 20 else "YELLOW" if score <= 40 else "RED"
    return {"verdict": verdict, "reasons": [], "score": score}


def cc_monitor_status(spot: float, strike: float, side: str,
                      sold_premium: float, current_premium: float = None) -> dict:
    """RGB monitoring for a sold covered-call/put leg (S1/S2/S3). Rules (user):
      premium ≥ sold premium        → ORANGE
      premium ≥ 2× sold premium     → RED
      spot within 5% of strike      → ORANGE
      spot within 3% of strike      → RED   (or already ITM)
    Returns worst-of with the explicit levels so the desk can show "exit if …"."""
    side = (side or "CE").upper()
    rank = {"GREEN": 0, "ORANGE": 1, "RED": 2}
    level, reasons = "GREEN", []

    def bump(lv, why):
        nonlocal level
        if rank[lv] > rank[level]:
            level = lv
        reasons.append(why)

    # premium bands
    captured_pct = profit_take = None
    if current_premium is not None and sold_premium:
        captured_pct = round((sold_premium - current_premium) / sold_premium * 100, 1)
        profit_take = captured_pct >= 80                # ≥80% of premium captured → safe / book
        if current_premium >= 2 * sold_premium:
            bump("RED", "premium ≥ 2× sold")
        elif current_premium >= sold_premium:
            bump("ORANGE", "premium ≥ sold")
        elif profit_take:
            reasons.append("80%+ premium captured — safe / take profit")

    # spot-vs-strike bands — danger is spot moving INTO the strike
    spot_levels = {}
    if spot and strike:
        if side == "CE":   # short call: danger as spot RISES toward/above strike
            dist = (strike - spot) / strike            # smaller / negative = danger
            orange_at = round(strike * 0.95)           # spot ≥ this = within 5%
            red_at = round(strike * 0.97)              # spot ≥ this = within 3%
        else:              # short put: danger as spot FALLS toward/below strike
            dist = (spot - strike) / strike
            orange_at = round(strike * 1.05)           # spot ≤ this = within 5%
            red_at = round(strike * 1.03)
        spot_levels = {"orange_at": orange_at, "red_at": red_at,
                       "dist_to_strike_pct": round(dist * 100, 1)}
        if dist <= 0.03:
            bump("RED", "spot within 3% of strike")
        elif dist <= 0.05:
            bump("ORANGE", "spot within 5% of strike")

    return {"level": level, "reasons": reasons,
            "captured_pct": captured_pct, "profit_take": profit_take,
            "premium_orange_at": round(sold_premium, 2) if sold_premium else None,
            "premium_red_at": round(2 * sold_premium, 2) if sold_premium else None,
            "premium_green_at": round(0.2 * sold_premium, 2) if sold_premium else None,
            **spot_levels}


def payoff_buy_write(fut_entry: float, strike: float, premium: float,
                     spot: float, lots: int, lot_size: int) -> dict:
    """Buy future @ fut_entry + short call @ strike (premium). Payoff at settle S.
    Net(S) = lots·lot·[(S−F) + P − max(0, S−K)].
    Used for regular_otm and itm_theta."""
    qty = lots * lot_size
    max_profit = (strike - fut_entry + premium) * qty       # if S ≥ K (capped)
    breakeven = fut_entry - premium
    cur = ((spot - fut_entry) + premium - max(0.0, spot - strike)) * qty
    return {
        "max_profit": round(max_profit), "breakeven": round(breakeven, 2),
        "current_pnl": round(cur), "capped_above": strike,
        "premium_income": round(premium * qty),
        "is_itm_target": strike <= fut_entry,   # ITM-by-design (theta harvest)
    }


def payoff_curve(fut_entry: float, strike: float, premium: float,
                 lots: int, lot_size: int, spot: float, span_pct: float = 12.0,
                 n: int = 41) -> list[dict]:
    qty = lots * lot_size
    lo, hi = spot * (1 - span_pct / 100), spot * (1 + span_pct / 100)
    out = []
    for i in range(n):
        s = lo + (hi - lo) * i / (n - 1)
        pnl = ((s - fut_entry) + premium - max(0.0, s - strike)) * qty
        out.append({"spot": round(s, 1), "pnl": round(pnl)})
    return out
