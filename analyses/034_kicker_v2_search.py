"""
ANALYSIS 034 — Kicker v2: open search (successor to 033's narrow recipe)

033's recipe (fixed dist + range<=0.6 gate) only trades ~1 day in 7. Goal here:
a kicker that trades MOST days. Search freely:
  entries  12:00 / 12:30 / 13:00 / 13:30 / 14:00
  strikes  FIXED dist {0.3..1.25%}  AND  ADAPTIVE dist = clamp(k x range-so-far, 0.3..1.5)
           (volatile day -> automatically wider strikes, instead of skipping the day)
  exits    TP capture {30,40,50}% x SL {1.5x, 2x} x time-stop {90 min, hold to 15:20}
  SL fills at the actual minute close (slippage-honest, unlike 033's at-level fill).
Hard constraint kept: worst day >= -Rs350k/Cr (the locked risk cap). Rank by mean EV
among frequent variants. Per instrument. Friction Rs35/lot RT.

Output: results/034_kicker_v2/{per_day.csv, by_variant.csv}
"""
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
import sys, os
import duckdb, numpy as np, pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "034_kicker_v2"; OUT.mkdir(parents=True, exist_ok=True)
con = duckdb.connect()

CFG = {"NIFTY":  {"grid": 50,  "lot": 75, "lpc": 43, "exp": set(NIFTY_WEEKLY_EXPIRIES)},
       "SENSEX": {"grid": 100, "lot": 20, "lpc": 40, "exp": set(SENSEX_WEEKLY_EXPIRIES)}}
ENTRIES = ["12:00", "12:30", "13:00", "13:30", "14:00"]
FIXED = [0.3, 0.4, 0.6, 0.8, 1.0, 1.25]
ADAPT_K = [0.75, 1.0, 1.25]            # dist = clamp(k*range_so_far, 0.3, 1.5)
CAPS = [0.30, 0.40, 0.50]
SLS = [1.5, 2.0]
TSTOPS = [90, 999]                      # 999 = hold to LAST_EXIT
FRICTION_LOT = 35
LAST_EXIT = "15:20"
CAP_WORST = -350_000                    # locked risk rule


def load_day(inst, d):
    p = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    spot = con.execute(f"""SELECT strftime(timestamp,'%H:%M') t, high, low, close
        FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type IN ('SPOT','FUT') AND CAST(timestamp AS DATE)=DATE '{d.isoformat()}'
        ORDER BY timestamp""").df()
    if spot.empty:
        return None, None
    spot = spot.drop_duplicates("t", keep="first")
    opt = con.execute(f"""SELECT strftime(timestamp,'%H:%M') t, strike, option_type side, close
        FROM read_parquet('{p}', union_by_name=True)
        WHERE expiry=DATE '{d.isoformat()}' AND CAST(timestamp AS DATE)=DATE '{d.isoformat()}'
          AND option_type IN ('CE','PE') AND strftime(timestamp,'%H:%M') >= '11:55'
        ORDER BY timestamp""").df()
    if opt.empty:
        return None, None
    return spot, opt.drop_duplicates(["t", "strike", "side"], keep="first")


def mins_after(hm, mins):
    t0 = datetime(2000, 1, 1, int(hm[:2]), int(hm[3:]))
    return (t0 + timedelta(minutes=mins)).strftime("%H:%M")


def sim_path(m, entry, cap, slx, deadline):
    """Walk the combined-premium series. Returns (exit_price, how)."""
    tp = entry * (1 - cap)
    sl = entry * slx
    for _, r in m.iloc[1:].iterrows():
        if r.t > deadline:
            return r.comb, "time"
        if r.comb <= tp:
            return tp, "tp"
        if r.comb >= sl:
            return r.comb, "sl"       # honest: fill at the minute close, not the level
    return m.comb.iloc[-1], "time"


rows = []
for inst, c in CFG.items():
    p = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    have = con.execute(f"SELECT DISTINCT CAST(timestamp AS DATE) d FROM read_parquet('{p}', union_by_name=True)").df()
    days = sorted(d for d in (pd.Timestamp(x).date() for x in have.d) if d in c["exp"])
    print(f"{inst}: {len(days)} E-0 days")
    for i, d in enumerate(days):
        spot, opt = load_day(inst, d)
        if spot is None:
            continue
        for E in ENTRIES:
            pre = spot[spot.t <= E]
            post = spot[spot.t >= E]
            if pre.empty or post.empty:
                continue
            openp = pre.close.iloc[0]
            rng = (pre.high.max() - pre.low.min()) / openp * 100
            spot_e = post.close.iloc[0]
            dists = [("f%.2f" % f, f) for f in FIXED] + \
                    [("a%.2fx" % k, max(0.3, min(1.5, k * rng))) for k in ADAPT_K]
            for dmode, dist in dists:
                pe_k = round(spot_e * (1 - dist / 100) / c["grid"]) * c["grid"]
                ce_k = round(spot_e * (1 + dist / 100) / c["grid"]) * c["grid"]
                pe = opt[(opt.strike == pe_k) & (opt.side == "PE") & (opt.t >= E)][["t", "close"]]
                ce = opt[(opt.strike == ce_k) & (opt.side == "CE") & (opt.t >= E)][["t", "close"]]
                m = pe.merge(ce, on="t", suffixes=("_pe", "_ce")).sort_values("t")
                if len(m) < 5:
                    continue
                m["comb"] = m.close_pe + m.close_ce
                entry = m.comb.iloc[0]
                if entry <= 0:
                    continue
                for cap in CAPS:
                    for slx in SLS:
                        for ts in TSTOPS:
                            deadline = LAST_EXIT if ts == 999 else min(mins_after(E, ts), LAST_EXIT)
                            exit_p, how = sim_path(m, entry, cap, slx, deadline)
                            pnl = (entry - exit_p) * c["lot"] * c["lpc"] - c["lpc"] * FRICTION_LOT
                            rows.append({"inst": inst, "date": d, "entry": E, "dmode": dmode,
                                         "dist": round(dist, 2), "cap": int(cap * 100),
                                         "sl": slx, "tstop": ts, "rng": round(rng, 2),
                                         "prem": round(entry, 2), "how": how, "pnl_pcr": round(pnl)})
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(days)}")

df = pd.DataFrame(rows)
df.to_csv(OUT / "per_day.csv", index=False)
print(f"\n{len(df)} sims")

g = df.groupby(["inst", "entry", "dmode", "cap", "sl", "tstop"]).agg(
    n=("pnl_pcr", "size"), mean_pcr=("pnl_pcr", "mean"),
    win=("pnl_pcr", lambda x: round((x > 0).mean() * 100)),
    tp=("how", lambda x: round((x == "tp").mean() * 100)),
    slh=("how", lambda x: round((x == "sl").mean() * 100)),
    med=("pnl_pcr", "median"), p5=("pnl_pcr", lambda x: round(np.percentile(x, 5))),
    worst=("pnl_pcr", "min"), prem=("prem", "median"),
).reset_index()
g["mean_pcr"] = g.mean_pcr.round(0)
g["total_ev"] = (g.mean_pcr * g.n).round(0)          # EV across the whole sample = freq x edge
g.to_csv(OUT / "by_variant.csv", index=False)

for inst in CFG:
    sub = g[(g.inst == inst) & (g.worst >= CAP_WORST) & (g.n >= 45) & (g.win >= 70)]
    sub = sub.sort_values("total_ev", ascending=False)
    print(f"\n=== {inst} — FREQUENT (n>=45/~60 days) + within -3.5L/Cr cap + win>=70, by total EV ===")
    print(sub.head(12).to_string(index=False))
