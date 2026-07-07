#!/bin/bash
# bot_stop.sh — stop the Telegram bot

if pgrep -f "telegram_bot.py" > /dev/null; then
    pkill -f "telegram_bot.py"
    sleep 1
    if pgrep -f "telegram_bot.py" > /dev/null; then
        pkill -9 -f "telegram_bot.py"
        echo "✓ Bot force-killed"
    else
        echo "✓ Bot stopped"
    fi
else
    echo "Bot was not running"
fi
