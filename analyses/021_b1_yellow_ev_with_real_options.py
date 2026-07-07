"""
ANALYSIS 021 — EV math for Yellow discipline with REAL option chain data

For each E-0 day, lookup:
  - Entry combined premium at 10:30 (CE + PE LTPs at the OTM strikes)
  - Exit combined premium at Yellow fire time (if Yellow fires)
  - Hold-to-expiry: ITM penalty if either leg ITM

Strategies tested:
  A. HOLD-TO-EXPIRY (no triggers, just hold)
  B. YELLOW-CLOSE-BOTH (close both legs at Yellow fire)
  C. YELLOW-CLOSE-LOSING (close only losing leg, hold winning to expiry)
  D. YELLOW-ROLL-LOSING (close losing leg, sell new at strike further OTM)

Output: EV table across 58 SENSEX + 61 NIFTY days
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path
import sys, os

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "021_b1_yellow_ev"
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()
ENTRY_TIME = time(10, 30)
CLOSE_TIME = time(15, 25)


def load_fut(inst: str) -> pd.DataFrame:
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    df = con.execute(f"""
        WITH ranked AS (
            SELECT timestamp, expiry, open, high, low, close,
                   ROW_NUMBER() OVER (PARTITION BY timestamp ORDER BY expiry ASC) rn
            FROM read_parquet('{glob}', union_by_name=True)
            WHERE option_type='FUT'
        )
        SELECT timestamp, open, high, low, close FROM ranked WHERE rn=1
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df = df[df["timestamp"].dt.weekday < 5].sort_values("timestamp").reset_index(drop=True)
    return df


def get_ltp(inst, d, exp, strike, opt, t):
    """Get option LTP at specific minute (or next minute available)."""
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    r = con.execute(f"""
        SELECT close FROM read_parquet('{glob}', union_by_name=True)
        WHERE option_type='{opt}' AND strike={strike} AND expiry=DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIME) >= TIME '{t.strftime("%H:%M:%S")}'
        ORDER BY timestamp LIMIT 1
    """).fetchone()
    return float(r[0]) if r else None


def _first_breach_time(df, col, threshold, direction):
    if direction == "below":
        m = df[df[col] <= threshold]
    else:
        m = df[df[col] >= threshold]
    return None if m.empty else m.iloc[0]["time"]


def compute_pe_yellow_fire_time(day_fut, spot_entry, pe_strike, buf_pct, move_thr_pct):
    """Compute time when PE-side Yellow fires: BUFFER_X% AND BIG_MOVE_30 (ANY 30-min)."""
    intraday = day_fut[day_fut["time"] >= ENTRY_TIME].copy().reset_index(drop=True)
    if intraday.empty: return None
    
    pe_buf = spot_entry - pe_strike
    buffer_trigger = spot_entry - (buf_pct/100) * pe_buf
    
    # When does spot break buffer threshold?
    buf_time = _first_breach_time(intraday, "low", buffer_trigger, "below")
    if buf_time is None: return None
    
    # When does big-move-30 fire for PE side (down move)?
    bm_time = None
    for i in range(30, len(intraday)):
        window = intraday.iloc[i-30:i+1]
        net_pct = (window["close"].iloc[-1] - window["close"].iloc[0]) / window["close"].iloc[0] * 100
        if net_pct <= -move_thr_pct:
            bm_time = intraday.iloc[i]["time"]
            break
    if bm_time is None: return None
    
    # Combined fires at LATER of the two
    return max(buf_time, bm_time)


def compute_ce_yellow_fire_time(day_fut, spot_entry, ce_strike, move_thr_pct):
    """Compute CE-side Yellow: RANGE_BREAK AND BIG_MOVE_30."""
    intraday = day_fut[day_fut["time"] >= ENTRY_TIME].copy().reset_index(drop=True)
    pre_entry = day_fut[day_fut["time"] < ENTRY_TIME].copy()
    if intraday.empty or pre_entry.empty: return None
    
    phi = pre_entry["high"].max()
    break_time = _first_breach_time(intraday, "high", phi * 1.001, "above")
    if break_time is None: return None
    
    bm_time = None
    for i in range(30, len(intraday)):
        window = intraday.iloc[i-30:i+1]
        net_pct = (window["close"].iloc[-1] - window["close"].iloc[0]) / window["close"].iloc[0] * 100
        if net_pct >= move_thr_pct:
            bm_time = intraday.iloc[i]["time"]
            break
    if bm_time is None: return None
    
    return max(break_time, bm_time)


def main():
    instruments_cfg = {
        "NIFTY":  {"expiries": set(NIFTY_WEEKLY_EXPIRIES),  "grid": 50,  "lot": 75, "lots_per_cr": 43,
                   "pe_rule_buf": 50, "pe_rule_move": 0.4, "ce_rule_move": 0.5},
        "SENSEX": {"expiries": set(SENSEX_WEEKLY_EXPIRIES), "grid": 100, "lot": 20, "lots_per_cr": 40,
                   "pe_rule_buf": 60, "pe_rule_move": 0.4, "ce_rule_move": 0.4},
    }
    OTM_USE = 0.7
    
    all_results = {}
    
    for inst, cfg in instruments_cfg.items():
        print(f"\n[{inst}] Loading FUT ...")
        fut = load_fut(inst)
        e0_days = sorted([d for d in fut["date"].unique() if d in cfg["expiries"]])
        print(f"  {len(e0_days)} E-0 days")
        
        results = []
        for d in e0_days:
            day_fut = fut[fut["date"] == d].copy()
            if day_fut.empty: continue
            ent = day_fut[day_fut["time"] >= ENTRY_TIME]
            if ent.empty: continue
            spot_entry = float(ent.iloc[0]["close"])
            cls = day_fut[day_fut["time"] <= CLOSE_TIME]
            if cls.empty: continue
            spot_close = float(cls.iloc[-1]["close"])
            
            pe_strike = int(round(spot_entry * (1 - OTM_USE/100) / cfg["grid"]) * cfg["grid"])
            ce_strike = int(round(spot_entry * (1 + OTM_USE/100) / cfg["grid"]) * cfg["grid"])
            
            # Entry premiums
            pe_entry_ltp = get_ltp(inst, d, d, pe_strike, "PE", ENTRY_TIME)
            ce_entry_ltp = get_ltp(inst, d, d, ce_strike, "CE", ENTRY_TIME)
            if pe_entry_ltp is None or ce_entry_ltp is None:
                continue
            
            # Yellow fire times for PE & CE
            pe_y_time = compute_pe_yellow_fire_time(
                day_fut, spot_entry, pe_strike, cfg["pe_rule_buf"], cfg["pe_rule_move"])
            ce_y_time = compute_ce_yellow_fire_time(
                day_fut, spot_entry, ce_strike, cfg["ce_rule_move"])
            
            # Exit premiums at Yellow fire (if any)
            pe_y_exit = get_ltp(inst, d, d, pe_strike, "PE", pe_y_time) if pe_y_time else None
            ce_y_exit_on_pe_fire = get_ltp(inst, d, d, ce_strike, "CE", pe_y_time) if pe_y_time else None
            ce_y_exit = get_ltp(inst, d, d, ce_strike, "CE", ce_y_time) if ce_y_time else None
            pe_y_exit_on_ce_fire = get_ltp(inst, d, d, pe_strike, "PE", ce_y_time) if ce_y_time else None
            
            # ITM penalties at expiry
            pe_itm_pts = max(0, pe_strike - spot_close)
            ce_itm_pts = max(0, spot_close - ce_strike)
            
            # P&L per share for each strategy
            # A. HOLD: collect entry premium, subtract ITM penalty on each leg
            pl_hold_pe = pe_entry_ltp - pe_itm_pts
            pl_hold_ce = ce_entry_ltp - ce_itm_pts
            pl_hold_total = pl_hold_pe + pl_hold_ce
            
            # B. YELLOW-CLOSE-BOTH (close both legs at Yellow fire)
            # If PE side Yellow fires first → close both at pe_y_time
            # If CE side Yellow fires first → close both at ce_y_time
            # If neither fires → hold to expiry
            yellow_fire_time = None
            yellow_side = None
            if pe_y_time is not None and ce_y_time is not None:
                if pe_y_time <= ce_y_time:
                    yellow_fire_time = pe_y_time; yellow_side = "PE"
                else:
                    yellow_fire_time = ce_y_time; yellow_side = "CE"
            elif pe_y_time is not None:
                yellow_fire_time = pe_y_time; yellow_side = "PE"
            elif ce_y_time is not None:
                yellow_fire_time = ce_y_time; yellow_side = "CE"
            
            if yellow_fire_time is not None:
                pe_exit = get_ltp(inst, d, d, pe_strike, "PE", yellow_fire_time)
                ce_exit = get_ltp(inst, d, d, ce_strike, "CE", yellow_fire_time)
                if pe_exit is not None and ce_exit is not None:
                    pl_yc_pe = pe_entry_ltp - pe_exit
                    pl_yc_ce = ce_entry_ltp - ce_exit
                    pl_yclose_both = pl_yc_pe + pl_yc_ce
                else:
                    pl_yclose_both = pl_hold_total  # fallback
            else:
                pl_yclose_both = pl_hold_total
            
            # C. YELLOW-CLOSE-LOSING (close only the leg that triggered Yellow)
            if yellow_side == "PE" and pe_y_exit is not None:
                pl_yclose_losing = (pe_entry_ltp - pe_y_exit) + (ce_entry_ltp - ce_itm_pts)
            elif yellow_side == "CE" and ce_y_exit is not None:
                pl_yclose_losing = (ce_entry_ltp - ce_y_exit) + (pe_entry_ltp - pe_itm_pts)
            else:
                pl_yclose_losing = pl_hold_total
            
            results.append({
                "date": d, "inst": inst,
                "spot_entry": spot_entry, "spot_close": spot_close,
                "pe_strike": pe_strike, "ce_strike": ce_strike,
                "pe_entry_ltp": pe_entry_ltp, "ce_entry_ltp": ce_entry_ltp,
                "entry_combined": pe_entry_ltp + ce_entry_ltp,
                "pe_yellow_fire": str(pe_y_time) if pe_y_time else None,
                "ce_yellow_fire": str(ce_y_time) if ce_y_time else None,
                "yellow_side": yellow_side,
                "pe_itm_pts": pe_itm_pts, "ce_itm_pts": ce_itm_pts,
                "pl_HOLD": round(pl_hold_total, 2),
                "pl_YELLOW_CLOSE_BOTH": round(pl_yclose_both, 2),
                "pl_YELLOW_CLOSE_LOSING": round(pl_yclose_losing, 2),
            })
        
        df = pd.DataFrame(results)
        all_results[inst] = df
        df.to_csv(OUT / f"{inst}_ev_per_day.csv", index=False)
    
    # ────── Aggregate stats ──────
    print("\n" + "="*100)
    print(f"EV COMPARISON at {OTM_USE}% OTM (entry 10:30 IST, expiry 15:25)")
    print("="*100)
    
    for inst, df in all_results.items():
        cfg = instruments_cfg[inst]
        per_lot = cfg["lot"]
        per_cr = cfg["lots_per_cr"]
        scale_to_cr = per_lot * per_cr  # multiply ₹/share by this to get ₹/Cr
        
        n = len(df)
        n_fired = df["yellow_side"].notna().sum()
        
        # Per share P&L
        mean_hold = df["pl_HOLD"].mean()
        mean_yc_both = df["pl_YELLOW_CLOSE_BOTH"].mean()
        mean_yc_losing = df["pl_YELLOW_CLOSE_LOSING"].mean()
        
        # Win rate
        win_hold = (df["pl_HOLD"] > 0).mean() * 100
        win_yc = (df["pl_YELLOW_CLOSE_BOTH"] > 0).mean() * 100
        
        # Worst day
        worst_hold = df["pl_HOLD"].min()
        worst_yc = df["pl_YELLOW_CLOSE_BOTH"].min()
        
        # ITM days specifically
        itm_days = df[(df["pe_itm_pts"] > 0) | (df["ce_itm_pts"] > 0)]
        n_itm = len(itm_days)
        
        print(f"\n{inst}: {n} E-0 days, Yellow fired on {n_fired} ({n_fired/n*100:.0f}%)")
        print(f"  Days with ITM event: {n_itm} ({n_itm/n*100:.0f}%)")
        print(f"\n  PER SHARE:                  HOLD      Y_CLOSE_BOTH   Y_CLOSE_LOSING")
        print(f"  Mean P&L:                   ₹{mean_hold:>+7.2f}  ₹{mean_yc_both:>+7.2f}    ₹{mean_yc_losing:>+7.2f}")
        print(f"  Worst day:                  ₹{worst_hold:>+7.2f}  ₹{worst_yc:>+7.2f}    ₹{df['pl_YELLOW_CLOSE_LOSING'].min():>+7.2f}")
        print(f"  Win rate:                   {win_hold:>5.0f}%    {win_yc:>5.0f}%       {(df['pl_YELLOW_CLOSE_LOSING'] > 0).mean()*100:>5.0f}%")
        print(f"  Mean ₹/Cr (lot {per_lot} × {per_cr}/Cr):")
        print(f"    HOLD:           ₹{mean_hold * scale_to_cr:>+10,.0f}/Cr/day")
        print(f"    Y_CLOSE_BOTH:   ₹{mean_yc_both * scale_to_cr:>+10,.0f}/Cr/day")
        print(f"    Y_CLOSE_LOSING: ₹{mean_yc_losing * scale_to_cr:>+10,.0f}/Cr/day")
        
        # On Yellow-fired days only
        fired_df = df[df["yellow_side"].notna()]
        if len(fired_df) > 0:
            print(f"\n  ON YELLOW-FIRED DAYS ({len(fired_df)} days):")
            print(f"  Mean HOLD P&L:           ₹{fired_df['pl_HOLD'].mean():>+7.2f}/share")
            print(f"  Mean Y_CLOSE_BOTH P&L:   ₹{fired_df['pl_YELLOW_CLOSE_BOTH'].mean():>+7.2f}/share")
            print(f"  Mean Y_CLOSE_LOSING:     ₹{fired_df['pl_YELLOW_CLOSE_LOSING'].mean():>+7.2f}/share")
            print(f"  Days saved (Y better than HOLD by ≥5): {(fired_df['pl_YELLOW_CLOSE_BOTH'] - fired_df['pl_HOLD'] >= 5).sum()}")
            print(f"  Days hurt (Y worse than HOLD by ≥5):  {(fired_df['pl_HOLD'] - fired_df['pl_YELLOW_CLOSE_BOTH'] >= 5).sum()}")
        
        # On non-fired days
        notfired_df = df[df["yellow_side"].isna()]
        if len(notfired_df) > 0:
            print(f"\n  ON YELLOW-NOT-FIRED DAYS ({len(notfired_df)} days):")
            print(f"  Mean HOLD P&L: ₹{notfired_df['pl_HOLD'].mean():>+7.2f}/share")
            print(f"  Worst day:     ₹{notfired_df['pl_HOLD'].min():>+7.2f}/share")
            print(f"  Win rate:      {(notfired_df['pl_HOLD'] > 0).mean()*100:.0f}%")
    
    print(f"\nDone. Full per-day results in {OUT}")


if __name__ == "__main__":
    main()
