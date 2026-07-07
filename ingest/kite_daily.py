"""
Daily / on-demand ingest of NIFTY + SENSEX minute candles via Kite Connect.

What it pulls per (instrument, day):
  - Underlying spot (NIFTY 50 / SENSEX) minute bars
  - Current-month FUT minute bars (and next-month if available)
  - All CE/PE strikes for the NEAREST WEEKLY expiry within ±SPOT_RANGE_PCT of
    the day's opening spot — minute bars
  - Same for the NEXT weekly expiry (so E-1, E-0, E-2, E-3 all see relevant
    chain even when "nearest" rolls)

Storage: appended to existing parquet store at
    data/parquet/instrument={NIFTY,SENSEX}/year=YYYY/month=MM/<hash>.parquet
in the same canonical schema as existing data.

Tracking: a simple data/kite_ingest_log.parquet records (instrument, trade_date,
n_rows, ingested_at) so reruns skip already-ingested days.

Uses Kite historical_data() — counts against your ₹2K/mo subscription, no
extra cost.  Rate-limited to 3 req/sec.
"""
from __future__ import annotations
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
import hashlib
import argparse

import pandas as pd
import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.kite_historical import RateLimitedKite, IST, session_window
from ingest.common import CANONICAL_COLS, empty_row

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet"
LOG = ROOT / "data" / "kite_ingest_log.parquet"

SPOT_RANGE_PCT = 5.0       # save strikes within ±5% of opening spot
NEAREST_N_WEEKLIES = 2     # capture nearest + next weekly expiries

# instrument-specific config
INSTRUMENTS = {
    "NIFTY": {
        "spot_symbol": "NSE:NIFTY 50",
        "spot_token_lookup": ("NSE", "NIFTY 50"),
        "exch_segment": "NFO",
        "name_in_dump": "NIFTY",
        "strike_grid": 50,
    },
    "SENSEX": {
        "spot_symbol": "BSE:SENSEX",
        "spot_token_lookup": ("BSE", "SENSEX"),
        "exch_segment": "BFO",
        "name_in_dump": "SENSEX",
        "strike_grid": 100,
    },
}


# ── Kite ⇒ canonical row ──────────────────────────────────────────────
def _bar_to_row(bar: dict, instrument: str, expiry, strike, option_type, dte) -> dict:
    row = empty_row()
    row.update({
        "timestamp": pd.Timestamp(bar["date"]).tz_convert(IST) if bar["date"].tzinfo else IST.localize(bar["date"]),
        "source": "KITE_LIVE",
        "instrument": instrument,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": int(bar.get("volume") or 0),
        "oi": int(bar.get("oi")) if bar.get("oi") is not None else None,
        "bar_minutes": 1,
        "dte": dte,
    })
    return row


# ── Tracking log ──────────────────────────────────────────────────────
def load_log() -> pd.DataFrame:
    if not LOG.exists():
        return pd.DataFrame(columns=["instrument", "trade_date", "n_rows", "ingested_at"])
    return pd.read_parquet(LOG)


def save_log(df: pd.DataFrame):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(LOG, index=False)


def already_ingested(instrument: str, d: date) -> bool:
    log = load_log()
    if log.empty: return False
    return ((log["instrument"] == instrument) &
            (pd.to_datetime(log["trade_date"]).dt.date == d)).any()


def append_log(instrument: str, d: date, n_rows: int):
    log = load_log()
    log = log[~((log["instrument"] == instrument) &
                (pd.to_datetime(log["trade_date"]).dt.date == d))]
    new_row = pd.DataFrame([{
        "instrument": instrument,
        "trade_date": pd.Timestamp(d),
        "n_rows": n_rows,
        "ingested_at": datetime.now(IST).isoformat(timespec="seconds"),
    }])
    save_log(pd.concat([log, new_row], ignore_index=True))


# ── Strike-range filter ───────────────────────────────────────────────
def relevant_chain(instruments_df: pd.DataFrame, instr_name: str,
                   target_date: date, spot_open: float, grid: int) -> pd.DataFrame:
    """Return rows of the instruments dump that match the criteria for ingest."""
    df = instruments_df[
        (instruments_df["name"] == instr_name) &
        (instruments_df["instrument_type"].isin(["CE", "PE"]))
    ].copy()
    # Strike range
    lo = round(spot_open * (1 - SPOT_RANGE_PCT/100) / grid) * grid
    hi = round(spot_open * (1 + SPOT_RANGE_PCT/100) / grid) * grid
    df = df[(df["strike"] >= lo) & (df["strike"] <= hi)]
    # Future-only expiries
    df = df[df["expiry"] >= target_date]
    # Nearest N weeklies — sort by expiry, take first N distinct
    expiries = sorted(set(df["expiry"]))[:NEAREST_N_WEEKLIES]
    df = df[df["expiry"].isin(expiries)]
    return df


def find_futures(instruments_df: pd.DataFrame, instr_name: str,
                  target_date: date) -> pd.DataFrame:
    """Return current-month + next-month FUT contracts."""
    df = instruments_df[
        (instruments_df["name"] == instr_name) &
        (instruments_df["instrument_type"] == "FUT") &
        (instruments_df["expiry"] >= target_date)
    ].copy()
    expiries = sorted(set(df["expiry"]))[:2]   # near + next month
    return df[df["expiry"].isin(expiries)]


def find_spot(instruments_df: pd.DataFrame, exchange: str, name: str) -> dict | None:
    df = instruments_df[
        (instruments_df["exchange"] == exchange) &
        (instruments_df["tradingsymbol"] == name)
    ]
    if df.empty: return None
    return df.iloc[0].to_dict()


# ── Main per-day worker ───────────────────────────────────────────────
def ingest_day(rk: RateLimitedKite, instrument: str, target_date: date,
                instruments_df: pd.DataFrame, force: bool = False) -> int:
    """Fetch + save all minute data for one (instrument, date). Returns rows added."""
    if already_ingested(instrument, target_date) and not force:
        print(f"  [skip] {instrument} {target_date} already in log")
        return 0

    cfg = INSTRUMENTS[instrument]
    start, end = session_window(target_date)

    # 1. Underlying spot
    spot_inst = find_spot(instruments_df, cfg["spot_token_lookup"][0],
                           cfg["spot_token_lookup"][1])
    if spot_inst is None:
        print(f"  [skip] {instrument}: spot instrument not found in dump")
        return 0

    print(f"  [{instrument} {target_date}] fetching spot...")
    try:
        spot_bars = rk.historical(spot_inst["instrument_token"], start, end, oi=False)
    except Exception as e:
        print(f"    ✗ spot fetch failed: {e}")
        return 0

    if not spot_bars:
        print(f"  [skip] {instrument} {target_date}: no spot bars (holiday?)")
        return 0

    spot_open = float(spot_bars[0]["open"])
    print(f"    spot open = ₹{spot_open:.2f}")

    rows = []
    for b in spot_bars:
        rows.append(_bar_to_row(b, instrument, None, None, "SPOT", None))

    # 2. Futures (near + next month)
    futs = find_futures(instruments_df, cfg["name_in_dump"], target_date)
    print(f"    fetching {len(futs)} FUT contracts...")
    for _, f in futs.iterrows():
        try:
            bars = rk.historical(f["instrument_token"], start, end, oi=True)
        except Exception as e:
            print(f"    ✗ FUT {f['tradingsymbol']} failed: {e}"); continue
        for b in bars:
            rows.append(_bar_to_row(b, instrument, f["expiry"], None, "FUT",
                                     (f["expiry"] - target_date).days))

    # 3. Options chain (nearest 2 weeklies, ±5% strikes)
    chain = relevant_chain(instruments_df, cfg["name_in_dump"], target_date,
                            spot_open, cfg["strike_grid"])
    print(f"    fetching {len(chain)} option contracts (±{SPOT_RANGE_PCT}%, {NEAREST_N_WEEKLIES} weeklies)...")
    fail = 0
    for i, (_, c) in enumerate(chain.iterrows(), 1):
        try:
            bars = rk.historical(c["instrument_token"], start, end, oi=True)
        except Exception as e:
            fail += 1
            if fail <= 3:
                print(f"      ✗ {c['tradingsymbol']}: {e}")
            continue
        for b in bars:
            rows.append(_bar_to_row(b, instrument, c["expiry"],
                                     int(c["strike"]), c["instrument_type"],
                                     (c["expiry"] - target_date).days))
        if i % 20 == 0:
            print(f"      ...{i}/{len(chain)}")

    if fail:
        print(f"    [warn] {fail} option fetches failed (skipped)")

    if not rows:
        print(f"  [skip] {instrument} {target_date}: no data fetched")
        return 0

    df = pd.DataFrame(rows, columns=CANONICAL_COLS)
    n = save_partition(df, instrument, target_date)
    append_log(instrument, target_date, n)
    print(f"  ✓ {instrument} {target_date}: {n:,} rows saved")
    return n


def save_partition(df: pd.DataFrame, instrument: str, target_date: date) -> int:
    """Append df to the appropriate parquet partition, deduping on
    (timestamp, instrument, expiry, strike, option_type)."""
    part = (STORE / f"instrument={instrument}" /
            f"year={target_date.year}" / f"month={target_date.month:02d}")
    part.mkdir(parents=True, exist_ok=True)

    file_id = hashlib.md5(f"kite_{instrument}_{target_date}".encode()).hexdigest()[:12]
    out_path = part / f"{file_id}.parquet"

    # Read existing if any
    if out_path.exists():
        old = pd.read_parquet(out_path)
        df = pd.concat([old, df], ignore_index=True)

    df = df.drop_duplicates(
        subset=["timestamp", "instrument", "expiry", "strike", "option_type"],
        keep="last")
    df.to_parquet(out_path, index=False)
    return len(df)


# ── Determine days to ingest ──────────────────────────────────────────
def is_trading_day(d: date) -> bool:
    return d.weekday() < 5    # Mon-Fri (skips weekends; doesn't know holidays)


def days_to_ingest(instrument: str, n_days: int = 7) -> list[date]:
    """Return last n_days trading dates not yet in log for this instrument."""
    today = datetime.now(IST).date()
    log = load_log()
    if not log.empty:
        log_set = set(pd.to_datetime(log[log["instrument"] == instrument]["trade_date"]).dt.date)
    else:
        log_set = set()
    out = []
    for i in range(n_days + 5):    # buffer for weekends
        d = today - timedelta(days=i)
        if not is_trading_day(d): continue
        if d in log_set: continue
        out.append(d)
        if len(out) >= n_days: break
    return list(reversed(out))


# ── Top-level CLI driver ──────────────────────────────────────────────
def run(instruments: Iterable[str], dates: Iterable[date], force: bool = False) -> None:
    rk = RateLimitedKite()
    print("[fetch] instruments dump (NSE)...")
    nse = pd.DataFrame(rk.instruments("NSE"))
    print("[fetch] instruments dump (NFO)...")
    nfo = pd.DataFrame(rk.instruments("NFO"))
    bse_dump = pd.DataFrame()
    bfo_dump = pd.DataFrame()
    if "SENSEX" in instruments:
        print("[fetch] instruments dump (BSE + BFO)...")
        bse_dump = pd.DataFrame(rk.instruments("BSE"))
        bfo_dump = pd.DataFrame(rk.instruments("BFO"))

    # Combine for lookup
    all_instr = pd.concat([nse, nfo, bse_dump, bfo_dump], ignore_index=True)

    # Normalize 'expiry' → date (kite returns string for some segments)
    def _to_date(x):
        if x is None or (isinstance(x, float) and pd.isna(x)) or x == "": return None
        if isinstance(x, date) and not isinstance(x, datetime): return x
        if isinstance(x, datetime): return x.date()
        try:
            return pd.to_datetime(x).date()
        except Exception:
            return None
    all_instr["expiry"] = all_instr["expiry"].apply(_to_date)

    total_rows = 0
    for inst in instruments:
        for d in dates:
            try:
                total_rows += ingest_day(rk, inst, d, all_instr, force=force)
            except Exception as e:
                print(f"  ✗ ERROR {inst} {d}: {e}")
                import traceback; traceback.print_exc()
    print(f"\n[done] total new rows: {total_rows:,}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--instruments", default="NIFTY,SENSEX",
                   help="Comma-separated. Default: NIFTY,SENSEX")
    p.add_argument("--days", type=int, default=7,
                   help="Last N trading days to ingest (default 7)")
    p.add_argument("--date", type=str, default=None,
                   help="Specific date YYYY-MM-DD (overrides --days)")
    p.add_argument("--force", action="store_true",
                   help="Re-ingest even if already in log")
    args = p.parse_args()

    insts = [x.strip().upper() for x in args.instruments.split(",") if x.strip()]
    if args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        # Compute per-instrument list inside run() — but for simplicity here use NIFTY's
        dates_per_inst = {i: days_to_ingest(i, args.days) for i in insts}
        # Union of dates across instruments
        all_d = sorted(set().union(*dates_per_inst.values()))
        dates = all_d

    print(f"Plan: instruments={insts} dates={dates}")
    run(insts, dates, force=args.force)
