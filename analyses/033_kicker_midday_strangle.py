"""
ANALYSIS 033 — "Kicker" midday near-OTM strangle (Rohan's new S4 sub-strategy)

The trade (as executed live): on E-0, between ~12:00-13:00, IF the day is range-bound,
sell a CLOSE strangle (0.4-1.0% OTM, fat premium ~Rs20/leg on NIFTY), take just 20-30%
of the premium and square off — 30-90 min hold. Discipline is the edge: he lost once
by NOT exiting at TP and the market ended under the strike.

Simulates every E-0 day, minute-level:
  entry E in {12:00,12:30,13:00} x dist in {0.4,0.6,0.8,1.0}% x TP-capture in {20,30}%
  exit = TP (combined premium <= entry*(1-cap)) OR SL (combined >= entry*2) OR
         time-stop (90 min) — whichever first. Friction Rs35/lot round-trip.
  Range-bound filter: range-from-open->entry <= 0.6% (reported with and without).

Output: results/033_kicker/{per_day.csv, by_variant.csv}
"""
from __future__ import annotations
from datetime import date, time, datetime, timedelta
from pathlib import Path
import sys, os
import duckdb, numpy as np, pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "033_kicker"; OUT.mkdir(parents=True, exist_ok=True)
con = duckdb.connect()

CFG = {"NIFTY":  {"grid": 50,  "lot": 75, "lpc": 43, "exp": set(NIFTY_WEEKLY_EXPIRIES)},
       "SENSEX": {"grid": 100, "lot": 20, "lpc": 40, "exp": set(SENSEX_WEEKLY_EXPIRIES)}}
ENTRIES = ["12:00", "12:30", "13:00"]
DISTS = [0.4, 0.6, 0.8, 1.0]
CAPS = [0.20, 0.30]
SL_MULT = 2.0
TSTOP_MIN = 90
RANGE_MAX = 0.6          # "relatively range-bound" gate at entry
FRICTION_LOT = 35        # round-trip Rs/lot (both legs executed both sides)
LAST_EXIT = "15:20"


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
    opt = opt.drop_duplicates(["t", "strike", "side"], keep="first")
    return spot, opt


def minutes_after(hm, mins):
    t0 = datetime(2000, 1, 1, int(hm[:2]), int(hm[3:]))
    return (t0 + timedelta(minutes=mins)).strftime("%H:%M")


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
            deadline = min(minutes_after(E, TSTOP_MIN), LAST_EXIT)
            for dist in DISTS:
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
                    tp_level = entry * (1 - cap)
                    sl_level = entry * SL_MULT
                    exit_p, how = None, None
                    for _, r in m.iloc[1:].iterrows():
                        if r.t > deadline:
                            exit_p, how = r.comb, "time"
                            break
                        if r.comb <= tp_level:
                            exit_p, how = tp_level, "tp"
                            break
                        if r.comb >= sl_level:
                            exit_p, how = sl_level, "sl"
                            break
                    if exit_p is None:
                        exit_p, how = m.comb.iloc[-1], "time"
                    pnl_share = entry - exit_p
                    pnl_pcr = pnl_share * c["lot"] * c["lpc"] - c["lpc"] * FRICTION_LOT
                    rows.append({"inst": inst, "date": d, "entry": E, "dist": dist,
                                 "cap": int(cap * 100), "rng_at_entry": round(rng, 2),
                                 "entry_comb": round(entry, 2), "exit_comb": round(exit_p, 2),
                                 "how": how, "pnl_pcr": round(pnl_pcr)})
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(days)}")

df = pd.DataFrame(rows)
df.to_csv(OUT / "per_day.csv", index=False)
print(f"\n{len(df)} sims")

# ── summarise: range-bound filtered (the actual strategy) + unfiltered for contrast ──
def summarise(sub, tag):
    g = sub.groupby(["inst", "entry", "dist", "cap"]).agg(
        n=("pnl_pcr", "size"), mean_pcr=("pnl_pcr", "mean"),
        win_pct=("pnl_pcr", lambda x: round((x > 0).mean() * 100)),
        tp_pct=("how", lambda x: round((x == "tp").mean() * 100)),
        sl_pct=("how", lambda x: round((x == "sl").mean() * 100)),
        med=("pnl_pcr", "median"), p5=("pnl_pcr", lambda x: round(np.percentile(x, 5))),
        worst=("pnl_pcr", "min"), entry_prem=("entry_comb", "median"),
    ).reset_index()
    g["mean_pcr"] = g.mean_pcr.round(0)
    g["filter"] = tag
    return g

filt = summarise(df[df.rng_at_entry <= RANGE_MAX], f"rng<={RANGE_MAX}")
unf = summarise(df, "all-days")
res = pd.concat([filt, unf]).sort_values("mean_pcr", ascending=False)
res.to_csv(OUT / "by_variant.csv", index=False)

print(f"\n=== KICKER — range-bound days only (rng<= {RANGE_MAX}%), per Cr after Rs{FRICTION_LOT}/lot ===")
show = filt.sort_values("mean_pcr", ascending=False)
print(show.head(18).to_string(index=False))
print("\n=== all days (no filter) — does the range gate matter? ===")
print(unf.sort_values("mean_pcr", ascending=False).head(8).to_string(index=False))
best = show.iloc[0]
print(f"\nBEST (range-bound): {best.inst} {best.entry} @ {best.dist}% cap {best.cap}% → "
      f"mean ₹{best.mean_pcr:,.0f}/Cr, win {best.win_pct}%, TP-hit {best.tp_pct}%, worst ₹{best.worst:,}")
