#!/bin/bash
# bot_status.sh — check if Telegram bot is running

if pgrep -f "telegram_bot.py" > /dev/null; then
    PID=$(pgrep -f "telegram_bot.py")
    echo "✓ Bot running (PID $PID)"
    echo ""
    echo "Recent activity:"
    tail -10 "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)/results/telegram_bot.log" 2>/dev/null
else
    echo "✗ Bot NOT running"
    echo "Run: ./bot_start.sh"
fi
