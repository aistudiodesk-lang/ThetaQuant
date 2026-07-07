"""
ANALYSIS 020 — Tighter Yellow rules for SENSEX (<30% fire rate, ≥50% TPR), 
                + EV math (follow vs not-follow), + Action playbook

Question (Rohan):
  1. SENSEX rules <30% fire rate (NIFTY already good at 20-21%)
  2. Show math: EV of following vs not following the rule
  3. What's typical loss/profit at Yellow exit?
  4. Action menu: roll losing leg further OTM, roll winning leg closer, etc.

Add tighter signal candidates:
  S1_BUFFER_60: spot moves 60% of buffer (vs 50% earlier)
  S9_BIG_MOVE_50: 30-min net move ≥ 0.5% (vs 0.4%)
  S9_BIG_MOVE_60: 30-min net move ≥ 0.6%
  S10_DOUBLE_CONFIRM: spot AT buffer-50 AND remains there for 5+ min

Build EV table:
  For each Yellow rule, compute realistic exit cost using:
    - At Yellow-fire moment, look up actual CE/PE LTPs in chain
    - Combined exit cost = sum of LTPs of both legs (close both)
  Compare:
    - Hold-to-expiry P&L: entry_premium − ITM_penalty
    - Yellow-act P&L: entry_premium − exit_combined_at_fire
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

OUT = ROOT / "results" / "020_b1_yellow_tighter_ev"
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


def get_option_ltp(inst, d, exp, strike, opt, t):
    """Get option LTP at specific minute."""
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    r = con.execute(f"""
        SELECT close FROM read_parquet('{glob}', union_by_name=True)
        WHERE option_type='{opt}' AND strike={strike} AND expiry=DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIME) >= TIME '{t.strftime("%H:%M:%S")}'
        ORDER BY timestamp LIMIT 1
    """).fetchone()
    return float(r[0]) if r else None


def _first_breach(df, col, threshold, direction):
    if direction == "below":
        m = df[df[col] <= threshold]
    else:
        m = df[df[col] >= threshold]
    return None if m.empty else m.iloc[0]


def evaluate_signals_with_timing(day_fut, spot_entry, pe_strike, ce_strike):
    """Return signal fire times (not just bools)."""
    intraday = day_fut[day_fut["time"] >= ENTRY_TIME].copy().reset_index(drop=True)
    pre_entry = day_fut[day_fut["time"] < ENTRY_TIME].copy()
    if intraday.empty: return {}
    
    pe_buffer = spot_entry - pe_strike
    ce_buffer = ce_strike - spot_entry
    out = {}
    
    # S1: BUFFER variants
    for pct in [50, 60, 70, 85]:
        pe_t = _first_breach(intraday, "low", spot_entry - pct/100 * pe_buffer, "below")
        ce_t = _first_breach(intraday, "high", spot_entry + pct/100 * ce_buffer, "above")
        out[f"S1_BUFFER_{pct}"] = {
            "pe": pe_t["time"] if pe_t is not None else None,
            "ce": ce_t["time"] if ce_t is not None else None,
        }
    
    # S2: RANGE_BREAK (0.1%)
    if not pre_entry.empty:
        plo = pre_entry["low"].min(); phi = pre_entry["high"].max()
        pe_t = _first_breach(intraday, "low", plo * 0.999, "below")
        ce_t = _first_breach(intraday, "high", phi * 1.001, "above")
        out["S2_RANGE_BREAK"] = {
            "pe": pe_t["time"] if pe_t is not None else None,
            "ce": ce_t["time"] if ce_t is not None else None,
        }
    else: out["S2_RANGE_BREAK"] = {"pe": None, "ce": None}
    
    # S9 BIG_MOVE: variants 0.4%, 0.5%, 0.6%
    for thr_pct in [0.4, 0.5, 0.6]:
        pe_t = None; ce_t = None
        for i in range(30, len(intraday)):
            window = intraday.iloc[i-30:i+1]
            net_pct = (window["close"].iloc[-1] - window["close"].iloc[0]) / window["close"].iloc[0] * 100
            if net_pct <= -thr_pct and pe_t is None:
                pe_t = intraday.iloc[i]["time"]
            if net_pct >= thr_pct and ce_t is None:
                ce_t = intraday.iloc[i]["time"]
            if pe_t and ce_t: break
        out[f"S9_BIG_MOVE_{int(thr_pct*100)}"] = {"pe": pe_t, "ce": ce_t}
    
    return out


def combine_AND_time(t1, t2):
    """When both signals fire, AND fires at the LATER of the two."""
    if t1 is None or t2 is None:
        return None
    return max(t1, t2)


def main():
    instruments = {
        "NIFTY": (NIFTY_WEEKLY_EXPIRIES, 50),
        "SENSEX": (SENSEX_WEEKLY_EXPIRIES, 100),
    }
    
    all_per_day = {}
    
    for inst, (expiries, grid) in instruments.items():
        print(f"\n[{inst}] Loading FUT ...")
        fut = load_fut(inst)
        expiry_set = set(expiries)
        e0_days = sorted([d for d in fut["date"].unique() if d in expiry_set])
        print(f"  {len(e0_days)} E-0 days")
        
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
                pe_buf = spot_entry - pe_strike
                ce_buf = ce_strike - spot_entry
                pe_itm = spot_close < pe_strike
                ce_itm = spot_close > ce_strike
                pe_itm_pts = max(0, pe_strike - spot_close)
                ce_itm_pts = max(0, spot_close - ce_strike)
                sigs = evaluate_signals_with_timing(day_fut, spot_entry, pe_strike, ce_strike)
                
                row = {
                    "date": d, "otm": otm,
                    "spot_entry": spot_entry, "spot_close": spot_close,
                    "pe_strike": pe_strike, "ce_strike": ce_strike,
                    "pe_buf_pts": int(pe_buf), "ce_buf_pts": int(ce_buf),
                    "pe_itm": pe_itm, "pe_itm_pts": int(pe_itm_pts),
                    "ce_itm": ce_itm, "ce_itm_pts": int(ce_itm_pts),
                }
                for s, sides in sigs.items():
                    row[f"{s}_pe_t"] = sides["pe"]
                    row[f"{s}_ce_t"] = sides["ce"]
                rows.append(row)
        
        all_per_day[inst] = pd.DataFrame(rows)
    
    # Define candidate Yellow rules to test (pairwise AND of new + old)
    pe_rules = {
        # Original baseline (from analysis 019)
        "Y_PE_BUFFER50_BIG40": [("S1_BUFFER_50", "S9_BIG_MOVE_40")],
        # Tighter variants
        "Y_PE_BUFFER60_BIG40": [("S1_BUFFER_60", "S9_BIG_MOVE_40")],
        "Y_PE_BUFFER50_BIG50": [("S1_BUFFER_50", "S9_BIG_MOVE_50")],
        "Y_PE_BUFFER60_BIG50": [("S1_BUFFER_60", "S9_BIG_MOVE_50")],
        "Y_PE_BUFFER50_BIG60": [("S1_BUFFER_50", "S9_BIG_MOVE_60")],
        # Triple: all 3 conditions
        "Y_PE_RANGE_BUFFER50_BIG50": [("S2_RANGE_BREAK", "S1_BUFFER_50", "S9_BIG_MOVE_50")],
    }
    ce_rules = {
        "Y_CE_BUFFER50_BIG40": [("S1_BUFFER_50", "S9_BIG_MOVE_40")],
        "Y_CE_RANGE_BIG40": [("S2_RANGE_BREAK", "S9_BIG_MOVE_40")],
        "Y_CE_BUFFER60_BIG40": [("S1_BUFFER_60", "S9_BIG_MOVE_40")],
        "Y_CE_BUFFER50_BIG50": [("S1_BUFFER_50", "S9_BIG_MOVE_50")],
        "Y_CE_BUFFER60_BIG50": [("S1_BUFFER_60", "S9_BIG_MOVE_50")],
        "Y_CE_RANGE_BIG50": [("S2_RANGE_BREAK", "S9_BIG_MOVE_50")],
        "Y_CE_RANGE_BUFFER50_BIG50": [("S2_RANGE_BREAK", "S1_BUFFER_50", "S9_BIG_MOVE_50")],
    }
    
    print("\n\n" + "="*100)
    print("STAGE 1: TIGHTER YELLOW RULES — search for <30% fire rate + 50%+ TPR + 0% FN")
    print("="*100)
    
    rule_summary = []
    for inst, df in all_per_day.items():
        for otm in OTM_TIERS:
            sub = df[df["otm"] == otm].copy()
            n = len(sub)
            
            # Evaluate each rule
            for side, rules_dict in [("pe", pe_rules), ("ce", ce_rules)]:
                itm_col = f"{side}_itm"
                base = sub[itm_col].mean() * 100
                for rule_name, sig_combos in rules_dict.items():
                    sigs = sig_combos[0]   # tuple of signal names
                    # Combined fires when ALL signals fired (AND), using max of times
                    cols = [f"{s}_{side}_t" for s in sigs]
                    valid = sub.copy()
                    for c in cols:
                        if c not in valid.columns:
                            valid[c] = None
                    # Mark fired if all are non-null
                    valid["combo_fired"] = valid[cols].notna().all(axis=1)
                    fired = valid[valid["combo_fired"]]
                    nofire = valid[~valid["combo_fired"]]
                    n_f = len(fired)
                    if n_f == 0: continue
                    p_itm_f = fired[itm_col].mean() * 100
                    p_itm_nf = nofire[itm_col].mean() * 100 if len(nofire) > 0 else None
                    rule_summary.append({
                        "instrument": inst, "side": side.upper(), "otm": otm,
                        "rule": rule_name, "conditions": " AND ".join(sigs),
                        "n_total": n, "fire_pct": round(n_f/n*100, 1),
                        "p_itm_baseline": round(base, 1),
                        "p_itm_when_fired": round(p_itm_f, 1),
                        "p_itm_when_not_fired": round(p_itm_nf, 1) if p_itm_nf is not None else None,
                    })
    
    rs = pd.DataFrame(rule_summary)
    rs.to_csv(OUT / "rule_search.csv", index=False)
    
    # Show top per (inst × side × otm) for "fire <30%, TPR >= 50%, FN <= 5%"
    print("\nFinal recommended rules (target: <30% fire, ≥50% P(ITM|fired), ≤5% P(ITM|not fired)):\n")
    for inst in ["NIFTY", "SENSEX"]:
        for side in ["PE", "CE"]:
            for otm in [0.5, 0.7, 1.0]:
                sub = rs[(rs["instrument"]==inst) & (rs["side"]==side) & (rs["otm"]==otm) &
                         (rs["fire_pct"] < 30) & (rs["p_itm_when_fired"] >= 50) &
                         (rs["p_itm_when_not_fired"] <= 5)].copy()
                if sub.empty: continue
                sub = sub.sort_values("p_itm_when_fired", ascending=False)
                base = sub["p_itm_baseline"].iloc[0]
                print(f"{inst} {side} @ {otm}% OTM  (baseline {base}%):")
                print(sub[["rule","fire_pct","p_itm_when_fired","p_itm_when_not_fired"]].head(3).to_string(index=False))
                print()
    
    # Also show "near hits" for SENSEX (under 30%, may not hit 50% TPR)
    print("\nSENSEX — fire <30% rules even if TPR <50% (best near-misses):\n")
    for side in ["PE", "CE"]:
        for otm in [0.5, 0.7, 1.0]:
            sub = rs[(rs["instrument"]=="SENSEX") & (rs["side"]==side) & (rs["otm"]==otm) &
                     (rs["fire_pct"] < 30) & (rs["p_itm_when_not_fired"] == 0)].copy()
            if sub.empty: continue
            sub = sub.sort_values("p_itm_when_fired", ascending=False)
            base = sub["p_itm_baseline"].iloc[0]
            print(f"SENSEX {side} @ {otm}% OTM  (baseline {base}%, FN=0 only):")
            print(sub[["rule","fire_pct","p_itm_when_fired"]].head(5).to_string(index=False))
            print()
    
    # Save per-day for EV computation
    for inst, df in all_per_day.items():
        df.to_csv(OUT / f"{inst}_per_day.csv", index=False)
    
    print(f"\nDone. Files in {OUT}")


if __name__ == "__main__":
    main()
