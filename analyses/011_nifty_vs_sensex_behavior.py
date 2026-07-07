"""
ANALYSIS 011 — NIFTY vs SENSEX Behavior Comparison

Side-by-side analysis of NIFTY (Tue weekly) vs SENSEX (Thu weekly) E-0 days,
with focus on deep-OTM short-strangle behavior:

  1. Premium structure (median 9:30 premium at 2.5%, 3%, 4% OTM)
  2. Pin behavior (max-pain proximity at expiry vs spot)
  3. Premium decay path (calm vs vega regime distribution)
  4. Worst-day characteristics (max intraday adverse excursion)
  5. % expire worthless at each distance
  6. Realized P&L per lot (using real Axis friction)
  7. Volatility profile (intraday range distribution)
  8. Implication for our strategy: should distances/sizing differ?

Uses lib.expiry_calendar.is_e0() for proper day classification (handles
Thu→Tue transition + holiday-shifted expiries).

Output: results/011_nifty_vs_sensex_behavior/
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
from lib.expiry_calendar import is_e0, NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "011_nifty_vs_sensex_behavior"
OUT.mkdir(parents=True, exist_ok=True)

# Per-instrument config
CFG = {
    "NIFTY":  {"lot": 65, "grid": 50,  "store": ROOT / "data/parquet/instrument=NIFTY"},
    "SENSEX": {"lot": 20, "grid": 100, "store": ROOT / "data/parquet/instrument=SENSEX"},
}

EXIT_AT = time(15, 25)
con = duckdb.connect()


def axis_friction(premium_per_share, lot, sq_off=False):
    """Axis broker all-in friction for one strangle (CE+PE)."""
    BROKER = 6.0
    legs = 2; tx = 2 if sq_off else 1
    sell_value = premium_per_share * lot
    sell_turnover = sell_value * legs
    total_turnover = sell_turnover * tx
    brokerage = BROKER * legs * tx
    stt = 0.0010 * sell_turnover
    exch = 0.00053 * total_turnover
    sebi = 0.000001 * total_turnover
    gst = 0.18 * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + gst


def load_fut_or_spot(instrument):
    p = str(CFG[instrument]["store"] / "**" / "*.parquet")
    df = con.execute(f"""
      SELECT timestamp, open, high, low, close, option_type
      FROM read_parquet('{p}', union_by_name=True)
      WHERE option_type IN ('SPOT','FUT')
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    return df


def get_spot_at(fut_df, d, at_time):
    g = fut_df[fut_df["date"] == d].sort_values("timestamp")
    if g.empty: return None
    sub = g[g["time"] >= at_time]
    if sub.empty: return float(g["close"].iloc[0])
    return float(sub["close"].iloc[0])


def load_legs(instrument, d, exp, ce_s, pe_s):
    p = str(CFG[instrument]["store"] / "**" / "*.parquet")
    df = con.execute(f"""
      SELECT timestamp, strike, option_type, close
      FROM read_parquet('{p}', union_by_name=True)
      WHERE option_type IN ('CE','PE') AND expiry = DATE '{exp.isoformat()}'
        AND CAST(timestamp AS DATE) = DATE '{d.isoformat()}'
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


def pick(spot, dp, grid):
    return (int(round(spot*(1+dp/100)/grid)*grid), int(round(spot*(1-dp/100)/grid)*grid))


def analyze_instrument(instrument):
    cfg = CFG[instrument]
    fut = load_fut_or_spot(instrument)
    if fut.empty:
        print(f"[{instrument}] no futures/spot data — skip"); return pd.DataFrame()

    days_in_data = sorted(fut["date"].unique())
    e0 = [d for d in (NIFTY_WEEKLY_EXPIRIES if instrument=='NIFTY' else SENSEX_WEEKLY_EXPIRIES)
           if d in days_in_data]
    print(f"[{instrument}] E-0 days in data: {len(e0)}")

    rows = []
    for i, d in enumerate(e0):
        fday = fut[fut["date"]==d].sort_values("timestamp").reset_index(drop=True)
        if fday.empty: continue
        open_p = float(fday.iloc[0]["open"])
        spot_930 = get_spot_at(fut, d, time(9, 30)) or open_p
        spot_close = float(fday.iloc[-1]["close"])

        # Compute first-15 range, drift, day range
        first_ts = fday.iloc[0]["timestamp"]
        f15 = fday[fday["timestamp"] <= first_ts + timedelta(minutes=15)]
        f15_range_pct = (f15["high"].max() - f15["low"].min()) / open_p * 100 if not f15.empty else np.nan
        day_range_pct = (fday["high"].max() - fday["low"].min()) / open_p * 100
        day_drift_pct = (spot_close - open_p) / open_p * 100

        # For each distance, get premium path + outcome
        for dp in [2.0, 2.5, 3.0, 3.5, 4.0]:
            ce_s, pe_s = pick(spot_930, dp, cfg["grid"])
            m = load_legs(instrument, d, d, ce_s, pe_s)
            if m is None or m.empty: continue
            m = m[m["timestamp"].dt.time <= EXIT_AT].copy()

            def at(t_):
                sub = m[m["timestamp"].dt.time >= t_]
                return float(sub.iloc[0]["combined"]) if not sub.empty else np.nan

            prem_915 = at(time(9,15)); prem_930 = at(time(9,30)); prem_1030 = at(time(10,30))
            prem_close = float(m["combined"].iloc[-1]) if not m.empty else np.nan

            # MAE intraday
            adv_max = m["combined"].max() if not m.empty else np.nan
            mae = max(0.0, adv_max - prem_930) if pd.notna(adv_max) and pd.notna(prem_930) else np.nan

            # Worthless = both legs < 1
            ce_close = float(m["ce"].iloc[-1]); pe_close = float(m["pe"].iloc[-1])
            worthless = int(ce_close < 1 and pe_close < 1)

            # Net P&L per lot @ 9:30 entry
            gross = (prem_930 - prem_close) * cfg["lot"] if pd.notna(prem_930) else np.nan
            fric = axis_friction(prem_930, cfg["lot"], sq_off=(not worthless)) if pd.notna(prem_930) else np.nan
            net = gross - fric if pd.notna(gross) else np.nan

            rows.append({
                "instrument": instrument, "date": d, "dow": pd.Timestamp(d).day_name(),
                "spot_930": round(spot_930, 2), "spot_close": round(spot_close, 2),
                "day_range_pct": round(day_range_pct, 3),
                "day_drift_pct": round(day_drift_pct, 3),
                "f15_range_pct": round(f15_range_pct, 3),
                "distance_pct": dp, "ce_strike": ce_s, "pe_strike": pe_s,
                "prem_915": round(prem_915, 2),
                "prem_930": round(prem_930, 2),
                "prem_1030": round(prem_1030, 2),
                "prem_close": round(prem_close, 2),
                "mae": round(mae, 2) if pd.notna(mae) else np.nan,
                "worthless": worthless,
                "gross_per_lot": round(gross, 0),
                "net_per_lot": round(net, 0),
            })
        if (i+1) % 20 == 0:
            print(f"  [{i+1}/{len(e0)}] processed")
    return pd.DataFrame(rows)


def main():
    nifty = analyze_instrument("NIFTY")
    sensex = analyze_instrument("SENSEX")
    full = pd.concat([nifty, sensex], ignore_index=True)
    full.to_csv(OUT / "per_event.csv", index=False)
    print(f"\nTotal events: NIFTY={len(nifty)//5} days × 5 distances, SENSEX={len(sensex)//5} days × 5 distances")

    # ── Aggregate by (instrument × distance) ──
    agg = full.groupby(["instrument","distance_pct"]).agg(
        days=("date","count"),
        median_prem_930=("prem_930","median"),
        mean_prem_930=("prem_930","mean"),
        median_day_range_pct=("day_range_pct","median"),
        median_f15_range_pct=("f15_range_pct","median"),
        median_mae=("mae","median"),
        p90_mae=("mae", lambda x: x.quantile(0.9)),
        worthless_pct=("worthless", lambda x: x.mean()*100),
        median_gross_per_lot=("gross_per_lot","median"),
        median_net_per_lot=("net_per_lot","median"),
        worst_net_per_lot=("net_per_lot","min"),
        win_pct=("net_per_lot", lambda x: (x>0).mean()*100),
    ).round(2).reset_index()
    agg.to_csv(OUT / "by_instrument_distance.csv", index=False)
    print("\n=== By instrument × distance ===")
    print(agg.to_string(index=False))

    # ── Volatility profile comparison ──
    print("\n=== Volatility profile comparison ===")
    vol_summary = full.groupby("instrument").agg(
        median_day_range_pct=("day_range_pct","median"),
        p75_day_range_pct=("day_range_pct", lambda x: x.quantile(0.75)),
        p95_day_range_pct=("day_range_pct", lambda x: x.quantile(0.95)),
        median_f15_range_pct=("f15_range_pct","median"),
        median_drift_pct=("day_drift_pct", lambda x: x.abs().median()),
        n_e0_days=("date","nunique"),
    ).round(3)
    print(vol_summary.to_string())

    # ── Premium-vs-distance chart ──
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax, metric, title in zip(axes, ["median_prem_930","worthless_pct"],
                                   ["Median 9:30 premium @ each distance","% expired worthless"]):
        for inst, color in [("NIFTY","#2563EB"), ("SENSEX","#DC2626")]:
            sub = agg[agg["instrument"]==inst].sort_values("distance_pct")
            ax.plot(sub["distance_pct"], sub[metric], "o-", lw=2, label=inst, color=color)
        ax.set_xlabel("Distance % OTM"); ax.set_title(title)
        ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "premium_and_worthless_vs_distance.png", dpi=140)
    plt.close(fig)

    # ── MAE comparison chart ──
    fig, ax = plt.subplots(figsize=(10, 5))
    for inst, color in [("NIFTY","#2563EB"), ("SENSEX","#DC2626")]:
        sub = agg[agg["instrument"]==inst].sort_values("distance_pct")
        ax.plot(sub["distance_pct"], sub["median_mae"], "o-", color=color, label=f"{inst} median MAE", lw=2)
        ax.plot(sub["distance_pct"], sub["p90_mae"], "s--", color=color, label=f"{inst} p90 MAE", lw=1, alpha=0.6)
    ax.set_xlabel("Distance % OTM"); ax.set_ylabel("MAE / share (intraday adverse from 9:30 entry)")
    ax.set_title("Intraday Max Adverse Excursion: NIFTY vs SENSEX"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "mae_comparison.png", dpi=140)
    plt.close(fig)

    # ── Net P&L per lot @ 9:30 entry ──
    fig, ax = plt.subplots(figsize=(10, 5))
    for inst, color in [("NIFTY","#2563EB"), ("SENSEX","#DC2626")]:
        sub = agg[agg["instrument"]==inst].sort_values("distance_pct")
        ax.plot(sub["distance_pct"], sub["median_net_per_lot"], "o-", color=color, label=f"{inst} median net", lw=2)
        ax.plot(sub["distance_pct"], sub["worst_net_per_lot"], "s--", color=color, label=f"{inst} worst net", lw=1, alpha=0.6)
    ax.axhline(0, color="gray", lw=0.5)
    ax.set_xlabel("Distance % OTM"); ax.set_ylabel("Net P&L per lot (₹) @ 9:30 entry, hold to expiry")
    ax.set_title("Per-lot net P&L: NIFTY vs SENSEX"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "net_pnl_comparison.png", dpi=140)
    plt.close(fig)

    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    main()
