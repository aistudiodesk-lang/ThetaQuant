"""
lib/cc_holdings.py — manual "holdings for investment" entry for Covered Calls
Against Investment (S1). The user wants to enter, per underlying, both the
EQUITY holding qty and the FUTURES holding qty held for investment, and then
see how much is still uncovered (pending to sell calls against) — the selling
plan. These manual entries OVERLAY the Excel "Selling Plan" sheet (we never
write to the workbook), so the user can run the desk without the Excel too.

Store: data/cc_holdings.json — list of per-symbol entries. Host-agnostic JSON,
same pattern as the other local stores (portable; no cloud dependency).
"""
from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "cc_holdings.json"

# keys a manual entry can set. Equity and futures are kept SEPARATE (qty + avg each),
# never blended. None/absent means "fall back to the Sheet/Excel".
FIELDS = ("equity_qty", "equity_avg", "futures_qty", "futures_avg",
          "lot", "current", "high52", "ce_sold_qty")


def _load() -> list[dict]:
    try:
        return json.loads(STORE.read_text()) if STORE.exists() else []
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows, indent=2))


def load_manual() -> list[dict]:
    return _load()


def upsert(symbol: str, **fields) -> dict:
    """Create/update the manual holding for `symbol`. Only known FIELDS are kept;
    a value of None/"" clears that override (falls back to Excel)."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol required")
    rows = _load()
    row = next((r for r in rows if (r.get("symbol") or "").upper() == symbol), None)
    if row is None:
        row = {"symbol": symbol}
        rows.append(row)
    for k, v in fields.items():
        if k not in FIELDS:
            continue
        if v in (None, ""):
            row.pop(k, None)
        else:
            try:
                row[k] = float(v)
            except (TypeError, ValueError):
                row[k] = v
    _save(rows)
    return row


def delete(symbol: str) -> bool:
    symbol = (symbol or "").strip().upper()
    rows = _load()
    new = [r for r in rows if (r.get("symbol") or "").upper() != symbol]
    if len(new) == len(rows):
        return False
    _save(new)
    return True


def _cov(total, sold):
    return round((sold or 0) / total * 100, 1) if total else None


def merged_holdings() -> list[dict]:
    """Manual entries as the base, OVERRIDDEN by the Sheet/Excel "Selling Plan"
    (the Google Sheet is the source of truth — manual is a fallback used until the
    Sheet has the symbol, and any Sheet value wins). Equity and futures stay
    separate; recomputes total, uncovered (= equity+futures − ce_sold) and coverage."""
    from lib import holdings as H
    try:
        excel = {(_r.get("symbol") or "").upper(): dict(_r) for _r in H.load_holdings()}
    except Exception:
        excel = {}
    manual = {(_r.get("symbol") or "").upper(): _r for _r in _load()}

    out = {}
    for sym, m in manual.items():                       # manual is the base
        out[sym] = {**m, "name": m.get("name") or sym, "source": "manual"}
    for sym, r in excel.items():                         # Sheet overrides manual
        base = out.get(sym, {"symbol": sym})
        for k in FIELDS + ("name", "equity_qty", "futures_qty", "total_qty",
                           "current", "high52", "ce_sold_qty", "lot", "avg_buy"):
            if r.get(k) is not None:
                base[k] = r[k]
        base["source"] = "sheet" if sym not in manual else "sheet+manual"
        out[sym] = base

    rows = []
    for sym, r in out.items():
        eq = r.get("equity_qty") or 0
        fut = r.get("futures_qty") or 0
        total = (eq + fut) or r.get("total_qty") or 0
        sold = r.get("ce_sold_qty") or 0
        cur = r.get("current")
        h52 = r.get("high52")
        rows.append({
            "symbol": sym, "name": r.get("name") or sym,
            "lot": r.get("lot"),
            "equity_qty": eq, "equity_avg": r.get("equity_avg") or r.get("avg_buy"),
            "futures_qty": fut, "futures_avg": r.get("futures_avg"),
            "total_qty": total,
            "current": cur, "high52": h52,
            "pct_off_high": round((cur - h52) / h52 * 100, 1) if (cur and h52) else None,
            "ce_sold_qty": sold,
            "uncovered_qty": round(total - sold) if total else None,
            "coverage_pct": _cov(total, sold),
            "value": round(total * cur) if (total and cur) else None,
            "equity_value": round(eq * cur) if (eq and cur) else None,
            "futures_value": round(fut * cur) if (fut and cur) else None,
            "source": r.get("source", "sheet"),
        })
    rows.sort(key=lambda x: -(x.get("uncovered_qty") or 0))
    return rows
