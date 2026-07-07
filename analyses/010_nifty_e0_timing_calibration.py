"""
ANALYSIS 010 — NIFTY E-0 Timing Calibration

For every NIFTY weekly E-0 day in the parquet store (now 48 days incl 28-Apr-2026):
  Compute the minute-by-minute combined CE+PE premium at 2.5% OTM.
  Find the peak premium time within the day.
  Classify each day as 'calm regime' (peak at open) or 'vega regime' (peak later).
  Then find the signals that predict regime + the optimal entry minute per regime.

This calibrates the timing rule the user proposed:
  - Calm days: enter 9:25-9:35 (theta decay starts immediately)
  - Vega days: wait for 10:30-11:30 peak (IV expansion adds premium)

Outputs:
  results/010_nifty_e0_timing_calibration/
    summary.md
    per_day.csv          (each E-0 day: regime classification, signals, peak time)
    regime_signal_table.csv
    optimal_entry_by_regime.png
"""
from __future__ import annotations
from datetime import date, time, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT = ROOT / "results" / "010_nifty_e0_timing_calibration"
OUT.mkdir(parents=True, exist_ok=True)

LOT = 65
GRID = 50
EXIT_AT = time(15, 25)
WEEKLY_EXP_WD = {1, 3}      # Tuesday, Thursday

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


def expiry_on(d):
    p = str(STORE / "**" / "*.parquet")
    r = con.execute(f"""
      SELECT MIN(expiry) FROM read_parquet('{p}', union_by_name=True)
      WHERE option_type IN ('CE','PE') AND expiry = DATE '{d.isoformat()}'
        AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
    """).fetchone()
    return r[0] if r and r[0] else None


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
    m = pd.merge(
        ce[["timestamp","t","close"]].rename(columns={"close":"ce"}),
        pe[["timestamp","t","close"]].rename(columns={"close":"pe"}),
        on=["timestamp","t"], how="inner")
    m["combined"] = m["ce"] + m["pe"]
    return m


def pick(spot, dp):
    return (int(round(spot*(1+dp/100)/GRID)*GRID),
            int(round(spot*(1-dp/100)/GRID)*GRID))


def main():
    print("\n=== 010 — NIFTY E-0 timing calibration ===\n")
    fut = load_fut()
    days = sorted(fut["date"].unique())
    e0_days = []
    for d in days:
        if pd.Timestamp(d).weekday() not in WEEKLY_EXP_WD: continue
        if expiry_on(d) != d: continue
        e0_days.append(d)
    print(f"E-0 days: {len(e0_days)}")

    # Pre-compute per-day futures features
    fut_by_date = {d: fut[fut["date"]==d].sort_values("timestamp").reset_index(drop=True)
                   for d in days}
    last_close = {d: float(fut_by_date[d].iloc[-1]["close"]) for d in fut_by_date if len(fut_by_date[d]) > 0}

    rows = []
    for i, d in enumerate(e0_days):
        # prev trading day
        idx = days.index(d)
        if idx == 0: continue
        prev_d = days[idx-1]
        prev_close = last_close.get(prev_d)
        fday = fut_by_date.get(d)
        if fday is None or fday.empty: continue

        first = fday.iloc[0]
        open_price = float(first["open"])
        gap_pct = (open_price - prev_close) / prev_close * 100 if prev_close else np.nan

        # First-15-min range = high-low / open
        cutoff = first["timestamp"] + timedelta(minutes=15)
        f15 = fday[(fday["timestamp"] >= first["timestamp"]) & (fday["timestamp"] <= cutoff)]
        f15_range = (f15["high"].max() - f15["low"].min()) / open_price * 100 if not f15.empty else np.nan
        f15_drift = (f15["close"].iloc[-1] - f15["open"].iloc[0]) / open_price * 100 if not f15.empty else np.nan

        # First-30-min range
        cutoff30 = first["timestamp"] + timedelta(minutes=30)
        f30 = fday[(fday["timestamp"] >= first["timestamp"]) & (fday["timestamp"] <= cutoff30)]
        f30_range = (f30["high"].max() - f30["low"].min()) / open_price * 100 if not f30.empty else np.nan
        f30_drift = (f30["close"].iloc[-1] - f30["open"].iloc[0]) / open_price * 100 if not f30.empty else np.nan

        # Pick 2.5% strikes based on 9:30-area spot
        spot_at_930 = fday[fday["time"] >= time(9,30)]["close"].iloc[0] if not fday[fday["time"] >= time(9,30)].empty else open_price
        ce_s, pe_s = pick(spot_at_930, 2.5)
        m = load_legs(d, d, ce_s, pe_s)
        if m is None or m.empty: continue

        # Time series of combined
        # Sample at 1-min, find peak time, premium at key marks
        m = m[m["timestamp"].dt.time <= EXIT_AT].copy()

        def at_time(target):
            sub = m[m["timestamp"].dt.time >= target]
            if sub.empty: return np.nan
            return float(sub.iloc[0]["combined"])

        prem_915 = at_time(time(9,15))
        prem_920 = at_time(time(9,20))
        prem_930 = at_time(time(9,30))
        prem_945 = at_time(time(9,45))
        prem_1000 = at_time(time(10,0))
        prem_1030 = at_time(time(10,30))
        prem_1100 = at_time(time(11,0))
        prem_1130 = at_time(time(11,30))
        prem_1200 = at_time(time(12,0))

        # Peak (first 3 hours)
        morn = m[m["timestamp"].dt.time <= time(12,0)].copy()
        if morn.empty:
            peak_combined = np.nan; peak_time = ""
        else:
            peak_idx = morn["combined"].idxmax()
            peak_combined = float(morn.loc[peak_idx, "combined"])
            peak_time = morn.loc[peak_idx, "t"]

        # Regime classification: peak after 10:00 AND >10% above 9:30 = vega regime
        if pd.isna(peak_combined) or pd.isna(prem_930) or prem_930 <= 0:
            regime = "unknown"
        else:
            peak_ratio = peak_combined / prem_930
            peak_after_10 = bool(peak_time and peak_time >= "10:00")
            if peak_after_10 and peak_ratio > 1.10:
                regime = "vega"
            elif peak_ratio < 1.05:
                regime = "calm"
            else:
                regime = "borderline"

        rows.append({
            "date": d,
            "dow": pd.Timestamp(d).day_name(),
            "open": round(open_price, 2),
            "prev_close": round(prev_close, 2) if prev_close else None,
            "gap_pct": round(gap_pct, 2),
            "f15_range_pct": round(f15_range, 3),
            "f15_drift_pct": round(f15_drift, 3),
            "f30_range_pct": round(f30_range, 3),
            "f30_drift_pct": round(f30_drift, 3),
            "spot_930": round(spot_at_930, 2),
            "ce_strike": ce_s, "pe_strike": pe_s,
            "prem_915": round(prem_915, 2),
            "prem_920": round(prem_920, 2),
            "prem_930": round(prem_930, 2),
            "prem_945": round(prem_945, 2),
            "prem_1000": round(prem_1000, 2),
            "prem_1030": round(prem_1030, 2),
            "prem_1100": round(prem_1100, 2),
            "prem_1130": round(prem_1130, 2),
            "prem_1200": round(prem_1200, 2),
            "peak_combined": round(peak_combined, 2) if peak_combined==peak_combined else np.nan,
            "peak_time": peak_time,
            "peak_vs_930_ratio": round(peak_combined/prem_930, 3) if prem_930 and prem_930>0 else np.nan,
            "regime": regime,
        })

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "per_day.csv", index=False)
    print(f"\n[per-day] {len(df)} E-0 days analyzed")

    # ── Regime distribution ──
    regime_counts = df["regime"].value_counts()
    print(f"\nRegime distribution:")
    print(regime_counts.to_string())

    # ── Signals that predict vega regime ──
    print("\n=== Signal analysis: predicting vega regime ===")
    sig_table = []
    for sig_name, threshold in [
        ("|gap_pct| > 0.4", lambda r: abs(r["gap_pct"])>0.4 if pd.notna(r["gap_pct"]) else False),
        ("|gap_pct| > 0.7", lambda r: abs(r["gap_pct"])>0.7 if pd.notna(r["gap_pct"]) else False),
        ("f15_range > 0.4%", lambda r: r["f15_range_pct"]>0.4 if pd.notna(r["f15_range_pct"]) else False),
        ("f15_range > 0.6%", lambda r: r["f15_range_pct"]>0.6 if pd.notna(r["f15_range_pct"]) else False),
        ("f30_range > 0.6%", lambda r: r["f30_range_pct"]>0.6 if pd.notna(r["f30_range_pct"]) else False),
        ("|f15_drift| > 0.3%", lambda r: abs(r["f15_drift_pct"])>0.3 if pd.notna(r["f15_drift_pct"]) else False),
        ("|f30_drift| > 0.5%", lambda r: abs(r["f30_drift_pct"])>0.5 if pd.notna(r["f30_drift_pct"]) else False),
    ]:
        df["sig"] = df.apply(threshold, axis=1)
        # Confusion: when sig=True, what % were vega?
        pos = df[df["sig"]]
        neg = df[~df["sig"]]
        if len(pos) >= 1 and len(neg) >= 1:
            vega_in_pos = (pos["regime"]=="vega").mean()*100
            vega_in_neg = (neg["regime"]=="vega").mean()*100
            sig_table.append({
                "signal": sig_name,
                "n_signal_true": len(pos),
                "n_signal_false": len(neg),
                "vega_rate_signal_true": round(vega_in_pos,1),
                "vega_rate_signal_false": round(vega_in_neg,1),
                "discrimination": round(vega_in_pos - vega_in_neg, 1),
            })
    sig_df = pd.DataFrame(sig_table).sort_values("discrimination", ascending=False)
    sig_df.to_csv(OUT / "regime_signal_table.csv", index=False)
    print(sig_df.to_string(index=False))

    # ── Optimal entry per regime: aggregate premium by minute ──
    print("\n=== Optimal entry by regime ===")
    by_regime_entry = []
    for regime in ["calm", "vega", "borderline"]:
        sub = df[df["regime"]==regime]
        if sub.empty: continue
        avg_at = {}
        for col, lbl in [("prem_915","9:15"),("prem_920","9:20"),("prem_930","9:30"),
                         ("prem_945","9:45"),("prem_1000","10:00"),("prem_1030","10:30"),
                         ("prem_1100","11:00"),("prem_1130","11:30"),("prem_1200","12:00")]:
            avg_at[lbl] = round(sub[col].mean(), 2)
        avg_at["peak_avg"] = round(sub["peak_combined"].mean(), 2)
        avg_at["peak_avg_time"] = sub["peak_time"].mode().iloc[0] if not sub["peak_time"].mode().empty else ""
        avg_at["regime"] = regime
        avg_at["n"] = len(sub)
        by_regime_entry.append(avg_at)
    re_df = pd.DataFrame(by_regime_entry)
    re_df.to_csv(OUT / "avg_premium_by_minute_per_regime.csv", index=False)
    print(re_df.to_string(index=False))

    # ── Plot: avg combined premium path per regime ──
    if not df.empty:
        time_cols = ["prem_915","prem_920","prem_930","prem_945","prem_1000","prem_1030","prem_1100","prem_1130","prem_1200"]
        time_labels = ["9:15","9:20","9:30","9:45","10:00","10:30","11:00","11:30","12:00"]
        fig, ax = plt.subplots(figsize=(10, 5.5))
        for regime, color in [("calm","#16A34A"),("vega","#B91C1C"),("borderline","#CA8A04")]:
            sub = df[df["regime"]==regime]
            if sub.empty: continue
            avgs = [sub[c].mean() for c in time_cols]
            ax.plot(time_labels, avgs, "o-", lw=2, label=f"{regime} (n={len(sub)})", color=color)
        ax.set_xlabel("Entry minute")
        ax.set_ylabel("Avg combined CE+PE premium @ 2.5% OTM (₹/share)")
        ax.set_title("NIFTY E-0 — average premium path by regime (48 days)")
        ax.legend(); ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(OUT / "premium_path_by_regime.png", dpi=140)
        plt.close(fig)

    # ── Build summary ──
    md = f"""# 010 — NIFTY E-0 Timing Calibration

Sample: **{len(df)} NIFTY weekly E-0 days** (2025-04-22 → 2026-04-28).
Goal: when does combined CE+PE premium at 2.5% OTM peak — and what predicts it?

## Regime classification

A day is classified as:
- **calm**: peak premium ≤ 105% of 9:30 premium (or peaks before 10:00) — i.e. theta wins from open
- **vega**: peak premium > 110% of 9:30 AND occurs after 10:00 — IV expansion drove a later peak
- **borderline**: in-between

```
{regime_counts.to_string()}
```

## Average premium path by regime

| Regime | n | 9:15 | 9:20 | 9:30 | 9:45 | 10:00 | 10:30 | 11:00 | 11:30 | 12:00 | Peak (avg) |
|---|---|---|---|---|---|---|---|---|---|---|---|
"""
    for _, r in re_df.iterrows():
        md += (f"| **{r['regime']}** | {int(r['n'])} | {r['9:15']} | {r['9:20']} | {r['9:30']} | "
               f"{r['9:45']} | {r['10:00']} | {r['10:30']} | {r['11:00']} | {r['11:30']} | "
               f"{r['12:00']} | {r['peak_avg']} |\n")

    md += "\n## Signals predicting vega regime\n\n"
    md += "| Signal | n where TRUE | n where FALSE | vega% TRUE | vega% FALSE | discrimination |\n"
    md += "|---|---|---|---|---|---|\n"
    for _, r in sig_df.iterrows():
        md += (f"| {r['signal']} | {r['n_signal_true']} | {r['n_signal_false']} | "
               f"{r['vega_rate_signal_true']}% | {r['vega_rate_signal_false']}% | "
               f"**{r['discrimination']}** |\n")

    md += f"""

## Calibrated rule (extracted from data above)

(Refine threshold combinations after reviewing regime_signal_table.csv)

## Files
- `per_day.csv` — every E-0 day's signals + premium path + regime classification
- `regime_signal_table.csv` — signal discrimination
- `avg_premium_by_minute_per_regime.csv` — premium curve by regime
- `premium_path_by_regime.png` — visual of the premium curves
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    main()
