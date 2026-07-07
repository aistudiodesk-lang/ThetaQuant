#!/bin/bash
# Pin to Python 3.11 — the OS python3 became 3.14 (no project deps) on 2026-06-13.
# All proven deps (fastapi, pandas, duckdb, pyobjc/Vision, kiteconnect) live in 3.11.
export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:$PATH"
# check.sh — quick health-check, no Kite calls, no token usage.
#
# Usage: ./check.sh
# Run this anytime to see: data coverage, session age, cron status.

ROOT="/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"
cd "$ROOT"

echo "════════════════════════════════════════════════════════════"
echo "  HEALTH CHECK — $(date '+%Y-%m-%d %A %H:%M %Z')"
echo "════════════════════════════════════════════════════════════"

# 1. Kite session
SESS="$HOME/.config/kite_session.json"
if [ -f "$SESS" ]; then
    AGE=$(python3 -c "import os, time; print(f'{(time.time()-os.path.getmtime(\"$SESS\"))/3600:.1f}')")
    USER=$(python3 -c "import json; print(json.load(open('$SESS')).get('user_id','?'))")
    if [ "$(python3 -c "print(int(float('$AGE')))")" -lt 18 ]; then
        echo "✓ Kite session: FRESH (${AGE}h, user=$USER)"
    else
        echo "⚠ Kite session: STALE (${AGE}h) — run ./morning.sh"
    fi
else
    echo "✗ Kite session: MISSING — run ./morning.sh"
fi

# 2. Parquet coverage
echo ""
echo "→ Last 5 trading days in parquet store:"
python3 << 'PYEOF'
import duckdb
con = duckdb.connect()
for inst in ["NIFTY", "SENSEX"]:
    r = con.execute(f"""
        SELECT CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) as d, COUNT(*) bars
        FROM read_parquet('/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)/data/parquet/instrument={inst}/**/*.parquet', union_by_name=True)
        GROUP BY 1 ORDER BY 1 DESC LIMIT 5
    """).fetchdf()
    print(f"\n  {inst}:")
    for _, row in r.iterrows():
        print(f"    {row['d']}  {row['bars']:>7,} bars")
PYEOF

# 3. Cron status
echo ""
echo "→ Launchd cron jobs:"
launchctl list 2>/dev/null | grep "rohanshah" | sed 's/^/  /' || echo "  (none)"

# 4. Last sync log
echo ""
echo "→ Last post_login_sync run:"
if [ -f "$ROOT/results/post_login_sync.log" ]; then
    grep "post_login_sync.py END" "$ROOT/results/post_login_sync.log" | tail -1 | sed 's/^/  /'
else
    echo "  (no log yet)"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
