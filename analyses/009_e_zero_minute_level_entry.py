"""
ANALYSIS 009 — E-0 minute-level entry sweep (9:15 → 10:30, 1-min step)

Two questions Rohan asked:
  1. If trade was taken after 9:20 (9:20-9:45 window) at 2.5%+ OTM both sides,
     was there 100% success across all 47 NIFTY E-0 days in the sample?
  2. What's the absolute best time to enter to maximise deep-OTM premium
     captured (gross + net of real Axis friction)?

Method: for each NIFTY E-0 day in store, for each candidate entry MINUTE in
[9:15, 9:16, ..., 10:30] and each distance in {2.5, 3.0, 4.0, 5.0}%:
  - take first bar at/after that minute
  - record combined CE+PE entry price
  - hold to 15:25, record exit
  - compute gross + net (real Axis friction model)

Output:
  results/009_e_zero_minute_level_entry/
    summary.md
    minute_grid.csv          (full minute × distance aggregate)
    best_entry_per_distance.csv
    premium_decay_chart.png  (line per distance: avg combined entry by minute)
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT = ROOT / "results" / "009_e_zero_minute_level_entry"
OUT.mkdir(parents=True, exist_ok=True)

LOT = 65
GRID = 50
EXIT_AT = time(15, 25)
WEEKLY_EXP_WD = {1, 3}

# Minute candidates from 9:15 to 10:30
ENTRY_MINUTES = []
for h in [9, 10]:
    for m in range(0, 60):
        if h == 9 and m < 15: continue
        if h == 10 and m > 30: continue
        ENTRY_MINUTES.append(time(h, m))

DISTANCES = [2.5, 3.0, 4.0, 5.0]

con = duckdb.connect()


def realistic_friction(premium_per_share: float, sq_off: bool) -> float:
    BROKERAGE = 6.0
    legs = 2
    transactions = 2 if sq_off else 1
    sell_value = premium_per_share * LOT
    sell_turnover = sell_value * legs
    total_turnover = sell_turnover * transactions
    brokerage = BROKERAGE * legs * transactions
    stt = 0.0010 * sell_turnover
    exch = 0.00053 * total_turnover
    sebi = 0.000001 * total_turnover
    gst = 0.18 * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + gst + 10.91


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
    df["time"] = df["timestamp"].dt.time
    ce = df[(df.option_type=="CE") & (df.strike==ce_s)].sort_values("timestamp")
    pe = df[(df.option_type=="PE") & (df.strike==pe_s)].sort_values("timestamp")
    if ce.empty or pe.empty: return None
    m = pd.merge(
        ce[["timestamp","close"]].rename(columns={"close":"ce_c"}),
        pe[["timestamp","close"]].rename(columns={"close":"pe_c"}),
        on="timestamp", how="inner")
    m["combined"] = m["ce_c"] + m["pe_c"]
    return m


def pick(spot, dp):
    return (int(round(spot*(1+dp/100)/GRID)*GRID),
            int(round(spot*(1-dp/100)/GRID)*GRID))


def main():
    print("\n=== 009 — E-0 minute-level entry sweep ===\n")
    fut = load_fut()
    days = sorted(fut["date"].unique())
    e0_days = []
    for d in days:
        if pd.Timestamp(d).weekday() not in WEEKLY_EXP_WD: continue
        if expiry_on(d) != d: continue
        e0_days.append(d)
    print(f"[days] E-0 days = {len(e0_days)}")

    rows = []
    for i, d in enumerate(e0_days, 1):
        fday = fut[fut["date"] == d]
        if fday.empty: continue
        # spot = first bar's close as anchor for strike pick
        first_bar = fday.sort_values("timestamp").iloc[0]
        spot = float(first_bar["close"])

        for dp in DISTANCES:
            ce_s, pe_s = pick(spot, dp)
            m = load_legs(d, d, ce_s, pe_s)
            if m is None or m.empty: continue
            # exit price = last bar at/before 15:25
            exit_bar = m[m["timestamp"].dt.time <= EXIT_AT].iloc[-1] if not m.empty else None
            if exit_bar is None: continue
            exit_combined = float(exit_bar["combined"])

            for et in ENTRY_MINUTES:
                e_after = m[m["timestamp"].dt.time >= et]
                if e_after.empty: continue
                entry_combined = float(e_after.iloc[0]["combined"])
                if entry_combined <= 0: continue
                gross = (entry_combined - exit_combined) * LOT
                worthless = int(exit_combined <= 2.0)
                fric = realistic_friction(entry_combined, sq_off=(not worthless))
                net = gross - fric
                rows.append({
                    "date": d, "distance_pct": dp,
                    "entry_time": et.strftime("%H:%M"),
                    "ce_strike": ce_s, "pe_strike": pe_s,
                    "spot_at_entry": spot,
                    "combined_entry": round(entry_combined, 2),
                    "combined_exit": round(exit_combined, 2),
                    "gross_per_lot": round(gross, 0),
                    "net_per_lot": round(net, 0),
                    "worthless": worthless,
                })
        if i % 10 == 0:
            print(f"  [{i}/{len(e0_days)}] rows so far = {len(rows):,}")
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "minute_grid.csv", index=False)
    print(f"[grid] {len(df):,} rows")

    # ── Aggregate by (entry_time, distance) ──
    agg = df.groupby(["entry_time","distance_pct"]).agg(
        days=("date","count"),
        median_entry=("combined_entry","median"),
        avg_entry=("combined_entry","mean"),
        avg_gross_per_lot=("gross_per_lot","mean"),
        avg_net_per_lot=("net_per_lot","mean"),
        worst_net_per_lot=("net_per_lot","min"),
        worthless_pct=("worthless", lambda x: round(x.mean()*100, 1)),
        win_pct=("net_per_lot", lambda x: round((x>0).mean()*100,1)),
    ).round(2).reset_index()
    agg.to_csv(OUT / "minute_distance_grid.csv", index=False)

    # ── Best entry time per distance ──
    best = (agg.sort_values("avg_net_per_lot", ascending=False)
            .groupby("distance_pct").head(3)
            .sort_values(["distance_pct","avg_net_per_lot"], ascending=[True,False]))
    best.to_csv(OUT / "best_entry_per_distance.csv", index=False)

    # ── Verify Rohan's claim: 9:20-9:45 × 2.5%+ → 100% success? ──
    claim_window = ["09:20","09:21","09:22","09:23","09:24","09:25",
                    "09:26","09:27","09:28","09:29","09:30",
                    "09:31","09:32","09:33","09:34","09:35",
                    "09:36","09:37","09:38","09:39","09:40",
                    "09:41","09:42","09:43","09:44","09:45"]
    claim_df = df[(df["entry_time"].isin(claim_window)) & (df["distance_pct"] >= 2.5)]
    claim_summary = claim_df.groupby(["distance_pct"]).agg(
        events=("date","count"),
        worthless_pct=("worthless", lambda x: round(x.mean()*100, 1)),
        win_pct=("net_per_lot", lambda x: round((x>0).mean()*100,1)),
        avg_net=("net_per_lot","mean"),
        worst_net=("net_per_lot","min"),
    ).round(0).reset_index()
    claim_summary.to_csv(OUT / "claim_check_9_20_to_9_45.csv", index=False)
    print("\n=== Claim check: 9:20-9:45 × 2.5%+ OTM ===")
    print(claim_summary.to_string(index=False))

    # ── Premium decay chart: avg combined entry vs minute, per distance ──
    fig, ax = plt.subplots(figsize=(12, 6))
    for dp in DISTANCES:
        sub = agg[agg["distance_pct"] == dp].sort_values("entry_time")
        ax.plot(sub["entry_time"], sub["avg_entry"], "o-", lw=1.4, label=f"{dp}% OTM")
    ax.set_xlabel("Entry minute")
    ax.set_ylabel("Avg combined CE+PE entry (₹/share)")
    ax.set_title("E-0 premium decay by entry minute · NIFTY (47 days)")
    ax.grid(alpha=0.2); ax.legend()
    # rotate x labels for readability
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(60); lbl.set_ha("right")
    fig.tight_layout(); fig.savefig(OUT / "premium_decay_chart.png", dpi=140)
    plt.close(fig)

    # ── Net P&L chart ──
    fig, ax = plt.subplots(figsize=(12, 6))
    for dp in DISTANCES:
        sub = agg[agg["distance_pct"] == dp].sort_values("entry_time")
        ax.plot(sub["entry_time"], sub["avg_net_per_lot"], "o-", lw=1.4, label=f"{dp}% OTM")
    ax.set_xlabel("Entry minute"); ax.set_ylabel("Avg net P&L per lot (₹)")
    ax.set_title("E-0 avg net P&L per lot vs entry minute · NIFTY (47 days)")
    ax.axhline(0, color="gray", lw=0.5)
    ax.grid(alpha=0.2); ax.legend()
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(60); lbl.set_ha("right")
    fig.tight_layout(); fig.savefig(OUT / "net_pnl_chart.png", dpi=140)
    plt.close(fig)

    # ── Summary.md ──
    md = f"""# 009 — E-0 Minute-Level Entry Sweep (NIFTY)

Two questions:
1. Was 9:20-9:45 entry at 2.5%+ OTM → 100% success in last 1 year?
2. What's the absolute best minute to enter for max premium?

Sample: **{len(e0_days)} NIFTY weekly E-0 days** (2025-04-17 → 2026-04-21).
Distances tested: {DISTANCES} % OTM (symmetric).
Entry minutes tested: 9:15-10:30 (every 1 min).
Hold: to 15:25 expiry close. Friction: real Axis (₹6/lot).

## Rohan's claim verification: 9:20-9:45 entry × ≥2.5% OTM

"""
    md += "| distance % | events | worthless % | win % (net positive) | avg net ₹/lot | worst net ₹/lot |\n"
    md += "|---|---|---|---|---|---|\n"
    for _, r in claim_summary.iterrows():
        md += (f"| {r['distance_pct']} | {int(r['events'])} | {r['worthless_pct']} | "
               f"{r['win_pct']} | {int(r['avg_net'])} | {int(r['worst_net'])} |\n")

    md += f"""

## Best entry minute per distance (top 3 by avg net)

"""
    md += "| dist % | entry | days | median entry ₹ | avg net ₹/lot | worst ₹/lot | worthless % |\n"
    md += "|---|---|---|---|---|---|---|\n"
    for _, r in best.iterrows():
        md += (f"| {r['distance_pct']} | {r['entry_time']} | {int(r['days'])} | "
               f"{r['median_entry']} | **{int(r['avg_net_per_lot'])}** | "
               f"{int(r['worst_net_per_lot'])} | {r['worthless_pct']} |\n")

    md += f"""

## Charts
- `premium_decay_chart.png` — how avg entry premium fades by minute
- `net_pnl_chart.png` — avg net P&L per lot by minute

## Files
- `minute_grid.csv` — full per-event data
- `minute_distance_grid.csv` — aggregate (entry × distance)
- `claim_check_9_20_to_9_45.csv` — verification table
- `best_entry_per_distance.csv` — top 3 minutes per distance
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    main()
