"""
ANALYSIS 019 — Search for two-condition Yellow rules with ≥50% precision + 0% FN

Question (Rohan, 2026-06-04 evening):
  "Want even better yellow. Can be 2 conditions (AND). Ideally if hits then ≥50%
   end ITM, if neither met then 100% OTM. For both NIFTY and SENSEX, PE and CE."

Setup:
  - Entry window: 10:00-11:00 IST (Rohan's typical B1 timing)
  - Use 10:30 entry as the anchor (mid-window)
  - Test 7 base signals + all pairwise AND combinations
  - For each: fire-rate, P(ITM|fired), P(ITM|not fired), conditional lift
  - Find: rules with P(ITM|fired) ≥ 50% AND P(ITM|not fired) = 0%
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path
import sys, os
from itertools import combinations

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "019_b1_yellow_two_condition"
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()
ENTRY_TIME = time(10, 30)
CLOSE_TIME = time(15, 25)
OTM_TIERS = [0.5, 0.7, 1.0]


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


def _first_breach(df, col, threshold, direction):
    if direction == "below":
        m = df[df[col] <= threshold]
    else:
        m = df[df[col] >= threshold]
    return None if m.empty else m.iloc[0]["time"]


def evaluate_signals(day_fut, spot_entry, pe_strike, ce_strike, grid):
    intraday = day_fut[day_fut["time"] >= ENTRY_TIME].copy().reset_index(drop=True)
    pre_entry = day_fut[day_fut["time"] < ENTRY_TIME].copy()
    if intraday.empty: return {}
    
    pe_buffer = spot_entry - pe_strike
    ce_buffer = ce_strike - spot_entry
    out = {}
    
    # S1: BUFFER_50
    out["S1_BUFFER_50"] = {
        "pe": _first_breach(intraday, "low", spot_entry - 0.50 * pe_buffer, "below"),
        "ce": _first_breach(intraday, "high", spot_entry + 0.50 * ce_buffer, "above"),
    }
    # S2: RANGE_BREAK (0.1%)
    if not pre_entry.empty:
        plo = pre_entry["low"].min(); phi = pre_entry["high"].max()
        out["S2_RANGE_BREAK"] = {
            "pe": _first_breach(intraday, "low", plo * 0.999, "below"),
            "ce": _first_breach(intraday, "high", phi * 1.001, "above"),
        }
    else: out["S2_RANGE_BREAK"] = {"pe": None, "ce": None}
    
    # S6: VOL_EXPANSION — 15-min realized vol > 2x first-30-min realized vol
    if len(intraday) >= 30:
        first30 = intraday.iloc[:30]
        first30_ret = np.log(first30["close"] / first30["close"].shift(1)).dropna()
        baseline_vol = first30_ret.std() if len(first30_ret) > 5 else 0
        out["S6_VOL_EXPANSION"] = {"pe": None, "ce": None}
        for i in range(30, len(intraday) - 15):
            window = intraday.iloc[i:i+15]
            wret = np.log(window["close"] / window["close"].shift(1)).dropna()
            if len(wret) < 5: continue
            cur_vol = wret.std()
            move = window["close"].iloc[-1] - window["close"].iloc[0]
            if baseline_vol > 0 and cur_vol > 2.0 * baseline_vol:
                if move < 0 and out["S6_VOL_EXPANSION"]["pe"] is None:
                    out["S6_VOL_EXPANSION"]["pe"] = intraday.iloc[i+15]["time"]
                if move > 0 and out["S6_VOL_EXPANSION"]["ce"] is None:
                    out["S6_VOL_EXPANSION"]["ce"] = intraday.iloc[i+15]["time"]
            if out["S6_VOL_EXPANSION"]["pe"] and out["S6_VOL_EXPANSION"]["ce"]: break
    else: out["S6_VOL_EXPANSION"] = {"pe": None, "ce": None}
    
    # S7: VWAP_BREAK — sustained (5min) cross below VWAP for PE
    intraday["typical"] = (intraday["high"] + intraday["low"] + intraday["close"]) / 3
    # No volume in FUT parquet generally, use simple cumulative mean as proxy
    intraday["vwap"] = intraday["typical"].expanding().mean()
    out["S7_VWAP_BREAK"] = {"pe": None, "ce": None}
    below = 0; above = 0
    for i in range(5, len(intraday)):
        if intraday["close"].iloc[i] < intraday["vwap"].iloc[i]:
            below += 1; above = 0
        elif intraday["close"].iloc[i] > intraday["vwap"].iloc[i]:
            above += 1; below = 0
        if below >= 5 and out["S7_VWAP_BREAK"]["pe"] is None:
            out["S7_VWAP_BREAK"]["pe"] = intraday.iloc[i]["time"]
        if above >= 5 and out["S7_VWAP_BREAK"]["ce"] is None:
            out["S7_VWAP_BREAK"]["ce"] = intraday.iloc[i]["time"]
        if out["S7_VWAP_BREAK"]["pe"] and out["S7_VWAP_BREAK"]["ce"]: break
    
    # S8: RANGE_EXPANSION — current 30-min range > 1.5x pre-entry 30-min range
    pre30_range = pre_entry["high"].max() - pre_entry["low"].min() if not pre_entry.empty else 0
    out["S8_RANGE_EXPANSION"] = {"pe": None, "ce": None}
    if pre30_range > 0:
        for i in range(30, len(intraday)):
            window = intraday.iloc[i-30:i+1]
            cur_range = window["high"].max() - window["low"].min()
            net = window["close"].iloc[-1] - window["close"].iloc[0]
            if cur_range > 1.5 * pre30_range:
                if net < 0 and out["S8_RANGE_EXPANSION"]["pe"] is None:
                    out["S8_RANGE_EXPANSION"]["pe"] = intraday.iloc[i]["time"]
                if net > 0 and out["S8_RANGE_EXPANSION"]["ce"] is None:
                    out["S8_RANGE_EXPANSION"]["ce"] = intraday.iloc[i]["time"]
            if out["S8_RANGE_EXPANSION"]["pe"] and out["S8_RANGE_EXPANSION"]["ce"]: break
    
    # S9: BIG_MOVE_30MIN — 30-min net move > 0.4%
    out["S9_BIG_MOVE_30"] = {"pe": None, "ce": None}
    for i in range(30, len(intraday)):
        window = intraday.iloc[i-30:i+1]
        net_pct = (window["close"].iloc[-1] - window["close"].iloc[0]) / window["close"].iloc[0] * 100
        if net_pct <= -0.4 and out["S9_BIG_MOVE_30"]["pe"] is None:
            out["S9_BIG_MOVE_30"]["pe"] = intraday.iloc[i]["time"]
        if net_pct >= 0.4 and out["S9_BIG_MOVE_30"]["ce"] is None:
            out["S9_BIG_MOVE_30"]["ce"] = intraday.iloc[i]["time"]
        if out["S9_BIG_MOVE_30"]["pe"] and out["S9_BIG_MOVE_30"]["ce"]: break
    
    return out


def main():
    instruments = {
        "NIFTY": (NIFTY_WEEKLY_EXPIRIES, 50),
        "SENSEX": (SENSEX_WEEKLY_EXPIRIES, 100),
    }
    
    all_results = {}
    
    for inst, (expiries, grid) in instruments.items():
        print(f"\n[{inst}] Loading FUT data ...")
        fut = load_fut(inst)
        expiry_set = set(expiries)
        e0_days = sorted([d for d in fut["date"].unique() if d in expiry_set])
        print(f"  E-0 days: {len(e0_days)}")
        
        rows = []
        for d in e0_days:
            day_fut = fut[fut["date"] == d].copy()
            if day_fut.empty: continue
            ent = day_fut[day_fut["time"] >= ENTRY_TIME]
            if ent.empty: continue
            spot_entry = float(ent.iloc[0]["close"])
            cls = day_fut[day_fut["time"] <= CLOSE_TIME]
            if cls.empty: continue
            spot_close = float(cls.iloc[-1]["close"])
            
            for otm in OTM_TIERS:
                pe_strike = int(round(spot_entry * (1 - otm/100) / grid) * grid)
                ce_strike = int(round(spot_entry * (1 + otm/100) / grid) * grid)
                sigs = evaluate_signals(day_fut, spot_entry, pe_strike, ce_strike, grid)
                row = {
                    "date": d, "otm": otm,
                    "pe_itm": spot_close < pe_strike,
                    "ce_itm": spot_close > ce_strike,
                }
                for s, sides in sigs.items():
                    row[f"{s}_pe"] = sides["pe"] is not None
                    row[f"{s}_ce"] = sides["ce"] is not None
                rows.append(row)
        
        df = pd.DataFrame(rows)
        df.to_csv(OUT / f"{inst}_per_day.csv", index=False)
        all_results[inst] = df
    
    # Now search for two-condition AND rules
    signals = ["S1_BUFFER_50", "S2_RANGE_BREAK", "S6_VOL_EXPANSION", "S7_VWAP_BREAK", 
               "S8_RANGE_EXPANSION", "S9_BIG_MOVE_30"]
    
    summary_rows = []
    for inst, df in all_results.items():
        for otm in OTM_TIERS:
            sub = df[df["otm"] == otm].copy()
            n = len(sub)
            if n == 0: continue
            
            for side in ["pe", "ce"]:
                itm_col = f"{side}_itm"
                base = sub[itm_col].mean() * 100
                
                # Individual signals
                for s in signals:
                    col = f"{s}_{side}"
                    if col not in sub.columns: continue
                    fired = sub[sub[col]]
                    nofire = sub[~sub[col]]
                    n_f = len(fired)
                    if n_f == 0: continue
                    p_itm_f = fired[itm_col].mean() * 100
                    p_itm_nf = nofire[itm_col].mean() * 100 if len(nofire) > 0 else None
                    summary_rows.append({
                        "instrument": inst, "side": side.upper(), "otm": otm,
                        "rule": s, "type": "individual",
                        "n_total": n, "fire_rate_pct": round(n_f/n*100, 1),
                        "p_itm_baseline_pct": round(base, 1),
                        "p_itm_when_fired_pct": round(p_itm_f, 1),
                        "p_itm_when_not_fired_pct": round(p_itm_nf, 1) if p_itm_nf is not None else None,
                    })
                
                # Pairwise AND
                for s1, s2 in combinations(signals, 2):
                    c1, c2 = f"{s1}_{side}", f"{s2}_{side}"
                    if c1 not in sub.columns or c2 not in sub.columns: continue
                    combined = sub[c1] & sub[c2]
                    fired = sub[combined]
                    nofire = sub[~combined]
                    n_f = len(fired)
                    if n_f == 0: continue
                    p_itm_f = fired[itm_col].mean() * 100
                    p_itm_nf = nofire[itm_col].mean() * 100 if len(nofire) > 0 else None
                    summary_rows.append({
                        "instrument": inst, "side": side.upper(), "otm": otm,
                        "rule": f"{s1} AND {s2}", "type": "AND",
                        "n_total": n, "fire_rate_pct": round(n_f/n*100, 1),
                        "p_itm_baseline_pct": round(base, 1),
                        "p_itm_when_fired_pct": round(p_itm_f, 1),
                        "p_itm_when_not_fired_pct": round(p_itm_nf, 1) if p_itm_nf is not None else None,
                    })
                
                # Pairwise OR (for completeness)
                for s1, s2 in combinations(signals, 2):
                    c1, c2 = f"{s1}_{side}", f"{s2}_{side}"
                    if c1 not in sub.columns or c2 not in sub.columns: continue
                    combined = sub[c1] | sub[c2]
                    fired = sub[combined]
                    nofire = sub[~combined]
                    n_f = len(fired)
                    if n_f == 0: continue
                    p_itm_f = fired[itm_col].mean() * 100
                    p_itm_nf = nofire[itm_col].mean() * 100 if len(nofire) > 0 else None
                    summary_rows.append({
                        "instrument": inst, "side": side.upper(), "otm": otm,
                        "rule": f"{s1} OR {s2}", "type": "OR",
                        "n_total": n, "fire_rate_pct": round(n_f/n*100, 1),
                        "p_itm_baseline_pct": round(base, 1),
                        "p_itm_when_fired_pct": round(p_itm_f, 1),
                        "p_itm_when_not_fired_pct": round(p_itm_nf, 1) if p_itm_nf is not None else None,
                    })
    
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT / "all_rules_summary.csv", index=False)
    
    # Find Pareto-optimal: P(ITM|fired) >= 50% AND P(ITM|not_fired) = 0% AND fire_rate >= 15%
    winners = summary[
        (summary["p_itm_when_fired_pct"] >= 50) &
        (summary["p_itm_when_not_fired_pct"] == 0) &
        (summary["fire_rate_pct"] >= 15)
    ].copy()
    winners = winners.sort_values(["instrument", "side", "otm", "p_itm_when_fired_pct"], ascending=[True, True, True, False])
    winners.to_csv(OUT / "winners.csv", index=False)
    
    print(f"\n{'='*100}")
    print(f"WINNERS — rules satisfying P(ITM|fired) ≥ 50%, P(ITM|not_fired) = 0%, fire_rate ≥ 15%")
    print(f"{'='*100}\n")
    
    if winners.empty:
        print("No rule meets all 3 thresholds. Showing best near-winners (P(ITM|not_fired) ≤ 5%, fire_rate ≥ 15%):")
        near = summary[
            (summary["p_itm_when_not_fired_pct"] <= 5) &
            (summary["fire_rate_pct"] >= 15)
        ].copy()
        near = near.sort_values("p_itm_when_fired_pct", ascending=False)
        for (inst, side, otm), grp in near.groupby(["instrument", "side", "otm"]):
            print(f"\n{inst} {side} @ {otm}% OTM (baseline P(ITM)={grp['p_itm_baseline_pct'].iloc[0]}%):")
            print(grp[["rule","fire_rate_pct","p_itm_when_fired_pct","p_itm_when_not_fired_pct"]].head(5).to_string(index=False))
    else:
        for (inst, side, otm), grp in winners.groupby(["instrument", "side", "otm"]):
            print(f"\n{inst} {side} @ {otm}% OTM (baseline P(ITM)={grp['p_itm_baseline_pct'].iloc[0]}%):")
            print(grp[["rule","fire_rate_pct","p_itm_when_fired_pct","p_itm_when_not_fired_pct"]].to_string(index=False))
    
    print(f"\nDone. Full results: {OUT}")


if __name__ == "__main__":
    main()
