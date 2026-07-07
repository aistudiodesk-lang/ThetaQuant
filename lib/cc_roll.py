"""Covered-call ROLL decision cards.

When a sold call gets threatened (spot grinding toward the strike), the desk needs
a fast "what are my options" card. Spec from the analyzer's STRATEGIES.md (roll was
only *planned* there, never coded) — built here to fit Theta Quant's flow:

  • cost to close the current short call
  • three roll candidates — UP (same expiry, higher strike),
    OUT (same strike, next expiry), UP-AND-OUT (higher + next expiry) —
    each with strike, model premium, delta, net credit/debit, new OTM%
  • a HOLD-AND-WATCH option with a defined invalidation level

Premiums/deltas are Black-Scholes model estimates. IV is *backed out of the live
current premium* of the position being rolled, so every candidate is priced on the
same vol surface as the real market — then flagged "model estimate, confirm live
premium before placing". No external data dependency (works without a chain fetch).
"""
from __future__ import annotations
import math
from datetime import date, datetime

R_FREE = 0.065        # India ~risk-free
_SQRT2 = math.sqrt(2.0)


# ----------------------------------------------------------------- BS math
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def bs_call(S: float, K: float, T: float, sigma: float, r: float = R_FREE) -> tuple[float, float]:
    """European call price + delta. T in years, sigma annualised vol."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        intrinsic = max(0.0, S - K)
        return intrinsic, (1.0 if S > K else 0.0)
    vt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vt
    d2 = d1 - vt
    price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return price, _norm_cdf(d1)


def implied_vol(price: float, S: float, K: float, T: float, r: float = R_FREE) -> float | None:
    """Back out IV from a market call price via bisection. None if unsolvable."""
    if not price or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(0.0, S - K * math.exp(-r * T))
    if price <= intrinsic + 1e-6:
        return None
    lo, hi = 1e-4, 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        p, _d = bs_call(S, K, T, mid, r)
        if abs(p - price) < 1e-4:
            return mid
        if p > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# ----------------------------------------------------------------- helpers
def _strike_step(price: float) -> int:
    if price < 250: return 5
    if price < 500: return 10
    if price < 1000: return 20
    if price < 2500: return 50
    return 100


def _round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def _parse_dte(expiry) -> int:
    """Days to expiry from an ISO date string / date. Fallback 14 (monthly-ish)."""
    if isinstance(expiry, (date, datetime)):
        d = expiry.date() if isinstance(expiry, datetime) else expiry
    else:
        s = str(expiry or "").strip()[:10]
        d = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                d = datetime.strptime(s, fmt).date(); break
            except ValueError:
                continue
        if d is None:
            return 14
    dte = (d - date.today()).days
    return max(dte, 1)


# ----------------------------------------------------------------- engine
def roll_candidates(symbol: str, strike: float, qty: float, current_premium: float,
                    spot: float, expiry=None, lot: int = 1,
                    iv: float | None = None, next_expiry_days: int = 30) -> dict:
    """Build the roll decision card for one short-call position.

    qty = absolute shorted quantity (shares). current_premium = live LTP per share
    (the buy-back cost). spot = live underlying. Returns a JSON-ready dict."""
    strike = float(strike or 0); spot = float(spot or 0)
    qty = abs(float(qty or 0)); current_premium = float(current_premium or 0)
    dte = _parse_dte(expiry)
    T = dte / 365.0
    step = _strike_step(spot or strike)

    # Anchor IV to the live premium of the position being rolled (else fall back).
    iv_used = iv
    iv_source = "given"
    if iv_used is None and current_premium and spot and strike:
        iv_used = implied_vol(current_premium, spot, strike, T)
        iv_source = "backed_out"
    if iv_used is None:
        iv_used = 0.28          # neutral fallback ~28% annualised
        iv_source = "fallback"

    cost_to_close = current_premium * qty       # debit to flatten now
    cur_otm = (strike - spot) / spot if spot else 0.0

    def _cand(new_K: float, new_dte: int, label: str, kind: str) -> dict:
        nT = new_dte / 365.0
        prem, delta = bs_call(spot, new_K, nT, iv_used)
        net = (prem - current_premium) * qty     # +credit / −debit vs closing
        return {
            "kind": kind, "label": label,
            "strike": int(new_K), "dte": new_dte,
            "premium": round(prem, 2), "delta": round(delta, 3),
            "otm_pct": round((new_K - spot) / spot * 100, 2) if spot else None,
            "net_credit": round(net, 0),
            "lots": round(qty / lot, 2) if lot else None,
        }

    # UP — same expiry, lift strike to restore buffer (≥ current OTM + ~4%, min +1 step)
    up_target = spot * (1 + max(cur_otm, 0.06) + 0.04)
    up_K = max(_round_to(up_target, step), int(strike) + step)
    # OUT — same strike, next monthly expiry
    out_dte = dte + next_expiry_days
    # UP-AND-OUT — lifted strike + next expiry
    cands = [
        _cand(up_K, dte, "Roll UP — higher strike, same expiry", "up"),
        _cand(strike, out_dte, "Roll OUT — same strike, next expiry", "out"),
        _cand(up_K, out_dte, "Roll UP & OUT — higher strike, next expiry", "up_out"),
    ]

    # Hold & watch — invalidation = strike (close above ⇒ ITM/assignment); early warn at 97% of strike
    invalidation = int(strike)
    early_warn = round(strike * 0.97, 0)
    buffer_pct = round(cur_otm * 100, 2)

    return {
        "symbol": (symbol or "").upper(),
        "spot": round(spot, 2), "strike": int(strike), "qty": int(qty), "lot": lot,
        "dte": dte, "current_premium": round(current_premium, 2),
        "cost_to_close": round(cost_to_close, 0),
        "buffer_pct": buffer_pct,
        "iv_pct": round(iv_used * 100, 1), "iv_source": iv_source,
        "candidates": cands,
        "hold_watch": {
            "invalidation": invalidation, "early_warn": early_warn,
            "note": f"Hold if spot stays below {invalidation}. Act if it closes above "
                    f"{early_warn} (≈97% of strike) — buffer then < 3%.",
        },
        "disclaimer": "Model estimates (Black-Scholes, IV from live premium). "
                      "Confirm live option premiums before placing any roll.",
    }
