"""
ANALYSIS 031 — POP (probability of profit) full distance curve by regime

Extends 025 down to the CLOSE strikes (0.5–1.25%) so Tier-3 gets a REAL win
rate instead of the hardcoded 100%. POP = P(strike NOT breached by expiry),
empirical, bucketed by (instrument, regime, distance). Also emits per-SIDE
worthless rates (PE-only / CE-only) so the engine can show per-strike POP, not
just the strangle. Feeds lib/pop.py (pop_table.json) served live by regime.

Distances: 0.5, 0.7, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5
Output: results/031_pop_curve/{by_regime_distance.csv, pop_table.json}
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path
import sys, os

import duckdb, numpy as np, pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "031_pop_curve"
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()
ENTRY = time(10, 30)
CLOSE = time(15, 25)
DISTANCES = [0.5, 0.7, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5]


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


def load_option(inst, d, strike, opt, t):
    glob = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    r = con.execute(f"""
        SELECT close FROM read_parquet('{glob}', union_by_name=True)
        WHERE option_type='{opt}' AND strike={strike} AND expiry=DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIME) >= TIME '{t.strftime("%H:%M:%S")}'
        ORDER BY timestamp LIMIT 1
    """).fetchone()
    return float(r[0]) if r else None


def vix_proxy(fut_daily, d, look=20):
    prior = fut_daily[fut_daily["date"] < d].tail(look)
    if len(prior) < 5: return None
    rets = np.log(prior["close"] / prior["close"].shift(1)).dropna()
    return float(rets.std() * np.sqrt(252) * 100) if len(rets) >= 5 else None


def run():
    INSTRUMENTS = {
        "NIFTY":  {"expiries": set(NIFTY_WEEKLY_EXPIRIES),  "grid": 50,  "lot": 75, "lots_per_cr": 43},
        "SENSEX": {"expiries": set(SENSEX_WEEKLY_EXPIRIES), "grid": 100, "lot": 20, "lots_per_cr": 40},
    }

    rows = []
    for inst, cfg in INSTRUMENTS.items():
        print(f"\n[{inst}] loading...")
        fut = load_fut(inst)
        fut_daily = fut.groupby("date").agg(open=("open","first"), high=("high","max"),
                                             low=("low","min"), close=("close","last")).reset_index()
        fut_daily["prev_close"] = fut_daily["close"].shift(1)
        e0_days = sorted([d for d in fut["date"].unique() if d in cfg["expiries"]])
        print(f"  {len(e0_days)} E-0 days")

        scale = cfg["lot"] * cfg["lots_per_cr"]

        for di, d in enumerate(e0_days):
            day = fut[fut["date"] == d]
            if day.empty: continue
            ent = day[day["time"] >= ENTRY]
            if ent.empty: continue
            spot_e = float(ent.iloc[0]["close"])
            cls_row = day[day["time"] <= CLOSE]
            if cls_row.empty: continue
            spot_c = float(cls_row.iloc[-1]["close"])

            # Conditions
            prev_close = float(fut_daily[fut_daily["date"] == d].iloc[0]["prev_close"]) if not np.isnan(fut_daily[fut_daily["date"] == d].iloc[0]["prev_close"]) else spot_e
            open_p = float(day.iloc[0]["open"])
            gap_pct = (open_p - prev_close) / prev_close * 100 if prev_close else 0
            pre = day[day["time"] < ENTRY]
            pre_range = (pre["high"].max() - pre["low"].min()) / pre.iloc[0]["close"] * 100 if not pre.empty else 0
            pre_move = (spot_e - open_p) / open_p * 100
            day_range = (day["high"].max() - day["low"].min()) / open_p * 100
            vix = vix_proxy(fut_daily, d) or 0

            for dist_pct in DISTANCES:
                pe_strike = int(round(spot_e * (1 - dist_pct/100) / cfg["grid"]) * cfg["grid"])
                ce_strike = int(round(spot_e * (1 + dist_pct/100) / cfg["grid"]) * cfg["grid"])

                pe_ent = load_option(inst, d, pe_strike, "PE", ENTRY)
                ce_ent = load_option(inst, d, ce_strike, "CE", ENTRY)
                if pe_ent is None or ce_ent is None: continue

                # P&L at close: entry premium - ITM penalty (if any)
                pe_itm = max(0, pe_strike - spot_c)
                ce_itm = max(0, spot_c - ce_strike)
                pnl_per_share = (pe_ent + ce_ent) - (pe_itm + ce_itm)
                pnl_pcr = pnl_per_share * scale

                rows.append({
                    "date": d, "inst": inst, "dist_pct": dist_pct,
                    "spot_e": round(spot_e, 2), "spot_c": round(spot_c, 2),
                    "gap_pct": round(gap_pct, 2),
                    "pre_range_pct": round(pre_range, 2),
                    "pre_move_pct": round(pre_move, 2),
                    "day_range_pct": round(day_range, 2),
                    "vix_proxy": round(vix, 2),
                    "pe_strike": pe_strike, "ce_strike": ce_strike,
                    "combined_entry": round(pe_ent + ce_ent, 2),
                    "combined_per_cr": round((pe_ent + ce_ent) * scale, 0),
                    "pe_itm": pe_itm > 0, "ce_itm": ce_itm > 0,
                    "pnl_per_share": round(pnl_per_share, 2),
                    "pnl_per_cr": round(pnl_pcr, 0),
                })
            if (di + 1) % 20 == 0:
                print(f"  {di+1}/{len(e0_days)} done")

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "per_day_per_distance.csv", index=False)
    print(f"\n{len(df)} (day, distance) sim rows")

    # ─── Bucket conditions ───
    # Categorize each day into condition buckets
    def regime(r):
        if abs(r["gap_pct"]) > 0.7 or r["pre_range_pct"] > 1.0 or r["vix_proxy"] > 18:
            return "high_risk"
        if abs(r["gap_pct"]) > 0.4 or r["pre_range_pct"] > 0.7 or r["vix_proxy"] > 15:
            return "moderate"
        if abs(r["gap_pct"]) <= 0.3 and r["pre_range_pct"] <= 0.5 and r["vix_proxy"] <= 13:
            return "calm_green"
        return "normal"

    df["regime"] = df.apply(regime, axis=1)

    # Stats per (regime, distance, instrument)
    summary = df.groupby(["inst", "regime", "dist_pct"]).agg(
        n=("date", "count"),
        median_premium_pcr=("combined_per_cr", "median"),
        worst_pcr=("pnl_per_cr", "min"),
        median_pnl_pcr=("pnl_per_cr", "median"),
        p5_loss=("pnl_per_cr", lambda x: round(x.quantile(0.05), 0)),
        win_rate=("pnl_per_cr", lambda x: round((x > 0).mean() * 100, 0)),
        itm_rate=("pe_itm", lambda x: 0),
    ).reset_index()
    # POP = P(not breached). Per-side + strangle (neither side breached).
    def _pops(g):
        return pd.Series({
            "itm_rate": round((g["pe_itm"] | g["ce_itm"]).mean() * 100, 0),
            "pop_strangle": round((~(g["pe_itm"] | g["ce_itm"])).mean() * 100, 1),
            "pop_pe": round((~g["pe_itm"]).mean() * 100, 1),
            "pop_ce": round((~g["ce_itm"]).mean() * 100, 1),
        })
    pops = df.groupby(["inst", "regime", "dist_pct"]).apply(_pops).reset_index()
    summary = summary.drop(columns="itm_rate").merge(pops, on=["inst", "regime", "dist_pct"])
    summary.to_csv(OUT / "by_regime_distance.csv", index=False)

    # ─── lib lookup table: pop_table[inst][regime][dist] = {strangle, pe, ce, n} ───
    import json
    table = {}
    for _, r in summary.iterrows():
        table.setdefault(r["inst"], {}).setdefault(r["regime"], {})[str(r["dist_pct"])] = {
            "strangle": float(r["pop_strangle"]), "pe": float(r["pop_pe"]),
            "ce": float(r["pop_ce"]), "n": int(r["n"]),
        }
    # also pooled-across-regime (fallback when a regime bucket is empty/thin)
    allr = df.groupby(["inst", "dist_pct"]).apply(_pops).reset_index()
    alln = df.groupby(["inst", "dist_pct"]).size().reset_index(name="n")
    allr = allr.merge(alln, on=["inst", "dist_pct"])
    for _, r in allr.iterrows():
        table.setdefault(r["inst"], {}).setdefault("ALL", {})[str(r["dist_pct"])] = {
            "strangle": float(r["pop_strangle"]), "pe": float(r["pop_pe"]),
            "ce": float(r["pop_ce"]), "n": int(r["n"]),
        }
    (OUT / "pop_table.json").write_text(json.dumps(table, indent=1))
    print(f"\nwrote pop_table.json ({sum(len(v) for v in table.values())} inst×regime buckets)")

    # Print results
    print("\n" + "="*100)
    print("Tier 1 distance × regime: when is closer safe?")
    print("="*100)
    for inst in ["NIFTY", "SENSEX"]:
        for regime_name in ["calm_green", "normal", "moderate", "high_risk"]:
            sub = summary[(summary["inst"] == inst) & (summary["regime"] == regime_name)]
            if sub.empty: continue
            n_days = int(sub.iloc[0]["n"])
            print(f"\n{inst} @ {regime_name} regime ({n_days} days in sample):")
            print(f"  {'dist':>5} {'median prem':>12} {'win%':>6} {'ITM%':>6} {'median P&L':>12} {'p5 loss':>12} {'worst':>12}")
            for _, r in sub.iterrows():
                print(f"  {r['dist_pct']:>4}% ₹{r['median_premium_pcr']:>10,.0f}/Cr "
                      f"{r['win_rate']:>5}% {r['itm_rate']:>5}% "
                      f"₹{r['median_pnl_pcr']:>+10,.0f}/Cr ₹{r['p5_loss']:>+10,.0f}/Cr ₹{r['worst_pcr']:>+10,.0f}/Cr")

    return summary


if __name__ == "__main__":
    run()
