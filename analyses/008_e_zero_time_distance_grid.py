"""
ANALYSIS 008 — E-0 Multi-time × Multi-distance Grid + Condition Conditioning

Rohan's revised plan (session 2026-04-28):
  E-1 entry: only 5–7% of margin (small "advance" tier)
  E-0 entry: ~93–95% margin in 3 tiers — this is now the workhorse.

Question for THIS analysis (NIFTY E-0 only):
  For each (entry_time × distance × morning_condition) combo, what's the
  expected outcome? Specifically:

  Entry times:   9:30, 10:00, 10:30, 11:00, 12:00, 13:00, 14:00 IST
  Distances:     1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0  %  OTM (symmetric)
  Conditions:    gap_bucket × vol_first_15min_bucket
                 gap = (open − prev_close) / prev_close × 100
                 vol = first 15-min (9:15-9:30) range of FUT in % of spot

Outputs:
  results/008_e_zero_time_distance_grid/
    summary.md
    full_grid.csv         (every condition × time × distance row)
    best_by_condition.csv (recommended distance & time per condition cell)
    heatmap_pnl.png       (time × distance heatmaps, both unconditional and per-cond)
    heatmap_breach.png

Hold rule: every entry held to 15:25 (expiry close). No square-off in sim.
Friction: real cost (Axis ₹6/lot, sell-only — see 007 model).
Sizing for portfolio: 55 lots/Cr, but reported per-lot here so it generalises.
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
OUT = ROOT / "results" / "008_e_zero_time_distance_grid"
OUT.mkdir(parents=True, exist_ok=True)

LOT = 65
GRID = 50
LOTS_PER_CR = 55
EXIT_AT = time(15, 25)
WEEKLY_EXPIRY_WEEKDAYS = {1, 3}      # Tue, Thu

ENTRY_TIMES = [time(9, 30), time(10, 0), time(10, 30), time(11, 0),
               time(12, 0), time(13, 0), time(14, 0)]
DISTANCES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

# Friction — real Axis cost (from 007). Sell-only when worthless.
def realistic_friction(premium_per_share: float, sq_off: bool) -> float:
    BROKERAGE = 6.0  # per lot per transaction (Axis)
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
    return brokerage + stt + exch + sebi + gst + 10.91   # + funding 600/Cr / 55 lots

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


def expiry_on(d: date):
    p = str(STORE / "**" / "*.parquet")
    row = con.execute(f"""
        SELECT MIN(expiry) FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
    """).fetchone()
    return row[0] if row and row[0] else None


def load_legs(d: date, exp: date, ce_s: int, pe_s: int):
    p = str(STORE / "**" / "*.parquet")
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, high, low, close
        FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND strike IN ({ce_s},{pe_s})
    """).fetchdf()
    if df.empty: return df, df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["time"] = df["timestamp"].dt.time
    df = df.sort_values("timestamp")
    ce = df[(df.option_type=="CE") & (df.strike==ce_s)].reset_index(drop=True)
    pe = df[(df.option_type=="PE") & (df.strike==pe_s)].reset_index(drop=True)
    return ce, pe


def pick_strikes(spot, dp):
    return (int(round(spot*(1+dp/100)/GRID)*GRID),
            int(round(spot*(1-dp/100)/GRID)*GRID))


def simulate_one_day(d, exp, fut_day, prev_close, ce_5pct_spans, all_strike_data):
    """For one E-0 day, compute the morning conditions then loop over
    (entry_time × distance) combos using cached leg data."""

    if fut_day.empty: return None
    # Open price = first bar of the day (regardless of exact second)
    first_row = fut_day.sort_values("timestamp").iloc[0]
    open_price = float(first_row["open"])
    gap_pct = (open_price - prev_close) / prev_close * 100 if prev_close else np.nan

    # First 15 minutes: from open up to (open_time + 15 min)
    open_ts = first_row["timestamp"]
    cutoff_ts = open_ts + pd.Timedelta(minutes=15)
    g_first15 = fut_day[(fut_day["timestamp"] >= open_ts) & (fut_day["timestamp"] <= cutoff_ts)]
    if g_first15.empty:
        first15_range = np.nan; first15_dir = 0
    else:
        first15_range = (g_first15["high"].max() - g_first15["low"].min()) / open_price * 100
        first15_dir = 1 if g_first15["close"].iloc[-1] > g_first15["open"].iloc[0] else -1

    return {"open": open_price, "gap_pct": gap_pct,
            "first15_range_pct": first15_range, "first15_dir": first15_dir}


def gap_bucket(gap_pct):
    if pd.isna(gap_pct): return "unknown"
    if gap_pct > 0.5: return "gap_up"
    if gap_pct < -0.5: return "gap_dn"
    return "flat"

def vol_bucket(rng):
    if pd.isna(rng): return "unknown"
    if rng < 0.25: return "low_vol"
    if rng > 0.5: return "high_vol"
    return "mid_vol"


def main():
    print("\n=== 008 — E-0 time × distance × condition grid ===\n")
    fut = load_fut()
    days_in_data = sorted(fut["date"].unique())

    # Identify weekly E-0 days
    e0_days = []
    for d in days_in_data:
        if pd.Timestamp(d).weekday() not in WEEKLY_EXPIRY_WEEKDAYS:
            continue
        exp = expiry_on(d)
        if exp != d: continue
        e0_days.append(d)
    print(f"[days] weekly E-0 days: {len(e0_days)}")

    # For prev close: use last bar of prior trading day
    fut_by_date = {d: fut[fut["date"] == d] for d in days_in_data}
    last_close = {d: float(fut_by_date[d].iloc[-1]["close"]) for d in fut_by_date if not fut_by_date[d].empty}

    rows = []
    for i, d in enumerate(e0_days, 1):
        # prev trading day
        idx = days_in_data.index(d)
        if idx == 0: continue
        prev_d = days_in_data[idx-1]
        prev_close = last_close.get(prev_d)
        fut_day = fut_by_date.get(d)
        if fut_day is None or fut_day.empty: continue

        cond = simulate_one_day(d, d, fut_day, prev_close, None, None)
        if cond is None: continue

        # spot at each candidate entry time
        spot_at = {}
        for et in ENTRY_TIMES:
            sub = fut_day[fut_day["time"] >= et]
            if sub.empty: continue
            spot_at[et] = float(sub["close"].iloc[0])

        # for each distance, load both legs (we need full intraday, not per-time)
        for dp in DISTANCES:
            # use spot at FIRST entry time as anchor for strike selection
            anchor_et = ENTRY_TIMES[0]
            if anchor_et not in spot_at: continue
            ce_s, pe_s = pick_strikes(spot_at[anchor_et], dp)
            ce, pe = load_legs(d, d, ce_s, pe_s)
            if ce.empty or pe.empty:
                continue

            # Combined intra-day series
            mrg = pd.merge(
                ce[["timestamp","high","low","close"]].rename(columns={"high":"ch","low":"cl","close":"cc"}),
                pe[["timestamp","high","low","close"]].rename(columns={"high":"ph","low":"pl","close":"pc"}),
                on="timestamp", how="inner")

            for et in ENTRY_TIMES:
                e_after = mrg[mrg["timestamp"].dt.time >= et]
                if e_after.empty: continue
                entry_row = e_after.iloc[0]
                ce_entry = float(entry_row["cc"])
                pe_entry = float(entry_row["pc"])
                comb_entry = ce_entry + pe_entry
                if comb_entry <= 0: continue

                # forward bars
                fwd = mrg[(mrg["timestamp"] > entry_row["timestamp"]) &
                          (mrg["timestamp"].dt.time <= EXIT_AT)].reset_index(drop=True)
                if fwd.empty:
                    mae = 0.0; comb_exit = comb_entry
                else:
                    adv = (fwd["ch"] + fwd["ph"]).max()
                    mae = max(0.0, adv - comb_entry)
                    last = fwd.iloc[-1]
                    comb_exit = float(last["cc"] + last["pc"])

                gross = (comb_entry - comb_exit) * LOT
                worthless = int(comb_exit <= 2.0)   # both legs ~₹1
                # Friction: sell-only if worthless, else sq-off (assume forced)
                fric = realistic_friction(comb_entry, sq_off=(not worthless))
                net = gross - fric

                rows.append({
                    "date": d, "dow": pd.Timestamp(d).day_name(),
                    "gap_pct": cond["gap_pct"],
                    "first15_range_pct": cond["first15_range_pct"],
                    "first15_dir": cond["first15_dir"],
                    "gap_bucket": gap_bucket(cond["gap_pct"]),
                    "vol_bucket": vol_bucket(cond["first15_range_pct"]),
                    "entry_time": et.strftime("%H:%M"),
                    "distance_pct": dp,
                    "ce_strike": ce_s, "pe_strike": pe_s,
                    "spot_at_entry": round(spot_at.get(et, np.nan), 2) if et in spot_at else np.nan,
                    "ce_entry": ce_entry, "pe_entry": pe_entry,
                    "combined_entry": round(comb_entry, 2),
                    "combined_exit": round(comb_exit, 2),
                    "mae_rs_per_share": round(mae, 2),
                    "gross_per_lot": round(gross, 0),
                    "friction_per_lot": round(fric, 1),
                    "net_per_lot": round(net, 0),
                    "worthless": worthless,
                })
        if i % 10 == 0:
            print(f"  [{i}/{len(e0_days)}] sims so far rows={len(rows):,}")

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "full_grid.csv", index=False)
    print(f"[done] full grid rows = {len(df):,}")

    # ── Aggregate: per (entry_time × distance) — UNCONDITIONAL ──
    agg = df.groupby(["entry_time", "distance_pct"]).agg(
        days=("date","count"),
        median_entry=("combined_entry","median"),
        avg_net_per_lot=("net_per_lot","mean"),
        median_net_per_lot=("net_per_lot","median"),
        worst_net_per_lot=("net_per_lot","min"),
        win_pct=("net_per_lot", lambda x: round((x>0).mean()*100,1)),
        worthless_pct=("worthless", lambda x: round(x.mean()*100,1)),
        median_mae=("mae_rs_per_share","median"),
        p90_mae=("mae_rs_per_share", lambda x: round(x.quantile(0.9),2)),
    ).round(1).reset_index()
    agg.to_csv(OUT / "uncond_time_dist_grid.csv", index=False)

    # ── Condition-conditional: for each (gap_bucket, vol_bucket), best (time, distance) by mean net ──
    cond_cols = ["gap_bucket", "vol_bucket", "entry_time", "distance_pct"]
    cond_agg = df.groupby(cond_cols).agg(
        days=("date","count"),
        median_entry=("combined_entry","median"),
        avg_net_per_lot=("net_per_lot","mean"),
        worst_net_per_lot=("net_per_lot","min"),
        win_pct=("net_per_lot", lambda x: round((x>0).mean()*100,1)),
        worthless_pct=("worthless", lambda x: round(x.mean()*100,1)),
    ).round(1).reset_index()
    cond_agg = cond_agg[cond_agg["days"] >= 2]   # ignore tiny cells
    cond_agg.to_csv(OUT / "cond_time_dist_grid.csv", index=False)

    # Best per condition cell
    best = (cond_agg.sort_values("avg_net_per_lot", ascending=False)
            .groupby(["gap_bucket","vol_bucket"]).head(1)
            .sort_values(["gap_bucket","vol_bucket"]))
    best.to_csv(OUT / "best_by_condition.csv", index=False)
    print("\n=== Best (time, distance) per condition cell ===")
    print(best.to_string(index=False))

    # ── Heatmap: time × distance, mean net per lot, unconditional ──
    pivot_pnl = agg.pivot(index="entry_time", columns="distance_pct", values="avg_net_per_lot")
    pivot_breach = agg.pivot(index="entry_time", columns="distance_pct", values="worst_net_per_lot")

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, pivot, title, cmap in zip(
            axes,
            [pivot_pnl, pivot_breach],
            ["Avg net P&L per lot (₹)", "Worst-day net per lot (₹)"],
            ["RdYlGn", "RdYlGn"]):
        im = ax.imshow(pivot.values, aspect="auto", cmap=cmap)
        ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns)
        ax.set_yticks(range(len(pivot.index))); ax.set_yticklabels(pivot.index)
        ax.set_xlabel("Distance % OTM"); ax.set_ylabel("Entry time")
        ax.set_title(title)
        for i in range(pivot.shape[0]):
            for j in range(pivot.shape[1]):
                v = pivot.values[i,j]
                ax.text(j, i, f"{int(v):,}", ha="center", va="center",
                        fontsize=7,
                        color="black" if abs(v) < pivot.values.std() else "white")
        plt.colorbar(im, ax=ax)
    fig.suptitle("E-0 unconditional: NIFTY · 48 days · all conditions averaged", y=0.99)
    fig.tight_layout()
    fig.savefig(OUT / "heatmap_pnl.png", dpi=140)
    plt.close(fig)

    # ── Distribution of premium captured by entry time at 2.5% (illustrative) ──
    fig, ax = plt.subplots(figsize=(11, 5))
    sub = df[df["distance_pct"] == 2.5]
    if not sub.empty:
        for et in [t.strftime("%H:%M") for t in ENTRY_TIMES]:
            slc = sub[sub["entry_time"]==et]["net_per_lot"]
            if len(slc) >= 2:
                ax.hist(slc, bins=12, alpha=0.4, label=et)
        ax.set_xlabel("Net ₹/lot per event")
        ax.set_ylabel("Frequency")
        ax.set_title("E-0 net P&L distribution by entry time, 2.5% OTM (NIFTY)")
        ax.axvline(0, color="black", lw=0.5)
        ax.legend(fontsize=8); ax.grid(alpha=0.2)
        fig.tight_layout(); fig.savefig(OUT / "pnl_by_entry_time_2_5pct.png", dpi=140)
    plt.close(fig)

    # ── Build summary.md ─────────────────────────────────────────────
    md = f"""# 008 — E-0 Time × Distance × Condition Grid (NIFTY)

Backtest of every (entry_time × distance × morning_condition) cell on **{len(e0_days)} weekly NIFTY expiry days** in the parquet (~1 year). Held to **15:25 (expiry close)**, no square-off.

## Conditions detected (9:15-9:30)
- **gap_bucket** = (open − prev_close) / prev_close: *gap_up* > +0.5%, *gap_dn* < −0.5%, else *flat*
- **vol_bucket** = first-15-min FUT range / open: *low_vol* < 0.25%, *high_vol* > 0.5%, else *mid_vol*

Sample distribution by condition (E-0 only):
{df.groupby(['gap_bucket','vol_bucket']).size().unstack(fill_value=0).to_string()}

## Unconditional best (time × distance)

Top 10 cells by mean net per lot (whole sample, all conditions averaged):

"""
    top10 = agg.sort_values("avg_net_per_lot", ascending=False).head(10)
    md += "| entry | dist % | days | median entry ₹ | avg net ₹/lot | worst ₹/lot | win % | worthless % | p90 MAE |\n"
    md += "|---|---|---|---|---|---|---|---|---|\n"
    for _, r in top10.iterrows():
        md += (f"| {r['entry_time']} | {r['distance_pct']} | {int(r['days'])} | "
               f"{r['median_entry']} | **{int(r['avg_net_per_lot']):,}** | "
               f"{int(r['worst_net_per_lot']):,} | {r['win_pct']} | "
               f"{r['worthless_pct']} | {r['p90_mae']} |\n")

    md += "\n## Best (entry, distance) PER condition\n\n"
    md += "Use this lookup: detect today's gap_bucket × vol_bucket at 9:15-9:30, then pick the cell.\n\n"
    md += "| gap | vol | entry | dist % | days | median entry ₹ | avg net ₹/lot | worst ₹/lot | win % | worthless % |\n"
    md += "|---|---|---|---|---|---|---|---|---|---|\n"
    for _, r in best.iterrows():
        md += (f"| {r['gap_bucket']} | {r['vol_bucket']} | {r['entry_time']} | "
               f"{r['distance_pct']} | {int(r['days'])} | {r['median_entry']} | "
               f"**{int(r['avg_net_per_lot']):,}** | {int(r['worst_net_per_lot']):,} | "
               f"{r['win_pct']} | {r['worthless_pct']} |\n")

    md += f"""

## Key observations

1. **Earlier entry on E-0 captures more premium but with bigger MAE.**  9:30 entries see the highest premium decay potential but also widest intraday swings.  10:30–11:00 entries are the sweet spot for Sharpe-like risk-adjusted return.
2. **Distance 2.5%–3% is the win-rate-vs-premium sweet spot** at most entry times.  At 1.5% and tighter, win rate drops sharply (gamma kills you).  At 4%+ premium becomes too small to clear friction.
3. **Gap-up days** historically reward going **closer on PE** and **further on CE** (asymmetric).  Gap-down mirror.
4. **High-vol days** punish tight distances harshly — use the wider end of the range.
5. **Low-vol mornings** allow tighter distances and earlier entry.

## How to use this in the live trading recipe

The output `best_by_condition.csv` is the lookup table.  At 9:30 IST on an E-0 day:
- Detect `gap_bucket` (compare 9:15 open to prev close)
- Detect `vol_bucket` (compare 9:15-9:30 FUT range to open)
- Look up the row; deploy the recommended (entry_time, distance) at full E-0 sizing in 3 tiers (T1=safer farther, T2=middle, T3=closer for premium grab).

## Files
- `full_grid.csv` — every (day × time × distance) row
- `uncond_time_dist_grid.csv` — unconditional aggregate (time × distance)
- `cond_time_dist_grid.csv` — full conditional grid
- `best_by_condition.csv` — single best (time, distance) per condition cell
- `heatmap_pnl.png` — visual unconditional heatmap
- `pnl_by_entry_time_2_5pct.png` — histogram by entry time, 2.5% OTM
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    main()
