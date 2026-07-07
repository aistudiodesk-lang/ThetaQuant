"""
ANALYSIS 032 — Harvest definitive EV (per-day, full P&L, after brokerage)

Settles: is the lottery-harvest +EV or scrap it? (failed live 3×). Unlike 014 (which
catalogued spike EVENTS), this simulates EVERY E-0 day end-to-end:
  • 14:00 entry, buy a ₹10k basket of deep-OTM strikes in a premium BAND (both sides)
  • each strike: sell at entry×SELL_MULT if its intraday high reaches it, else exit at
    the day's close (≈0 for worthless deep OTM)
  • net per day after round-trip brokerage; aggregate mean EV, win-rate, worst day.
Sweeps BAND × SELL_MULT to find any +EV variant.
Output: results/032_harvest_ev/by_variant.csv
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path
import sys, os
import duckdb, numpy as np, pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "032_harvest_ev"; OUT.mkdir(parents=True, exist_ok=True)
con = duckdb.connect()

CFG = {"NIFTY": {"lot": 75, "exp": set(NIFTY_WEEKLY_EXPIRIES)},
       "SENSEX": {"lot": 20, "exp": set(SENSEX_WEEKLY_EXPIRIES)}}
BUDGET = 10_000
N_LEGS = 4                      # ~2 CE + 2 PE
BUY_COST_PER_LOT = 7           # buy-side brokerage+GST (Axis ₹6 + GST); paid on EVERY leg
SELL_COST_PER_LOT = 7          # sell-side, only when the limit fills (winners)
BANDS = [(0.10, 0.25), (0.15, 0.40), (0.20, 0.50), (0.05, 0.15)]
SELL_MULTS = [4, 5, 6, 8, 12]


def day_strikes(inst, d):
    """Per band-eligible strike on day d: entry(14:00 close), peak(max high 14:00→close),
    exit(last close). Returns DataFrame[strike, side, entry, peak, exit, oi]."""
    p = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    spot = con.execute(f"""SELECT close FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type IN ('SPOT','FUT') AND CAST(timestamp AS DATE)=DATE '{d.isoformat()}'
        AND strftime(timestamp,'%H:%M') >= '14:00'
        ORDER BY timestamp LIMIT 1""").df()
    if spot.empty:
        return None
    df = con.execute(f"""
        SELECT strike, option_type AS side,
               arg_min(close, timestamp) AS entry,
               max(high) AS peak,
               arg_max(close, timestamp) AS exitc,
               max(oi) AS oi
        FROM read_parquet('{p}', union_by_name=True)
        WHERE expiry=DATE '{d.isoformat()}' AND CAST(timestamp AS DATE)=DATE '{d.isoformat()}'
          AND option_type IN ('CE','PE')
          AND strftime(timestamp,'%H:%M') >= '14:00'
        GROUP BY strike, option_type""").df()
    return df if not df.empty else None


def sim_day(df, lot, band, sell_mult):
    """Build the ₹BUDGET basket from band strikes (top OI, both sides), return net P&L."""
    lo, hi = band
    pool = df[(df.entry >= lo) & (df.entry <= hi) & (df.oi > 0)].copy()
    if pool.empty:
        return None
    pool = pool.sort_values("oi", ascending=False)
    ce = pool[pool.side == "CE"].head(N_LEGS // 2)
    pe = pool[pool.side == "PE"].head(N_LEGS // 2)
    legs = pd.concat([ce, pe])
    if legs.empty:
        return None
    per = BUDGET / len(legs)
    net = 0.0
    for _, r in legs.iterrows():
        lots = int(per // (r.entry * lot))
        if lots < 1:
            continue
        target = r.entry * sell_mult
        filled = r.peak >= target
        exitp = target if filled else r.exitc                 # filled at limit, else exit at close
        brok = lots * BUY_COST_PER_LOT + (lots * SELL_COST_PER_LOT if filled else 0)
        net += lots * lot * (exitp - r.entry) - brok
    return round(net)


# ── run ──
e0_days = {}
for inst, c in CFG.items():
    p = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    have = con.execute(f"SELECT DISTINCT CAST(timestamp AS DATE) d FROM read_parquet('{p}', union_by_name=True)").df()
    days = [pd.Timestamp(x).date() for x in have.d]
    e0_days[inst] = sorted([d for d in days if d in c["exp"]])
    print(f"{inst}: {len(e0_days[inst])} E-0 days in parquet")

# cache per-day strike frames once
cache = {}
for inst in CFG:
    for d in e0_days[inst]:
        df = day_strikes(inst, d)
        if df is not None:
            cache[(inst, d)] = df

rows = []
for band in BANDS:
    for sm in SELL_MULTS:
        nets = []
        for (inst, d), df in cache.items():
            r = sim_day(df, CFG[inst]["lot"], band, sm)
            if r is not None:
                nets.append(r)
        if not nets:
            continue
        a = np.array(nets)
        rows.append({"band": f"{band[0]}-{band[1]}", "sell_mult": sm, "days": len(a),
                     "mean_net": round(a.mean()), "win_pct": round((a > 0).mean() * 100),
                     "median": round(np.median(a)), "worst": int(a.min()), "best": int(a.max()),
                     "p25": round(np.percentile(a, 25))})

res = pd.DataFrame(rows).sort_values("mean_net", ascending=False)
res.to_csv(OUT / "by_variant.csv", index=False)
print(f"\n=== Harvest ₹{BUDGET:,} basket — net P&L per E-0 day, after brokerage ===")
print(res.to_string(index=False))
best = res.iloc[0]
print(f"\nBEST variant: band {best['band']} @ {best['sell_mult']}× → mean ₹{best['mean_net']:,}/day, "
      f"{best['win_pct']}% win, worst ₹{best['worst']:,}")
print("VERDICT:", "VIABLE (+EV)" if best["mean_net"] > 0 else "SCRAP — no variant is +EV after costs")
