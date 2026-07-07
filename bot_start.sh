#!/bin/bash
# Pin to Python 3.11 — the OS python3 became 3.14 (no project deps) on 2026-06-13.
# All proven deps (fastapi, pandas, duckdb, pyobjc/Vision, kiteconnect) live in 3.11.
export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:$PATH"
# bot_start.sh — start the Telegram bot in background (survives Terminal close)
#
# Usage:
#   ./bot_start.sh          # start
#   ./bot_status.sh         # check if running
#   ./bot_stop.sh           # stop
#
# Run once and it stays running until you stop it OR reboot.

ROOT="/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"
cd "$ROOT"

# Kill ALL existing instances first — duplicate bots fight over the same
# Telegram getUpdates session and silently break each other.
EXISTING=$(pgrep -f "telegram_bot.py" 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "⚠ Found existing bot processes ($EXISTING) — killing to prevent collisions..."
    pkill -9 -f "telegram_bot.py" 2>/dev/null
    sleep 2
    LEFT=$(pgrep -f "telegram_bot.py" 2>/dev/null)
    if [ -n "$LEFT" ]; then
        echo "✗ Could not kill: $LEFT"; exit 1
    fi
    echo "✓ Cleared. Starting fresh."
fi

# Start in background with nohup so it survives Terminal close
nohup python3 scripts/telegram_bot.py > /dev/null 2>&1 &
BOT_PID=$!
disown $BOT_PID

sleep 2

if ps -p $BOT_PID > /dev/null; then
    echo "✓ Bot started (PID $BOT_PID)"
    echo ""
    echo "Logs: results/telegram_bot.log"
    echo ""
    echo "It will:"
    echo "  • Send scheduled messages at 8:30, 9:20, 12:00, 13:00, 14:00, 15:00, 15:30 IST"
    echo "  • Reply to your /commands and screenshots in real-time"
    echo "  • Survive Terminal close (but NOT a reboot — re-run after restart)"
else
    echo "✗ Bot failed to start. Check logs:"
    tail -5 results/telegram_bot.log
fi
