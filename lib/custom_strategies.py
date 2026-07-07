"""
lib/custom_strategies.py — user-defined strategies created from the UI.

Each becomes a strategy desk (generic positions + entry + monitoring) and a
strategy_group in the shared journal, so its trades flow into reporting like any
other. Storage: data/custom_strategies.json.
"""
from __future__ import annotations
import json, re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "custom_strategies.json"

KINDS = {"option_sell": "Option selling (CE/PE/strangle)",
         "buy_write": "Buy-write (future + short call)",
         "generic": "Generic (any legs)"}


def _load() -> list[dict]:
    try:
        return json.loads(STORE.read_text())
    except Exception:
        return []


def _save(items):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(items, indent=2, default=str))


def all_custom() -> list[dict]:
    return _load()


def get(slug: str) -> dict | None:
    return next((s for s in _load() if s["slug"] == slug), None)


def create(name: str, kind: str = "generic", instruments: str = "", note: str = "") -> dict:
    items = _load()
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:40] or "strategy"
    base, i = slug, 2
    while any(s["slug"] == slug for s in items):
        slug = f"{base}-{i}"; i += 1
    rec = {"slug": slug, "name": name.strip(), "kind": kind if kind in KINDS else "generic",
           "instruments": [x.strip().upper() for x in instruments.split(",") if x.strip()],
           "note": note, "created_at": datetime.now(IST).isoformat()}
    items.append(rec); _save(items)
    return rec


def delete(slug: str) -> None:
    _save([s for s in _load() if s["slug"] != slug])
