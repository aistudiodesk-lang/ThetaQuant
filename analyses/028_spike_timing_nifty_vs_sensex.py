"""
ANALYSIS 028 — WHEN do deep-OTM premiums spike intraday: NIFTY vs SENSEX

User hypothesis (11-Jun): "NIFTY very rarely spikes randomly after 10:00,
sometimes even after 12:00. But SENSEX very frequently does."

Method: every E-0 day, anchor Tier-1 strikes at 09:30 spot (2.0% OTM both
sides, grid-rounded away). Reference = leg premium at 09:30. Track minute path
to 15:25. SPIKE = leg premium >= 1.3x its 09:30 quote (ref >= 0.75/sh to kill
tick noise). For each spike: first-spike time, peak ratio + time, cause
(TREND = spot moved >=0.35% toward that strike by spike time, else QUIET =
vol/flow spike with spot <0.20%, else MIXED), fade (back <=1.1x before 15:00),
breach at settlement. Buckets: instrument x hour x regime.
Outputs: results/028_spike_timing_nifty_vs_sensex/
"""
from __future__ import annotations
from datetime import time
from pathlib import Path
import sys, os
import duckdb, numpy as np, pandas as pd

ROOT = Path(os.environ.get("BACKTEST_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "028_spike_timing_nifty_vs_sensex"
OUT.mkdir(parents=True, exist_ok=True)
con = duckdb.connect()

DIST = 2.0
SPIKE_R, BIG_R, MIN_REF = 1.3, 1.5, 0.75
T0 = time(9, 30)
CFG = {"NIFTY":  {"exp": set(NIFTY_WEEKLY_EXPIRIES),  "grid": 50},
       "SENSEX": {"exp": set(SENSEX_WEEKLY_EXPIRIES), "grid": 100}}


def load_fut(inst):
    g = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    df = con.execute(f"""
        WITH r AS (SELECT timestamp, expiry, open, high, low, close,
                   ROW_NUMBER() OVER (PARTITION BY timestamp ORDER BY expiry) rn
                   FROM read_parquet('{g}', union_by_name=True) WHERE option_type='FUT')
        SELECT timestamp, open, high, low, close FROM r WHERE rn=1""").fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.floor("min")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    return df[df["timestamp"].dt.weekday < 5].sort_values("timestamp").reset_index(drop=True)


def regime_of(gap, rng, vix):
    if abs(gap) > 0.7 or rng > 1.0 or vix > 18: return "high_risk"
    if abs(gap) > 0.4 or rng > 0.7 or vix > 15: return "moderate"
    if abs(gap) <= 0.3 and rng <= 0.5 and vix <= 13: return "calm_green"
    return "normal"


def vixp(fd, d):
    pr = fd[fd["date"] < d].groupby("date")["close"].last().tail(20)
    if len(pr) < 5: return 0
    r = np.log(pr / pr.shift(1)).dropna()
    return float(r.std() * np.sqrt(252) * 100)


events, day_rows = [], []
for inst, cfg in CFG.items():
    fd = load_fut(inst)
    days = sorted(d for d in fd["date"].unique() if d in cfg["exp"])
    # anchor strikes per day from 09:30 spot
    anchors = {}
    for d in days:
        dd = fd[fd["date"] == d]
        ref = dd[dd["time"] >= T0].head(3)
        if ref.empty: continue
        spot0 = float(ref["close"].iloc[0])
        g = cfg["grid"]
        ce_k = int(np.ceil(spot0 * (1 + DIST/100) / g) * g)
        pe_k = int(np.floor(spot0 * (1 - DIST/100) / g) * g)
        op = dd[dd["time"] < T0]
        prev = fd[fd["date"] < d]
        prev_close = float(prev["close"].iloc[-1]) if len(prev) else spot0
        day_open = float(dd["open"].iloc[0])
        gap = (day_open - prev_close) / prev_close * 100
        rng = (op["high"].max() - op["low"].min()) / day_open * 100 if len(op) else 0
        anchors[d] = (spot0, ce_k, pe_k, regime_of(gap, rng, vixp(fd, d)))
    if not anchors: continue

    # ONE bulk query: all (day, strike) pairs
    conds = " OR ".join(
        f"(expiry = DATE '{d.isoformat()}' AND strike IN ({ce},{pe}))"
        for d, (_, ce, pe, _) in anchors.items())
    g = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    odf = con.execute(f"""
        SELECT CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) d,
               CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS TIME) t,
               strike, option_type ot, close
        FROM read_parquet('{g}', union_by_name=True)
        WHERE option_type IN ('CE','PE') AND ({conds})
          AND expiry = CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE)
    """).fetchdf()
    odf["t"] = odf["t"].astype(str).str.slice(0, 5)
    odf["d"] = pd.to_datetime(odf["d"]).dt.date

    for d, (spot0, ce_k, pe_k, reg) in anchors.items():
        dd = odf[odf["d"] == d]
        fut_day = fd[fd["date"] == d].copy()
        fut_day["tm"] = fut_day["time"].astype(str).str.slice(0, 5)
        fut_d = fut_day.groupby("tm")["close"].last()
        spot_close = float(fut_d.iloc[-1])
        day_spiked = {"CE": False, "PE": False}
        for side, k in (("CE", ce_k), ("PE", pe_k)):
            leg = dd[(dd["strike"] == k) & (dd["ot"] == side)].sort_values("t")
            ref_rows = leg[leg["t"] >= "09:30"]
            if ref_rows.empty: continue
            ref = float(ref_rows["close"].iloc[0])
            if ref < MIN_REF: continue
            path = leg[(leg["t"] > "09:31") & (leg["t"] <= "15:25")]
            if path.empty: continue
            r = path["close"].values / ref
            ts = path["t"].values
            spike_idx = np.argmax(r >= SPIKE_R) if (r >= SPIKE_R).any() else -1
            breached = (spot_close >= k) if side == "CE" else (spot_close <= k)
            if spike_idx < 0:
                events.append(dict(inst=inst, date=str(d), side=side, regime=reg,
                                   spiked=0, breached=int(breached)))
                continue
            day_spiked[side] = True
            st = ts[spike_idx]
            spot_at = float(fut_d.get(st, fut_d.iloc[-1]))
            move = (spot_at - spot0) / spot0 * 100          # signed
            toward = move if side == "CE" else -move        # + = toward strike
            cause = "TREND" if toward >= 0.35 else ("QUIET" if abs(move) <= 0.20 else "MIXED")
            after = r[spike_idx:]
            after_ts = ts[spike_idx:]
            peak_i = int(np.argmax(after))
            faded = bool((after[(after_ts >= after_ts[peak_i]) & (after_ts <= "15:00")] <= 1.1).any())
            events.append(dict(inst=inst, date=str(d), side=side, regime=reg,
                               spiked=1, spike_t=st, peak_r=round(float(after.max()), 2),
                               peak_t=after_ts[peak_i], big=int(after.max() >= BIG_R),
                               cause=cause, faded=int(faded), breached=int(breached),
                               ref=ref))
        day_rows.append(dict(inst=inst, date=str(d), regime=reg,
                             any_spike=int(day_spiked["CE"] or day_spiked["PE"])))

ev = pd.DataFrame(events)
dy = pd.DataFrame(day_rows)
ev.to_csv(OUT / "spike_events.csv", index=False)
dy.to_csv(OUT / "per_day.csv", index=False)

sp = ev[ev["spiked"] == 1].copy()
sp["hour"] = sp["spike_t"].str.slice(0, 2).astype(int)

print("=== days with ANY 1.3x spike (Tier-1 strikes anchored 09:30) ===")
print(dy.groupby("inst")["any_spike"].agg(["mean", "count"]).round(3))

print("\n=== first-spike START time distribution (% of all spikes) ===")
tt = sp.groupby(["inst", pd.cut(sp["hour"], [9, 10, 11, 12, 13, 16],
                labels=["09:3x", "10-11", "11-12", "12-13", "13+"])], observed=True
                ).size().unstack(fill_value=0)
print((tt.div(tt.sum(axis=1), axis=0) * 100).round(1))

print("\n=== spikes STARTING after cutoff, as % of trading days ===")
n_days = dy.groupby("inst")["date"].nunique()
for cut in ("10:00", "11:00", "12:00", "13:00"):
    late = sp[sp["spike_t"] >= cut].groupby("inst")["date"].nunique()
    print(cut, ((late / n_days) * 100).round(1).to_dict())

print("\n=== cause split (of spikes) ===")
print(sp.groupby(["inst", "cause"]).size().unstack(fill_value=0))

print("\n=== outcome: faded vs breached (of spikes) ===")
print(sp.groupby("inst")[["faded", "breached", "big"]].mean().round(3))

print("\n=== by regime: spike day-rate ===")
print(dy.groupby(["inst", "regime"])["any_spike"].agg(["mean", "count"]).round(2))

sp["mins"] = sp["spike_t"].str.slice(0, 2).astype(int) * 60 + sp["spike_t"].str.slice(3, 5).astype(int)
q = sp.groupby("inst")["mins"].quantile([0.5, 0.9]).unstack()
q = q.map(lambda m: f"{int(m)//60:02d}:{int(m)%60:02d}")
q.columns = ["median_t", "p90_t"]
print("\n=== first-spike time: median / p90 ===")
print(q)
