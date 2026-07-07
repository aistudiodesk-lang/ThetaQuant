#!/usr/bin/env python3
"""
Self-service CSV exporter from the parquet store.

Examples:
  # Today's NIFTY 2.5%-OTM-ish strikes
  python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \
      --strikes 23400,23500,24500,24700 --opt CE,PE --out today_strikes.csv

  # Full intraday for one strike
  python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \
      --strike 24700 --opt CE --out 24700_CE.csv

  # Spot path for a date
  python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \
      --spot --out nifty_spot_28apr.csv

  # P&L summary for your live trade (provide entries)
  python3 scripts/export_csv.py --pnl-summary --instrument NIFTY \
      --date 2026-04-28 --positions 24700:CE:42900:0.81,23400:PE:50700:0.85 \
      --out trade_pnl.csv

Output files land in results/exports/ unless you give an absolute path.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet"
EXPORT_DIR = ROOT / "results" / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def resolve_out(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (EXPORT_DIR / path)


def export_intraday(instrument: str, d: str, strikes: list[int],
                     opts: list[str], out: Path):
    con = duckdb.connect()
    strike_clause = "AND strike IN ({})".format(",".join(map(str, strikes))) if strikes else ""
    opt_clause = "AND option_type IN ({})".format(",".join(f"'{o}'" for o in opts)) if opts else ""
    df = con.execute(f"""
      SELECT timestamp, instrument, expiry, strike, option_type,
             open, high, low, close, volume, oi
      FROM read_parquet('{STORE}/instrument={instrument}/**/*.parquet', union_by_name=True)
      WHERE CAST(timestamp AS DATE) = DATE '{d}'
        {strike_clause}
        {opt_clause}
      ORDER BY timestamp, option_type, strike
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("Asia/Kolkata")
    df.to_csv(out, index=False)
    print(f"✓ {len(df):,} rows → {out}")


def export_spot(instrument: str, d: str, out: Path):
    con = duckdb.connect()
    df = con.execute(f"""
      SELECT timestamp, open, high, low, close, volume
      FROM read_parquet('{STORE}/instrument={instrument}/**/*.parquet', union_by_name=True)
      WHERE CAST(timestamp AS DATE) = DATE '{d}'
        AND option_type = 'SPOT'
      ORDER BY timestamp
    """).fetchdf()
    if df.empty:
        # fallback to FUT
        df = con.execute(f"""
          SELECT timestamp, open, high, low, close, volume
          FROM read_parquet('{STORE}/instrument={instrument}/**/*.parquet', union_by_name=True)
          WHERE CAST(timestamp AS DATE) = DATE '{d}'
            AND option_type = 'FUT'
          ORDER BY timestamp, expiry
        """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("Asia/Kolkata")
    df.to_csv(out, index=False)
    print(f"✓ {len(df):,} rows → {out}")


def export_chain_snapshot(instrument: str, d: str, time_str: str, out: Path):
    """Snapshot of full option chain at a given time."""
    con = duckdb.connect()
    df = con.execute(f"""
      SELECT strike, option_type, close, volume, oi, expiry
      FROM read_parquet('{STORE}/instrument={instrument}/**/*.parquet', union_by_name=True)
      WHERE CAST(timestamp AS DATE) = DATE '{d}'
        AND option_type IN ('CE','PE')
        AND strftime(timestamp AT TIME ZONE 'Asia/Kolkata', '%H:%M') = '{time_str}'
      ORDER BY expiry, option_type, strike
    """).fetchdf()
    df.to_csv(out, index=False)
    print(f"✓ {len(df):,} rows → {out}")


def export_pnl_summary(instrument: str, d: str, positions: list[str], out: Path):
    """Reconstruct P&L for given positions on a given date.
    positions = list of "strike:CE_or_PE:qty:avg_entry_price"
    Uses the DAY-CLOSE LTP as exit price (= 15:25 last bar)."""
    con = duckdb.connect()
    rows = []
    for pos in positions:
        s, opt, qty, avg = pos.split(":")
        s, qty, avg = int(s), int(qty), float(avg)
        last = con.execute(f"""
          SELECT close FROM read_parquet('{STORE}/instrument={instrument}/**/*.parquet', union_by_name=True)
          WHERE CAST(timestamp AS DATE) = DATE '{d}'
            AND strike = {s} AND option_type = '{opt}'
            AND expiry = DATE '{d}'
          ORDER BY timestamp DESC LIMIT 1
        """).fetchone()
        ltp = last[0] if last else None
        pnl = qty * (avg - ltp) if ltp is not None else None
        rows.append({
            "instrument": instrument, "date": d,
            "strike": s, "option_type": opt, "qty": qty,
            "avg_entry": avg, "close_ltp": ltp,
            "pnl_gross": round(pnl, 2) if pnl else None,
        })
    df = pd.DataFrame(rows)
    df.loc["TOTAL"] = ["", "", "", "", df["qty"].sum(), "", "", df["pnl_gross"].sum()]
    df.to_csv(out, index=False)
    print(f"✓ {len(df):,} rows → {out}")
    print(df.to_string(index=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--instrument", default="NIFTY", choices=["NIFTY", "SENSEX"])
    p.add_argument("--date", required=False, default=datetime.now().date().isoformat())
    p.add_argument("--strikes", type=str, help="Comma-separated, e.g. '23400,24700'")
    p.add_argument("--strike", type=int, help="Single strike (alternative to --strikes)")
    p.add_argument("--opt", default="CE,PE", help="CE / PE / CE,PE")
    p.add_argument("--spot", action="store_true", help="Export spot/FUT path only")
    p.add_argument("--chain-at", type=str, help="HH:MM — snapshot the full chain at this time")
    p.add_argument("--pnl-summary", action="store_true", help="Compute P&L for given positions")
    p.add_argument("--positions", type=str,
                   help="strike:CE_or_PE:qty:avg, comma-separated, e.g. '24700:CE:42900:0.81,23400:PE:50700:0.85'")
    p.add_argument("--out", default=None, help="Output filename (default auto-named)")
    args = p.parse_args()

    if args.spot:
        out = resolve_out(args.out or f"{args.instrument}_spot_{args.date}.csv")
        export_spot(args.instrument, args.date, out)
        return
    if args.chain_at:
        out = resolve_out(args.out or f"{args.instrument}_chain_{args.date}_{args.chain_at.replace(':','')}.csv")
        export_chain_snapshot(args.instrument, args.date, args.chain_at, out)
        return
    if args.pnl_summary:
        if not args.positions:
            print("--pnl-summary needs --positions"); sys.exit(1)
        positions = [x.strip() for x in args.positions.split(",")]
        out = resolve_out(args.out or f"{args.instrument}_pnl_{args.date}.csv")
        export_pnl_summary(args.instrument, args.date, positions, out)
        return

    # Default: intraday for given strike(s)
    strikes = []
    if args.strikes:
        strikes = [int(x) for x in args.strikes.split(",")]
    elif args.strike:
        strikes = [args.strike]
    opts = [o.strip() for o in args.opt.split(",")]
    out = resolve_out(args.out or f"{args.instrument}_intraday_{args.date}.csv")
    export_intraday(args.instrument, args.date, strikes, opts, out)


if __name__ == "__main__":
    main()
