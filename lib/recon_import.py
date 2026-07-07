"""Reconciliation ingestion — parsers for the truth-side feeds (Layer B).

Source formats (samples 2026-06-28):
  • mProfit transaction export (CSV) — auto-collected from contract notes via email
    auto-forward. Convenient + automated, but mProfit is weak at options/futures so it
    must be verified against raw contract notes.
  • Broker contract notes (PDF, per-broker formats: Monarch, Axis, …) — the ground truth.

This module owns the parsing → a normalized transaction model that the reconciliation
engine diffs against Layer A (Google sheet / manual). Read-only; never writes the source.

Normalized transaction:
  {date, side(BUY/SELL), underlying, kind(FUTSTK/OPTSTK/OPTIDX/FUTIDX), is_option,
   is_index, expiry, strike, opt_type(CE/PE), qty, price, amount, source, raw_asset}
"""
from __future__ import annotations
import csv
from datetime import datetime


def _num(s) -> float:
    if s is None:
        return 0.0
    s = str(s).replace(",", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_asset(asset: str) -> dict:
    """'SENSEX-OPTIDX:25-06-26:79100.00:CE' or 'CANBK-FUTSTK:30-06-26' → parts."""
    a = (asset or "").strip()
    parts = a.split(":")
    head = parts[0]                         # e.g. CANBK-FUTSTK
    if "-" in head:
        und, kind = head.rsplit("-", 1)
    else:
        und, kind = head, ""
    kind = kind.upper()
    expiry = parts[1] if len(parts) > 1 else None
    strike = _num(parts[2]) if len(parts) > 2 else None
    opt_type = parts[3].upper() if len(parts) > 3 else None
    return {
        "underlying": und.upper(), "kind": kind,
        "is_option": kind in ("OPTSTK", "OPTIDX"),
        "is_index": kind in ("OPTIDX", "FUTIDX"),
        "expiry": expiry, "strike": strike, "opt_type": opt_type,
    }


def _norm_date(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%m-%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def parse_mprofit_csv(path: str) -> list[dict]:
    """Parse an mProfit 'Transaction Export' CSV → normalized transactions."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            asset = r.get("Asset") or ""
            if not asset:
                continue
            a = parse_asset(asset)
            out.append({
                "date": _norm_date(r.get("Date")),
                "side": (r.get("Trans. Type") or "").strip().upper(),   # BUY / SELL
                "qty": int(_num(r.get("Qty"))),
                "price": _num(r.get("Price")),
                "amount": _num(r.get("Amount")),
                "source": "mprofit", "raw_asset": asset, **a,
            })
    return out


import re

_MON_MAP = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
            "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}
# 'OPTSTK BAJFINANCE 28Jul2026 1060 CE-NSE'  /  'FUTSTK AMBUJACEM 30Jun2026-NSE'
_DESC_RE = re.compile(
    r"^(FUT|OPT)(STK|IDX)\s+(\S+)\s+(\d{1,2})([A-Za-z]{3})(\d{4})(?:\s+([\d.]+)\s+(CE|PE))?")


def parse_contract_desc(desc: str) -> dict:
    m = _DESC_RE.match((desc or "").strip())
    if not m:
        return {"underlying": (desc or "").strip()[:20], "kind": "", "is_option": False,
                "is_index": False, "expiry": None, "strike": None, "opt_type": None}
    fo, si, sym, dd, mon, yyyy, strike, ot = m.groups()
    kind = f"{fo}{si}"                                   # FUTSTK / OPTSTK / OPTIDX / FUTIDX
    exp = f"{dd.zfill(2)}-{_MON_MAP.get(mon.title(), '00')}-{yyyy[2:]}" if mon else None
    return {"underlying": sym.upper(), "kind": kind,
            "is_option": si == "IDX" and ot is not None or fo == "OPT",
            "is_index": si == "IDX", "expiry": exp,
            "strike": _num(strike) if strike else None, "opt_type": ot}


def parse_monarch_pdf(path: str, password: str = "") -> dict:
    """Parse a Monarch Networth contract note (PDF) → header + day totals + normalized
    transactions. Per-broker parser (Axis/others get their own). Needs pikepdf+pdfplumber."""
    import pikepdf, pdfplumber, tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
    try:
        with pikepdf.open(path, password=password) as pdf:
            pdf.save(tmp)
        head, totals, txns = {}, {}, []
        with pdfplumber.open(tmp) as p:
            txt = p.pages[0].extract_text() or ""
            for key, label in [("ucc", "Client Code/UCC"), ("client", "Client Name"),
                               ("pan", "PAN of Client"), ("trade_date", "Trade Date"),
                               ("contract_no", "Contract / Invoice No")]:
                m = re.search(rf"{re.escape(label)}\s*:\s*([^\n]+)", txt)
                if m:
                    head[key] = m.group(1).split("  ")[0].strip()
            tot_keys = {"Buy": "buy", "Sell": "sell", "Brokerage": "brokerage",
                        "Statutory Charges": "statutory", "Net Amount Receiva": "net_receivable",
                        "Net Amount Payab": "net_payable", "Ledger Balance": "ledger"}
            for pg in p.pages:
                for t in pg.extract_tables():
                    if not t:
                        continue
                    h0 = (t[0][0] or "")
                    if len(t[0]) == 2 and "Total" in h0:                       # totals table
                        for row in t:
                            lbl = (row[0] or "").strip()
                            for k, dest in tot_keys.items():
                                if lbl.startswith(k):
                                    totals[dest] = _num(row[1])
                    if len(t[0]) >= 10 and "Contract" in h0:                    # F&O txns
                        for row in t[1:]:
                            d = (row[0] or "").replace("\n", " ").strip()
                            side = (row[1] or "").strip().upper()
                            if not d or d.startswith("NSEFNO") or not side:
                                continue
                            a = parse_contract_desc(d)
                            norm = {"BUY": "BUY", "BF": "BUY", "SALE": "SELL", "CF": "SELL"}.get(side, side)
                            txns.append({
                                "date": head.get("trade_date"), "side": norm, "carry": side,
                                "qty": abs(int(_num(row[2]))), "price": _num(row[4]),
                                "amount": abs(_num(row[8])), "net_signed": _num(row[8]),
                                "source": "monarch_cn", "raw_asset": d, **a,
                            })
        return {"broker": "Monarch", "header": head, "totals": totals,
                "transactions": txns, "n_txns": len(txns)}
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def positions(txns: list[dict]) -> dict:
    """Aggregate into positions keyed by instrument. Options → realized P&L
    (sell premium − buy premium, since they settle as premium). Futures → kept as
    notional/qty (per data_net_total_amount_semantics: futures are NOT realised here)."""
    pos: dict = {}
    for t in txns:
        key = (t["underlying"], t["kind"], t.get("expiry"), t.get("strike"), t.get("opt_type"))
        p = pos.setdefault(key, {
            "underlying": t["underlying"], "kind": t["kind"], "expiry": t.get("expiry"),
            "strike": t.get("strike"), "opt_type": t.get("opt_type"), "is_option": t["is_option"],
            "buy_qty": 0, "sell_qty": 0, "buy_amt": 0.0, "sell_amt": 0.0, "n": 0,
        })
        p["n"] += 1
        if t["side"] == "BUY":
            p["buy_qty"] += t["qty"]; p["buy_amt"] += t["amount"]
        elif t["side"] == "SELL":
            p["sell_qty"] += t["qty"]; p["sell_amt"] += t["amount"]
    for p in pos.values():
        if p["is_option"]:
            p["realized"] = round(p["sell_amt"] - p["buy_amt"], 2)   # premium captured
        else:
            p["realized"] = None                                     # futures = notional, not here
            p["net_qty"] = p["buy_qty"] - p["sell_qty"]
            p["net_notional"] = round(p["buy_amt"] - p["sell_amt"], 2)
    return pos


def summary(txns: list[dict]) -> dict:
    pos = positions(txns)
    opt = [p for p in pos.values() if p["is_option"]]
    fut = [p for p in pos.values() if not p["is_option"]]
    dates = sorted({t["date"] for t in txns})
    return {
        "n_txns": len(txns), "n_positions": len(pos),
        "n_option_positions": len(opt), "n_future_positions": len(fut),
        "options_realized": round(sum(p["realized"] for p in opt), 2),
        "date_from": dates[0] if dates else None, "date_to": dates[-1] if dates else None,
        "underlyings": sorted({t["underlying"] for t in txns}),
    }
