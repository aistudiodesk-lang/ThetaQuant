"""
ANALYSIS 022 — Profit-take rules for B1 (close OTM) sells

Question (Rohan):
  "I take profit at ~30% of sold value when close-OTM (0.5-1%). Backtest:
   what % decay + time combo gives best EV without exposing me to late spikes?"

Strategies tested (B1 entry at 10:30 with PE+CE at OTM%):
  HOLD                — to expiry 15:25 (baseline)
  PT_X                — close both when combined ≤ (1-X) × entry  (X = 20,30,40,50,60,70)
  TIME_HHMM           — close both at HH:MM regardless of P&L (12:00, 13:00, 14:00, 14:30)
  PT_30_OR_TIME_1400  — combined: profit-take 30% OR time stop 14:00

Output:
  Per-strategy mean P&L, win-rate, worst day, fire-time distribution.
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

OUT = ROOT / "results" / "022_b1_profit_take"
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
        SELECT timestamp, close FROM ranked WHERE rn=1
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df = df[df["timestamp"].dt.weekday < 5].sort_values("timestamp").reset_index(drop=True)
    return df


def load_combined_premium_series(inst, d, exp, pe_strike, ce_strike):
    """Load minute-by-minute CE+PE combined premium from 10:30 to 15:25."""
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, close
        FROM read_parquet('{glob}', union_by_name=True)
        WHERE expiry=DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND ((strike={pe_strike} AND option_type='PE') OR (strike={ce_strike} AND option_type='CE'))
    """).fetchdf()
    if df.empty:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["time"] = df["timestamp"].dt.time
    # Filter to entry window
    df = df[(df["time"] >= ENTRY_TIME) & (df["time"] <= CLOSE_TIME)].copy()
    if df.empty:
        return None
    # Pivot to wide
    piv = df.pivot_table(index="timestamp", columns="option_type", values="close", aggfunc="last")
    if "CE" not in piv.columns or "PE" not in piv.columns:
        return None
    piv = piv.dropna()
    piv["combined"] = piv["CE"] + piv["PE"]
    piv["time"] = piv.index.time
    return piv.sort_index().reset_index()


def simulate_strategies(series, entry_combined, spot_close, pe_strike, ce_strike):
    """Run all strategies on one day's combined-premium series."""
    if series is None or series.empty:
        return None
    
    pl = {}
    
    # HOLD to expiry — exit at 15:25 (or last available)
    last = series.iloc[-1]
    pe_itm_pts = max(0, pe_strike - spot_close)
    ce_itm_pts = max(0, spot_close - ce_strike)
    # P&L = entry_premium - exit_premium (at close, with ITM penalty effectively in LTP)
    pl["HOLD"] = entry_combined - last["combined"]
    
    # PROFIT_TAKE rules — close when combined ≤ (1-X)*entry
    for x in [20, 30, 40, 50, 60, 70]:
        target = entry_combined * (1 - x/100)
        hit = series[series["combined"] <= target]
        if hit.empty:
            # Never hit profit target — hold to expiry (same as HOLD)
            pl[f"PT_{x}"] = pl["HOLD"]
        else:
            # Exit at first time combined ≤ target
            exit_combined = hit.iloc[0]["combined"]
            pl[f"PT_{x}"] = entry_combined - exit_combined
    
    # TIME stops — exit at specific times
    for t_str in ["12:00", "13:00", "13:30", "14:00", "14:30", "15:00"]:
        hh, mm = map(int, t_str.split(":"))
        target_time = time(hh, mm)
        hit = series[series["time"] >= target_time]
        if hit.empty:
            pl[f"TIME_{t_str.replace(':','')}"] = pl["HOLD"]
        else:
            exit_combined = hit.iloc[0]["combined"]
            pl[f"TIME_{t_str.replace(':','')}"] = entry_combined - exit_combined
    
    # COMBINED: PT_30 OR TIME_1400 (whichever fires first)
    pt30_target = entry_combined * 0.70
    pt30_hits = series[series["combined"] <= pt30_target]
    time1400_hits = series[series["time"] >= time(14, 0)]
    
    if not pt30_hits.empty and not time1400_hits.empty:
        pt30_t = pt30_hits.iloc[0]["timestamp"]
        time1400_t = time1400_hits.iloc[0]["timestamp"]
        if pt30_t <= time1400_t:
            exit_combined = pt30_hits.iloc[0]["combined"]
            fire = "PT30"
        else:
            exit_combined = time1400_hits.iloc[0]["combined"]
            fire = "TIME"
        pl["PT30_OR_TIME1400"] = entry_combined - exit_combined
    elif not pt30_hits.empty:
        pl["PT30_OR_TIME1400"] = entry_combined - pt30_hits.iloc[0]["combined"]
    elif not time1400_hits.empty:
        pl["PT30_OR_TIME1400"] = entry_combined - time1400_hits.iloc[0]["combined"]
    else:
        pl["PT30_OR_TIME1400"] = pl["HOLD"]
    
    # COMBINED: PT_50 OR TIME_1330
    pt50_target = entry_combined * 0.50
    pt50_hits = series[series["combined"] <= pt50_target]
    time1330_hits = series[series["time"] >= time(13, 30)]
    
    if not pt50_hits.empty and not time1330_hits.empty:
        pt50_t = pt50_hits.iloc[0]["timestamp"]
        time1330_t = time1330_hits.iloc[0]["timestamp"]
        if pt50_t <= time1330_t:
            exit_combined = pt50_hits.iloc[0]["combined"]
        else:
            exit_combined = time1330_hits.iloc[0]["combined"]
        pl["PT50_OR_TIME1330"] = entry_combined - exit_combined
    elif not pt50_hits.empty:
        pl["PT50_OR_TIME1330"] = entry_combined - pt50_hits.iloc[0]["combined"]
    elif not time1330_hits.empty:
        pl["PT50_OR_TIME1330"] = entry_combined - time1330_hits.iloc[0]["combined"]
    else:
        pl["PT50_OR_TIME1330"] = pl["HOLD"]
    
    # Also track when PT levels are hit (for time distribution)
    pl["_pt30_fire_time"] = None
    pt30_hits_t = series[series["combined"] <= entry_combined * 0.70]
    if not pt30_hits_t.empty:
        pl["_pt30_fire_time"] = pt30_hits_t.iloc[0]["time"]
    pl["_pt50_fire_time"] = None
    pt50_hits_t = series[series["combined"] <= entry_combined * 0.50]
    if not pt50_hits_t.empty:
        pl["_pt50_fire_time"] = pt50_hits_t.iloc[0]["time"]
    
    return pl


def main():
    instruments_cfg = {
        "NIFTY":  {"expiries": set(NIFTY_WEEKLY_EXPIRIES),  "grid": 50,  "lot": 75, "lots_per_cr": 43},
        "SENSEX": {"expiries": set(SENSEX_WEEKLY_EXPIRIES), "grid": 100, "lot": 20, "lots_per_cr": 40},
    }
    
    for inst, cfg in instruments_cfg.items():
        print(f"\n[{inst}] Loading FUT ...")
        fut = load_fut(inst)
        e0_days = sorted([d for d in fut["date"].unique() if d in cfg["expiries"]])
        print(f"  {len(e0_days)} E-0 days")
        
        for otm in [0.5, 0.7, 1.0]:
            print(f"\n  Processing {inst} {otm}% OTM ...")
            results = []
            for d in e0_days:
                day_fut = fut[fut["date"] == d]
                ent = day_fut[day_fut["time"] >= ENTRY_TIME]
                if ent.empty: continue
                spot_entry = float(ent.iloc[0]["close"])
                cls = day_fut[day_fut["time"] <= CLOSE_TIME]
                if cls.empty: continue
                spot_close = float(cls.iloc[-1]["close"])
                
                pe_strike = int(round(spot_entry * (1 - otm/100) / cfg["grid"]) * cfg["grid"])
                ce_strike = int(round(spot_entry * (1 + otm/100) / cfg["grid"]) * cfg["grid"])
                
                series = load_combined_premium_series(inst, d, d, pe_strike, ce_strike)
                if series is None or series.empty: continue
                entry_combined = float(series.iloc[0]["combined"])
                if entry_combined <= 0: continue
                
                pl = simulate_strategies(series, entry_combined, spot_close, pe_strike, ce_strike)
                if pl is None: continue
                
                row = {"date": d, "inst": inst, "otm": otm,
                       "spot_entry": spot_entry, "spot_close": spot_close,
                       "entry_combined": round(entry_combined, 2),
                       "pe_itm": spot_close < pe_strike,
                       "ce_itm": spot_close > ce_strike}
                for k, v in pl.items():
                    if not k.startswith("_"):
                        row[k] = round(v, 2)
                row["pt30_fire_time"] = str(pl.get("_pt30_fire_time", ""))
                row["pt50_fire_time"] = str(pl.get("_pt50_fire_time", ""))
                results.append(row)
            
            df = pd.DataFrame(results)
            if df.empty: continue
            df.to_csv(OUT / f"{inst}_{int(otm*10)}pct_per_day.csv", index=False)
            
            # Aggregate stats
            strategies = [c for c in df.columns if c in ["HOLD", "PT_20", "PT_30", "PT_40", "PT_50", "PT_60", "PT_70",
                                                          "TIME_1200", "TIME_1300", "TIME_1330", "TIME_1400", "TIME_1430", "TIME_1500",
                                                          "PT30_OR_TIME1400", "PT50_OR_TIME1330"]]
            stats = []
            for s in strategies:
                vals = df[s].dropna()
                if vals.empty: continue
                mean_pl = vals.mean()
                win_rate = (vals > 0).mean() * 100
                worst = vals.min()
                # Mean in Rs/Cr terms
                rs_per_cr = mean_pl * cfg["lot"] * cfg["lots_per_cr"]
                worst_rs_per_cr = worst * cfg["lot"] * cfg["lots_per_cr"]
                stats.append({
                    "strategy": s,
                    "mean_per_share": round(mean_pl, 2),
                    "win_rate_pct": round(win_rate, 0),
                    "worst_per_share": round(worst, 2),
                    "mean_rs_per_cr_per_day": round(rs_per_cr, 0),
                    "worst_rs_per_cr": round(worst_rs_per_cr, 0),
                })
            sdf = pd.DataFrame(stats)
            sdf.to_csv(OUT / f"{inst}_{int(otm*10)}pct_strategy_stats.csv", index=False)
            
            print(f"\n  {inst} {otm}% OTM — strategy comparison (n={len(df)}):")
            print(sdf.to_string(index=False))
    
    print(f"\nDone. Files in {OUT}")


if __name__ == "__main__":
    main()
