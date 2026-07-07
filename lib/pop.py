"""
lib/pop.py — empirical Probability-of-Profit, conditioned on the live regime.

POP = P(short strike NOT breached by expiry), from analysis 031's backtest table
(results/031_pop_curve/pop_table.json), bucketed by (instrument, regime, distance).
Replaces the hardcoded 100% — close strikes get their REAL (lower) win rate, and
the number moves as the day's regime (gap / first-15-min range / VIX) changes.

Usage:
    from lib import pop
    pop.conditional_pop("NIFTY", 2.0, regime="normal")          -> {"pop": 100.0, ...}
    pop.conditional_pop("SENSEX", 0.7, regime="high_risk", basis="ce")
"""
from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
_TABLE_PATH = ROOT / "results" / "031_pop_curve" / "pop_table.json"
_table = None


def _load() -> dict:
    global _table
    if _table is None:
        try:
            _table = json.loads(_TABLE_PATH.read_text())
        except Exception:
            _table = {}
    return _table


def reload() -> None:
    """Drop the cache (after re-running analysis 031)."""
    global _table
    _table = None


def _interp(curve: dict, dist_pct: float, basis: str):
    """Linear-interpolate POP across the table's distance points. curve maps
    str(dist) -> {strangle, pe, ce, n}. Clamps outside the sampled range."""
    pts = sorted((float(k), v) for k, v in curve.items())
    if not pts:
        return None, 0
    if dist_pct <= pts[0][0]:
        return pts[0][1].get(basis), pts[0][1].get("n", 0)
    if dist_pct >= pts[-1][0]:
        return pts[-1][1].get(basis), pts[-1][1].get("n", 0)
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= dist_pct <= d1:
            w = (dist_pct - d0) / (d1 - d0) if d1 != d0 else 0
            p0, p1 = v0.get(basis), v1.get(basis)
            if p0 is None or p1 is None:
                return (p1 if p0 is None else p0), v0.get("n", 0)
            return round(p0 + (p1 - p0) * w, 1), min(v0.get("n", 0), v1.get("n", 0))
    return None, 0


def conditional_pop(instrument: str, dist_pct: float, regime: str = "normal",
                    basis: str = "strangle", min_n: int = 12) -> dict | None:
    """Empirical POP% for selling at `dist_pct` OTM under `regime`.
    basis ∈ {strangle, pe, ce}. Falls back to the pooled (ALL-regime) curve when
    the specific regime bucket is missing or thin (<min_n). Returns:
      {pop, basis, regime_used, n, low_sample, source}  — or None if no data."""
    inst = (instrument or "").upper()
    t = _load().get(inst)
    if not t:
        return None
    dist_pct = abs(float(dist_pct))
    # try the specific regime; fall back to pooled 'ALL' when missing/thin
    for reg in (regime, "ALL"):
        curve = t.get(reg)
        if not curve:
            continue
        val, n = _interp(curve, dist_pct, basis)
        if val is None:
            continue
        # keep the regime-specific value (that's the signal) — only flag thin samples,
        # don't silently pool them away. Fall back to ALL only if the regime is MISSING.
        return {
            "pop": val, "basis": basis, "regime_used": reg, "n": int(n),
            "low_sample": n < min_n,
            "source": "backtest-031",
        }
    return None


def both_legs(instrument: str, dist_pct: float, regime: str = "normal") -> dict | None:
    """Convenience: per-leg + strangle POP for a symmetric strangle at dist_pct."""
    out = {}
    for b in ("pe", "ce", "strangle"):
        r = conditional_pop(instrument, dist_pct, regime, basis=b)
        if r:
            out[b] = r["pop"]
            out["regime_used"] = r["regime_used"]
            out["n"] = r["n"]
            out["low_sample"] = r["low_sample"]
    return out or None
