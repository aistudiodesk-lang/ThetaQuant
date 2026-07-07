"""
lib/holdings_book.py — the "Holdings with strategy" book.

Each underlying is held in one or more BUCKETS, every bucket tagged with:
  - type:     equity | futures
  - purpose:  investment | trading
  - strategy: S1 (CC against investment) | S2A (regular CC) | S2B (ITM CC) | NONE (trading, no CC)
  - qty, avg

So RELIANCE can be e.g.
  equity/investment/S1   50,000 @2400
  futures/investment/S1  30,000 @2410
  futures/trading/S2A    25,000 @2500
  equity/trading/NONE     5,000 @2550
→ 1,10,000 held, with strategy-wise splits that flow into each covered-call desk.

Everything is scoped to a CLIENT (default "RHS" — the internal book). The same
structures support future client-wise views: pick a client, the book loads for them.

Store: data/holdings_book.json (local JSON; host-agnostic, portability rule).
Data flows in from the internal sheets or manual entry.
"""
from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "holdings_book.json"

DEFAULT_CLIENT = "RHS"
TYPES = ("equity", "futures")
PURPOSES = ("investment", "trading")
STRATEGIES = ("S1", "S2A", "S2B", "NONE")   # NONE = trading position, no covered call


def _load() -> list[dict]:
    try:
        return json.loads(STORE.read_text()) if STORE.exists() else []
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(rows, indent=2))


def list_clients() -> list[str]:
    cl = sorted({(r.get("client") or DEFAULT_CLIENT) for r in _load()})
    return cl or [DEFAULT_CLIENT]


def _is_all(client: str) -> bool:
    return (client or "").strip().lower() in ("", "all")


def _rows(client: str) -> list[dict]:
    if _is_all(client):
        return _load()                       # All clients
    return [r for r in _load() if (r.get("client") or DEFAULT_CLIENT) == client]


def upsert_holding(client: str, symbol: str, buckets: list[dict],
                   lot=None, current=None, high52=None, assignment_price=None,
                   family=None) -> dict:
    """Replace the full bucket list for one (client, symbol).
    assignment_price = the OK "let-go" price for this investment (the price we've
    agreed we'd accept assignment at — sent to the client for approval).
    family = optional grouping above client (e.g. 'Navin Group' over HUF entities).
    Each bucket may carry an optional demat + broker so holdings can be grouped/
    reported by demat account and broker too."""
    client = (client or DEFAULT_CLIENT)
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol required")
    clean = []
    for b in buckets or []:
        q = b.get("qty")
        if not q:
            continue
        cb = {
            "type": b.get("type") if b.get("type") in TYPES else "equity",
            "purpose": b.get("purpose") if b.get("purpose") in PURPOSES else "investment",
            "strategy": (b.get("strategy") or "NONE").upper() if (b.get("strategy") or "NONE").upper() in STRATEGIES else "NONE",
            "qty": float(q),
            "avg": float(b["avg"]) if b.get("avg") not in (None, "") else None,
        }
        if (b.get("demat") or "").strip():
            cb["demat"] = str(b["demat"]).strip()
        if (b.get("broker") or "").strip():
            cb["broker"] = str(b["broker"]).strip()
        clean.append(cb)
    all_rows = _load()
    all_rows = [r for r in all_rows
                if not ((r.get("client") or DEFAULT_CLIENT) == client and (r.get("symbol") or "").upper() == symbol)]
    row = {"client": client, "symbol": symbol, "buckets": clean}
    if (family or "").strip():
        row["family"] = str(family).strip()
    for k, v in (("lot", lot), ("current", current), ("high52", high52),
                 ("assignment_price", assignment_price)):
        if v not in (None, ""):
            row[k] = float(v)
    all_rows.append(row)
    _save(all_rows)
    return row


def list_families() -> list[str]:
    return sorted({(r.get("family") or "").strip() for r in _load() if (r.get("family") or "").strip()})


def list_demats(client: str = "") -> list[str]:
    out = set()
    for r in _rows(client):
        for b in (r.get("buckets") or []):
            if (b.get("demat") or "").strip():
                out.add(b["demat"].strip())
    return sorted(out)


def _sold_calls_by_symbol(client: str) -> dict:
    """Open short-CE quantity per underlying (the cover already sold), from the
    universal journal. Used to split holdings into covered vs uncovered."""
    out = {}
    try:
        from lib import journal
        want_all = _is_all(client)
        for t in journal.all_trades():
            if t.get("status") != "open":
                continue
            for l in t.get("legs") or []:
                lt = (l.get("leg_type") or l.get("side") or "").upper()
                q = l.get("qty") or 0
                if lt == "CE" and q < 0:
                    sym = (l.get("underlying") or t.get("instrument") or "").upper()
                    if sym:
                        out[sym] = out.get(sym, 0) + abs(q)
    except Exception:
        pass
    return out


def _sold_options_by_symbol(client: str) -> dict:
    """All open CE/PE legs per underlying — the full 'what's sold against this name'
    picture: side, strike, qty, premium, the strategy it sits under, and demat."""
    out = {}
    try:
        from lib import journal
        for t in journal.all_trades():
            if t.get("status") != "open":
                continue
            grp = t.get("strategy_group") or ""
            tier = t.get("strategy_name") or t.get("tier") or ""
            for l in t.get("legs") or []:
                lt = (l.get("leg_type") or l.get("side") or "").upper()
                q = l.get("qty") or 0
                if lt in ("CE", "PE") and q < 0:
                    sym = (l.get("underlying") or t.get("instrument") or "").upper()
                    if sym:
                        out.setdefault(sym, []).append({
                            "side": lt, "strike": l.get("strike"), "qty": abs(q),
                            "price": l.get("price"), "group": grp, "tier": tier,
                            "demat": l.get("demat") or "",
                        })
    except Exception:
        pass
    # sort each symbol's legs by side then strike
    for sym in out:
        out[sym].sort(key=lambda x: (x["side"], x.get("strike") or 0))
    return out


def delete_holding(client: str, symbol: str) -> bool:
    client = client or DEFAULT_CLIENT
    symbol = (symbol or "").strip().upper()
    rows = _load()
    new = [r for r in rows if not ((r.get("client") or DEFAULT_CLIENT) == client and (r.get("symbol") or "").upper() == symbol)]
    if len(new) == len(rows):
        return False
    _save(new)
    return True


def _purpose_for(s_code: str, group: str) -> str:
    """Investment book (FUT / S1 / Investment group) vs trading (S2A/S2B momentum)."""
    sc = (s_code or "").upper()
    if sc in ("S2A", "S2B"):
        return "trading"
    return "investment"


def _workbook_legs():
    """The ingested master workbook is the source of truth for the held book:
    futures positions (per strategy + demat) and the option legs sold against each
    underlying. Equity quantities come from the Selling-Plan sheet (merged_holdings).
    Returns (futures_by_symbol, options_by_symbol). Only the equity-name books
    (Covered Calls + Investment groups) — Index/Commodity/Expiry have their own tabs."""
    fut_by, opt_by = {}, {}
    try:
        from lib import full_report as fr
        trades = fr.load_report().get("trades", [])
    except Exception:
        return fut_by, opt_by
    KEEP = ("Covered Calls", "Investment")
    for r in trades:
        grp = r.get("strategy_group") or ""
        if grp not in KEEP:
            continue
        sym = (r.get("symbol") or "").upper()
        if not sym:
            continue
        typ = (r.get("type") or "").upper()
        s_code = r.get("s_code") or ""
        if typ == "FUTURE":
            q = r.get("buy_qty") or r.get("sell_qty") or 0
            if not q:
                continue
            notional = r.get("notional")
            avg = round(abs(notional) / q, 2) if (notional and q) else None
            fut_by.setdefault(sym, []).append({
                "type": "futures", "purpose": _purpose_for(s_code, grp),
                "strategy": s_code or "NONE", "qty": float(q), "avg": avg,
                "demat": r.get("demat") or "",
            })
        elif typ in ("CE", "PE"):
            strike = r.get("strike")
            q = r.get("sell_qty") or 0
            if strike is None or not q:
                continue
            opt_by.setdefault(sym, []).append({
                "side": typ, "strike": strike, "qty": abs(q),
                "price": r.get("sell_price"), "group": s_code or grp,
                "demat": r.get("demat") or "", "expiry": r.get("expiry") or "",
                "expiry_key": r.get("expiry_key") or "", "status": r.get("status") or "",
            })
    for sym in opt_by:
        opt_by[sym].sort(key=lambda x: (x["side"], x.get("strike") or 0))
    return fut_by, opt_by


def book(client: str = DEFAULT_CLIENT) -> dict:
    """Per-symbol totals + strategy/purpose/type splits, covered-vs-uncovered, and
    assignment price. The held book is built from the INGESTED workbook (equity from
    the Selling-Plan sheet, futures + option legs per strategy/demat from the trade
    rows) so every strategy is linked; manual holdings_book.json buckets override a
    symbol when present."""
    from lib import cc_holdings as CCH
    manual_by = {(_r.get("symbol") or "").upper(): _r for _r in _rows(client)}
    fut_by, opt_by = ({}, {})
    merged = {}
    if _is_all(client) or (client or DEFAULT_CLIENT) == DEFAULT_CLIENT:
        fut_by, opt_by = _workbook_legs()
        try:
            merged = {(_h.get("symbol") or "").upper(): _h for _h in CCH.merged_holdings()}
        except Exception:
            merged = {}

    rows = []
    tot = {"total_qty": 0.0, "value": 0.0, "invested": 0.0, "covered_qty": 0.0, "uncovered_qty": 0.0,
           "equity_value": 0.0, "futures_value": 0.0,
           "by_strategy": {}, "by_purpose": {p: 0.0 for p in PURPOSES},
           "by_type": {t: 0.0 for t in TYPES}}

    # assemble one source record per symbol: workbook-derived buckets (equity from the
    # Selling-Plan sheet, futures per strategy/demat from trade rows), manual overrides win.
    syms = set(merged) | set(manual_by) | set(fut_by)
    recs = []
    for sym in syms:
        m = merged.get(sym, {})
        man = manual_by.get(sym, {})
        cur = man.get("current") if man.get("current") not in (None, "") else m.get("current")
        if man.get("buckets"):
            buckets = man["buckets"]                              # manual override wins
        else:
            buckets = []
            eq = m.get("equity_qty") or 0
            if eq:
                buckets.append({"type": "equity", "purpose": "investment",
                                "strategy": "S1", "qty": float(eq), "avg": m.get("equity_avg")})
            fb = fut_by.get(sym, [])
            if fb:
                buckets.extend(fb)
            elif (m.get("futures_qty") or 0):
                buckets.append({"type": "futures", "purpose": "investment",
                                "strategy": "NONE", "qty": float(m["futures_qty"]), "avg": m.get("futures_avg")})
        if not buckets:
            continue
        recs.append({"symbol": sym, "buckets": buckets, "current": cur,
                     "high52": man.get("high52") or m.get("high52"),
                     "lot": man.get("lot") or m.get("lot"),
                     "family": man.get("family") or "", "client": man.get("client") or DEFAULT_CLIENT,
                     "assignment_price": man.get("assignment_price"),
                     "ce_sold": m.get("ce_sold_qty") or 0})

    for r in recs:
        buckets = r["buckets"]
        total = sum(b.get("qty") or 0 for b in buckets)
        cur = r.get("current")
        by_strategy = {}
        by_purpose = {p: 0.0 for p in PURPOSES}
        by_type = {t: 0.0 for t in TYPES}
        for b in buckets:
            q = b.get("qty") or 0
            st = b.get("strategy", "NONE")
            by_strategy[st] = by_strategy.get(st, 0) + q
            by_purpose[b.get("purpose", "investment")] = by_purpose.get(b.get("purpose", "investment"), 0) + q
            by_type[b.get("type", "equity")] = by_type.get(b.get("type", "equity"), 0) + q
        sym = r["symbol"]
        # covered = short-CE qty (the cover sold against the held stock), capped at equity held
        eq_held = by_type["equity"]
        legs = opt_by.get(sym, [])
        # CE coverage split by expiry month (June vs July …) — a name can carry both
        cov_by_exp = {}
        for o in legs:
            if o.get("side") == "CE":
                k = o.get("expiry_key") or "—"
                cov_by_exp[k] = cov_by_exp.get(k, 0) + (o.get("qty") or 0)
        ce_total_legs = sum(cov_by_exp.values())
        # prefer the sheet's ce_sold total when present (authoritative); else sum of legs
        ce_sold = r.get("ce_sold", 0) or ce_total_legs
        covered = min(eq_held, ce_sold) if eq_held else 0
        uncovered = max(0, eq_held - covered)
        cov_by_exp = {k: round(v) for k, v in cov_by_exp.items()}
        eq_v = round(by_type["equity"] * cur) if cur else None
        fut_v = round(by_type["futures"] * cur) if cur else None
        ap = r.get("assignment_price")
        assign_gain = round((ap - cur) * covered) if (ap and cur and covered) else None
        invested = sum((b.get("qty") or 0) * (b.get("avg") or 0) for b in buckets if b.get("avg"))
        cur_value = round(total * cur) if (total and cur) else None
        blended_avg = round(invested / total, 2) if (total and invested) else None
        unreal = round((cur_value - invested)) if (cur_value is not None and invested) else None
        unreal_pct = round(unreal / invested * 100, 2) if (unreal is not None and invested) else None
        rows.append({
            "symbol": sym, "lot": r.get("lot"),
            "client": r.get("client") or DEFAULT_CLIENT, "family": r.get("family") or "",
            "current": cur, "high52": r.get("high52"),
            "assignment_price": ap, "assignment_gain": assign_gain,
            "total_qty": total, "value": cur_value,
            "invested": round(invested) if invested else None, "avg": blended_avg,
            "unrealised": unreal, "unrealised_pct": unreal_pct,
            "covered_qty": covered, "uncovered_qty": uncovered,
            "covered_pct": round(covered / eq_held * 100) if eq_held else 0,
            "coverage_by_expiry": cov_by_exp,
            "equity_qty": by_type["equity"], "futures_qty": by_type["futures"],
            "equity_value": eq_v, "futures_value": fut_v,
            "by_strategy": {k: v for k, v in by_strategy.items() if v},
            "by_purpose": {k: v for k, v in by_purpose.items() if v},
            "by_type": {k: v for k, v in by_type.items() if v},
            "buckets": buckets,
            "options": opt_by.get(sym, []),
        })
        tot["total_qty"] += total
        tot["covered_qty"] += covered; tot["uncovered_qty"] += uncovered
        tot["invested"] += invested
        if total and cur:
            tot["value"] += total * cur
            tot["equity_value"] += by_type["equity"] * cur
            tot["futures_value"] += by_type["futures"] * cur
        for s, v in by_strategy.items(): tot["by_strategy"][s] = tot["by_strategy"].get(s, 0) + v
        for p in PURPOSES: tot["by_purpose"][p] += by_purpose[p]
        for t in TYPES: tot["by_type"][t] += by_type[t]
    rows.sort(key=lambda x: -(x.get("value") or 0))
    tot["unrealised"] = round(tot["value"] - tot["invested"]) if tot["invested"] else None
    tot["unrealised_pct"] = round(tot["unrealised"] / tot["invested"] * 100, 2) if tot["invested"] else None
    for k in ("total_qty", "value", "invested", "covered_qty", "uncovered_qty", "equity_value", "futures_value"):
        tot[k] = round(tot[k])
    tot["covered_pct"] = round(tot["covered_qty"] / tot["total_qty"] * 100) if tot["total_qty"] else 0
    tot["by_strategy"] = {k: round(v) for k, v in tot["by_strategy"].items() if v}
    tot["by_purpose"] = {k: round(v) for k, v in tot["by_purpose"].items() if v}
    tot["by_type"] = {k: round(v) for k, v in tot["by_type"].items() if v}
    # available expiry months for coverage filtering (e.g. Jun-26, Jul-26), sorted
    exp_months = {}
    for r in rows:
        for k in (r.get("coverage_by_expiry") or {}):
            if k and k != "—":
                exp_months[k] = _month_label(k)
    expiry_months = [{"key": k, "label": v} for k, v in sorted(exp_months.items())]
    return {"client": client or DEFAULT_CLIENT, "clients": list_clients(),
            "families": list_families(), "demats": list_demats(client),
            "rows": rows, "totals": tot, "expiry_months": expiry_months}


def _month_label(key: str) -> str:
    """'2026-06' -> 'Jun-26'."""
    try:
        y, m = key.split("-")
        names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return f"{names[int(m)]}-{y[2:]}"
    except Exception:
        return key
