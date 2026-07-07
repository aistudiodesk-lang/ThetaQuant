#!/bin/bash
# Pin to Python 3.11 — the OS python3 became 3.14 (no project deps) on 2026-06-13.
# All proven deps (fastapi, pandas, duckdb, pyobjc/Vision, kiteconnect) live in 3.11.
export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:$PATH"
# morning.sh — daily morning routine. Run this once per day before market open.
#
# Usage: ./morning.sh
#
# What it does:
#   1. Checks if Kite session is fresh (< 18 hours old)
#   2. If stale, opens browser to login URL — you log in & paste back the callback URL
#   3. Auto-fires post_login_sync.py which backfills parquet + saves snapshot
#   4. Verifies coverage and reports

set -e

ROOT="/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"
SESS="$HOME/.config/kite_session.json"
CRED="$HOME/.config/kite_credentials.json"

cd "$ROOT"

echo "════════════════════════════════════════════════════════════"
echo "  MORNING ROUTINE — $(date '+%Y-%m-%d %A %H:%M %Z')"
echo "════════════════════════════════════════════════════════════"

# Check session freshness
SESSION_FRESH=false
if [ -f "$SESS" ]; then
    AGE_HOURS=$(python3 -c "
import os, time
age = (time.time() - os.path.getmtime('$SESS')) / 3600
print(f'{age:.1f}')
")
    AGE_INT=$(python3 -c "print(int(float('$AGE_HOURS')))")
    echo "→ Kite session age: ${AGE_HOURS}h"
    if [ "$AGE_INT" -lt 18 ]; then
        SESSION_FRESH=true
        echo "  ✓ Session is fresh"
    else
        echo "  ⚠ Session is stale — need new login"
    fi
else
    echo "→ No Kite session found — need login"
fi

if [ "$SESSION_FRESH" = "false" ]; then
    API_KEY=$(python3 -c "import json; print(json.load(open('$CRED'))['api_key'])")
    LOGIN_URL="https://kite.zerodha.com/connect/login?api_key=${API_KEY}&v=3"
    echo ""
    echo "→ Opening browser to Kite login..."
    open "$LOGIN_URL"
    echo ""
    echo "After login, your browser will redirect to a URL like:"
    echo "   http://127.0.0.1:5000/callback?...&request_token=XXXXXXXX"
    echo ""
    echo "Copy that FULL URL and paste here:"
    read -p "→ Paste URL: " CALLBACK_URL

    # Extract request_token
    TOKEN=$(echo "$CALLBACK_URL" | sed 's/.*request_token=\([^&]*\).*/\1/')
    if [ -z "$TOKEN" ] || [ "$TOKEN" = "$CALLBACK_URL" ]; then
        echo "✗ Could not extract request_token from URL. Exiting."
        exit 1
    fi

    echo ""
    echo "→ Exchanging request_token for access_token..."
    echo "$TOKEN" | python3 scripts/kite_login.py
    # kite_login.py auto-fires post_login_sync.py in background

    # Wait for sync to start
    sleep 3
    echo ""
    echo "→ Background sync fired (parquet backfill + snapshot save)"
    echo "  Watching log..."

    # Wait up to 5 minutes for sync to complete
    for i in {1..60}; do
        if grep -q "post_login_sync.py END" "$ROOT/results/post_login_sync.log" 2>/dev/null; then
            LAST_END=$(grep "post_login_sync.py END" "$ROOT/results/post_login_sync.log" | tail -1)
            if [[ "$LAST_END" == *"$(date '+%Y-%m-%d')"* ]]; then
                echo "  ✓ Sync complete"
                break
            fi
        fi
        sleep 5
        echo "    ...still running (${i}/60 × 5s)"
    done
fi

echo ""
echo "→ Verifying data coverage..."
python3 << 'PYEOF'
import duckdb
from datetime import date, timedelta
con = duckdb.connect()
today = date.today()
print(f"\nLast 5 trading days in parquet store:")
for inst in ["NIFTY", "SENSEX"]:
    r = con.execute(f"""
        SELECT CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) as d, COUNT(*) bars
        FROM read_parquet('/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)/data/parquet/instrument={inst}/**/*.parquet', union_by_name=True)
        GROUP BY 1 ORDER BY 1 DESC LIMIT 5
    """).fetchdf()
    print(f"\n{inst}:")
    for _, row in r.iterrows():
        marker = "  ← today" if row['d'] == today else ""
        print(f"  {row['d']}  {row['bars']:>7,} bars{marker}")
PYEOF

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  MORNING ROUTINE COMPLETE"
echo "════════════════════════════════════════════════════════════"
