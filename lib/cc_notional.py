"""
lib/cc_notional.py — month-start "notional" price per underlying for the
Covered-Call Against-Investment assignment P&L.

Why: the strategy rule is to NEVER let stock get assigned. But if a CE goes ITM,
showing only the CE leg's mark-to-market reads as a huge phantom loss — it's
offset by the stock you hold. So assignment P&L is measured as
(effective_strike − basis) × qty against TWO bases: the original buy price
(lifetime) and the month-start "notional" price (this-month view).

The notional price is auto-captured from Kite on the first trading day of the
month (overridable). Stored per (month, symbol) in data/cc_notional.json — a
local, host-agnostic JSON store (portability rule).
"""
from __future__ import annotations
from pathlib import Path
from datetime import date
import json

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "cc_notional.json"


def _load() -> dict:
    try:
        return json.loads(STORE.read_text()) if STORE.exists() else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, indent=2))


def month_key(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def get(symbol: str, month: str | None = None) -> float | None:
    month = month or month_key()
    return _load().get(month, {}).get((symbol or "").upper())


def all_for_month(month: str | None = None) -> dict:
    month = month or month_key()
    return _load().get(month, {})


def set_price(symbol: str, price: float, month: str | None = None) -> None:
    """Manual override of a month-start notional price."""
    month = month or month_key()
    d = _load()
    d.setdefault(month, {})[(symbol or "").upper()] = float(price)
    _save(d)


def capture_from_kite(symbols: list[str], month: str | None = None,
                      overwrite: bool = False) -> dict:
    """Snapshot the current NSE last price for each symbol into this month's
    notional store. Run on the first trading day of the month. Skips symbols
    already set unless overwrite=True. Returns {captured, skipped, errors}."""
    month = month or month_key()
    d = _load()
    existing = d.setdefault(month, {})
    captured, skipped, errors = {}, [], []
    try:
        from lib.kite_live import _kite
        k = _kite()
    except Exception as e:
        return {"captured": {}, "skipped": [], "errors": [f"kite: {e}"], "month": month}
    keys = {s: f"NSE:{s.upper()}" for s in symbols if s}
    todo = {s: kk for s, kk in keys.items() if overwrite or s.upper() not in existing}
    if not todo:
        return {"captured": {}, "skipped": list(keys), "errors": [], "month": month}
    try:
        q = k.quote(list(todo.values()))
    except Exception as e:
        return {"captured": {}, "skipped": [], "errors": [f"quote: {e}"], "month": month}
    for s, kk in todo.items():
        try:
            lp = q.get(kk, {}).get("last_price")
            if lp:
                existing[s.upper()] = float(lp)
                captured[s.upper()] = float(lp)
            else:
                errors.append(s)
        except Exception:
            errors.append(s)
    _save(d)
    return {"captured": captured, "skipped": skipped, "errors": errors, "month": month}
