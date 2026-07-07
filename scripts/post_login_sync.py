#!/usr/bin/env python3
"""
post_login_sync.py

Fires automatically after every Kite Connect login:
  - via kite_login.py (subprocess.Popen at end)
  - via dashboard /api/kite-exchange (subprocess.Popen at end)
  - via launchd WatchPaths on ~/.config/kite_session.json

Responsibilities:
  1. Backfill parquet store with any missing trading days (last 14 days)
  2. Save / refresh today's dashboard snapshot (preserves existing positions)
  3. Log everything to results/post_login_sync.log

Idempotent: run as many times per day as you like, no duplication.

Why Python and not bash? macOS launchd TCC permits python3 to read ~/Desktop
(grandfathered grant from earlier plists). /bin/bash gets blocked.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path("/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)")
LOG = ROOT / "results" / "post_login_sync.log"
SNAP_DIR = ROOT / "data" / "dashboard_snapshots"
INGEST = ROOT / "scripts" / "run_kite_ingest.py"
DASHBOARD_URL = "http://127.0.0.1:8000"

LOG.parent.mkdir(parents=True, exist_ok=True)
SNAP_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with LOG.open("a") as f:
        f.write(line)


def banner(msg: str) -> None:
    bar = "═" * 60
    log("")
    log(bar)
    log(msg)
    log(bar)


# ─── 1. Parquet ingest ─────────────────────────────────────────────────────────
def parquet_ingest(days: int = 14) -> bool:
    """Stream ingest output to a side log file so memory doesn't blow + we see progress."""
    log(f"Step 1: parquet ingest (last {days} days)…")
    side_log = ROOT / "results" / f"ingest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    try:
        with side_log.open("w") as out:
            proc = subprocess.Popen(
                [sys.executable, str(INGEST), "--days", str(days)],
                cwd=str(ROOT),
                stdout=out, stderr=subprocess.STDOUT,
            )
            # Wait up to 45 minutes
            try:
                rc = proc.wait(timeout=2700)
            except subprocess.TimeoutExpired:
                proc.kill()
                log(f"  ✗ ingest TIMED OUT after 45 min — see {side_log.name}")
                return False
        if rc == 0:
            log(f"  ✓ ingest succeeded (full log: {side_log.name})")
            # Append last 5 lines of side_log for context
            try:
                tail = side_log.read_text().splitlines()[-5:]
                if tail:
                    log(f"  stdout tail:\n  " + "\n  ".join(tail))
            except Exception:
                pass
            return True
        log(f"  ✗ ingest exit={rc} — see {side_log.name}")
        return False
    except Exception as e:
        log(f"  ✗ ingest exception: {e}")
        return False


# ─── 2. Snapshot save ─────────────────────────────────────────────────────────
def snapshot_save() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    existing = SNAP_DIR / f"{today}.json"
    positions = []
    if existing.exists():
        try:
            positions = json.loads(existing.read_text()).get("positions", [])
        except Exception:
            positions = []
    log(f"Step 2: snapshot save for {today} (preserving {len(positions)} positions)…")

    try:
        import urllib.request
        body = json.dumps({
            "date": today,
            "positions": positions,
            "note": "auto-saved via post_login_sync",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{DASHBOARD_URL}/api/snapshot/save",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        log(f"  ✓ snapshot saved: {data.get('n_positions', 0)} positions, "
            f"MTM=₹{data.get('total_mtm', 0):,.0f}, path={data.get('path')}")
        return True
    except Exception as e:
        log(f"  ⚠ dashboard not running on :8000 (or error): {e}")
        return False


# ─── 3. Verification ──────────────────────────────────────────────────────────
def verify_parquet() -> None:
    log("Step 3: parquet verification…")
    try:
        import duckdb
        con = duckdb.connect()
        r = con.execute(f"""
            SELECT CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) as d, COUNT(*) cnt
            FROM read_parquet('{ROOT}/data/parquet/**/*.parquet', union_by_name=True)
            WHERE option_type='FUT'
            GROUP BY 1 ORDER BY 1 DESC LIMIT 7
        """).fetchdf()
        log("  parquet store (last 7 trading days):")
        for row in r.itertuples():
            log(f"    {row.d}  {row.cnt:>5} bars")
    except Exception as e:
        log(f"  ✗ verification failed: {e}")


def main():
    banner("post_login_sync.py START")
    parquet_ingest(days=14)
    snapshot_save()
    verify_parquet()
    banner("post_login_sync.py END")


if __name__ == "__main__":
    main()
