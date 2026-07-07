#!/usr/bin/env python3
"""
Convenience wrapper to run the Kite-based daily ingest.

Usage:
  python3 scripts/run_kite_ingest.py                 # last 7 trading days, both instruments
  python3 scripts/run_kite_ingest.py --days 1        # just yesterday
  python3 scripts/run_kite_ingest.py --date 2026-04-28
  python3 scripts/run_kite_ingest.py --instruments NIFTY
  python3 scripts/run_kite_ingest.py --force         # re-ingest

Pre-requisite: run scripts/kite_login.py earlier today (or yesterday) to have
a fresh access_token in ~/.config/kite_session.json.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.kite_daily import run, days_to_ingest
from datetime import datetime
import argparse


def preflight() -> bool:
    """Verify Kite session exists + works. Cleanly fail loudly if not."""
    cred = Path.home() / ".config" / "kite_credentials.json"
    sess = Path.home() / ".config" / "kite_session.json"
    if not cred.exists():
        print(f"ERROR: missing {cred}. Set up Kite credentials first.")
        return False
    if not sess.exists():
        print(f"ERROR: missing {sess}.")
        print("→ Run scripts/kite_login.py to log in (Kite tokens expire daily ~6 AM IST).")
        return False
    # Quick liveness check
    try:
        from kiteconnect import KiteConnect
        creds = json.loads(cred.read_text())
        s = json.loads(sess.read_text())
        k = KiteConnect(api_key=creds["api_key"])
        k.set_access_token(s["access_token"])
        # cheap call to verify token still valid
        _ = k.profile()
        return True
    except Exception as e:
        print(f"ERROR: Kite session invalid: {e}")
        print("→ Today's token expired or was revoked. Run scripts/kite_login.py to refresh.")
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--instruments", default="NIFTY,SENSEX")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--date", type=str, default=None)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    insts = [x.strip().upper() for x in args.instruments.split(",")]
    if args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        dates_per_inst = {i: days_to_ingest(i, args.days) for i in insts}
        dates = sorted(set().union(*dates_per_inst.values())) if dates_per_inst else []

    if not dates:
        print(f"[{datetime.now().isoformat(timespec='seconds')}] No new dates to ingest.")
        return

    if not preflight():
        sys.exit(1)

    print(f"[{datetime.now().isoformat(timespec='seconds')}] Plan: instruments={insts} dates={dates}")
    run(insts, dates, force=args.force)


if __name__ == "__main__":
    main()
