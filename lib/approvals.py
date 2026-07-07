"""Approved-order list — the front of the trade lifecycle.

PLANNED (strategy suggests) → APPROVED (Rohan ticks it + sets a limit price) → INSTRUCTED
(batch sent to broker on WhatsApp) → CLAIMED (team/broker reports taken@price) → later
CONFIRMED by contract-note reco (see project_reconciliation_engine).

This module owns the APPROVED→CLAIMED part: a daily, WhatsApp-ready list. Local-only
append-log (data/ is gitignored). IST throughout.
"""
from __future__ import annotations
from pathlib import Path
import json
import uuid
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "approved_orders.jsonl"


def _now() -> datetime:
    return datetime.now(IST)


def _load() -> list[dict]:
    """Replay the append-log; last write per id wins; honour soft-deletes."""
    if not STORE.exists():
        return []
    by: dict[str, dict] = {}
    for line in STORE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        i = r.get("id")
        if not i:
            continue
        if r.get("_deleted"):
            by.pop(i, None)
            continue
        by[i] = {**by.get(i, {}), **r}
    return list(by.values())


def _append(obj: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(obj) + "\n")


def add(order: dict) -> dict:
    now = _now()
    rec = {
        "id": uuid.uuid4().hex[:10], "created_at": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"), "status": "approved",
        "symbol": (order.get("symbol") or "").upper(),
        "side": (order.get("side") or "CE").upper(),
        "strike": order.get("strike"), "qty": order.get("qty"), "lots": order.get("lots"),
        "premium_shown": order.get("premium_shown"),
        "limit_price": order.get("limit_price"),
        "month": order.get("month") or "", "strategy": order.get("strategy") or "S1",
        "bucket": order.get("bucket") or "", "note": order.get("note") or "",
    }
    _append(rec)
    return rec


def update(oid: str, **fields) -> bool:
    fields["id"] = oid
    _append(fields)
    return True


def set_claimed(oid: str, price=None, taken: bool = True) -> bool:
    return update(oid, status=("claimed" if taken else "not_taken"),
                  claimed_price=price, claimed_at=_now().isoformat())


def delete(oid: str) -> bool:
    _append({"id": oid, "_deleted": True})
    return True


def list_orders(date=None, status=None, month=None) -> list[dict]:
    rows = _load()
    if date:
        rows = [r for r in rows if r.get("date") == date]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if month:
        rows = [r for r in rows if r.get("month") == month]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


def today() -> list[dict]:
    return list_orders(date=_now().strftime("%Y-%m-%d"))


def whatsapp_text(rows: list[dict]) -> str:
    """Broker-ready batch: one line per order, copy-paste / screenshot friendly."""
    if not rows:
        return "No approved trades."
    d = rows[0].get("date", _now().strftime("%Y-%m-%d"))
    lines = [f"*Trades to take — {d}*"]
    for i, r in enumerate(sorted(rows, key=lambda x: (x.get("symbol") or "")), 1):
        qty = r.get("qty")
        qtxt = f" x{int(qty):,}" if qty else (f" x{r.get('lots')}lot" if r.get("lots") else "")
        lim = r.get("limit_price")
        ltxt = f" @ limit ₹{lim}" if lim not in (None, "") else " @ market"
        m = f" ({r['month']})" if r.get("month") else ""
        lines.append(f"{i}. SELL {r.get('symbol')} {r.get('strike')}{r.get('side')}{m}{qtxt}{ltxt}")
    return "\n".join(lines)
