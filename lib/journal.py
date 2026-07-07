"""
lib/journal.py — trade journal: the learning substrate.

Every trade (from desk button, manual entry, CSV upload, or screenshot) lands
here as one JSONL row with FULL context captured at entry:
  entry_time, regime snapshot, tier, strikes, premiums, qty, broker, source.

On close, exit details append. analyses/900_learning_loop.py consumes this to:
  - compare actual vs backtest expectation per (tier, regime)
  - flag drift → FINDINGS_LOG
  - feed recalibration of playbook tables as data grows.

Storage: data/trade_journal.jsonl (append-only; one JSON object per line).
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from lib.config import JOURNAL  # env-overridable; default data/trade_journal.jsonl

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent


def _append(obj: dict) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def _load() -> list[dict]:
    if not JOURNAL.exists():
        return []
    out = []
    for line in JOURNAL.read_text().splitlines():
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def add_trade(instrument: str, tier: str, legs: list[dict],
              entry_time: str = None, entry_date: str = None,
              broker: str = "", source: str = "manual",
              regime_snapshot: dict = None, note: str = "",
              strategy_name: str = "", portfolio: str = "",
              strategy_group: str = "Expiry", margin_at_entry: float = None,
              demat: str = "", client: str = "", trader: str = "",
              trade_uid: str = None, content_hash: str = None,
              fields: dict = None, sl: dict = None, tp: dict = None) -> dict:
    """Register a new trade/strategy across ANY strategy group. The one ledger for
    the whole platform. legs = [{strike, side, qty, price, demat?, leg_type?, underlying?}].
    leg_type ∈ CE|PE|FUT|EQ (default inferred from side). underlying = stock symbol
    for covered calls. strategy_group ∈ Expiry|Index|Covered Calls|Dummy|...
    entry_time 'HH:MM' (IST). strategy_name e.g. 'ADANIENT CC Aug'.
    """
    now = datetime.now(IST)
    # normalise legs: ensure leg_type present
    for l in legs or []:
        if not l.get("leg_type"):
            l["leg_type"] = (l.get("side") or "").upper() or "CE"
    rec = {
        "id": uuid.uuid4().hex[:10],
        "type": "entry",
        "recorded_at": now.isoformat(),
        "entry_date": entry_date or now.strftime("%Y-%m-%d"),
        # '' kept blank on purpose (screenshot upload — fill time later via Edit);
        # only a genuinely-omitted (None) entry_time defaults to upload time.
        "entry_time": (entry_time if entry_time is not None else now.strftime("%H:%M")),
        "instrument": instrument.upper(),
        "tier": tier,
        "strategy_group": strategy_group,
        "legs": legs,
        "broker": broker,
        "demat": demat, "client": client, "trader": trader,
        "margin_at_entry": margin_at_entry,   # manual: locks the original-yield logic
        "source": source,             # manual | csv | screenshot | desk
        "regime_snapshot": regime_snapshot or {},
        "note": note,
        "strategy_name": strategy_name,
        "portfolio": portfolio,
        # Strategy-level stop-loss / take-profit (optional). Each is a small dict:
        #   sl = {"kind": "premium"|"spot"|"note", "value": <num>, "note": <str>}
        #   tp = {"kind": "premium"|"profit_pct"|"profit_amt", "value": <num>}
        "sl": sl or None,
        "tp": tp or None,
        "status": "open",
    }
    # import provenance: stable identity + full source row (all columns) for later
    # reporting/analysis. fields = every sheet column, raw — nothing dropped.
    if trade_uid is not None:
        rec["trade_uid"] = trade_uid
    if content_hash is not None:
        rec["content_hash"] = content_hash
    if fields:
        rec["fields"] = fields
    _append(rec)
    return rec


def find_by_uid(uid: str) -> dict | None:
    """Latest live (non-deleted) imported trade carrying this trade_uid, else None.
    Powers idempotent re-import: same uid + same content_hash → skip; changed →
    caller deletes + re-adds; absent → add."""
    deleted = set()
    hit = None
    for r in _load():
        if r.get("type") == "delete":
            deleted.add(r.get("id"))
        elif r.get("type") == "entry" and r.get("trade_uid") == uid:
            hit = r            # keep the latest by file order
    if hit and hit.get("id") not in deleted:
        return {"id": hit["id"], "content_hash": hit.get("content_hash"),
                "status": hit.get("status")}
    return None


AMEND_META_KEYS = ("strategy_name", "tier", "broker", "entry_time", "entry_date", "sl", "tp",
                   "portfolio", "strategy_group", "margin_at_entry", "demat", "client", "trader")


def amend_trade(trade_id: str, legs: list[dict], note: str = "", meta: dict = None) -> dict:
    """Replace a strategy's legs wholesale (edits + additions). Append-only record.
    meta: optional corrections to strategy fields (AMEND_META_KEYS)."""
    rec = {"id": trade_id, "type": "amend", "legs": legs, "note": note,
           "recorded_at": datetime.now(IST).isoformat()}
    if meta:
        rec["meta"] = {k: v for k, v in meta.items() if k in AMEND_META_KEYS and v not in (None, "")}
    _append(rec)
    return rec


def set_meta(trade_id: str, meta: dict) -> dict:
    """Patch strategy-level fields (margin_at_entry, demat, trader, …) without
    touching legs. Append-only."""
    rec = {"id": trade_id, "type": "amend",
           "meta": {k: v for k, v in meta.items() if k in AMEND_META_KEYS},
           "recorded_at": datetime.now(IST).isoformat()}
    _append(rec)
    return rec


def close_leg(trade_id: str, strike: int, side: str, qty: int, price: float,
              exit_time: str = None, note: str = "", entry_price: float = None,
              reduce_qty: bool = True, demat: str = None) -> dict:
    """Square off part/all of one leg at `price` for `qty` units (sign-free).
    entry_price snapshot makes the booking self-contained (survives later amends).
    reduce_qty=False for corrections of historical bookings — books P&L without
    touching the live open quantity. demat disambiguates same strike+side legs
    held across different demat accounts."""
    rec = {"id": trade_id, "type": "leg_close",
           "strike": strike, "side": side, "qty": abs(int(qty)),
           "price": price, "entry_price": entry_price,
           "exit_time": exit_time or datetime.now(IST).strftime("%H:%M"),
           "note": note, "recorded_at": datetime.now(IST).isoformat()}
    if not reduce_qty:
        rec["no_reduce"] = True
    if demat is not None:
        rec["demat"] = demat
    _append(rec)
    return rec


def unbook_leg(trade_id: str, strike: int, side: str, qty: int, exit_price: float,
               exit_time: str = None) -> dict:
    """Remove a wrong booking (matches last booked exit with same strike/side/
    qty/exit_price[/exit_time]). Reverses its P&L AND reopens the leg qty it had
    closed (so fixing a fat-finger exit restores the position)."""
    rec = {"id": trade_id, "type": "unbook", "strike": strike, "side": side,
           "qty": abs(int(qty)), "exit_price": exit_price, "exit_time": exit_time,
           "recorded_at": datetime.now(IST).isoformat()}
    _append(rec)
    return rec


def delete_trade(trade_id: str) -> dict:
    rec = {"id": trade_id, "type": "delete",
           "recorded_at": datetime.now(IST).isoformat()}
    _append(rec)
    return rec


def compute_pnl(entry_legs: list[dict], exit_legs: list[dict]) -> float:
    """Match exit legs to entry legs by (strike, side); shorts: (entry−exit)×|qty|."""
    total = 0.0
    for ex in exit_legs or []:
        for en in entry_legs or []:
            if en.get("strike") == ex.get("strike") and en.get("side") == ex.get("side"):
                qty = en.get("qty") or 0
                e_p = en.get("price") or 0
                x_p = ex.get("price") or 0
                if qty < 0:
                    total += (e_p - x_p) * abs(qty)
                else:
                    total += (x_p - e_p) * qty
                break
    return round(total, 2)


def close_trade(trade_id: str, exit_time: str = None, exit_date: str = None,
                exit_legs: list[dict] = None, pnl: float = None, note: str = "") -> dict:
    now = datetime.now(IST)
    rec = {
        "id": trade_id, "type": "exit",
        "recorded_at": now.isoformat(),
        "exit_date": exit_date or now.strftime("%Y-%m-%d"),
        "exit_time": exit_time or now.strftime("%H:%M"),
        "exit_legs": exit_legs or [],
        "pnl": pnl, "note": note,
    }
    _append(rec)
    return rec


def all_trades() -> list[dict]:
    """Merge entries with amends/leg-closes/exits; drop deleted.
    Invariants: `pnl` only from full-exit records; `partial_booked_pnl` is the
    running sum of per-leg bookings and is never folded or lost (a leg_close
    carries its own entry_price, so even amending the leg away later cannot
    erase already-booked P&L)."""
    rows = _load()
    entries = {r["id"]: dict(r) for r in rows if r.get("type") == "entry"}
    for r in rows:
        if r.get("type") == "exit" and r["id"] in entries:
            e = entries[r["id"]]
            e["full_exit"] = True
            e["exit_date"] = r.get("exit_date"); e["exit_time"] = r.get("exit_time")
            e["exit_legs"] = r.get("exit_legs"); e["pnl"] = r.get("pnl")
            e["exit_note"] = r.get("note")
        elif r.get("type") == "amend" and r["id"] in entries:
            if "legs" in r and r.get("legs") is not None:   # meta-only amend leaves legs intact
                entries[r["id"]]["legs"] = [dict(l) for l in r["legs"]]
            for k, v in (r.get("meta") or {}).items():
                if k in AMEND_META_KEYS:
                    entries[r["id"]][k] = v
        elif r.get("type") == "leg_close" and r["id"] in entries:
            e = entries[r["id"]]
            closed_qty = r.get("qty") or 0
            entry_p = r.get("entry_price")
            matched = None
            want_dm = r.get("demat")
            for l in e.get("legs", []):
                if l.get("strike") == r.get("strike") and l.get("side") == r.get("side") and (l.get("qty") or 0) != 0:
                    if want_dm is not None and (l.get("demat") or "") != want_dm:
                        continue
                    matched = l
                    break
            if matched is not None and entry_p is None:
                entry_p = matched.get("price") or 0
            if entry_p is None:
                entry_p = 0
            exit_p = r.get("price") or 0
            short = (matched["qty"] < 0) if matched is not None else True
            pnl = (entry_p - exit_p) * closed_qty if short else (exit_p - entry_p) * closed_qty
            e["partial_booked_pnl"] = round((e.get("partial_booked_pnl") or 0) + pnl, 2)
            did_reduce = matched is not None and not r.get("no_reduce")
            e.setdefault("booked_legs", []).append(
                {"strike": r["strike"], "side": r["side"], "qty": closed_qty,
                 "entry_price": entry_p, "exit_price": exit_p,
                 "demat": (matched.get("demat") if matched else "") or "",
                 "exit_time": r.get("exit_time"), "pnl": round(pnl, 2),
                 "reduced": did_reduce, "short": short})
            if did_reduce:
                sign = -1 if matched["qty"] < 0 else 1
                matched["qty"] = sign * max(0, abs(matched["qty"]) - closed_qty)
        elif r.get("type") == "unbook" and r["id"] in entries:
            e = entries[r["id"]]
            bl = e.get("booked_legs") or []
            for i in range(len(bl) - 1, -1, -1):
                b = bl[i]
                if (b.get("strike") == r.get("strike") and b.get("side") == r.get("side")
                        and b.get("qty") == r.get("qty")
                        and float(b.get("exit_price") or 0) == float(r.get("exit_price") or 0)
                        and (not r.get("exit_time") or b.get("exit_time") == r.get("exit_time"))):
                    e["partial_booked_pnl"] = round((e.get("partial_booked_pnl") or 0) - (b.get("pnl") or 0), 2)
                    # reopen the leg qty this booking had closed (fixes a fat-finger exit)
                    if b.get("reduced"):
                        for l in (e.get("legs") or []):
                            if l.get("strike") == b.get("strike") and l.get("side") == b.get("side"):
                                sign = -1 if b.get("short") else 1
                                l["qty"] = sign * (abs(l.get("qty") or 0) + (b.get("qty") or 0))
                                break
                    e.pop("full_exit", None)
                    bl.pop(i)
                    break
        elif r.get("type") == "delete" and r["id"] in entries:
            del entries[r["id"]]
    for e in entries.values():
        legs = e.get("legs") or []
        if e.get("full_exit"):
            e["status"] = "closed"
        elif legs and all((l.get("qty") or 0) == 0 for l in legs):
            e["status"] = "closed"
        else:
            e["status"] = "open"
    return sorted(entries.values(), key=lambda x: x.get("recorded_at", ""), reverse=True)


def booked_pnl(t: dict) -> float:
    """Total booked so far = full-exit pnl + per-leg bookings. Safe on any trade."""
    return (t.get("pnl") or 0.0) + (t.get("partial_booked_pnl") or 0.0)


def open_trades() -> list[dict]:
    return [t for t in all_trades() if t.get("status") == "open"]


def summary(days: int = 30) -> dict:
    trades = all_trades()
    closed = [t for t in trades if t.get("status") == "closed"]
    for t in closed:
        t["pnl"] = booked_pnl(t)
    return {
        "n_total": len(trades), "n_open": len(trades) - len(closed),
        "n_closed": len(closed),
        "total_pnl": sum(t["pnl"] for t in closed),
        "by_tier": _group_pnl(closed, "tier"),
        "by_entry_hour": _group_pnl(closed, None, key_fn=lambda t: (t.get("entry_time") or "?")[:2] + ":00"),
    }


def _group_pnl(trades, field, key_fn=None):
    out = {}
    for t in trades:
        k = key_fn(t) if key_fn else (t.get(field) or "?")
        d = out.setdefault(k, {"n": 0, "pnl": 0.0})
        d["n"] += 1; d["pnl"] += t.get("pnl") or 0
    return out
