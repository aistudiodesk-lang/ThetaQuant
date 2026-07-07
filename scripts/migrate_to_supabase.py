"""
Supabase migration script — pushes local trade_journal.jsonl to Supabase.

Run:
    python3 scripts/migrate_to_supabase.py

Step 1 will print the SQL to create the table — run it once in the
Supabase SQL editor (https://supabase.com/dashboard → SQL Editor).
Step 2 pushes all records using the service role key.
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# ── Step 1: Print CREATE TABLE SQL ───────────────────────────────────────
CREATE_SQL = """
-- Run this once in Supabase SQL Editor before pushing data.

create table if not exists trade_journal (
    id              text primary key,
    type            text,
    recorded_at     timestamptz,
    entry_date      date,
    entry_time      text,
    exit_time       text,
    instrument      text,
    tier            text,
    strategy_group  text,
    strategy_name   text,
    legs            jsonb,
    broker          text,
    demat           text,
    client          text,
    trader          text,
    portfolio       text,
    strike          numeric,
    side            text,
    qty             numeric,
    price           numeric,
    entry_price     numeric,
    exit_price      numeric,
    margin_at_entry numeric,
    source          text,
    status          text,
    note            text,
    no_reduce       boolean,
    regime_snapshot jsonb,
    meta            jsonb,
    created_at      timestamptz default now()
);

-- index for common queries
create index if not exists idx_tj_instrument    on trade_journal(instrument);
create index if not exists idx_tj_entry_date    on trade_journal(entry_date);
create index if not exists idx_tj_status        on trade_journal(status);
create index if not exists idx_tj_type          on trade_journal(type);
"""

print("=" * 60)
print("STEP 1 — Run this SQL in Supabase SQL Editor first:")
print("=" * 60)
print(CREATE_SQL)

# ── Step 2: Push data ────────────────────────────────────────────────────
try:
    from supabase import create_client
except ImportError:
    print("ERROR: run `pip3 install supabase` first")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

JOURNAL_PATH = ROOT / "data" / "trade_journal.jsonl"
# Also check sibling v0.2 2 folder if not found locally
if not JOURNAL_PATH.exists():
    alt = ROOT.parent / "Theta-gainers-feat-thetadesk-dashboard-v0.2 2" / "data" / "trade_journal.jsonl"
    if alt.exists():
        JOURNAL_PATH = alt
        print(f"Note: reading journal from {JOURNAL_PATH}")

if not JOURNAL_PATH.exists():
    print(f"ERROR: trade_journal.jsonl not found at {JOURNAL_PATH}")
    sys.exit(1)

records = []
with open(JOURNAL_PATH) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        # Normalise: ensure all columns are present (None if missing)
        records.append({
            "id":              r.get("id"),
            "type":            r.get("type"),
            "recorded_at":     r.get("recorded_at"),
            "entry_date":      r.get("entry_date") or None,
            "entry_time":      r.get("entry_time") or None,
            "exit_time":       r.get("exit_time") or None,
            "instrument":      r.get("instrument"),
            "tier":            r.get("tier"),
            "strategy_group":  r.get("strategy_group"),
            "strategy_name":   r.get("strategy_name"),
            "legs":            r.get("legs"),          # stored as jsonb
            "broker":          r.get("broker"),
            "demat":           r.get("demat") or None,
            "client":          r.get("client") or None,
            "trader":          r.get("trader") or None,
            "portfolio":       r.get("portfolio") or None,
            "strike":          r.get("strike"),
            "side":            r.get("side"),
            "qty":             r.get("qty"),
            "price":           r.get("price"),
            "entry_price":     r.get("entry_price"),
            "exit_price":      r.get("exit_price"),
            "margin_at_entry": r.get("margin_at_entry"),
            "source":          r.get("source"),
            "status":          r.get("status"),
            "note":            r.get("note") or None,
            "no_reduce":       r.get("no_reduce"),
            "regime_snapshot": r.get("regime_snapshot") or None,
            "meta":            r.get("meta") or None,
        })

print(f"\nLoaded {len(records)} records from {JOURNAL_PATH.name}")
print("Pushing to Supabase in batches of 100...")

BATCH = 100
pushed = 0
errors = 0
for i in range(0, len(records), BATCH):
    batch = records[i:i+BATCH]
    try:
        sb.table("trade_journal").upsert(batch, on_conflict="id").execute()
        pushed += len(batch)
        print(f"  {pushed}/{len(records)} pushed")
    except Exception as e:
        errors += len(batch)
        print(f"  ERROR on batch {i}–{i+BATCH}: {e}")

print(f"\nDone. {pushed} pushed, {errors} errors.")
if errors == 0:
    print("All records in Supabase. trade_journal table is live.")
