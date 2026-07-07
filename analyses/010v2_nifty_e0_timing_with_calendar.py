"""
ANALYSIS 010v2 — NIFTY E-0 Timing Calibration (with hardcoded calendar)

Same as 010 but uses lib.expiry_calendar.is_e0() instead of weekday heuristic.
This includes the 10 holiday-shifted / transition E-0 days that 010 missed.

Output: results/010v2_nifty_e0_timing_with_calendar/
"""
from __future__ import annotations
from datetime import date, time, timedelta
from pathlib import Path
import sys

import duckdb, numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, is_special, is_in_thursday_era

STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT = ROOT / "results" / "010v2_nifty_e0_timing_with_calendar"
OUT.mkdir(parents=True, exist_ok=True)

GRID = 50
EXIT_AT = time(15, 25)
con = duckdb.connect()


def load_fut():
    p = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
      SELECT timestamp, open, high, low, close
      FROM read_parquet('{p}', union_by_name=True)
      WHERE option_type='FUT'
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    return df.sort_values("timestamp").reset_index(drop=True)


def load_legs(d, exp, ce_s, pe_s):
    p = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
      SELECT timestamp, strike, option_type, close
      FROM read_parquet('{p}', union_by_name=True)
      WHERE option_type IN ('CE','PE') AND expiry = DATE '{exp.isoformat()}'
        AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
        AND strike IN ({ce_s},{pe_s})
    """).fetchdf()
    if df.empty: return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["t"] = df["timestamp"].dt.strftime("%H:%M")
    ce = df[(df.option_type=="CE") & (df.strike==ce_s)].sort_values("timestamp")
    pe = df[(df.option_type=="PE") & (df.strike==pe_s)].sort_values("timestamp")
    if ce.empty or pe.empty: return None
    m = pd.merge(ce[["timestamp","t","close"]].rename(columns={"close":"ce"}),
                 pe[["timestamp","t","close"]].rename(columns={"close":"pe"}),
                 on=["timestamp","t"], how="inner")
    m["combined"] = m["ce"] + m["pe"]
    return m


def pick(spot, dp):
    return (int(round(spot*(1+dp/100)/GRID)*GRID), int(round(spot*(1-dp/100)/GRID)*GRID))


def main():
    print("=== 010v2 — NIFTY E-0 (calendar-based) ===\n")
    fut = load_fut()
    days = sorted(fut["date"].unique())
    sample_min, sample_max = min(days), max(days)
    e0_days = [d for d in NIFTY_WEEKLY_EXPIRIES if sample_min <= d <= sample_max and d in days]
    print(f"E-0 days in calendar within sample: {len(e0_days)}")

    fut_by_date = {d: fut[fut["date"]==d].sort_values("timestamp").reset_index(drop=True)
                   for d in days}
    last_close = {d: float(fut_by_date[d].iloc[-1]["close"]) for d in fut_by_date if len(fut_by_date[d]) > 0}

    rows = []
    for d in e0_days:
        idx = days.index(d)
        if idx == 0: continue
        prev_d = days[idx-1]
        prev_close = last_close.get(prev_d)
        fday = fut_by_date.get(d)
        if fday is None or fday.empty: continue
        first = fday.iloc[0]
        open_price = float(first["open"])
        gap_pct = (open_price - prev_close) / prev_close * 100 if prev_close else np.nan

        cutoff = first["timestamp"] + timedelta(minutes=15)
        f15 = fday[(fday["timestamp"] >= first["timestamp"]) & (fday["timestamp"] <= cutoff)]
        f15_range = (f15["high"].max() - f15["low"].min()) / open_price * 100 if not f15.empty else np.nan
        f15_drift = (f15["close"].iloc[-1] - f15["open"].iloc[0]) / open_price * 100 if not f15.empty else np.nan

        spot_at_930 = fday[fday["time"] >= time(9,30)]["close"].iloc[0] if not fday[fday["time"] >= time(9,30)].empty else open_price
        ce_s, pe_s = pick(spot_at_930, 2.5)
        m = load_legs(d, d, ce_s, pe_s)
        if m is None or m.empty:
            rows.append({"date": d, "status": "no_options", "dow": pd.Timestamp(d).day_name()})
            continue
        m = m[m["timestamp"].dt.time <= EXIT_AT].copy()

        def at_time(target):
            sub = m[m["timestamp"].dt.time >= target]
            if sub.empty: return np.nan
            return float(sub.iloc[0]["combined"])

        prem_915 = at_time(time(9,15)); prem_920 = at_time(time(9,20))
        prem_930 = at_time(time(9,30)); prem_945 = at_time(time(9,45))
        prem_1000 = at_time(time(10,0)); prem_1030 = at_time(time(10,30))
        prem_1100 = at_time(time(11,0)); prem_1130 = at_time(time(11,30))
        prem_1200 = at_time(time(12,0))

        morn = m[m["timestamp"].dt.time <= time(12,0)].copy()
        peak_combined = float(morn["combined"].max()) if not morn.empty else np.nan
        peak_time = morn.loc[morn["combined"].idxmax(), "t"] if not morn.empty else ""

        if pd.isna(peak_combined) or pd.isna(prem_930) or prem_930 <= 0:
            regime = "unknown"
        else:
            ratio = peak_combined/prem_930
            if peak_time >= "10:00" and ratio > 1.10:
                regime = "vega"
            elif ratio < 1.05:
                regime = "calm"
            else:
                regime = "borderline"

        rows.append({
            "date": d, "status": "ok",
            "dow": pd.Timestamp(d).day_name(),
            "thursday_era": is_in_thursday_era(d),
            "special_note": is_special(d, "NIFTY") or "",
            "open": round(open_price, 2),
            "gap_pct": round(gap_pct, 2),
            "f15_range_pct": round(f15_range, 3),
            "f15_drift_pct": round(f15_drift, 3),
            "prem_915": round(prem_915,2), "prem_920": round(prem_920,2),
            "prem_930": round(prem_930,2), "prem_945": round(prem_945,2),
            "prem_1000": round(prem_1000,2), "prem_1030": round(prem_1030,2),
            "prem_1100": round(prem_1100,2), "prem_1130": round(prem_1130,2),
            "prem_1200": round(prem_1200,2),
            "peak_combined": round(peak_combined,2) if peak_combined==peak_combined else np.nan,
            "peak_time": peak_time,
            "peak_vs_930_ratio": round(peak_combined/prem_930,3) if prem_930 and prem_930>0 else np.nan,
            "regime": regime,
        })
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "per_day.csv", index=False)
    ok = df[df["status"]=="ok"]
    print(f"Days analyzed: {len(ok)} / {len(e0_days)} (excluded: data gaps)")
    print(f"\nRegime distribution:")
    print(ok["regime"].value_counts().to_string())

    # Re-run signal tests
    print(f"\n=== Refined signal tests (with calendar-based sample) ===\n")
    for sig_name, threshold in [
        ("f15_range > 1.0%", lambda r: r["f15_range_pct"]>1.0 if pd.notna(r["f15_range_pct"]) else False),
        ("9:30 prem ≤ 4", lambda r: r["prem_930"]<=4 if pd.notna(r["prem_930"]) else False),
        ("9:30 prem ≤ 3", lambda r: r["prem_930"]<=3 if pd.notna(r["prem_930"]) else False),
        ("Thursday era day", lambda r: bool(r["thursday_era"])),
        ("Special day (shifted)", lambda r: bool(r["special_note"])),
        ("|gap_pct| > 0.5%", lambda r: abs(r["gap_pct"])>0.5 if pd.notna(r["gap_pct"]) else False),
        ("gap_pct > +0.3% (positive gap)", lambda r: r["gap_pct"]>0.3 if pd.notna(r["gap_pct"]) else False),
    ]:
        ok2 = ok.copy()
        ok2["sig"] = ok2.apply(threshold, axis=1)
        pos = ok2[ok2["sig"]]
        neg = ok2[~ok2["sig"]]
        if len(pos)>=1 and len(neg)>=1:
            vp = (pos["regime"]=="vega").mean()*100
            vn = (neg["regime"]=="vega").mean()*100
            print(f"{sig_name:35s} TRUE n={len(pos):2d} (vega {vp:.0f}%) | FALSE n={len(neg):2d} (vega {vn:.0f}%) | discrim {vp-vn:+.1f}")

    # COMBO signal tests (the actual rule we'll use)
    print(f"\n=== Compound signal tests ===\n")
    combo1 = (ok["f15_range_pct"]>1.0) & (ok["prem_930"]<=4)
    combo2 = (ok["f15_range_pct"]>1.0) & (ok["prem_930"]<=4) & (ok["thursday_era"])
    combo3 = (ok["f15_range_pct"]>1.0) & (ok["prem_930"]<=4) & ((ok["gap_pct"]>0.3) | (ok["thursday_era"]))
    for name, sig in [("range>1% AND 9:30≤4", combo1),
                       ("range>1% AND 9:30≤4 AND Thu-era", combo2),
                       ("range>1% AND 9:30≤4 AND (gap>0.3% OR Thu-era)", combo3)]:
        pos = ok[sig]; neg = ok[~sig]
        if len(pos)>=1 and len(neg)>=1:
            vp = (pos["regime"]=="vega").mean()*100
            vn = (neg["regime"]=="vega").mean()*100
            recall = ((pos["regime"]=="vega").sum()) / (ok["regime"]=="vega").sum() * 100 if (ok["regime"]=="vega").any() else 0
            precision = (pos["regime"]=="vega").mean() * 100
            print(f"{name:50s} TRUE n={len(pos):2d} | recall {recall:.0f}% | precision {precision:.0f}% | discrim {vp-vn:+.1f}")

    # Era-stratified analysis
    print(f"\n=== Stratified by era (Thu vs Tue) ===\n")
    for era_name, era_mask in [("Thu-era (pre-Sep 2025)", ok["thursday_era"]),
                                ("Tue-era (post-Sep 2025)", ~ok["thursday_era"])]:
        sub = ok[era_mask]
        if sub.empty: continue
        n_vega = (sub["regime"]=="vega").sum()
        print(f"{era_name}: n={len(sub)}, vega={n_vega} ({n_vega/len(sub)*100:.0f}%), "
              f"median peak ratio={sub['peak_vs_930_ratio'].median():.2f}")

    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    main()
