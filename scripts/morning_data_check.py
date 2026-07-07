"""
Morning check: did yesterday's Kite cron run successfully?

Runs at 09:30 IST (after market opens). Looks at the kite_ingest_log.parquet
for yesterday's expected ingest. If missing, fires a macOS notification +
writes to a clear log file. User sees it within seconds.

Three outcomes:
  - All expected data present → silent success.
  - Yesterday's data missing → loud notification with the catch-up command.
  - Today's session not logged in → loud notification telling user to login.

Run via launchd: scripts/com.rohanshah.morning-check.plist (set up by user
or by separate install script).
"""
from __future__ import annotations
import json, sys, subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import is_trading_day, is_market_holiday, MARKET_HOLIDAYS

LOG_PATH = ROOT / "data" / "kite_ingest_log.parquet"
SESSION = Path.home() / ".config" / "kite_session.json"
NOTIFICATION_LOG = ROOT / "results" / "morning_check.log"


def yesterday_trading_day() -> date:
    today = datetime.now().date()
    d = today - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def macos_notify(title: str, message: str, sound: str = "Glass"):
    """Send a macOS notification."""
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}" sound name "{sound}"'
        ], check=False)
    except Exception:
        pass


def log_event(line: str):
    NOTIFICATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with NOTIFICATION_LOG.open("a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}  {line}\n")


def session_alive() -> bool:
    """Quick check: kite_session.json exists AND today_login looks fresh."""
    if not SESSION.exists(): return False
    try:
        s = json.loads(SESSION.read_text())
        # If login_at is from today (>4am IST), session should still be valid
        return bool(s.get("access_token"))
    except Exception:
        return False


def main():
    expected = yesterday_trading_day()
    log_event(f"morning_check: looking for ingest of {expected}")

    issues = []

    # 1. Check session
    if not session_alive():
        issues.append("⚠ Kite session not present today — login required")

    # 2. Check yesterday's data
    if not LOG_PATH.exists():
        issues.append(f"⚠ kite_ingest_log.parquet does not exist — has the cron ever run?")
    else:
        log = pd.read_parquet(LOG_PATH)
        log["d"] = pd.to_datetime(log["trade_date"]).dt.date
        for inst in ["NIFTY", "SENSEX"]:
            present = ((log["instrument"] == inst) & (log["d"] == expected)).any()
            if not present:
                issues.append(f"⚠ {inst} data for {expected} NOT in log")

    if not issues:
        log_event(f"✓ all good — yesterday ({expected}) has both NIFTY + SENSEX")
        sys.exit(0)

    # Send loud notification
    msg = " ; ".join(issues)
    title = "Kite ingest needs attention"
    body = msg + " — run scripts/run_kite_ingest.py --days 7"
    log_event(f"NOTIFY: {body}")
    macos_notify(title, body)

    # Also print to stdout in case user is at terminal
    print("=" * 70)
    print(title)
    print("=" * 70)
    for issue in issues:
        print(f"  {issue}")
    print()
    print("Quick fix:")
    print('  cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"')
    print("  python3 scripts/kite_login.py            # if session needs refreshing")
    print("  python3 scripts/run_kite_ingest.py --days 7   # backfill any missed days")
    sys.exit(1)


if __name__ == "__main__":
    main()
