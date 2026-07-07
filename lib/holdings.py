"""
lib/holdings.py — covered-call holdings & coverage, ported from the Covered Call
Analyzer's "Selling Plan" concept.

Reads the master Excel "Selling Plan" sheet (read-only) for the investment book:
per stock — equity qty, futures qty, total held, avg buy, current price, 52w high,
qty of calls already sold (coverage), remaining uncovered qty, planned strike.

Coverage = qty sold / total held. Uncovered = held − sold.
"""
from __future__ import annotations
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parent.parent
from lib.full_report import _find_workbook as _fwb   # single source for the workbook location
WORKBOOK = _fwb()


def _num(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            if v in ("", "-", "nan"):
                return None
        return float(v)
    except Exception:
        return None


def _s(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def load_holdings() -> list[dict]:
    import pandas as pd
    if not WORKBOOK.exists():
        return []
    try:
        df = pd.read_excel(WORKBOOK, sheet_name="Selling Plan")
    except Exception:
        return []
    C = {c: c for c in df.columns}
    def col(*names):
        for n in names:
            for c in df.columns:
                if str(c).strip().lower() == n.lower():
                    return c
        for n in names:
            for c in df.columns:
                if str(c).strip().lower().startswith(n.lower()):
                    return c
        return None
    m = {
        "name": col("Stock Name"), "symbol": col("SCRIPT"),
        "lot": col("Lot Size (Common)", "Lot Size"),
        "equity": col("Total Equity"), "equity_rate": col("Buy Rate"),
        "futures": col("Total Future S1", "Total Future"),
        "total": col("Total F+E"),
        "sold": col("Actual Qty sold", "Total to be sold"),
        "remaining": col("Remaining Qty"),
        "current": col("Current Rate"), "high52": col("52 WK HIGH"),
        "strike": col("Strike to Sell"), "dist": col("% Distance from Current Price"),
    }
    out = []
    for _, r in df.iterrows():
        sym = _s(r.get(m["symbol"])) if m["symbol"] else ""
        held = _num(r.get(m["total"])) if m["total"] else None
        eq = _num(r.get(m["equity"])) if m["equity"] else None
        fut = _num(r.get(m["futures"])) if m["futures"] else None
        if not sym or not (held or eq or fut):
            continue
        sold = _num(r.get(m["sold"])) if m["sold"] else None
        cur = _num(r.get(m["current"])) if m["current"] else None
        h52 = _num(r.get(m["high52"])) if m["high52"] else None
        total = held or ((eq or 0) + (fut or 0))
        coverage = round((sold or 0) / total * 100, 1) if total else None
        out.append({
            "name": _s(r.get(m["name"])) if m["name"] else sym, "symbol": sym,
            "lot": _num(r.get(m["lot"])) if m["lot"] else None,
            "equity_qty": eq, "futures_qty": fut, "total_qty": total,
            "avg_buy": _num(r.get(m["equity_rate"])) if m["equity_rate"] else None,
            "current": cur, "high52": h52,
            "pct_off_high": round((cur - h52) / h52 * 100, 1) if (cur and h52) else None,
            "ce_sold_qty": sold or 0,
            "uncovered_qty": round(total - (sold or 0)) if total else None,
            "coverage_pct": coverage,
            "planned_strike": _num(r.get(m["strike"])) if m["strike"] else None,
            "value": round(total * cur) if (total and cur) else None,
        })
    return out


def load_stockwise() -> list[dict]:
    """Per-underlying covered-call view (Stockwise sheet): future qty, CE/PE sold
    qty + value, options net, future value, NET current p/l, margin, % return.
    This is the authoritative per-stock P&L for covered calls (options netted)."""
    import pandas as pd
    if not WORKBOOK.exists():
        return []
    try:
        df = pd.read_excel(WORKBOOK, sheet_name="Stockwise", header=1)
    except Exception:
        return []
    def col(name):
        for c in df.columns:
            if str(c).strip().lower() == name.lower():
                return c
        return None
    m = {k: col(v) for k, v in {
        "underlying": "Underlying", "ce_qty": "CE", "ce_val": "Calls value",
        "pe_qty": "PE", "pe_val": "Put value", "options_net": "Options Net",
        "fut_qty": "Future Qty.1", "fut_val": "Future Value",
        "net_pnl": "Net current p/l", "margin": "Margin Used", "ret": "% Return"}.items()}
    if not m["underlying"]:
        return []
    out = []
    for _, r in df.iterrows():
        u = _s(r.get(m["underlying"]))
        if not u or u.lower() in ("underlying", "total", "grand total"):
            continue
        out.append({
            "underlying": u,
            "ce_sold_qty": _num(r.get(m["ce_qty"])) or 0,
            "pe_sold_qty": _num(r.get(m["pe_qty"])) or 0,
            "options_net": _num(r.get(m["options_net"])),
            "fut_qty": _num(r.get(m["fut_qty"])) or 0,
            "fut_value": _num(r.get(m["fut_val"])),
            "net_pnl": _num(r.get(m["net_pnl"])),
            "margin": _num(r.get(m["margin"])),
            "return_pct": _num(r.get(m["ret"])),
        })
    return out


def load_futures_m2m() -> list[dict]:
    """Investment futures with clean M2M (Futures Holding sheet): original buy →
    live, daily & monthly M2M, qty, margin. These are NOT assigned monthly."""
    import pandas as pd
    if not WORKBOOK.exists():
        return []
    try:
        df = pd.read_excel(WORKBOOK, sheet_name="Futures Holding")
    except Exception:
        return []
    def col(*names):
        for n in names:
            for c in df.columns:
                if str(c).strip().lower() == n.lower():
                    return c
        return None
    m = {k: col(*v) for k, v in {
        "broker": ("Broker",), "demat": ("Company",), "stock": ("Stock name",),
        "symbol": ("Symbol",), "orig_buy": ("Avg Original Buy Price",),
        "live": ("Live Stock Price",), "lot": ("Lot Size",), "qty": ("Buy Qty",),
        "buy_total": ("Buy Total (Amt)",), "daily_m2m": ("Daily M2M",),
        "monthly_m2m": ("Monthly M2M",), "margin": ("Total Margin Used",)}.items()}
    out = []
    for _, r in df.iterrows():
        sym = _s(r.get(m["symbol"])) if m["symbol"] else ""
        qty = _num(r.get(m["qty"])) if m["qty"] else None
        if not sym or not qty:
            continue
        orig = _num(r.get(m["orig_buy"])) if m["orig_buy"] else None
        live = _num(r.get(m["live"])) if m["live"] else None
        m2m_total = round((live - orig) * qty) if (orig and live) else None
        out.append({
            "broker": _s(r.get(m["broker"])) if m["broker"] else "",
            "demat": _s(r.get(m["demat"])) if m["demat"] else "",
            "stock": _s(r.get(m["stock"])) if m["stock"] else sym, "symbol": sym,
            "orig_buy": orig, "live": live, "qty": round(qty),
            "buy_total": _num(r.get(m["buy_total"])) if m["buy_total"] else None,
            "m2m_total": m2m_total,
            "daily_m2m": _num(r.get(m["daily_m2m"])) if m["daily_m2m"] else None,
            "monthly_m2m": _num(r.get(m["monthly_m2m"])) if m["monthly_m2m"] else None,
            "margin": _num(r.get(m["margin"])) if m["margin"] else None,
        })
    return out


def portfolio_summary(holdings: list[dict]) -> dict:
    total_qty = sum(h.get("total_qty") or 0 for h in holdings)
    sold = sum(h.get("ce_sold_qty") or 0 for h in holdings)
    value = sum(h.get("value") or 0 for h in holdings)
    return {
        "n_stocks": len(holdings), "total_value": round(value),
        "total_qty": round(total_qty), "ce_sold_qty": round(sold),
        "uncovered_qty": round(total_qty - sold),
        "coverage_pct": round(sold / total_qty * 100, 1) if total_qty else None,
    }
