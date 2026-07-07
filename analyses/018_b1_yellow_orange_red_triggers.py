"""
ANALYSIS 018 — Backtest Yellow/Orange/Red trigger rules for B1 (tight-OTM) on SENSEX E-0

Question (Rohan, 2026-06-04):
  "Yellow can't be premium-based (premium swings normally). Need technical/regime
   triggers. Backtest: when these criteria fire, how often does the close strike
   actually go ITM, and by how much?"

Setup:
  - Simulate B1 entry at 10:30 IST on each SENSEX E-0 day
  - Strikes at 0.5%, 0.7%, 1.0% OTM both sides (PE below spot, CE above)
  - Track FUT spot minute-by-minute from 10:30 to 15:25
  - Compute candidate Yellow signals at each minute
  - Compare: signal-fired days vs signal-not-fired days
  - Metric: P(strike ITM at close) conditional on signal

Yellow signal candidates (technical/regime, NOT premium):
  S1_BUFFER_30: spot eats 30% of entry buffer toward strike
  S1_BUFFER_50: spot eats 50% of buffer (more conservative)
  S2_RANGE_BREAK: spot breaks 9:15-10:30 extreme (after 10:30) by >0.1%
  S3_FAST_MOVE: 15-min realized move > 0.3%
  S4_TREND_3X15: 3 consecutive 15-min closes against position
  S5_COMBINED: any 2 of S1_BUFFER_50, S2, S3, S4

Orange signal candidates (more permissive — won't be acted on but logged):
  ORANGE_BUFFER_70: spot eats 70% of buffer

Red signal candidates (mechanical close):
  RED_BUFFER_85: spot eats 85% of buffer (= 100-150pts from strike)
  RED_BREACH: spot crosses INTO strike intraday

Output:
  results/018_b1_yellow_orange_red/
    per_day_signals.csv          — every (date, otm%, signal) with fire-time + ITM outcome
    signal_summary.csv           — aggregate stats per (signal × otm%)
    summary.md                   — human-readable
"""
from __future__ import annotations
from datetime import date, time, datetime, timedelta
from pathlib import Path
import sys, os

import duckdb
import numpy as np
import pandas as pd

_env_root = os.environ.get("BACKTEST_ROOT")
ROOT = Path(_env_root) if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import SENSEX_WEEKLY_EXPIRIES

STORE = ROOT / "data" / "parquet" / "instrument=SENSEX"
OUT = ROOT / "results" / "018_b1_yellow_orange_red"
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()
PATH_GLOB = str(STORE / "**" / "*.parquet")

OTM_TIERS = [0.5, 0.7, 1.0]   # % OTM to test
ENTRY_TIME = time(10, 30)
CLOSE_TIME = time(15, 25)


def load_fut_all() -> pd.DataFrame:
    df = con.execute(f"""
        WITH ranked AS (
            SELECT timestamp, expiry, open, high, low, close,
                   ROW_NUMBER() OVER (PARTITION BY timestamp ORDER BY expiry ASC) rn
            FROM read_parquet('{PATH_GLOB}', union_by_name=True)
            WHERE option_type='FUT'
        )
        SELECT timestamp, open, high, low, close FROM ranked WHERE rn=1
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df = df[df["timestamp"].dt.weekday < 5].sort_values("timestamp").reset_index(drop=True)
    return df


def evaluate_signals(day_fut: pd.DataFrame, spot_entry: float, pe_strike: int, ce_strike: int) -> dict:
    """For one day, compute when each candidate signal fires for each side.
    Returns dict: {signal_name: {pe: fire_time or None, ce: fire_time or None}}"""
    # Slice to entry-time onwards
    intraday = day_fut[day_fut["time"] >= ENTRY_TIME].copy().reset_index(drop=True)
    pre_entry = day_fut[day_fut["time"] < ENTRY_TIME].copy()
    
    if intraday.empty:
        return {}
    
    # Buffers
    pe_buffer = spot_entry - pe_strike  # always positive
    ce_buffer = ce_strike - spot_entry  # always positive
    
    results = {}
    
    # S1_BUFFER_30: spot eats 30% of buffer
    pe_trig_30 = spot_entry - 0.30 * pe_buffer
    ce_trig_30 = spot_entry + 0.30 * ce_buffer
    results["S1_BUFFER_30"] = {
        "pe": _first_breach(intraday, "low", pe_trig_30, "below"),
        "ce": _first_breach(intraday, "high", ce_trig_30, "above"),
    }
    
    # S1_BUFFER_50: 50% of buffer
    pe_trig_50 = spot_entry - 0.50 * pe_buffer
    ce_trig_50 = spot_entry + 0.50 * ce_buffer
    results["S1_BUFFER_50"] = {
        "pe": _first_breach(intraday, "low", pe_trig_50, "below"),
        "ce": _first_breach(intraday, "high", ce_trig_50, "above"),
    }
    
    # S2_RANGE_BREAK: spot breaks 9:15-10:30 extreme after 10:30 by >0.1%
    if not pre_entry.empty:
        pre_low = pre_entry["low"].min()
        pre_high = pre_entry["high"].max()
        break_pe = pre_low * (1 - 0.001)   # 0.1% below pre-entry low
        break_ce = pre_high * (1 + 0.001)  # 0.1% above pre-entry high
        results["S2_RANGE_BREAK"] = {
            "pe": _first_breach(intraday, "low", break_pe, "below"),
            "ce": _first_breach(intraday, "high", break_ce, "above"),
        }
    else:
        results["S2_RANGE_BREAK"] = {"pe": None, "ce": None}
    
    # S3_FAST_MOVE: any 15-min rolling move > 0.3% (against position)
    intraday["minute_idx"] = range(len(intraday))
    results["S3_FAST_MOVE"] = {"pe": None, "ce": None}
    for i in range(15, len(intraday)):
        window = intraday.iloc[i-15:i+1]
        move = (window["close"].iloc[-1] - window["close"].iloc[0]) / window["close"].iloc[0] * 100
        if move <= -0.3 and results["S3_FAST_MOVE"]["pe"] is None:
            results["S3_FAST_MOVE"]["pe"] = intraday.iloc[i]["time"]
        if move >= 0.3 and results["S3_FAST_MOVE"]["ce"] is None:
            results["S3_FAST_MOVE"]["ce"] = intraday.iloc[i]["time"]
        if results["S3_FAST_MOVE"]["pe"] and results["S3_FAST_MOVE"]["ce"]:
            break
    
    # S4_TREND_3X15: 3 consecutive 15-min closes against position
    bars_15min = intraday.iloc[::15].reset_index(drop=True)
    results["S4_TREND_3X15"] = {"pe": None, "ce": None}
    for i in range(3, len(bars_15min)):
        if results["S4_TREND_3X15"]["pe"] is None and bars_15min["close"].iloc[i] < bars_15min["close"].iloc[i-1] < bars_15min["close"].iloc[i-2] < bars_15min["close"].iloc[i-3]:
            results["S4_TREND_3X15"]["pe"] = bars_15min.iloc[i]["time"]
        if results["S4_TREND_3X15"]["ce"] is None and bars_15min["close"].iloc[i] > bars_15min["close"].iloc[i-1] > bars_15min["close"].iloc[i-2] > bars_15min["close"].iloc[i-3]:
            results["S4_TREND_3X15"]["ce"] = bars_15min.iloc[i]["time"]
    
    # ORANGE_BUFFER_70
    pe_trig_70 = spot_entry - 0.70 * pe_buffer
    ce_trig_70 = spot_entry + 0.70 * ce_buffer
    results["ORANGE_BUFFER_70"] = {
        "pe": _first_breach(intraday, "low", pe_trig_70, "below"),
        "ce": _first_breach(intraday, "high", ce_trig_70, "above"),
    }
    
    # RED_BUFFER_85
    pe_trig_85 = spot_entry - 0.85 * pe_buffer
    ce_trig_85 = spot_entry + 0.85 * ce_buffer
    results["RED_BUFFER_85"] = {
        "pe": _first_breach(intraday, "low", pe_trig_85, "below"),
        "ce": _first_breach(intraday, "high", ce_trig_85, "above"),
    }
    
    # RED_BREACH (spot crosses INTO strike)
    results["RED_BREACH"] = {
        "pe": _first_breach(intraday, "low", pe_strike, "below"),
        "ce": _first_breach(intraday, "high", ce_strike, "above"),
    }
    
    return results


def _first_breach(df: pd.DataFrame, col: str, threshold: float, direction: str):
    """Return first time when df[col] breaches threshold."""
    if direction == "below":
        m = df[df[col] <= threshold]
    else:
        m = df[df[col] >= threshold]
    if m.empty:
        return None
    return m.iloc[0]["time"]


def main():
    print("[1/3] Loading SENSEX FUT data ...")
    fut = load_fut_all()
    expiry_set = set(SENSEX_WEEKLY_EXPIRIES)
    e0_days = sorted([d for d in fut["date"].unique() if d in expiry_set])
    print(f"  E-0 days available: {len(e0_days)}")
    
    print("[2/3] Simulating B1 entries + evaluating signals ...")
    rows = []
    for d in e0_days:
        day_fut = fut[fut["date"] == d].copy()
        if day_fut.empty:
            continue
        entry_row = day_fut[day_fut["time"] >= ENTRY_TIME]
        if entry_row.empty:
            continue
        spot_entry = float(entry_row.iloc[0]["close"])
        close_row = day_fut[day_fut["time"] <= CLOSE_TIME]
        if close_row.empty:
            continue
        spot_close = float(close_row.iloc[-1]["close"])
        
        for otm_pct in OTM_TIERS:
            pe_strike = int(round(spot_entry * (1 - otm_pct/100) / 100) * 100)
            ce_strike = int(round(spot_entry * (1 + otm_pct/100) / 100) * 100)
            pe_buffer_pts = spot_entry - pe_strike
            ce_buffer_pts = ce_strike - spot_entry
            
            pe_itm = spot_close < pe_strike
            ce_itm = spot_close > ce_strike
            pe_itm_pts = max(0, pe_strike - spot_close)
            ce_itm_pts = max(0, spot_close - ce_strike)
            
            # Evaluate signals
            sigs = evaluate_signals(day_fut, spot_entry, pe_strike, ce_strike)
            
            row = {
                "date": d, "otm_pct": otm_pct,
                "spot_entry": round(spot_entry, 2),
                "spot_close": round(spot_close, 2),
                "pe_strike": pe_strike, "ce_strike": ce_strike,
                "pe_buffer_pts": int(pe_buffer_pts),
                "ce_buffer_pts": int(ce_buffer_pts),
                "pe_itm": pe_itm, "pe_itm_pts": int(pe_itm_pts),
                "ce_itm": ce_itm, "ce_itm_pts": int(ce_itm_pts),
                "day_close_pct": round((spot_close - spot_entry) / spot_entry * 100, 2),
            }
            for sig_name, sides in sigs.items():
                row[f"{sig_name}_pe_fire"] = sides["pe"].strftime("%H:%M") if sides["pe"] else None
                row[f"{sig_name}_ce_fire"] = sides["ce"].strftime("%H:%M") if sides["ce"] else None
            rows.append(row)
    
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "per_day_signals.csv", index=False)
    print(f"  → per_day_signals.csv ({len(df)} rows)")
    
    print("[3/3] Computing signal effectiveness ...")
    
    # For each signal × OTM tier × side:
    # n_total, n_fired, P(ITM|fired), P(ITM|not_fired), median time-to-fire, mean ITM pts when ITM
    signal_names = ["S1_BUFFER_30", "S1_BUFFER_50", "S2_RANGE_BREAK", "S3_FAST_MOVE",
                    "S4_TREND_3X15", "ORANGE_BUFFER_70", "RED_BUFFER_85", "RED_BREACH"]
    
    summary_rows = []
    for otm_pct in OTM_TIERS:
        sub = df[df["otm_pct"] == otm_pct].copy()
        n_total = len(sub)
        if n_total == 0: continue
        
        # Baseline ITM rates (no signal)
        pe_itm_base = sub["pe_itm"].mean() * 100
        ce_itm_base = sub["ce_itm"].mean() * 100
        
        for sig in signal_names:
            for side in ["pe", "ce"]:
                fire_col = f"{sig}_{side}_fire"
                itm_col = f"{side}_itm"
                itm_pts_col = f"{side}_itm_pts"
                fired = sub[sub[fire_col].notna()]
                not_fired = sub[sub[fire_col].isna()]
                n_fired = len(fired)
                if n_fired == 0:
                    p_itm_fired = None
                    avg_itm_pts_fired = None
                else:
                    p_itm_fired = fired[itm_col].mean() * 100
                    avg_itm_pts_fired = fired[itm_pts_col].mean()
                p_itm_not_fired = not_fired[itm_col].mean() * 100 if len(not_fired) > 0 else None
                base = pe_itm_base if side == "pe" else ce_itm_base
                
                summary_rows.append({
                    "otm_pct": otm_pct, "signal": sig, "side": side.upper(),
                    "n_total": n_total, "n_fired": n_fired,
                    "fire_rate_pct": round(n_fired / n_total * 100, 1) if n_total else None,
                    "p_itm_baseline": round(base, 1),
                    "p_itm_when_fired": round(p_itm_fired, 1) if p_itm_fired is not None else None,
                    "p_itm_when_not_fired": round(p_itm_not_fired, 1) if p_itm_not_fired is not None else None,
                    "conditional_lift": round(p_itm_fired - p_itm_not_fired, 1) if p_itm_fired is not None and p_itm_not_fired is not None else None,
                    "avg_itm_pts_when_fired_and_itm": round(avg_itm_pts_fired, 1) if avg_itm_pts_fired else 0,
                })
    
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT / "signal_summary.csv", index=False)
    print(f"  → signal_summary.csv ({len(summary)} rows)")
    print()
    print("=" * 80)
    print("Signal effectiveness (PE side, 0.7% OTM — typical B1):")
    print("=" * 80)
    pe07 = summary[(summary["side"] == "PE") & (summary["otm_pct"] == 0.7)].copy()
    print(pe07[["signal", "fire_rate_pct", "p_itm_baseline", "p_itm_when_fired", "p_itm_when_not_fired", "conditional_lift"]].to_string(index=False))
    print()
    print("Signal effectiveness (CE side, 0.7% OTM — typical B1):")
    print("=" * 80)
    ce07 = summary[(summary["side"] == "CE") & (summary["otm_pct"] == 0.7)].copy()
    print(ce07[["signal", "fire_rate_pct", "p_itm_baseline", "p_itm_when_fired", "p_itm_when_not_fired", "conditional_lift"]].to_string(index=False))
    
    print(f"\nDone. Full results in {OUT}")


if __name__ == "__main__":
    main()
