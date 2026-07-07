"""
lib/full_report.py — parse the master "Full strategy Reporting" workbook into
structured data for the unified Full Reporting tab.

This is the user's live Google-Sheets/Excel reporting system. We read it (never
write it) and normalise into:
  - trades:    one row per strategy leg across ALL strategy sheets (S1..S7, Master)
  - bank_reco: per-entity daily margin / funding / revenue / expense / net (M1..M5)
  - notes:     per-strategy notes (Strategy Note sheet)
  - dropdowns: strategy-group / strategy / symbol lists

Defensive by design: sheets have messy, shifting headers, so we fuzzy-match
column names and skip junk rows rather than assume fixed positions.
"""
from __future__ import annotations
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parent.parent
_WB_NAME = "Full strategy Reporting - including strategy notes.xlsx"


def _find_workbook() -> Path:
    """Locate the master workbook — the 'Google sheet excel reporting' folder first
    (where Rohan keeps it), then project root, then ~/Downloads. Returns the root
    path if none found (so callers report a clean 'not found')."""
    for cand in (ROOT / "Google sheet excel reporting" / _WB_NAME,
                 ROOT / _WB_NAME,
                 Path.home() / "Downloads" / _WB_NAME):
        if cand.exists():
            return cand
    return ROOT / _WB_NAME


WORKBOOK = _find_workbook()

# strategy sheets → (group, default_strategy, S-code). S-codes are the user's
# canonical labels: S1 CC-against-investment, S2A regular CC, S2B ITM CC, etc.
STRATEGY_SHEETS = {
    "S1 Options CC Inv":    ("Covered Calls", "Against Investment", "S1"),
    "S2 RHS New CC":        ("Covered Calls", "Regular CC", "S2"),       # split S2A/S2B per row
    "S3 RHS Indx":          ("Index", "Monthly/Weekly", "S3"),
    "S3 RHS Indx (Axis)":   ("Index", "Monthly/Weekly", "S3"),
    "S4 Expiry Opt":        ("Expiry", "Deep OTM Expiry", "S4"),
    "S4 Expiry Opt(B)":     ("Expiry", "Deep OTM Expiry", "S4"),
    "S5 Vish Indx":         ("Index", "Vish Index", "S5"),
    "S6 Long NIFTY":        ("Index", "Long NIFTY", "S6"),
    "S7 Commodity":         ("Commodity", "Commodity", "S7"),
    "FUTURES FOR INVESTMENT": ("Investment", "Futures for Investment", "FUT"),
}
M_SHEETS = {"M1 Mrgn SE": "SE", "M2 Mrgn SH": "SH", "M3 Mrgn MHS": "MHS",
            "M4 SAFAL ": "SAFAL", "M5 H1884": "H1884"}


def _num(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        if isinstance(v, str):
            v = v.replace(",", "").replace("₹", "").strip()
            if v in ("", "-", "nan", "NaT"):
                return None
        return float(v)
    except Exception:
        return None


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "NaT", "None") else s


_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

def _parse_expiry(opt_symbol: str):
    """From an option symbol like 'RELIANCE26JUL3000CE' pull the expiry → a label
    'Jul-26' and a sortable key '2026-07'. Returns (label, sort_key) or ('', '')."""
    import re
    m = re.search(r"(\d{2})([A-Z]{3})", (opt_symbol or "").upper())
    if not m or m.group(2) not in _MONTHS:
        return "", ""
    yy, mon = m.group(1), m.group(2)
    mi = _MONTHS.index(mon) + 1
    return f"{mon.title()}-{yy}", f"20{yy}-{mi:02d}"


def _find(cols, *names):
    """Return the column label whose normalised name matches any of `names`."""
    norm = {str(c).strip().lower(): c for c in cols}
    for want in names:
        w = want.lower()
        for k, orig in norm.items():
            if k == w or k.startswith(w):
                return orig
    return None


_CACHE = {"data": None, "mtime": None}

def load_report() -> dict:
    # cache by workbook mtime — re-parse only when the Excel actually changes
    try:
        mt = WORKBOOK.stat().st_mtime if WORKBOOK.exists() else None
        if _CACHE["data"] is not None and _CACHE["mtime"] == mt:
            return _CACHE["data"]
    except Exception:
        mt = None
    d = _load_report_uncached()
    _CACHE["data"], _CACHE["mtime"] = d, mt
    return d


def _load_report_uncached() -> dict:
    import pandas as pd
    if not WORKBOOK.exists():
        return {"error": f"workbook not found: {WORKBOOK.name}", "trades": [],
                "bank_reco": {}, "notes": [], "dropdowns": {}}
    xl = pd.ExcelFile(WORKBOOK)

    trades = []
    for sheet, (grp_default, strat_default, s_code) in STRATEGY_SHEETS.items():
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(xl, sheet_name=sheet)
        c = df.columns
        col = {
            "broker": _find(c, "broker"), "demat": _find(c, "demat", "demate"),
            "entity": _find(c, "entity", "client"), "status": _find(c, "status"),
            "sgroup": _find(c, "strategy group"), "strategy": _find(c, "strategy"),
            "trader": _find(c, "trader"), "date": _find(c, "trade date"),
            "stock": _find(c, "stock name"), "symbol": _find(c, "stock symbol", "symbol"),
            "lot": _find(c, "lot size"), "type": _find(c, "type"),
            "opt_symbol": _find(c, "option symbol"),
            "cur_price": _find(c, "current stock price"), "ltp": _find(c, "ltp"),
            "strike": _find(c, "strike"), "sell_price": _find(c, "sell price"),
            "sell_qty": _find(c, "sell qty"), "buy_price": _find(c, "buy price"),
            "buy_qty": _find(c, "buy qty"),
            "sell_total": _find(c, "sell total"),
            # P&L = realised/MTM gain, NOT "Net Total Amount" (that's notional value)
            "net_amt": _find(c, "net current gain", "net current p/l", "net current gain/loss", "net p/l", "net gain"),
            "notional": _find(c, "net total amount"),
            # TWO margins: original (manual, locked at entry → original yield logic) + live
            "margin_entry": _find(c, "margin consumed when trade taken", "margin when trade taken", "margin at entry"),
            "margin_live": _find(c, "current margin used", "total margin used", "margin used"),
            "ret": _find(c, "return %"), "notes": _find(c, "notes"),
        }
        for _, r in df.iterrows():
            sym = _s(r.get(col["symbol"])) if col["symbol"] else ""
            strike = _num(r.get(col["strike"])) if col["strike"] else None
            sell_q = _num(r.get(col["sell_qty"])) if col["sell_qty"] else None
            if not sym and strike is None and sell_q is None:
                continue
            grp = (_s(r.get(col["sgroup"])) if col["sgroup"] else "") or grp_default
            strat = (_s(r.get(col["strategy"])) if col["strategy"] else "") or strat_default
            # S-code: split S2 into S2A (regular) / S2B (ITM) by the row's strategy text
            code = s_code
            if s_code == "S2":
                code = "S2B" if "itm" in strat.lower() else "S2A"
            d = r.get(col["date"]) if col["date"] else None
            trades.append({
                "sheet": sheet, "s_code": code,
                "strategy_group": grp, "strategy": f"{code} · {strat}" if code else strat,
                "broker": _s(r.get(col["broker"])) if col["broker"] else "",
                "demat": (_s(r.get(col["demat"])) if col["demat"] else "") or (_s(r.get(col["entity"])) if col["entity"] else ""),
                "status": _s(r.get(col["status"])) if col["status"] else "",
                "trade_date": str(d)[:10] if d is not None and _s(d) not in ("", "NaT") else "",
                "symbol": sym, "stock": _s(r.get(col["stock"])) if col["stock"] else "",
                "type": _s(r.get(col["type"])) if col["type"] else "",
                "option_symbol": _s(r.get(col["opt_symbol"])) if col["opt_symbol"] else "",
                "expiry": _parse_expiry(_s(r.get(col["opt_symbol"])) if col["opt_symbol"] else "")[0],
                "expiry_key": _parse_expiry(_s(r.get(col["opt_symbol"])) if col["opt_symbol"] else "")[1],
                "strike": strike,
                "cur_price": _num(r.get(col["cur_price"])) if col["cur_price"] else None,
                "ltp": _num(r.get(col["ltp"])) if col["ltp"] else None,
                "sell_price": _num(r.get(col["sell_price"])) if col["sell_price"] else None,
                "sell_qty": sell_q,
                "buy_price": _num(r.get(col["buy_price"])) if col["buy_price"] else None,
                "buy_qty": _num(r.get(col["buy_qty"])) if col["buy_qty"] else None,
                "net_amount": _num(r.get(col["net_amt"])) if col["net_amt"] else None,
                "notional": _num(r.get(col["notional"])) if col["notional"] else None,
                "sell_total": _num(r.get(col["sell_total"])) if col["sell_total"] else None,
                "margin_entry": _num(r.get(col["margin_entry"])) if col["margin_entry"] else None,
                "margin_live": _num(r.get(col["margin_live"])) if col["margin_live"] else None,
                "trader": _s(r.get(col["trader"])) if col["trader"] else "",
                "note": _s(r.get(col["notes"])) if col["notes"] else "",
            })

    # ── Bank reco (M-sheets): per entity, daily margin/funding/revenue/expense/net ──
    bank = {}
    for sheet, entity in M_SHEETS.items():
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(xl, sheet_name=sheet)
        c = df.columns
        col = {k: _find(c, *names) for k, names in {
            "date": ("date",), "mfut": ("margin used (future)",),
            "mopt": ("margin used (options)",), "mtot": ("total margin used", "total required"),
            "avail": ("total available",), "ledger": ("ledger",),
            "pledge": ("margin from stock", "stock margin", "pledge"),
            "gsec": ("gsec", "g-sec"), "fd": ("fd",),
            "cash_blocked": ("cash blocked",), "req_fo": ("total req (as per f&o", "f&o report"),
            "in": ("money in",), "out": ("money out", "out"),
            "funding": ("funding",), "revenue": ("revenue",),
            "expense": ("expense",), "net": ("net",),
        }.items()}
        rows = []
        for _, r in df.iterrows():
            d = r.get(col["date"]) if col["date"] else None
            ds = str(d)[:10] if d is not None and _s(d) not in ("", "NaT") else ""
            if not ds:
                continue
            rows.append({"date": ds,
                "margin_fut": _num(r.get(col["mfut"])) if col["mfut"] else None,
                "margin_opt": _num(r.get(col["mopt"])) if col["mopt"] else None,
                "margin_total": _num(r.get(col["mtot"])) if col["mtot"] else None,
                "available": _num(r.get(col["avail"])) if col["avail"] else None,
                "ledger": _num(r.get(col["ledger"])) if col["ledger"] else None,
                "pledge": _num(r.get(col["pledge"])) if col["pledge"] else None,
                "gsec": _num(r.get(col["gsec"])) if col["gsec"] else None,
                "fd": _num(r.get(col["fd"])) if col["fd"] else None,
                "cash_blocked": _num(r.get(col["cash_blocked"])) if col["cash_blocked"] else None,
                "req_fo": _num(r.get(col["req_fo"])) if col["req_fo"] else None,
                "money_in": _num(r.get(col["in"])) if col["in"] else None,
                "money_out": _num(r.get(col["out"])) if col["out"] else None,
                "funding": _num(r.get(col["funding"])) if col["funding"] else None,
                "revenue": _num(r.get(col["revenue"])) if col["revenue"] else None,
                "expense": _num(r.get(col["expense"])) if col["expense"] else None,
                "net": _num(r.get(col["net"])) if col["net"] else None})
        bank[entity] = rows

    # ── Strategy notes ──
    notes = []
    if "Strategy Note" in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name="Strategy Note")
        c = df.columns
        col = {k: _find(c, *n) for k, n in {
            "sr": ("sr no",), "strategy": ("strategy",), "details": ("details",),
            "mgmt": ("management",), "target": ("target return",),
            "pledge": ("stock pledge",), "cash": ("cash fund",),
            "margin": ("total margin",), "pa": ("return pa",)}.items()}
        for _, r in df.iterrows():
            strat = _s(r.get(col["strategy"])) if col["strategy"] else ""
            if not strat or strat.lower() == "strategy":
                continue
            notes.append({"strategy": strat,
                "details": _s(r.get(col["details"])) if col["details"] else "",
                "management": _s(r.get(col["mgmt"])) if col["mgmt"] else "",
                "target_pm": _num(r.get(col["target"])) if col["target"] else None,
                "return_pa": _num(r.get(col["pa"])) if col["pa"] else None,
                "margin_cr": _num(r.get(col["margin"])) if col["margin"] else None})

    # ── Dropdowns ──
    dropdowns = {"strategy_groups": [], "strategies": []}
    if "Dropdowns" in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name="Dropdowns")
        gcol = _find(df.columns, "strategy group"); scol = _find(df.columns, "strategy list")
        if gcol is not None:
            dropdowns["strategy_groups"] = sorted({_s(v) for v in df[gcol].dropna() if _s(v)})
        if scol is not None:
            dropdowns["strategies"] = sorted({_s(v) for v in df[scol].dropna() if _s(v)})

    return {"trades": trades, "bank_reco": bank, "notes": notes,
            "dropdowns": dropdowns, "source": WORKBOOK.name}
