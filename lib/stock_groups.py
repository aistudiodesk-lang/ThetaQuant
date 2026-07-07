"""
lib/stock_groups.py — named baskets of stock symbols (e.g. the Adani group) so the
dashboard can show a whole group's combined position/P&L/coverage.

The DEFAULT mapping ships in code (below); user edits are saved to
data/stock_groups.json (local-only, since data/ is gitignored). This keeps a sane
seed shipping with the app while letting Rohan add/fix members without a code change.
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "stock_groups.json"

# Seed — editable via the API / UI. Symbols are NSE tradingsymbols (upper-case).
DEFAULT_GROUPS: dict[str, list[str]] = {
    "Adani": ["ADANIENT", "ADANIPORTS", "ADANIGREEN", "ADANIPOWER",
              "AMBUJACEM", "ACC", "ADANIENSOL", "ATGL", "AWL"],
}


def _norm(sym: str) -> str:
    return (sym or "").strip().upper()


def all_groups() -> dict[str, list[str]]:
    """Group-name → list of symbols. File overrides the seed once the user edits."""
    try:
        d = json.loads(STORE.read_text())
        if isinstance(d, dict):
            return {k: [_norm(s) for s in (v or [])] for k, v in d.items()}
    except Exception:
        pass
    return {k: [_norm(s) for s in v] for k, v in DEFAULT_GROUPS.items()}


def save_groups(groups: dict) -> dict:
    """Replace the whole mapping (name → [symbols]). Empty groups are dropped."""
    clean = {}
    for name, syms in (groups or {}).items():
        name = (name or "").strip()
        syms = [_norm(s) for s in (syms or []) if _norm(s)]
        if name and syms:
            clean[name] = sorted(set(syms))
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(clean, indent=2))
    return clean


def group_of(symbol: str) -> str | None:
    """The group a symbol belongs to (first match), else None."""
    s = _norm(symbol)
    for name, syms in all_groups().items():
        if s in syms:
            return name
    return None
