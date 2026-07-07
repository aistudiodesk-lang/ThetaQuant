"""
ANALYSIS 026 — E-1 regime × distance × entry-time grid (both indices)

Reconciles the 006/007 vs §9O contradiction:
  006/007: E-1 10:00 entry @ 2.5% OTM = 100% win (46 NIFTY days, no news filter)
  §9O:     E-1 14:45+ entry @ ≥3.5% OTM (from one bad live day, 6-May war news)

Design:
  E-1 day = 1 trading day before weekly expiry (per lib.expiry_calendar).
  Entry times: 10:00, 12:00, 14:45
  Distances: 2.0, 2.5, 3.0, 3.5% OTM
  Hold: to next-day expiry settlement (15:25 close of E-0).
  P&L = entry combined premium − ITM penalty at expiry settlement.
  Regime buckets: same classifier as analysis 025 (gap/range/vix-proxy on E-1 day).
  ALSO tracks: overnight gap (E-0 open vs E-1 close) per trade — the real tail.

Efficient: bulk-loads all needed option entry prices in ONE DuckDB query per
instrument (lesson from 025's slowness).
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path
import sys, os

import duckdb, numpy as np, pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "026_e1_regime_distance"
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()
ENTRY_TIMES = [time(9, 20), time(9, 30), time(10, 0), time(11, 0), time(12, 0), time(14, 45)]
DISTANCES = [2.0, 2.5, 3.0, 3.5]
E0_CLOSE = time(15, 25)

INSTRUMENTS = {
    "NIFTY":  {"expiries": sorted(NIFTY_WEEKLY_EXPIRIES),  "grid": 50,  "lot": 75, "lots_per_cr": 43},
    "SENSEX": {"expiries": sorted(SENSEX_WEEKLY_EXPIRIES), "grid": 100, "lot": 20, "lots_per_cr": 40},
}


def load_fut(inst):
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
    return df[df["timestamp"].dt.weekday < 5].sort_values("timestamp").reset_index(drop=True)


def bulk_load_options(inst, jobs):
    """jobs: list of (e1_date, expiry, strike, opt, entry_time).
    ONE query: load all (date, strike, opt) bars >= 10:00, then resolve in pandas."""
    if not jobs:
        return {}
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    pairs = {(j[0], j[1]) for j in jobs}
    strikes = {j[2] for j in jobs}
    dates_sql = ",".join(f"DATE '{d.isoformat()}'" for d, _ in pairs)
    exp_sql = ",".join(f"DATE '{e.isoformat()}'" for _, e in pairs)
    strikes_sql = ",".join(str(s) for s in strikes)
    df = con.execute(f"""
        SELECT CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) AS d,
               CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIME) AS t,
               expiry, strike, option_type, close
        FROM read_parquet('{glob}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND strike IN ({strikes_sql})
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) IN ({dates_sql})
          AND expiry IN ({exp_sql})
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIME) >= TIME '09:59:00'
    """).fetchdf()
    if df.empty:
        return {}
    df["d"] = pd.to_datetime(df["d"]).dt.date
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    out = {}
    for (d, exp, strike, opt, et), _ in {(j[0], j[1], j[2], j[3], j[4]): 1 for j in jobs}.items():
        sub = df[(df["d"] == d) & (df["expiry"] == exp) & (df["strike"] == strike) &
                 (df["option_type"] == opt) & (df["t"] >= et)]
        if not sub.empty:
            out[(d, exp, strike, opt, et)] = float(sub.sort_values("t").iloc[0]["close"])
    return out


def vix_proxy(fut_daily, d, look=20):
    prior = fut_daily[fut_daily["date"] < d].tail(look)
    if len(prior) < 5: return None
    rets = np.log(prior["close"] / prior["close"].shift(1)).dropna()
    return float(rets.std() * np.sqrt(252) * 100) if len(rets) >= 5 else None


def classify(gap, rng, vix):
    if abs(gap) > 0.7 or rng > 1.0 or (vix or 0) > 18: return "high_risk"
    if abs(gap) > 0.4 or rng > 0.7 or (vix or 0) > 15: return "moderate"
    if abs(gap) <= 0.3 and rng <= 0.5 and (vix or 0) <= 13: return "calm_green"
    return "normal"


def run():
    all_rows = []
    for inst, cfg in INSTRUMENTS.items():
        print(f"[{inst}] loading FUT...")
        fut = load_fut(inst)
        fut_daily = fut.groupby("date").agg(open=("open","first"), high=("high","max"),
                                             low=("low","min"), close=("close","last")).reset_index()
        fut_daily["prev_close"] = fut_daily["close"].shift(1)
        trading_days = sorted(fut["date"].unique())
        td_set = set(trading_days)

        # E-1 day = last trading day strictly before each expiry
        e1_pairs = []
        for exp in cfg["expiries"]:
            if exp not in td_set: continue
            prior = [d for d in trading_days if d < exp]
            if prior:
                e1_pairs.append((prior[-1], exp))
        print(f"  {len(e1_pairs)} E-1 days")

        # Build job list (spot at each entry time -> strikes)
        jobs = []
        meta = {}  # (d, exp, et) -> context
        for d, exp in e1_pairs:
            day = fut[fut["date"] == d]
            if day.empty: continue
            row_fd = fut_daily[fut_daily["date"] == d]
            if row_fd.empty: continue
            prev_close = row_fd.iloc[0]["prev_close"]
            open_p = float(day.iloc[0]["open"])
            gap = (open_p - prev_close) / prev_close * 100 if prev_close and not np.isnan(prev_close) else 0
            vix = vix_proxy(fut_daily, d)

            # E-0 settlement spot
            e0_day = fut[fut["date"] == exp]
            if e0_day.empty: continue
            e0_cls = e0_day[e0_day["time"] <= E0_CLOSE]
            if e0_cls.empty: continue
            settle = float(e0_cls.iloc[-1]["close"])
            e0_open = float(e0_day.iloc[0]["open"])

            for et in ENTRY_TIMES:
                ent = day[day["time"] >= et]
                if ent.empty: continue
                spot_e = float(ent.iloc[0]["close"])
                pre = day[day["time"] < et]
                rng = (pre["high"].max() - pre["low"].min()) / pre.iloc[0]["close"] * 100 if not pre.empty else 0
                e1_close = float(day.iloc[-1]["close"])
                overnight_gap = (e0_open - e1_close) / e1_close * 100
                meta[(d, exp, et)] = {
                    "spot_e": spot_e, "gap": gap, "rng": rng, "vix": vix,
                    "settle": settle, "overnight_gap": overnight_gap,
                }
                for dist in DISTANCES:
                    pe_k = int(round(spot_e * (1 - dist/100) / cfg["grid"]) * cfg["grid"])
                    ce_k = int(round(spot_e * (1 + dist/100) / cfg["grid"]) * cfg["grid"])
                    jobs.append((d, exp, pe_k, "PE", et))
                    jobs.append((d, exp, ce_k, "CE", et))

        print(f"  bulk loading {len(jobs)} option lookups in one query...")
        prices = bulk_load_options(inst, jobs)
        print(f"  resolved {len(prices)} prices")

        scale = cfg["lot"] * cfg["lots_per_cr"]
        for (d, exp, et), ctx in meta.items():
            for dist in DISTANCES:
                pe_k = int(round(ctx["spot_e"] * (1 - dist/100) / cfg["grid"]) * cfg["grid"])
                ce_k = int(round(ctx["spot_e"] * (1 + dist/100) / cfg["grid"]) * cfg["grid"])
                pe_p = prices.get((d, exp, pe_k, "PE", et))
                ce_p = prices.get((d, exp, ce_k, "CE", et))
                if pe_p is None or ce_p is None: continue
                pe_itm = max(0, pe_k - ctx["settle"])
                ce_itm = max(0, ctx["settle"] - ce_k)
                pnl_share = (pe_p + ce_p) - (pe_itm + ce_itm)
                all_rows.append({
                    "inst": inst, "date": d, "expiry": exp,
                    "entry_time": et.strftime("%H:%M"), "dist_pct": dist,
                    "regime": classify(ctx["gap"], ctx["rng"], ctx["vix"]),
                    "gap_pct": round(ctx["gap"], 2), "pre_range_pct": round(ctx["rng"], 2),
                    "vix_proxy": round(ctx["vix"] or 0, 1),
                    "overnight_gap_pct": round(ctx["overnight_gap"], 2),
                    "combined_entry": round(pe_p + ce_p, 2),
                    "premium_per_cr": round((pe_p + ce_p) * scale, 0),
                    "pe_itm_pts": round(pe_itm, 1), "ce_itm_pts": round(ce_itm, 1),
                    "itm": (pe_itm > 0 or ce_itm > 0),
                    "pnl_per_cr": round(pnl_share * scale, 0),
                })

    df = pd.DataFrame(all_rows)
    df.to_csv(OUT / "per_day_results.csv", index=False)
    print(f"\n{len(df)} sim rows")

    # Summary: (inst, regime, entry_time, dist)
    g = df.groupby(["inst", "regime", "entry_time", "dist_pct"]).agg(
        n=("date", "count"),
        median_prem=("premium_per_cr", "median"),
        win_pct=("pnl_per_cr", lambda x: round((x > 0).mean()*100, 0)),
        itm_pct=("itm", lambda x: round(x.mean()*100, 0)),
        median_pnl=("pnl_per_cr", "median"),
        worst=("pnl_per_cr", "min"),
    ).reset_index()
    g.to_csv(OUT / "summary.csv", index=False)

    print("\n=== 100%-win cells only (the safe menu) ===")
    safe = g[(g["win_pct"] == 100) & (g["n"] >= 8)].sort_values(["inst", "regime", "median_pnl"], ascending=[True, True, False])
    print(safe.to_string(index=False))
    print("\n=== Danger cells (any ITM) ===")
    danger = g[g["itm_pct"] > 0].sort_values("worst")
    print(danger.head(20).to_string(index=False))


if __name__ == "__main__":
    run()
