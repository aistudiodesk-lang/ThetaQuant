"""
lib/import_io.py — the import hub: downloadable templates + parsers for every
data type, with headers matching Rohan's "Full strategy Reporting" workbook.

Each type has:
  • columns  — (header, example1, example2, note) matching the Excel sheet
  • ingest() — parse a DataFrame into the right store (idempotent upserts)

Stores written:
  holdings      → lib.holdings_book   (Holdings-with-strategy book)
  cc_holdings   → lib.cc_holdings      (equity+futures per underlying for CC)
  trades        → lib.journal          (universal position ledger → reporting)
  broker_costs  → data/broker_costs.json

CSV is the canonical template (opens in Excel/Sheets); .xlsx uploads also parse.
"""
from __future__ import annotations
from pathlib import Path
import csv
import io
import hashlib
import json

ROOT = Path(__file__).resolve().parent.parent
BROKER_COSTS_FILE = ROOT / "data" / "broker_costs.json"

# ── column registry (headers mirror the workbook) ─────────────────────────
TYPES = {
    "holdings": {
        "title": "Holdings with strategy",
        "group": "Holdings",
        "target": "Holdings book (per client)",
        "desc": "Your equity + futures holdings, split by purpose and the strategy that covers them. One row per bucket; the tool groups rows by Client+Symbol.",
        "columns": [
            ("Client",       "RHS",        "RHS",        "Client/entity code"),
            ("Family",       "Navin Group","Navin Group","optional group above client"),
            ("Broker",       "Monarch",    "Axis",       "optional"),
            ("Demat",        "SE",         "MHS",        "optional demat account"),
            ("Stock Symbol", "ADANIENT",   "RELIANCE",   "NSE symbol"),
            ("Stock Name",   "Adani Enterprise", "Reliance Industries", "optional"),
            ("Holding Type", "Equity",     "Futures",    "Equity | Futures"),
            ("Purpose",      "Investment", "Trading",    "Investment | Trading"),
            ("Strategy",     "S1",         "NONE",       "S1 | S2A | S2B | S3 | S6 | S7 | NONE"),
            ("Quantity",     "85284",      "2500",       "shares (or fut qty)"),
            ("Avg Price",    "1190",       "2850",       "your average buy price"),
            ("Lot Size",     "300",        "250",        "F&O lot size"),
            ("Current Price","2943.8",     "1492",       "optional (auto-fills live)"),
            ("Let-Go Price", "1500",       "",           "agreed assignment price (optional)"),
        ],
    },
    "cc_holdings": {
        "title": "Covered-call holdings (equity + futures)",
        "group": "Covered Calls",
        "target": "CC holdings store",
        "desc": "Per-underlying equity and futures you hold, against which covered calls are sold. One row per symbol.",
        "columns": [
            ("Symbol",      "ADANIENT", "RELIANCE", "NSE symbol"),
            ("Name",        "Adani Enterprise", "Reliance", "optional"),
            ("Equity Qty",  "85284",   "0",        "shares held"),
            ("Equity Avg",  "1190",    "0",        "avg buy"),
            ("Futures Qty", "0",       "2500",     "futures held"),
            ("Futures Avg", "0",       "2850",     "avg buy"),
            ("Lot Size",    "300",     "250",      "F&O lot"),
            ("Current Price","2943.8", "1492",     "optional"),
            ("52wk High",   "3200",    "1600",     "optional"),
        ],
    },
    "trades": {
        "title": "Trades / positions (Master Entry)",
        "group": "Positions",
        "target": "Trade ledger → Reporting",
        "desc": "Your full options/futures ledger — one row per position. Matches your Master Entry sheet (Sell = entry, Buy = exit). Closed rows book the Net Total Amount as realized P&L; open rows stay live. Status drives open vs closed.",
        "columns": [
            ("Broker",            "Monarch",   "Axis",      ""),
            ("Entity",            "SHPL",      "SE",        "client/entity code"),
            ("Demat",             "S3859",     "S3858",     ""),
            ("Status",            "Closed",    "Open",      "Open | Closed"),
            ("Strategy",          "Deep OTM Expiry", "Covered Calls Against Investment", "your strategy name"),
            ("Trader",            "RHS",       "JM",        "optional"),
            ("Trade Date",        "2026-05-08","2026-05-12","YYYY-MM-DD"),
            ("Trade Time",        "10:15",     "",          "HH:MM (optional)"),
            ("Stock Symbol",      "NIFTY",     "ADANIENT",  "NIFTY/SENSEX or stock"),
            ("Option Symbol",     "NIFTY26MAY24500CE", "ADANIENT26MAY2600CE", "optional label"),
            ("Type",              "CE",        "PE",        "CE | PE | Future"),
            ("Strike price",      "24500",     "2600",      "blank for futures"),
            ("Sell Price",        "42.5",      "26.2",      "premium received (entry)"),
            ("Sell Qty",          "975",       "4944",      "total quantity sold"),
            ("Buy Price",         "0",         "",          "buyback price (blank if open)"),
            ("Buy Qty",           "0",         "",          "buyback qty (0 = expired worthless)"),
            ("Net Total Amount",  "41437",     "",          "realized P&L for closed (₹)"),
            ("Total Margin Used", "230000",    "185000",    "optional"),
        ],
    },
    "broker_costs": {
        "title": "Broker cost profiles",
        "group": "Settings",
        "target": "data/broker_costs.json",
        "desc": "Per-broker brokerage and statutory charges used in net-P&L. One row per broker.",
        "columns": [
            ("Broker",            "Monarch", "Axis", "broker name"),
            ("Brokerage Per Lot", "10",      "6",    "₹ per lot"),
            ("STT %",             "0.0625",  "0.0625","% of premium"),
            ("Txn %",             "0.035",   "0.035", "exchange txn %"),
            ("GST %",             "18",      "18",    "% on (brokerage+txn)"),
            ("Stamp %",           "0.003",   "0.003", "stamp duty %"),
            ("SEBI Per Cr",       "10",      "10",    "₹ per crore turnover"),
        ],
    },
}


def template_csv(key: str) -> str:
    t = TYPES[key]
    buf = io.StringIO()
    w = csv.writer(buf)
    headers = [c[0] for c in t["columns"]]
    w.writerow(headers)
    # example rows: first cell prefixed '#' so re-uploading the blank template
    # unchanged does NOT import these sample symbols (reader drops '#' rows).
    w.writerow(["# example: " + str(t["columns"][0][1])] + [c[1] for c in t["columns"][1:]])
    w.writerow(["# example: " + str(t["columns"][0][2])] + [c[2] for c in t["columns"][1:]])
    # a trailing notes line (commented) so the meaning of each column is on-sheet
    w.writerow(["# " + (t["columns"][0][3] or "")] + [c[3] for c in t["columns"][1:]])
    return buf.getvalue()


# ── parsing helpers ───────────────────────────────────────────────────────
def read_table(raw: bytes, filename: str):
    import pandas as pd
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xls")):
        # Read ALL sheets, not just the first — a multi-sheet monthly workbook
        # (April-26 / May-26 / June-26 …) must not silently drop months. Concat
        # every sheet that shares the first sheet's core columns; skip helper
        # sheets (Master Entry, Dropdowns…) whose headers don't match.
        book = pd.read_excel(io.BytesIO(raw), sheet_name=None)
        frames = []
        base_cols = None
        for sh, sdf in book.items():
            if sdf is None or sdf.empty:
                continue
            cols = {str(c).strip().lower() for c in sdf.columns}
            if base_cols is None:
                base_cols = cols
                frames.append(sdf)
            elif len(base_cols & cols) >= max(3, int(len(base_cols) * 0.6)):
                frames.append(sdf)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    else:
        df = pd.read_csv(io.BytesIO(raw))
    # drop the commented notes row if present (first col starts with '#')
    if len(df):
        first = str(df.iloc[df.index[0], 0] if df.shape[1] else "")
        # also drop any row whose first cell begins with '#'
        c0 = df.columns[0]
        df = df[~df[c0].astype(str).str.startswith("#")]
    return df


def _norm(s) -> str:
    return str(s).strip().lower().replace(" ", "").replace("_", "").replace("/", "").replace("%", "pct")


def _colmap(df):
    return {_norm(c): c for c in df.columns}


def _g(row, cmap, *aliases, default=None):
    import pandas as pd
    for a in aliases:
        c = cmap.get(_norm(a))
        if c is not None:
            v = row.get(c)
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                if isinstance(v, str) and v.strip().lower() in ("nan", "none", ""):
                    continue
                return v
    return default


def _num(v, default=None):
    if v is None:
        return default
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return default


def _norm_date(v):
    """Normalise any date cell to ISO YYYY-MM-DD (handles datetime, ISO, DD/MM/YYYY,
    DD-MM-YYYY, with 2- or 4-digit years). Returns None if unparseable."""
    import datetime, re
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):          # pandas NaT/NaN passes the isinstance(datetime) check → guard first
            return None
    except Exception:
        pass
    if isinstance(v, (datetime.datetime, datetime.date)):
        try:
            return v.strftime("%Y-%m-%d")
        except Exception:
            return None
    s = str(v).strip().split(" ")[0].split("T")[0]
    if not s or s.lower() in ("nan", "none"):
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", s)   # DD/MM/YYYY or DD-MM-YYYY
    if m:
        d, mo, y = m.groups(); y = ("20" + y) if len(y) == 2 else y
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except Exception:
            return None
    return None


# ── ingest per type ───────────────────────────────────────────────────────
def _ingest_holdings(df):
    from lib import holdings_book as HB
    cmap = _colmap(df)
    groups = {}      # (client, symbol) -> {lot, current, buckets[]}
    errors, skipped = [], 0
    for i, row in df.iterrows():
        sym = _g(row, cmap, "Stock Symbol", "Symbol", "Scrip", "Script")
        if not sym:
            skipped += 1
            continue
        sym = str(sym).strip().upper()
        client = str(_g(row, cmap, "Client", "Entity", default="RHS")).strip() or "RHS"
        htype = str(_g(row, cmap, "Holding Type", "Type", default="equity")).strip().lower()
        htype = "futures" if htype.startswith(("fut", "future")) else "equity"
        purpose = str(_g(row, cmap, "Purpose", default="investment")).strip().lower()
        purpose = "trading" if purpose.startswith("trad") else "investment"
        strat = str(_g(row, cmap, "Strategy", default="NONE")).strip().upper() or "NONE"
        qty = _num(_g(row, cmap, "Quantity", "Qty"), 0)
        avg = _num(_g(row, cmap, "Avg Price", "Avg", "Buy Rate"))
        demat = str(_g(row, cmap, "Demat", "Demat Account", default="")).strip()
        broker = str(_g(row, cmap, "Broker", default="")).strip()
        family = str(_g(row, cmap, "Family", "Group", default="")).strip()
        g = groups.setdefault((client, sym), {"lot": None, "current": None, "assignment_price": None, "family": "", "buckets": []})
        g["lot"] = g["lot"] or _num(_g(row, cmap, "Lot Size", "Lot"))
        g["current"] = g["current"] or _num(_g(row, cmap, "Current Price", "LTP", "CMP"))
        g["assignment_price"] = g["assignment_price"] or _num(_g(row, cmap, "Let-Go Price", "Let Go Price", "Assignment Price", "OK Price"))
        g["family"] = g["family"] or family
        if qty:
            bucket = {"type": htype, "purpose": purpose, "strategy": strat, "qty": qty, "avg": avg}
            if demat:
                bucket["demat"] = demat
            if broker:
                bucket["broker"] = broker
            g["buckets"].append(bucket)
    n = 0
    for (client, sym), g in groups.items():
        try:
            HB.upsert_holding(client, sym, g["buckets"], lot=g["lot"], current=g["current"],
                              assignment_price=g["assignment_price"], family=g["family"])
            n += 1
        except Exception as e:
            errors.append(f"{client}/{sym}: {e}")
    return {"imported": n, "skipped": skipped, "errors": errors}


def _ingest_cc_holdings(df):
    from lib import cc_holdings as CCH
    cmap = _colmap(df)
    n, skipped, errors = 0, 0, []
    for i, row in df.iterrows():
        sym = _g(row, cmap, "Symbol", "Stock Symbol", "Scrip")
        if not sym:
            skipped += 1
            continue
        sym = str(sym).strip().upper()
        fields = {
            "equity_qty": _num(_g(row, cmap, "Equity Qty", "Equity")),
            "equity_avg": _num(_g(row, cmap, "Equity Avg", "Equity Buy")),
            "futures_qty": _num(_g(row, cmap, "Futures Qty", "Future Qty", "Futures")),
            "futures_avg": _num(_g(row, cmap, "Futures Avg", "Future Avg")),
            "lot": _num(_g(row, cmap, "Lot Size", "Lot")),
            "current": _num(_g(row, cmap, "Current Price", "LTP", "CMP")),
            "high52": _num(_g(row, cmap, "52wk High", "52w High", "High52")),
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        try:
            CCH.upsert(sym, **fields)
            n += 1
        except Exception as e:
            errors.append(f"{sym}: {e}")
    return {"imported": n, "skipped": skipped, "errors": errors}


# Map the sheet's free-text Strategy → the platform's strategy_group bucket.
_STRAT_GROUP = {
    "deep otm expiry": "Expiry", "risky strangle expiry": "Expiry", "risky straddle expiry": "Expiry",
    "medium risk expiry": "Expiry", "expiry trades": "Expiry",
    "covered calls against investment": "Covered Calls", "options cc against inv": "Covered Calls",
    "itm covered calls": "Covered Calls", "regular new covered calls": "Covered Calls",
    "new rhs cc": "Covered Calls",
    "monthly rhs index": "Index", "monthly/weekly index": "Index", "long nifty": "Index",
}


def _derived_uid(broker, demat, sym, leg_type, strike, opt_sym, date, trade_group,
                 trade_time=None, entry_price=None):
    """Stable trade identity. qty is NOT in the key (a carry add/reduce must UPDATE its
    record, not mint a phantom). trade_time + entry_price ARE in the key so two DISTINCT
    same-strike tranches taken the same day don't collide and overwrite each other (C2):
    different fills differ by time and/or price. A byte-identical carry row keeps the
    same time+price → still resolves to the same uid (a clean no-op on re-import)."""
    key = "|".join(str(x if x is not None else "").strip().upper() for x in
                   (broker, demat, sym, leg_type, strike, opt_sym, date, trade_group,
                    trade_time, entry_price))
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _ingest_trades(df, dry_run=False):
    """One row = one leg. Captures EVERY column (raw, in `fields`) for later reporting
    /analysis, and promotes the analytically-load-bearing ones to first-class:
    entry-margin (locks the yield the trade was TAKEN on — margin drifts live),
    entry-spot (locks the distance %), entry-time, distance %, return %, trader,
    strategy/group codes, trade group. Idempotent upsert by derived trade_uid:
    same uid+content → skip (no churn); changed → replace; new → add."""
    from lib import journal
    import pandas as pd
    cmap = _colmap(df)
    cols = list(df.columns)
    n = n_open = n_closed = skipped = added = updated = unchanged = 0
    realized = 0.0
    errors = []
    for i, row in df.iterrows():
        sym = _g(row, cmap, "Stock Symbol", "Symbol")
        ttype = str(_g(row, cmap, "Type", default="")).strip().upper()
        opt_sym = str(_g(row, cmap, "Option Symbol", "Option", default="")).strip()
        sell_qty = _num(_g(row, cmap, "Sell Qty", "Qty", "Quantity"), 0) or 0
        if not sym or not ttype or not sell_qty:
            skipped += 1
            continue
        sym = str(sym).strip().upper()
        # robustness: if Type isn't CE/PE/FUT, recover it from the option symbol tail
        if ttype not in ("CE", "PE") and not (ttype.startswith("FUT") or ttype == "FUTURE"):
            o = opt_sym.upper()
            if o.endswith("CE"): ttype = "CE"
            elif o.endswith("PE"): ttype = "PE"
        leg_type = "FUT" if ttype.startswith("FUT") or ttype == "FUTURE" else ("EQ" if ttype in ("EQ", "EQUITY", "PHYSICAL") else ttype)
        # FLAG, don't guess (hard rule): a row whose Type can't resolve to CE/PE/FUT/EQ is
        # column-shifted/scrambled — importing it would book wrong strike/side/price.
        # Skip it and surface it in errors for MIS to fix.
        if leg_type not in ("CE", "PE", "FUT", "EQ"):
            skipped += 1
            if len(errors) < 30:
                errors.append(f"row {i}: unresolvable Type {str(ttype)[:40]!r} — column-shifted? SKIPPED (fix in sheet)")
            continue
        sell_price = _num(_g(row, cmap, "Sell Price", "Price", "Premium"), 0) or 0
        strike = _num(_g(row, cmap, "Strike Price", "Strike"))
        date = _norm_date(_g(row, cmap, "Trade Date", "Date"))
        tt = _g(row, cmap, "Trade Time", "Time")
        ttime = str(tt).strip()[:5] if (tt is not None and str(tt).strip().lower() not in ("", "none")) else None
        broker = str(_g(row, cmap, "Broker", default="")).strip()
        demat = str(_g(row, cmap, "Demat", default="")).strip()
        client = str(_g(row, cmap, "Client", "Entity", default="")).strip()
        strat = str(_g(row, cmap, "Strategy", "Strategy Group", default="")).strip()
        sgroup = _STRAT_GROUP.get(strat.lower(), strat or "Expiry")
        trader = str(_g(row, cmap, "Trader", default="")).strip()
        trade_group = str(_g(row, cmap, "Trade Group", default="")).strip()
        # entry-margin (yield-at-entry basis) vs live/auto margin — keep BOTH
        margin_entry = _num(_g(row, cmap, "Margin Consumed When Trade Taken",
                                "Margin When Trade Taken", "Margin At Entry"))
        margin_live = _num(_g(row, cmap, "Total Margin Used (Auto)", "Total Margin Used", "Margin Used", "Margin"))
        spot_entry = _num(_g(row, cmap, "Price When Trade Taken", "Spot When Trade Taken"))
        distance_pct = _num(_g(row, cmap, "Distance Call % when trade taken", "Distance %", "Distance"))
        return_pct = _num(_g(row, cmap, "Return %", "Return"))
        status = str(_g(row, cmap, "Status", default="")).strip().lower()
        # premium collected + the yield the trade was actually taken on (stable)
        premium = round(sell_price * abs(int(sell_qty)), 2) if sell_price else None
        entry_yield_pct = round(premium / margin_entry * 100, 3) if (premium and margin_entry) else None
        leg = {"strike": int(strike) if strike is not None else None, "side": leg_type,
               "qty": -abs(int(sell_qty)), "price": sell_price, "leg_type": leg_type,
               "premium": premium, "margin_at_entry": margin_entry, "margin_live": margin_live,
               "spot_at_entry": spot_entry, "distance_pct": distance_pct,
               "return_pct": return_pct, "entry_yield_pct": entry_yield_pct,
               "strategy_code": str(_g(row, cmap, "Strategy Code", default="")).strip(),
               "strategy_group_code": str(_g(row, cmap, "Strategy Group Code", default="")).strip(),
               "trade_group": trade_group}
        if opt_sym and opt_sym.lower() not in ("nan", "none"):
            leg["option_symbol"] = opt_sym
        if sym not in ("NIFTY", "SENSEX", "BANKNIFTY"):
            leg["underlying"] = sym
        # FULL raw row — every column, nothing dropped (used in reporting/analysis later)
        fields = {}
        for c in cols:
            v = row.get(c)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            fields[str(c).strip()] = v.item() if hasattr(v, "item") else v

        # P&L source differs by leg type (CRITICAL — see memory):
        #  • OPTIONS → "Net Total Amount" = realised P&L (closed) / premium (open).
        #  • FUTURES/EQUITY → "Net Total Amount" is NOTIONAL → use M2M instead.
        booked_pnl = None
        if status == "closed":
            if leg_type in ("FUT", "EQ"):
                pnl = _num(_g(row, cmap, "Net Current Gain/Loss", "Net Current Gain", "Daily M2M", "M2M"), 0) or 0
            else:
                pnl = _num(_g(row, cmap, "Net Total Amount", "Net Total", "Net Amount"))
                if pnl is None:
                    buy_qty = _num(_g(row, cmap, "Buy Qty"), 0) or 0
                    buy_price = _num(_g(row, cmap, "Buy Price"))
                    exit_qty = abs(int(buy_qty)) if buy_qty else abs(int(sell_qty))
                    exit_price = buy_price if buy_price is not None else 0.0
                    pnl = (sell_price - exit_price) * exit_qty
            booked_pnl = round(pnl or 0, 2)

        # identity + change-detection
        uid = _derived_uid(broker, demat, sym, leg_type, strike, opt_sym, date, trade_group,
                           trade_time=ttime, entry_price=sell_price)
        mutable = (status, _num(_g(row, cmap, "Buy Price")), _num(_g(row, cmap, "Buy Qty")),
                   _num(_g(row, cmap, "LTP")), _num(_g(row, cmap, "Net Current Gain/Loss")),
                   margin_live, booked_pnl)
        content_hash = hashlib.md5(repr(mutable).encode()).hexdigest()[:12]

        try:
            existing = journal.find_by_uid(uid)
            if existing and existing.get("content_hash") == content_hash:
                unchanged += 1
                n += 1
                n_closed += 1 if status == "closed" else 0
                n_open += 0 if status == "closed" else 1
                if booked_pnl is not None:
                    realized += booked_pnl
                continue                      # idempotent no-op — no churn
            if not dry_run:
                if existing:
                    journal.delete_trade(existing["id"])   # changed → replace cleanly
                t = journal.add_trade(instrument=sym, tier=strat or sgroup, legs=[leg],
                        entry_date=date, entry_time=ttime, broker=broker, demat=demat,
                        client=client, trader=trader, strategy_group=sgroup,
                        margin_at_entry=margin_entry, source="import",
                        trade_uid=uid, content_hash=content_hash, fields=fields)
                if booked_pnl is not None:
                    journal.close_trade(t["id"], exit_date=date, pnl=booked_pnl,
                                        note="imported (closed)")
            updated += 1 if existing else 0
            added += 0 if existing else 1
            if booked_pnl is not None:
                realized += booked_pnl
            n += 1
            n_closed += 1 if status == "closed" else 0
            n_open += 0 if status == "closed" else 1
        except Exception as e:
            errors.append(f"row {i}: {e}")
    return {"imported": n, "open": n_open, "closed": n_closed,
            "added": added, "updated": updated, "unchanged": unchanged,
            "realized": round(realized), "skipped": skipped, "errors": errors[:8],
            "note": f"{n} legs ({n_open} open · {n_closed} closed) · {added} new · {updated} changed · {unchanged} unchanged · realized ₹{round(realized):,}"}


def _ingest_broker_costs(df):
    cmap = _colmap(df)
    try:
        cfg = json.loads(BROKER_COSTS_FILE.read_text()) if BROKER_COSTS_FILE.exists() else {}
    except Exception:
        cfg = {}
    n, skipped, errors = 0, 0, []
    for i, row in df.iterrows():
        bk = _g(row, cmap, "Broker", "Broker Name")
        if not bk:
            skipped += 1
            continue
        bk = str(bk).strip()
        prof = dict(cfg.get(bk, {}))
        for field, *aliases in [("brokerage_per_lot", "Brokerage Per Lot", "Per Lot"),
                                ("stt_pct", "STT %", "STT"), ("txn_pct", "Txn %", "Txn"),
                                ("gst_pct", "GST %", "GST"), ("stamp_pct", "Stamp %", "Stamp"),
                                ("sebi_per_cr", "SEBI Per Cr", "SEBI")]:
            v = _num(_g(row, cmap, *aliases))
            if v is not None:
                prof[field] = v
        cfg[bk] = prof
        n += 1
    try:
        BROKER_COSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        BROKER_COSTS_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        errors.append(str(e))
    return {"imported": n, "skipped": skipped, "errors": errors}


_INGEST = {
    "holdings": _ingest_holdings,
    "cc_holdings": _ingest_cc_holdings,
    "trades": _ingest_trades,
    "broker_costs": _ingest_broker_costs,
}


def ingest(key: str, df) -> dict:
    if key not in _INGEST:
        raise ValueError(f"unknown import type: {key}")
    return _INGEST[key](df)
