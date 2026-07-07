"""
Export 5-min candles for every strike traded today (from the journal),
plus SENSEX spot and India VIX. → results/exports/trade_strikes_5min_<date>.csv

Usage: python3 scripts/export_trade_candles.py [YYYY-MM-DD]
"""
from __future__ import annotations
from datetime import datetime, date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pandas as pd
from lib.kite_historical import RateLimitedKite, IST
from lib import journal

d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(IST).date()
start = datetime(d.year, d.month, d.day, 9, 15)
end = datetime(d.year, d.month, d.day, 15, 30)

# strikes traded that day, from the journal (entry legs + booked legs)
want = {}   # (instrument, strike, side)
for t in journal.all_trades():
    if t.get("entry_date") != d.isoformat():
        continue
    inst = t["instrument"]
    for l in (t.get("legs") or []):
        if l.get("strike"):
            want[(inst, int(l["strike"]), l.get("side"))] = True
    for b in (t.get("booked_legs") or []):
        if b.get("strike"):
            want[(inst, int(b["strike"]), b.get("side"))] = True
if not want:
    sys.exit(f"no journal trades on {d}")

rk = RateLimitedKite()
frames = []

def fetch(token, label):
    bars = rk.historical(token, start, end, interval="5minute")
    df = pd.DataFrame(bars)
    if df.empty:
        print(f"  !! no data: {label}")
        return
    df["symbol"] = label
    frames.append(df[["date", "symbol", "open", "high", "low", "close", "volume"]]
                  if "volume" in df else df[["date", "symbol", "open", "high", "low", "close"]])

# option strikes (BFO for SENSEX, NFO for NIFTY)
by_exch = {}
for (inst, strike, side) in want:
    by_exch.setdefault("BFO" if inst == "SENSEX" else "NFO", []).append((inst, strike, side))
for exch, items in by_exch.items():
    idf = pd.DataFrame(rk.instruments(exch))
    idf["expiry"] = pd.to_datetime(idf["expiry"]).dt.date
    for inst, strike, side in sorted(items, key=lambda x: (x[2], x[1])):
        row = idf[(idf["name"] == inst) & (idf["strike"] == strike) &
                  (idf["instrument_type"] == side) & (idf["expiry"] == d)]
        if row.empty:
            print(f"  !! not in {exch} dump: {inst} {strike} {side} exp {d}")
            continue
        fetch(int(row["instrument_token"].iloc[0]), f"{inst} {strike} {side}")

# index + vix
bse = pd.DataFrame(rk.instruments("BSE"))
nse = pd.DataFrame(rk.instruments("NSE"))
sx = bse[bse["tradingsymbol"] == "SENSEX"]
vx = nse[nse["tradingsymbol"] == "INDIA VIX"]
if len(sx): fetch(int(sx["instrument_token"].iloc[0]), "SENSEX")
if len(vx): fetch(int(vx["instrument_token"].iloc[0]), "INDIA VIX")

out = pd.concat(frames, ignore_index=True)
out = out.rename(columns={"date": "time"})
out["time"] = pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%d %H:%M")
out = out.sort_values(["symbol", "time"])
dest = ROOT / "results" / "exports"
dest.mkdir(parents=True, exist_ok=True)
fp = dest / f"trade_strikes_5min_{d.isoformat()}.csv"
out.to_csv(fp, index=False)
print(f"wrote {fp} · {len(out)} rows · {out['symbol'].nunique()} series:")
print(" ", " | ".join(sorted(out["symbol"].unique())))
