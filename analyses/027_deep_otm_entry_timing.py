"""
ANALYSIS 027 — Deep OTM E-0 entry-timing optimization (THE premium question)

Context (11-Jun live failure): Deep OTM sold 09:35-09:40 at 2.0-2.45/sh traded
40-80% HIGHER by 12:05 (vega/range expansion on a trend day). §9V already showed
premium peaks after 9:30 on 37-52% of days. 008/009's 'early beats late' was
NIFTY-only and averaged across days. This analysis settles it properly.

For each E-0 day × instrument × distance (2.0/2.25/2.5/3.0%) × entry time
(09:20→14:00 half-hourly):
  - capture = entry premium IF strikes expire worthless else premium − ITM penalty
  - strikes FIXED per entry time (re-anchored to spot at that time)
Strategies compared:
  A. FIXED-TIME entry (each time slot)
  B. TRANCHE: half 09:30 + half 11:30 (avg of captures, same-day strikes at each anchor)
  C. SPIKE-SELL: enter at first time ≥10:00 when combined quote ≥ 1.3× the 09:30
     quote for SAME strikes (sell into vega expansion); else enter 09:30.
Buckets: regime (gap/range/vix at 09:30) × instrument.
Outputs: results/027_deep_otm_entry_timing/
"""
from __future__ import annotations
from datetime import date, time
from pathlib import Path
import sys, os
import duckdb, numpy as np, pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "027_deep_otm_entry_timing"
OUT.mkdir(parents=True, exist_ok=True)
con = duckdb.connect()

ENTRY_TIMES = [time(9,20), time(9,30), time(10,0), time(10,30), time(11,0),
               time(11,30), time(12,0), time(12,30), time(13,0), time(14,0)]
DISTANCES = [2.0, 2.25, 2.5, 3.0]
CLOSE_T = time(15, 25)
CFG = {"NIFTY":  {"exp": set(NIFTY_WEEKLY_EXPIRIES),  "grid": 50,  "lot": 75, "lpc": 43},
       "SENSEX": {"exp": set(SENSEX_WEEKLY_EXPIRIES), "grid": 100, "lot": 20, "lpc": 40}}


def load_fut(inst):
    g = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    df = con.execute(f"""
        WITH r AS (SELECT timestamp, expiry, open, high, low, close,
                   ROW_NUMBER() OVER (PARTITION BY timestamp ORDER BY expiry) rn
                   FROM read_parquet('{g}', union_by_name=True) WHERE option_type='FUT')
        SELECT timestamp, open, high, low, close FROM r WHERE rn=1""").fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["date"] = df["timestamp"].dt.date; df["time"] = df["timestamp"].dt.time
    return df[df["timestamp"].dt.weekday < 5].sort_values("timestamp").reset_index(drop=True)


def load_day_options(inst, d, strikes):
    g = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    ss = ",".join(str(s) for s in strikes)
    df = con.execute(f"""
        SELECT CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIME) t,
               strike, option_type, close
        FROM read_parquet('{g}', union_by_name=True)
        WHERE option_type IN ('CE','PE') AND strike IN ({ss})
          AND expiry = DATE '{d.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
    """).fetchdf()
    return df


def vixp(fd, d):
    pr = fd[fd["date"] < d].tail(20)
    if len(pr) < 5: return 0
    r = np.log(pr["close"]/pr["close"].shift(1)).dropna()
    return float(r.std()*np.sqrt(252)*100)


def regime_of(gap, rng, vix):
    if abs(gap) > 0.7 or rng > 1.0 or vix > 18: return "high_risk"
    if abs(gap) > 0.4 or rng > 0.7 or vix > 15: return "moderate"
    if abs(gap) <= 0.3 and rng <= 0.5 and vix <= 13: return "calm_green"
    return "normal"


def q_at(opt_df, strike, side, t):
    m = opt_df[(opt_df["strike"]==strike) & (opt_df["option_type"]==side) & (opt_df["t"]>=t)]
    return float(m.sort_values("t").iloc[0]["close"]) if len(m) else None


def run():
    rows = []
    for inst, cfg in CFG.items():
        fut = load_fut(inst)
        fd = fut.groupby("date").agg(open=("open","first"), close=("close","last")).reset_index()
        fd["prev_close"] = fd["close"].shift(1)
        e0 = sorted([d for d in fut["date"].unique() if d in cfg["exp"]])
        print(f"[{inst}] {len(e0)} E-0 days")
        for di, d in enumerate(e0):
            day = fut[fut["date"]==d]
            if day.empty: continue
            cls = day[day["time"]<=CLOSE_T]
            if cls.empty: continue
            settle = float(cls.iloc[-1]["close"])
            o = float(day.iloc[0]["open"])
            row_fd = fd[fd["date"]==d]
            pc = float(row_fd.iloc[0]["prev_close"]) if not row_fd.empty and not np.isnan(row_fd.iloc[0]["prev_close"]) else o
            gap = (o-pc)/pc*100
            pre = day[day["time"] <= time(9,30)]
            rng930 = (pre["high"].max()-pre["low"].min())/o*100 if len(pre) else 0
            reg = regime_of(gap, rng930, vixp(fd, d))

            # strikes needed: anchored per entry time
            anchors = {}
            needed = set()
            for et in ENTRY_TIMES:
                ent = day[day["time"]>=et]
                if ent.empty: continue
                se = float(ent.iloc[0]["close"])
                anchors[et] = se
                for dist in DISTANCES:
                    needed.add(int(round(se*(1-dist/100)/cfg["grid"])*cfg["grid"]))
                    needed.add(int(round(se*(1+dist/100)/cfg["grid"])*cfg["grid"]))
            if not anchors: continue
            opt = load_day_options(inst, d, needed)
            if opt.empty: continue

            for dist in DISTANCES:
                # A: fixed times
                captures = {}
                for et, se in anchors.items():
                    pe_k = int(round(se*(1-dist/100)/cfg["grid"])*cfg["grid"])
                    ce_k = int(round(se*(1+dist/100)/cfg["grid"])*cfg["grid"])
                    pe = q_at(opt, pe_k, "PE", et); ce = q_at(opt, ce_k, "CE", et)
                    if pe is None or ce is None: continue
                    prem = pe+ce
                    pen = max(0, pe_k-settle) + max(0, settle-ce_k)
                    captures[et] = {"prem": prem, "capture": prem-pen, "breach": pen>0,
                                    "pe_k": pe_k, "ce_k": ce_k}
                    rows.append({"inst": inst, "date": d, "regime": reg, "dist": dist,
                                 "strategy": et.strftime("%H:%M"),
                                 "prem": round(prem,2), "capture_pcr": round((prem-pen)*cfg["lot"]*cfg["lpc"],0),
                                 "breach": pen>0})
                # B: tranche 09:30 + 11:30
                if time(9,30) in captures and time(11,30) in captures:
                    cap = (captures[time(9,30)]["capture"] + captures[time(11,30)]["capture"]) / 2
                    br = captures[time(9,30)]["breach"] or captures[time(11,30)]["breach"]
                    rows.append({"inst": inst, "date": d, "regime": reg, "dist": dist,
                                 "strategy": "TRANCHE", "prem": round((captures[time(9,30)]["prem"]+captures[time(11,30)]["prem"])/2,2),
                                 "capture_pcr": round(cap*cfg["lot"]*cfg["lpc"],0), "breach": br})
                # C: spike-sell — same 09:30 strikes, enter when quote >= 1.3x 09:30 quote
                if time(9,30) in captures:
                    base = captures[time(9,30)]
                    fired = None
                    for et in ENTRY_TIMES:
                        if et < time(10,0): continue
                        pe = q_at(opt, base["pe_k"], "PE", et); ce = q_at(opt, base["ce_k"], "CE", et)
                        if pe is None or ce is None: continue
                        if pe+ce >= 1.3*base["prem"]:
                            fired = (et, pe+ce); break
                    if fired:
                        prem = fired[1]
                        pen = max(0, base["pe_k"]-settle) + max(0, settle-base["ce_k"])
                        rows.append({"inst": inst, "date": d, "regime": reg, "dist": dist,
                                     "strategy": "SPIKE_SELL", "prem": round(prem,2),
                                     "capture_pcr": round((prem-pen)*cfg["lot"]*cfg["lpc"],0), "breach": pen>0})
                    else:
                        rows.append({"inst": inst, "date": d, "regime": reg, "dist": dist,
                                     "strategy": "SPIKE_SELL", "prem": base["prem"],
                                     "capture_pcr": round(base["capture"]*cfg["lot"]*cfg["lpc"],0), "breach": base["breach"]})
            if (di+1)%15==0: print(f"  {di+1}/{len(e0)}")

    df = pd.DataFrame(rows)
    df.to_csv(OUT/"per_day.csv", index=False)
    g = df.groupby(["inst","regime","dist","strategy"]).agg(
        n=("date","count"), median_capture=("capture_pcr","median"),
        mean_capture=("capture_pcr","mean"), breach_pct=("breach", lambda x: round(x.mean()*100,1)),
        worst=("capture_pcr","min")).reset_index()
    g.to_csv(OUT/"summary.csv", index=False)
    print("\nDone:", len(df), "rows")

if __name__ == "__main__":
    run()
