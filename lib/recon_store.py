"""Reconciliation store — the Layer-B (truth) ledger of ingested contract-note / mProfit
transactions, so Full Reporting can show a 'Contract-note based' view next to the
manual/Google-sheet (Layer-A) view.

Local-only append-log (data/, gitignored). Each upload is tagged by (source, demat,
batch) so re-uploading the same file replaces, not duplicates. Never holds passwords.
"""
from __future__ import annotations
from pathlib import Path
import json
import uuid
from datetime import datetime
import pytz

IST = pytz.timezone("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "recon_transactions.jsonl"


def _append(obj: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(obj) + "\n")


def add_batch(txns: list[dict], source: str, demat: str = "", meta: dict | None = None) -> dict:
    """Store a parsed batch of transactions. Replaces any prior batch with the same
    (source, demat, batch_key) so re-uploads don't double-count."""
    batch_key = (meta or {}).get("batch_key") or f"{source}:{demat}:{(meta or {}).get('date','')}"
    bid = uuid.uuid4().hex[:10]
    rec = {"_batch": bid, "_batch_key": batch_key, "source": source, "demat": (demat or "").upper(),
           "uploaded_at": datetime.now(IST).isoformat(), "meta": meta or {},
           "txns": txns, "n": len(txns)}
    _append({"_supersede": batch_key})        # tombstone older batches with same key
    _append(rec)
    return {"batch": bid, "n": len(txns), "batch_key": batch_key}


def _load_batches() -> list[dict]:
    if not STORE.exists():
        return []
    superseded_before: dict[str, int] = {}
    raw = []
    for i, line in enumerate(STORE.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("_supersede"):
            superseded_before[r["_supersede"]] = i
        else:
            raw.append((i, r))
    # keep only the latest batch per batch_key, and drop any batch that appears before its tombstone
    latest: dict[str, tuple] = {}
    for i, r in raw:
        key = r.get("_batch_key")
        tomb = superseded_before.get(key, -1)
        if i < tomb:
            continue                       # an older batch wiped by a later upload
        if key not in latest or i > latest[key][0]:
            latest[key] = (i, r)
    return [r for _, r in latest.values()]


def all_txns(demat: str = "") -> list[dict]:
    out = []
    for b in _load_batches():
        if demat and b.get("demat") != demat.upper():
            continue
        for t in b.get("txns", []):
            out.append({**t, "demat": b.get("demat"), "_source": b.get("source")})
    return out


def batches_summary() -> list[dict]:
    return sorted(
        [{"batch": b["_batch"], "source": b.get("source"), "demat": b.get("demat"),
          "n": b.get("n"), "uploaded_at": b.get("uploaded_at"), "meta": b.get("meta", {})}
         for b in _load_batches()],
        key=lambda x: x.get("uploaded_at", ""), reverse=True)


def report() -> dict:
    """Layer-B aggregate from the stored contract-note/mProfit transactions:
    options realized P&L + futures M2M, by underlying and by demat."""
    from lib import recon_import as RI
    txns = all_txns()
    pos = RI.positions(txns)
    by_underlying: dict = {}
    opt_realized = 0.0
    fut_m2m = 0.0
    for p in pos.values():
        u = by_underlying.setdefault(p["underlying"], {"underlying": p["underlying"],
                                                        "opt_realized": 0.0, "fut_m2m": 0.0, "n": 0})
        u["n"] += 1
        if p["is_option"]:
            opt_realized += p["realized"] or 0
            u["opt_realized"] += p["realized"] or 0
        else:
            fut_m2m += p.get("net_notional") or 0
            u["fut_m2m"] += p.get("net_notional") or 0
    rows = sorted(by_underlying.values(), key=lambda x: -(abs(x["opt_realized"]) + abs(x["fut_m2m"])))
    demats = sorted({t.get("demat") for t in txns if t.get("demat")})
    return {
        "n_txns": len(txns), "n_positions": len(pos),
        "options_realized": round(opt_realized, 2), "futures_m2m": round(fut_m2m, 2),
        "by_underlying": rows, "demats": demats,
        "batches": batches_summary(),
        "computed_at": datetime.now(IST).strftime("%H:%M:%S"),
    }
