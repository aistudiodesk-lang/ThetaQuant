"""
ANALYSIS 023 — Mid OTM (1.25-2%) & Near OTM (0.5-1%) backtest with entry filters

Goals:
  - NEAR OTM (0.5-1%): combined premium ≥₹25K/Cr (ideal ≥35K), PT at 30%-left (PT_70)
  - MID OTM (1.25-2%): combined premium ≥₹12.5K/Cr
  - Worst day must not exceed ₹3.5L/Cr loss
  - Find entry filters: premium floor + spot range
  - Test exit rules: HOLD, PT_70 (30%-left), PT_60 (40%-left), TIME stops

Backtest design:
  For each E-0 day at each OTM%:
    1. Compute spot at 10:30, pre-entry range (9:15-10:30), entry premium per Cr
    2. Apply entry filters: premium floor + range ≤ X%
    3. If passes: simulate exit strategies
    4. Report mean ₹/Cr/day, win rate, worst day, days-passing-filter
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

OUT = ROOT / "results" / "023_b1_filtered_otm"
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


def load_combined_series(inst, d, exp, pe_strike, ce_strike):
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, close
        FROM read_parquet('{glob}', union_by_name=True)
        WHERE expiry=DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND ((strike={pe_strike} AND option_type='PE') OR (strike={ce_strike} AND option_type='CE'))
    """).fetchdf()
    if df.empty: return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["time"] = df["timestamp"].dt.time
    df = df[(df["time"] >= ENTRY_TIME) & (df["time"] <= CLOSE_TIME)].copy()
    if df.empty: return None
    piv = df.pivot_table(index="timestamp", columns="option_type", values="close", aggfunc="last").dropna()
    if "CE" not in piv.columns or "PE" not in piv.columns: return None
    piv["combined"] = piv["CE"] + piv["PE"]
    piv["time"] = piv.index.time
    return piv.sort_index().reset_index()


def simulate(series, entry_combined, spot_close, pe_strike, ce_strike):
    out = {}
    last = series.iloc[-1]
    out["HOLD"] = entry_combined - last["combined"]
    
    # PT_X where X = % decay captured (X=70 means take profit when 70% decay = 30% premium left)
    for x in [50, 60, 65, 70, 75, 80]:
        target = entry_combined * (1 - x/100)
        hit = series[series["combined"] <= target]
        if hit.empty: out[f"PT_{x}"] = out["HOLD"]
        else: out[f"PT_{x}"] = entry_combined - hit.iloc[0]["combined"]
    
    # Time stops
    for t_str in ["1230", "1300", "1330", "1400", "1430"]:
        hh, mm = int(t_str[:2]), int(t_str[2:])
        target_t = time(hh, mm)
        hit = series[series["time"] >= target_t]
        if hit.empty: out[f"T_{t_str}"] = out["HOLD"]
        else: out[f"T_{t_str}"] = entry_combined - hit.iloc[0]["combined"]
    
    # PT_70 OR TIME_1400
    pt70_target = entry_combined * 0.30
    pt70_hits = series[series["combined"] <= pt70_target]
    t14_hits = series[series["time"] >= time(14, 0)]
    if not pt70_hits.empty and not t14_hits.empty:
        if pt70_hits.iloc[0]["timestamp"] <= t14_hits.iloc[0]["timestamp"]:
            out["PT70_OR_T1400"] = entry_combined - pt70_hits.iloc[0]["combined"]
        else:
            out["PT70_OR_T1400"] = entry_combined - t14_hits.iloc[0]["combined"]
    elif not pt70_hits.empty:
        out["PT70_OR_T1400"] = entry_combined - pt70_hits.iloc[0]["combined"]
    elif not t14_hits.empty:
        out["PT70_OR_T1400"] = entry_combined - t14_hits.iloc[0]["combined"]
    else: out["PT70_OR_T1400"] = out["HOLD"]
    
    # PT_70 OR TIME_1330
    t1330_hits = series[series["time"] >= time(13, 30)]
    if not pt70_hits.empty and not t1330_hits.empty:
        if pt70_hits.iloc[0]["timestamp"] <= t1330_hits.iloc[0]["timestamp"]:
            out["PT70_OR_T1330"] = entry_combined - pt70_hits.iloc[0]["combined"]
        else:
            out["PT70_OR_T1330"] = entry_combined - t1330_hits.iloc[0]["combined"]
    elif not pt70_hits.empty:
        out["PT70_OR_T1330"] = entry_combined - pt70_hits.iloc[0]["combined"]
    elif not t1330_hits.empty:
        out["PT70_OR_T1330"] = entry_combined - t1330_hits.iloc[0]["combined"]
    else: out["PT70_OR_T1330"] = out["HOLD"]
    
    return out


def analyze_instrument(inst, cfg, otm_tiers):
    """Run full pipeline for one instrument across multiple OTM tiers."""
    print(f"\n[{inst}] Loading FUT ...")
    fut = load_fut(inst)
    e0_days = sorted([d for d in fut["date"].unique() if d in cfg["expiries"]])
    print(f"  {len(e0_days)} E-0 days")
    
    scale_to_cr = cfg["lot"] * cfg["lots_per_cr"]
    
    all_rows = []
    for d in e0_days:
        day_fut = fut[fut["date"] == d]
        ent = day_fut[day_fut["time"] >= ENTRY_TIME]
        if ent.empty: continue
        spot_entry = float(ent.iloc[0]["close"])
        cls = day_fut[day_fut["time"] <= CLOSE_TIME]
        if cls.empty: continue
        spot_close = float(cls.iloc[-1]["close"])
        
        # Pre-entry range (9:15-10:30)
        pre = day_fut[day_fut["time"] < ENTRY_TIME]
        if not pre.empty:
            pre_range_pct = (pre["high"].max() - pre["low"].min()) / pre.iloc[0]["close"] * 100
        else:
            pre_range_pct = 0
        # Pre-entry net move
        pre_move_pct = (spot_entry - pre.iloc[0]["close"]) / pre.iloc[0]["close"] * 100 if not pre.empty else 0
        # Day range
        day_range_pct = (day_fut["high"].max() - day_fut["low"].min()) / spot_entry * 100
        
        for otm in otm_tiers:
            pe_strike = int(round(spot_entry * (1 - otm/100) / cfg["grid"]) * cfg["grid"])
            ce_strike = int(round(spot_entry * (1 + otm/100) / cfg["grid"]) * cfg["grid"])
            series = load_combined_series(inst, d, d, pe_strike, ce_strike)
            if series is None or series.empty: continue
            entry_combined = float(series.iloc[0]["combined"])
            if entry_combined <= 0: continue
            entry_per_cr = entry_combined * scale_to_cr
            
            res = simulate(series, entry_combined, spot_close, pe_strike, ce_strike)
            row = {"date": d, "inst": inst, "otm": otm,
                   "spot_entry": round(spot_entry, 2), "spot_close": round(spot_close, 2),
                   "pre_range_pct": round(pre_range_pct, 2),
                   "pre_move_pct": round(pre_move_pct, 2),
                   "day_range_pct": round(day_range_pct, 2),
                   "entry_combined": round(entry_combined, 2),
                   "entry_per_cr": round(entry_per_cr, 0),
                   "pe_itm": spot_close < pe_strike,
                   "ce_itm": spot_close > ce_strike,
                   "pe_itm_pts": max(0, pe_strike - spot_close),
                   "ce_itm_pts": max(0, spot_close - ce_strike),
            }
            for k, v in res.items():
                row[f"pl_{k}"] = round(v, 2)
                row[f"pl_{k}_per_cr"] = round(v * scale_to_cr, 0)
            all_rows.append(row)
    
    df = pd.DataFrame(all_rows)
    df.to_csv(OUT / f"{inst}_per_day_all_otm.csv", index=False)
    return df, scale_to_cr


def main():
    instruments_cfg = {
        "NIFTY":  {"expiries": set(NIFTY_WEEKLY_EXPIRIES),  "grid": 50,  "lot": 75, "lots_per_cr": 43},
        "SENSEX": {"expiries": set(SENSEX_WEEKLY_EXPIRIES), "grid": 100, "lot": 20, "lots_per_cr": 40},
    }
    
    # OTM tiers to backtest: near + mid + deep
    OTM_TIERS = [0.5, 0.7, 1.0, 1.25, 1.5, 1.75, 2.0]
    
    print("\nRunning analyses ...")
    nifty_df, nifty_scale = analyze_instrument("NIFTY", instruments_cfg["NIFTY"], OTM_TIERS)
    sensex_df, sensex_scale = analyze_instrument("SENSEX", instruments_cfg["SENSEX"], OTM_TIERS)
    
    # Now print clean summary
    strategies = ["HOLD", "PT_50", "PT_60", "PT_65", "PT_70", "PT_75", "PT_80",
                  "T_1230", "T_1300", "T_1330", "T_1400", "T_1430",
                  "PT70_OR_T1400", "PT70_OR_T1330"]
    
    print("\n" + "="*100)
    print("RESULTS WITH ENTRY FILTERS (premium floor + pre-entry range ≤ 1%)")
    print("="*100)
    
    # Define filter rules per OTM
    # User's: 0.7% needs ₹30K/Cr, 0.5% needs ₹40K/Cr — extrapolate
    # Mid 1.25-2%: ≥₹12.5K/Cr
    # Near 0.5-1%: ≥₹25-35K/Cr
    premium_floors = {
        0.5:  40000, 0.7:  30000, 1.0: 20000,
        1.25: 15000, 1.5:  12500, 1.75: 10000, 2.0: 8000,
    }
    range_ceiling = 1.0  # 1% pre-entry range max
    
    summary_rows = []
    for inst, df in [("NIFTY", nifty_df), ("SENSEX", sensex_df)]:
        cfg = instruments_cfg[inst]
        scale = cfg["lot"] * cfg["lots_per_cr"]
        for otm in OTM_TIERS:
            sub_unfiltered = df[df["otm"] == otm].copy()
            n_total = len(sub_unfiltered)
            if n_total == 0: continue
            
            # Apply filters
            floor = premium_floors.get(otm, 0)
            sub = sub_unfiltered[
                (sub_unfiltered["entry_per_cr"] >= floor) &
                (sub_unfiltered["pre_range_pct"] <= range_ceiling)
            ].copy()
            n_filtered = len(sub)
            
            if n_filtered == 0:
                continue
            
            for s in strategies:
                col = f"pl_{s}_per_cr"
                if col not in sub.columns: continue
                vals = sub[col].dropna()
                if vals.empty: continue
                summary_rows.append({
                    "inst": inst, "otm_pct": otm, "strategy": s,
                    "n_total": n_total, "n_filtered": n_filtered,
                    "filter_pass_pct": round(n_filtered/n_total*100, 0),
                    "mean_rs_per_cr": round(vals.mean(), 0),
                    "median_rs_per_cr": round(vals.median(), 0),
                    "win_pct": round((vals > 0).mean()*100, 0),
                    "worst_rs_per_cr": round(vals.min(), 0),
                    "p10_rs_per_cr": round(vals.quantile(0.1), 0),
                    "p90_rs_per_cr": round(vals.quantile(0.9), 0),
                })
    
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT / "filtered_strategy_summary.csv", index=False)
    
    # Print per OTM, best strategies (worst day ≤ ₹3.5L/Cr loss = -350K)
    WORST_CAP = -350_000
    
    print(f"\nConstraints: worst day ≥ {WORST_CAP:,}/Cr, sorted by mean ₹/Cr/day\n")
    
    for inst in ["NIFTY", "SENSEX"]:
        for otm in OTM_TIERS:
            sub = summary[(summary["inst"] == inst) & (summary["otm_pct"] == otm) &
                          (summary["worst_rs_per_cr"] >= WORST_CAP)].copy()
            if sub.empty:
                # Show unfiltered if no strategy meets cap
                sub2 = summary[(summary["inst"] == inst) & (summary["otm_pct"] == otm)].copy()
                if sub2.empty: continue
                sub2 = sub2.sort_values("mean_rs_per_cr", ascending=False).head(3)
                print(f"\n{inst} @ {otm}% OTM (n_pass={sub2['n_filtered'].iloc[0]}/{sub2['n_total'].iloc[0]})  --  NO STRATEGY MEETS -350K CAP, showing top 3 by mean:")
                print(sub2[["strategy", "mean_rs_per_cr", "win_pct", "worst_rs_per_cr"]].to_string(index=False))
            else:
                sub = sub.sort_values("mean_rs_per_cr", ascending=False).head(3)
                print(f"\n{inst} @ {otm}% OTM (n_pass={sub['n_filtered'].iloc[0]}/{sub['n_total'].iloc[0]}, filter ≥₹{premium_floors.get(otm,0):,}/Cr & range ≤1%):")
                print(sub[["strategy", "mean_rs_per_cr", "win_pct", "worst_rs_per_cr"]].to_string(index=False))
    
    print(f"\n\nDone. Full results in {OUT}")


if __name__ == "__main__":
    main()
