"""
029_late_session_spikes.py — how often does the spot make a sudden large move,
especially the last 30 min into close (today: NIFTY −100pts at 15:00, closed
−1.15%, pushing the 23850 PE ITM 0.8→25)? Split expiry vs non-expiry, since
expiry-day late moves are structural (gamma/settlement), not "out of nowhere".

Pulls ~1.5y of 15-min index candles from Kite. Output → results/029_late_session_spikes/.
"""
from __future__ import annotations
import sys, os, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd
from lib.kite_live import _kite
from lib.expiry_calendar import is_e0

OUT = Path(__file__).resolve().parent.parent / "results" / "029_late_session_spikes"
OUT.mkdir(parents=True, exist_ok=True)
TOKENS = {"NIFTY": 256265, "SENSEX": 265}   # 265 = BSE SENSEX


def pull(token, days=420):
    k = _kite()
    end = dt.datetime.now(); out = []
    cur = end
    while (end - cur).days < days:
        frm = cur - dt.timedelta(days=190)
        try:
            out += k.historical_data(token, frm, cur, "15minute")
        except Exception as e:
            print("  pull err", e); break
        cur = frm - dt.timedelta(days=1)
    df = pd.DataFrame(out).drop_duplicates("date")
    if not len(df):
        return df
    df["dt"] = pd.to_datetime(df["date"]); df["d"] = df["dt"].dt.date
    df["hm"] = df["dt"].dt.strftime("%H:%M")
    return df.sort_values("dt")


def analyse(name, token):
    df = pull(token)
    if not len(df):
        print(f"{name}: no data"); return None
    rows = []
    for d, g in df.groupby("d"):
        g = g.sort_values("dt")
        op = g.iloc[0]["open"]; cl = g.iloc[-1]["close"]
        p1500 = g[g["hm"] >= "15:00"]["open"]
        last30 = (cl - p1500.iloc[0]) / p1500.iloc[0] * 100 if len(p1500) else 0.0
        # biggest single 15-min candle move of the day, and in the last hour
        g = g.assign(mv=(g["close"] - g["open"]) / g["open"] * 100)
        big = g["mv"].abs().max()
        lasthr = g[g["hm"] >= "14:30"]["mv"].abs().max() if len(g[g["hm"] >= "14:30"]) else 0
        rows.append({"d": d, "expiry": is_e0(d, name), "day_pct": (cl - op) / op * 100,
                     "last30_pct": last30, "max_candle_pct": big, "lasthr_candle_pct": lasthr})
    r = pd.DataFrame(rows)
    r.to_csv(OUT / f"{name}_daily.csv", index=False)
    n = len(r)
    def freq(col, thr):
        return (r[col].abs() >= thr).sum(), (r[r["expiry"]][col].abs() >= thr).sum()
    print(f"\n{name}: {n} days ({r['expiry'].sum()} expiry)")
    print(f"  last-30-min move ≥0.5% : {freq('last30_pct',0.5)[0]:>3} days ({freq('last30_pct',0.5)[0]/n*100:.0f}%) · of which expiry {freq('last30_pct',0.5)[1]}")
    print(f"  last-30-min move ≥0.75%: {freq('last30_pct',0.75)[0]:>3} days ({freq('last30_pct',0.75)[0]/n*100:.0f}%) · expiry {freq('last30_pct',0.75)[1]}")
    print(f"  last-30-min move ≥1.0% : {freq('last30_pct',1.0)[0]:>3} days ({freq('last30_pct',1.0)[0]/n*100:.0f}%) · expiry {freq('last30_pct',1.0)[1]}")
    print(f"  single 15-min candle ≥0.5%: {freq('max_candle_pct',0.5)[0]} days · in last hour ≥0.5%: {(r['lasthr_candle_pct']>=0.5).sum()}")
    print(f"  single 15-min candle ≥0.75%: {freq('max_candle_pct',0.75)[0]} days")
    # worst late-session days
    print("  worst last-30-min moves:")
    for _, x in r.reindex(r["last30_pct"].abs().sort_values(ascending=False).index).head(6).iterrows():
        print(f"    {x['d']} {'E' if x['expiry'] else ' '} last30 {x['last30_pct']:+.2f}%  day {x['day_pct']:+.2f}%")
    return r


if __name__ == "__main__":
    for nm, tk in TOKENS.items():
        try:
            analyse(nm, tk)
        except Exception as e:
            print(f"{nm}: failed {e}")
