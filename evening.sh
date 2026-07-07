#!/bin/bash
# Pin to Python 3.11 — the OS python3 became 3.14 (no project deps) on 2026-06-13.
# All proven deps (fastapi, pandas, duckdb, pyobjc/Vision, kiteconnect) live in 3.11.
export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:$PATH"
# evening.sh — daily after-market routine. Run after 16:00 IST or at end of day.
#
# Usage: ./evening.sh
#
# What it does:
#   1. Re-runs ingest for TODAY specifically (captures full trading session)
#   2. Verifies bars count matches expected (~1500 per instrument for full day)
#   3. Reports
#
# Why this exists: morning login captures partial day. Evening run gets the full
# session ingested after market closes (15:30 IST + ~30 min for Kite data lag).

set -e

ROOT="/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"
cd "$ROOT"

TODAY=$(date '+%Y-%m-%d')
echo "════════════════════════════════════════════════════════════"
echo "  EVENING ROUTINE — $TODAY $(date '+%H:%M %Z')"
echo "════════════════════════════════════════════════════════════"

# Verify Kite session still valid
SESS="$HOME/.config/kite_session.json"
if [ ! -f "$SESS" ]; then
    echo "✗ No Kite session — run ./morning.sh first"
    exit 1
fi

echo "→ Force-ingesting today ($TODAY) for full-day data..."
python3 scripts/run_kite_ingest.py --date "$TODAY" --force --instruments NIFTY,SENSEX 2>&1 | tail -10

echo ""
echo "→ Verifying coverage..."
python3 << PYEOF
import duckdb
from datetime import date
con = duckdb.connect()
today = date.fromisoformat("$TODAY")
for inst in ["NIFTY", "SENSEX"]:
    r = con.execute(f"""
        SELECT COUNT(*) bars,
               COUNT(DISTINCT strike) FILTER (WHERE option_type IN ('CE','PE')) strikes,
               MIN(CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIMESTAMP)) first_bar,
               MAX(CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIMESTAMP)) last_bar
        FROM read_parquet('$ROOT/data/parquet/instrument={inst}/**/*.parquet', union_by_name=True)
        WHERE CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '$TODAY'
    """).fetchone()
    if r and r[0] > 0:
        print(f"{inst}: {r[0]:>7,} bars, {r[1]} strikes, {r[2]} → {r[3]}")
    else:
        print(f"{inst}: NO DATA (holiday or weekend?)")
PYEOF

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  EVENING ROUTINE COMPLETE"
echo "════════════════════════════════════════════════════════════"

echo ""
echo "-> Learning loop (journal vs backtest)..."
python3 analyses/900_learning_loop.py 2>&1 | tail -3
