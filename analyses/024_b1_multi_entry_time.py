"""
ANALYSIS 024 — Multi-entry-time backtest for Tier 2/3 (mid/near OTM) sells

Tests 8 entry times × 7 OTM tiers × multiple filter thresholds across
both NIFTY (56 E-0 days) and SENSEX (54 E-0 days).

KEY QUESTIONS:
  1. What's the optimal entry time per tier? (User has done 9:45-1pm)
  2. Does later entry help on volatile-morning days?
  3. Should Tier 3 be allowed on volatile-morning days at all?

Filter scaling per instrument (based on actual pre-range distributions):
  NIFTY: median 0.51%, so use 0.4-0.5% threshold (TIGHT works)
  SENSEX: median 0.85%, need 0.7-0.8% threshold to get usable sample
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

OUT = ROOT / "results" / "024_b1_multi_entry_time"
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()
CLOSE_TIME = time(15, 25)

ENTRY_TIMES = [time(9,45), time(10,0), time(10,30), time(11,0),
               time(11,30), time(12,0), time(12,30), time(13,0)]
OTM_TIERS = [0.5, 0.7, 1.0, 1.25, 1.5, 2.0]


def load_fut(inst: str) -> pd.DataFrame:
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    df = con.execute(f"""
        WITH ranked AS (
            SELECT timestamp, expiry, open, high, low, close,
                   ROW_NUMBER() OVER (PARTITION BY timestamp ORDER BY expiry ASC) rn
            FROM read_parquet('{glob}', union_by_name=True) WHERE option_type='FUT'
        )
        SELECT timestamp, open, high, low, close FROM ranked WHERE rn=1
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df = df[df["timestamp"].dt.weekday < 5].sort_values("timestamp").reset_index(drop=True)
    return df


def load_day_options(inst, d, exp, strikes_pe, strikes_ce):
    """Bulk load all relevant CE+PE bars for one day."""
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    pe_list = ",".join(str(s) for s in strikes_pe)
    ce_list = ",".join(str(s) for s in strikes_ce)
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, close
        FROM read_parquet('{glob}', union_by_name=True)
        WHERE expiry=DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND ((option_type='PE' AND strike IN ({pe_list})) OR
               (option_type='CE' AND strike IN ({ce_list})))
    """).fetchdf()
    if df.empty: return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["time"] = df["timestamp"].dt.time
    return df


def get_premium_at(opt_df, strike, opt_type, entry_t):
    """Get LTP at or after entry_t."""
    m = opt_df[(opt_df["strike"]==strike) & (opt_df["option_type"]==opt_type) & (opt_df["time"]>=entry_t)]
    if m.empty: return None
    return float(m.sort_values("timestamp").iloc[0]["close"])


def get_series(opt_df, strike, opt_type, entry_t):
    """Get full minute series for one strike from entry_t to close."""
    m = opt_df[(opt_df["strike"]==strike) & (opt_df["option_type"]==opt_type) & 
               (opt_df["time"]>=entry_t) & (opt_df["time"]<=CLOSE_TIME)]
    return m.sort_values("timestamp").reset_index(drop=True)


def simulate(combined_series, entry_combined, spot_close, pe_strike, ce_strike):
    """Simulate exit strategies on a combined-premium series."""
    if combined_series is None or combined_series.empty: return None
    last = combined_series.iloc[-1]["combined"]
    out = {"HOLD": entry_combined - last}
    
    for x in [60, 70, 80]:
        target = entry_combined * (1 - x/100)
        hit = combined_series[combined_series["combined"] <= target]
        if hit.empty: out[f"PT_{x}"] = out["HOLD"]
        else: out[f"PT_{x}"] = entry_combined - hit.iloc[0]["combined"]
    
    for t_str in ["1300", "1400", "1430"]:
        hh, mm = int(t_str[:2]), int(t_str[2:])
        target_t = time(hh, mm)
        hit = combined_series[combined_series["time"] >= target_t]
        if hit.empty: out[f"T_{t_str}"] = out["HOLD"]
        else: out[f"T_{t_str}"] = entry_combined - hit.iloc[0]["combined"]
    
    return out


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
        scale = cfg["lot"] * cfg["lots_per_cr"]
        
        all_rows = []
        for di, d in enumerate(e0_days):
            day_fut = fut[fut["date"] == d]
            if day_fut.empty: continue
            cls = day_fut[day_fut["time"] <= CLOSE_TIME]
            if cls.empty: continue
            spot_close = float(cls.iloc[-1]["close"])
            day_open = float(day_fut.iloc[0]["close"])
            
            # Compute strikes needed for all (entry_time, otm) combos
            pe_strikes = set(); ce_strikes = set()
            spot_at_entry = {}
            for et in ENTRY_TIMES:
                ent = day_fut[day_fut["time"] >= et]
                if ent.empty: continue
                se = float(ent.iloc[0]["close"])
                spot_at_entry[et] = se
                for otm in OTM_TIERS:
                    pe = int(round(se * (1 - otm/100) / cfg["grid"]) * cfg["grid"])
                    ce = int(round(se * (1 + otm/100) / cfg["grid"]) * cfg["grid"])
                    pe_strikes.add(pe); ce_strikes.add(ce)
            
            if not pe_strikes or not ce_strikes: continue
            
            # Bulk load options
            opt_df = load_day_options(inst, d, d, sorted(pe_strikes), sorted(ce_strikes))
            if opt_df is None or opt_df.empty: continue
            
            # For each (entry, otm)
            for et in ENTRY_TIMES:
                if et not in spot_at_entry: continue
                se = spot_at_entry[et]
                
                # Pre-entry context (from 9:15 to entry)
                pre = day_fut[day_fut["time"] < et]
                if pre.empty: continue
                pre_range_pct = (pre["high"].max() - pre["low"].min()) / pre.iloc[0]["close"] * 100
                pre_move_pct = (se - pre.iloc[0]["close"]) / pre.iloc[0]["close"] * 100
                
                for otm in OTM_TIERS:
                    pe_strike = int(round(se * (1 - otm/100) / cfg["grid"]) * cfg["grid"])
                    ce_strike = int(round(se * (1 + otm/100) / cfg["grid"]) * cfg["grid"])
                    
                    pe_ent = get_premium_at(opt_df, pe_strike, "PE", et)
                    ce_ent = get_premium_at(opt_df, ce_strike, "CE", et)
                    if pe_ent is None or ce_ent is None: continue
                    entry_combined = pe_ent + ce_ent
                    if entry_combined <= 0: continue
                    
                    # Build combined-premium series
                    pe_series = get_series(opt_df, pe_strike, "PE", et)
                    ce_series = get_series(opt_df, ce_strike, "CE", et)
                    if pe_series.empty or ce_series.empty: continue
                    
                    # Merge on timestamp
                    merged = pe_series.merge(ce_series, on="timestamp", suffixes=("_pe","_ce"))
                    if merged.empty: continue
                    merged["combined"] = merged["close_pe"] + merged["close_ce"]
                    merged["time"] = merged["timestamp"].dt.time
                    
                    res = simulate(merged[["timestamp","combined","time"]], entry_combined, spot_close, pe_strike, ce_strike)
                    if res is None: continue
                    
                    row = {"date": d, "inst": inst, "entry": et.strftime("%H:%M"), "otm": otm,
                           "spot_entry": round(se, 2), "spot_close": round(spot_close, 2),
                           "pe_strike": pe_strike, "ce_strike": ce_strike,
                           "pre_range_pct": round(pre_range_pct, 2),
                           "pre_move_pct": round(pre_move_pct, 2),
                           "entry_combined": round(entry_combined, 2),
                           "entry_per_cr": round(entry_combined * scale, 0),
                           "pe_itm": spot_close < pe_strike,
                           "ce_itm": spot_close > ce_strike}
                    for k, v in res.items():
                        row[f"pl_{k}_pcr"] = round(v * scale, 0)
                    all_rows.append(row)
            
            if (di+1) % 10 == 0:
                print(f"  Done {di+1}/{len(e0_days)} days")
        
        df = pd.DataFrame(all_rows)
        df.to_csv(OUT / f"{inst}_multi_entry.csv", index=False)
        print(f"  {inst} → {len(df)} (day×entry×otm) sim rows saved")
    
    print("\nNow running aggregation ...")
    
    # Test filter combos per instrument×entry×OTM and find sweet spots
    filter_configs = {
        "NIFTY": [
            (0.5, 0.4, 40000, "tight"),
            (0.5, 0.5, 40000, "med"),
            (0.7, 0.4, 30000, "tight"),
            (0.7, 0.5, 30000, "med"),
            (0.7, 0.7, 30000, "loose"),
            (1.0, 0.5, 20000, "med"),
            (1.0, 0.7, 20000, "loose"),
            (1.25, 0.7, 15000, "loose"),
            (1.5, 0.7, 12500, "loose"),
            (2.0, 1.0, 8000, "loosest"),
        ],
        "SENSEX": [
            (0.5, 0.5, 40000, "tight"),
            (0.5, 0.7, 40000, "med"),
            (0.7, 0.5, 30000, "tight"),
            (0.7, 0.7, 30000, "med"),
            (0.7, 0.8, 30000, "loose"),
            (1.0, 0.7, 20000, "med"),
            (1.0, 0.8, 20000, "loose"),
            (1.25, 0.8, 15000, "loose"),
            (1.5, 1.0, 12500, "loosest"),
            (2.0, 1.0, 8000, "loosest"),
        ],
    }
    
    strategies = ["HOLD", "PT_60", "PT_70", "PT_80", "T_1400", "T_1430"]
    
    summary_rows = []
    for inst in ["NIFTY", "SENSEX"]:
        df = pd.read_csv(OUT / f"{inst}_multi_entry.csv")
        for otm, range_max, prem_min, label in filter_configs[inst]:
            for et_str in ["09:45", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00"]:
                sub = df[(df["otm"] == otm) & (df["entry"] == et_str) &
                         (df["pre_range_pct"] <= range_max) &
                         (df["entry_per_cr"] >= prem_min)]
                if len(sub) < 4: continue
                
                for s in strategies:
                    col = f"pl_{s}_pcr"
                    if col not in sub.columns: continue
                    vals = sub[col].dropna()
                    if vals.empty: continue
                    summary_rows.append({
                        "inst": inst, "otm": otm, "entry": et_str,
                        "range_max": range_max, "prem_min": prem_min, "label": label,
                        "strategy": s, "n": len(sub),
                        "mean_pcr": round(vals.mean(), 0),
                        "win_pct": round((vals>0).mean()*100, 0),
                        "worst_pcr": round(vals.min(), 0),
                        "p25_pcr": round(vals.quantile(0.25), 0),
                    })
    
    s_df = pd.DataFrame(summary_rows)
    s_df.to_csv(OUT / "summary.csv", index=False)
    
    # Print best (entry time × strategy) per (inst × otm × filter) with worst ≥ -200K
    print("\n" + "="*120)
    print("BEST ENTRY TIME + STRATEGY per (instrument × OTM × filter)  (constraint: worst ≥ -200K)")
    print("="*120)
    for inst in ["NIFTY", "SENSEX"]:
        for otm in OTM_TIERS:
            print(f"\n{inst} @ {otm}% OTM:")
            for range_max, prem_min, label in [(r,p,l) for o,r,p,l in filter_configs[inst] if o == otm]:
                sub = s_df[(s_df["inst"]==inst) & (s_df["otm"]==otm) & 
                           (s_df["range_max"]==range_max) & (s_df["prem_min"]==prem_min) &
                           (s_df["worst_pcr"] >= -200000)]
                if sub.empty:
                    sub_all = s_df[(s_df["inst"]==inst) & (s_df["otm"]==otm) & 
                                   (s_df["range_max"]==range_max) & (s_df["prem_min"]==prem_min)]
                    if not sub_all.empty:
                        worst_strat = sub_all.loc[sub_all["worst_pcr"].idxmin()]
                        print(f"  Filter [{label}: range≤{range_max}% prem≥{prem_min}]: NO STRATEGY MEETS -200K cap. Worst across all: {worst_strat['strategy']} @ {worst_strat['entry']} = ₹{worst_strat['worst_pcr']:,.0f}")
                    continue
                # Top 3 by mean
                top = sub.sort_values("mean_pcr", ascending=False).head(3)
                print(f"  Filter [{label}: range≤{range_max}% prem≥{prem_min}]:")
                for _, r in top.iterrows():
                    print(f"    {r['entry']:>5} × {r['strategy']:>8} → mean=₹{r['mean_pcr']:+,.0f}/Cr, n={int(r['n'])}, win={int(r['win_pct'])}%, worst=₹{r['worst_pcr']:+,.0f}/Cr")
    
    print(f"\nFull summary in {OUT}")


if __name__ == "__main__":
    main()
