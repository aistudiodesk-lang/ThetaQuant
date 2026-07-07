"""
lib/master_import.py — ingest a monthly MASTER workbook (the 28-sheet
"…Options Trade.xlsx" format with per-strategy S1..S7 sheets) into the shared
journal, so its trades flow into the dashboard / reports like any other.

Reuses the existing row→journal converter (lib/import_io._ingest_trades): one
sheet row = one leg, closed rows book their realised P&L ("Net Total Amount" for
options, M2M for futures), idempotent upsert by derived trade_uid.

Scope: the SELL-based option strategy sheets (S1/S2/S3/S5/S7). Long-futures /
investment sheets (S6 Long NIFTY, FUTURES FOR INVESTMENT) are holdings, handled
by the holdings pipeline — not sold-option trades — so they're skipped here.
Expiry (S4) can be excluded (Rohan corrects it separately).
"""
from __future__ import annotations
from pathlib import Path

# per-strategy sheet → canonical S-code (mirrors lib/full_report.STRATEGY_SHEETS)
_SELL_SHEETS = {
    "S1 Options CC Inv": "S1",
    "S2 RHS New CC": "S2",
    "S3 RHS Indx": "S3",
    "S3 RHS Indx (Axis)": "S3",
    "S4 Expiry Opt": "S4",
    "S4 Expiry Opt(B)": "S4",
    "S5 Vish Indx": "S5",
    "S7 Commodity": "S7",
}


def ingest_master_workbook(path, exclude_expiry=True, purge_months=None, dry_run=False):
    """Import the workbook's option-strategy sheets into the journal.

    exclude_expiry : skip the S4 sheets (default True — corrected separately).
    purge_months   : list of "YYYY-MM"; before importing, delete existing NON-import
                     (live/manual/screenshot) journal trades in those months whose
                     canonical code != S4, so a re-import replaces rather than doubles.
    """
    import pandas as pd
    from lib import import_io as IO, journal

    path = Path(path)
    if not path.exists():
        return {"error": f"file not found: {path}"}
    xl = pd.ExcelFile(path)

    sheets = [s for s, code in _SELL_SHEETS.items()
              if s in xl.sheet_names and not (exclude_expiry and code == "S4")]
    if not sheets:
        return {"error": "no importable strategy sheets found", "have": xl.sheet_names}

    # ── purge overlapping live trades for the covered months (idempotent replace) ──
    purged = 0
    if purge_months and not dry_run:
        pset = set(purge_months)
        for t in list(journal.all_trades()):
            ed = (t.get("entry_date") or "")[:7]
            xm = (t.get("expiry_month") or "")            # some legs are dated by expiry
            sc = (next((l.get("strategy_group_code") for l in t.get("legs", [])
                        if l.get("strategy_group_code")), "") or "").upper()
            grp = (t.get("strategy_group") or "").lower()
            is_s4 = sc == "S4" or "expiry" in grp
            in_month = ed in pset or xm in pset
            # purge only the live (non-import) trades this run will REPLACE: when
            # excluding expiry we keep S4; when including it we replace S4 too.
            covers = (not is_s4) if exclude_expiry else True
            if in_month and covers and t.get("source") != "import":
                journal.delete_trade(t["id"])
                purged += 1

    # ── ingest each sheet through the shared row→journal converter ──
    per_sheet = {}
    combined = []
    for s in sheets:
        df = pd.read_excel(xl, sheet_name=s)
        r = IO._ingest_trades(df, dry_run=dry_run)
        per_sheet[s] = {"imported": r.get("imported"), "closed": r.get("closed"),
                        "realized": r.get("realized"), "skipped": r.get("skipped"),
                        "added": r.get("added"), "errors": r.get("errors")}
    return {"file": path.name, "sheets": sheets, "purged_live": purged,
            "per_sheet": per_sheet, "dry_run": dry_run}
