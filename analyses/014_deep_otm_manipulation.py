"""
ANALYSIS 014 — Deep-OTM late-day MANIPULATION detector

Rohan's specific pattern (from live trading memory):
  - Deep OTM strike (4-6% OTM)
  - Low premium (₹0.10-0.30 range)
  - Low-ish OI (<20 lakh — easy to manipulate)
  - Late afternoon (14:30-15:00 esp)
  - Sudden spike: premium 0.1 → 22 (100x+) in seconds-to-minutes
  - Spot does NOT move
  - Spike collapses back within minutes

Why detect these:
  1. Anyone with hard SL on deep OTM gets stopped out on FAKE move
  2. Big desks sweep illiquid books = "hunt the stops"
  3. Lesson: NEVER put hard SL on deep OTM. Use spot-based stops.

Detection criteria (much more aggressive than 013):
  Window: 14:00-15:25
  Distance: 3.0%-6.0% OTM
  Base premium: ₹0.05-0.50 (catches the manipulated deep strikes)
  Spike: leg LTP jumps to ≥5x base in 5-min window (single-leg, not combined)
  Spot move during spike: <0.30% (decoupled from price)
  OI filter: <20 lakh (manipulable)
"""
from __future__ import annotations
from datetime import date, time, timedelta
from pathlib import Path
import sys

import duckdb, numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "014_deep_otm_manipulation"
OUT.mkdir(parents=True, exist_ok=True)

CFG = {
    "NIFTY":  {"grid": 50,  "store": ROOT / "data/parquet/instrument=NIFTY"},
    "SENSEX": {"grid": 100, "store": ROOT / "data/parquet/instrument=SENSEX"},
}

con = duckdb.connect()


def detect(instrument):
    cfg = CFG[instrument]
    expiries = NIFTY_WEEKLY_EXPIRIES if instrument == 'NIFTY' else SENSEX_WEEKLY_EXPIRIES
    p = str(cfg["store"] / "**" / "*.parquet")
    spikes = []
    for d in expiries:
        # Get spot path
        spot_df = con.execute(f"""
          SELECT timestamp, close FROM read_parquet('{p}', union_by_name=True)
          WHERE option_type IN ('SPOT','FUT')
            AND CAST(timestamp AS DATE) = DATE '{d.isoformat()}'
        """).fetchdf()
        if spot_df.empty: continue
        spot_df['timestamp'] = pd.to_datetime(spot_df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        spot_df = spot_df.sort_values('timestamp').drop_duplicates('timestamp', keep='first')
        spot_df['t'] = spot_df['timestamp'].dt.strftime('%H:%M')
        s14 = spot_df[spot_df['t'] >= '14:00']
        if s14.empty: continue
        spot_1400 = float(s14['close'].iloc[0])

        # Pull all option bars for this day (we'll filter later)
        opts = con.execute(f"""
          SELECT timestamp, strike, option_type, high, low, close, oi
          FROM read_parquet('{p}', union_by_name=True)
          WHERE expiry = DATE '{d.isoformat()}'
            AND CAST(timestamp AS DATE) = DATE '{d.isoformat()}'
            AND option_type IN ('CE','PE')
        """).fetchdf()
        if opts.empty: continue
        opts['timestamp'] = pd.to_datetime(opts['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        opts['t'] = opts['timestamp'].dt.strftime('%H:%M')
        opts = opts[opts['t'] >= '14:00'].copy()
        if opts.empty: continue

        # Compute distance %
        opts['dist_pct'] = (opts['strike'] - spot_1400) / spot_1400 * 100
        opts['abs_dist'] = opts['dist_pct'].abs()
        # Filter to deep OTM 2-6% (CE side: 2-6 above; PE side: 2-6 below)
        # User's actual case was 2.5-3% so widening to 2-6% catches the relevant zone
        deep = opts[((opts['option_type']=='CE') & (opts['dist_pct'].between(2.0, 6.0))) |
                    ((opts['option_type']=='PE') & (opts['dist_pct'].between(-6.0, -2.0)))]
        if deep.empty: continue

        # For each (strike,side), look at intraday path
        for (strike, side), grp in deep.groupby(['strike','option_type']):
            grp = grp.sort_values('timestamp').reset_index(drop=True)
            if len(grp) < 6: continue
            # OI filter — get representative OI early afternoon
            oi_sample = float(grp['oi'].iloc[0]) if pd.notna(grp['oi'].iloc[0]) else 0
            if oi_sample > 2_000_000:   # >20 lakh — too liquid to easily manipulate
                continue
            # Walk through 5-min windows
            for i in range(5, len(grp)):
                base = float(grp['close'].iloc[i-5])
                peak = float(grp['high'].iloc[i])   # use high to catch wick spikes
                if base < 0.05 or base > 2.00: continue   # widen base range — user's case was 0.1-0.2
                if peak < base * 5: continue   # 5× minimum spike
                # Spot decoupling check
                t_start = grp['timestamp'].iloc[i-5]
                t_now = grp['timestamp'].iloc[i]
                spot_then_row = spot_df[spot_df['timestamp'] >= t_start]
                spot_now_row = spot_df[spot_df['timestamp'] >= t_now]
                if spot_then_row.empty or spot_now_row.empty: continue
                spot_then = float(spot_then_row['close'].iloc[0])
                spot_now = float(spot_now_row['close'].iloc[0])
                spot_move_pct = abs(spot_now - spot_then) / spot_then * 100
                if spot_move_pct >= 0.30: continue
                spike_x = peak / base
                spikes.append({
                    'instrument': instrument,
                    'date': d,
                    't_start': grp['t'].iloc[i-5],
                    't_peak': grp['t'].iloc[i],
                    'strike': int(strike),
                    'side': side,
                    'dist_pct': round(grp['dist_pct'].iloc[i], 2),
                    'oi_lakh': round(oi_sample / 1e5, 1),
                    'spot_then': round(spot_then, 1),
                    'spot_now': round(spot_now, 1),
                    'spot_move_pct': round(spot_move_pct, 3),
                    'prem_base': round(base, 2),
                    'prem_peak': round(peak, 2),
                    'spike_x': round(spike_x, 1),
                    'close_after_spike': round(float(grp['close'].iloc[i]), 2),
                })
    return pd.DataFrame(spikes)


def main():
    all_spikes = []
    for inst in ['NIFTY', 'SENSEX']:
        print(f'Scanning {inst}...')
        s = detect(inst)
        print(f'  Found {len(s)} deep-OTM manipulation spikes')
        all_spikes.append(s)
    df = pd.concat(all_spikes, ignore_index=True) if all_spikes else pd.DataFrame()
    if df.empty:
        print('No deep-OTM manipulation found.')
        return
    df = df.sort_values(['instrument','date','t_peak'])
    df.to_csv(OUT / 'deep_otm_spikes.csv', index=False)

    print(f'\n=== Top 30 most extreme spikes ===')
    top = df.nlargest(30, 'spike_x')
    print(top[['instrument','date','t_peak','strike','side','dist_pct','oi_lakh',
                'prem_base','prem_peak','spike_x','spot_move_pct','close_after_spike']].to_string(index=False))

    print(f'\n=== Summary by instrument ===')
    print(df.groupby('instrument').agg(
        total_spikes=('date','count'),
        unique_days=('date','nunique'),
        median_spike_x=('spike_x','median'),
        max_spike_x=('spike_x','max'),
        median_oi_lakh=('oi_lakh','median'),
    ))

    print(f'\n=== By time-of-spike bucket ===')
    df['hour_bucket'] = df['t_peak'].apply(lambda t:
        '14:00-14:30' if t < '14:30' else
        '14:30-15:00' if t < '15:00' else
        '15:00-15:25')
    print(df.groupby(['instrument','hour_bucket']).size().unstack(fill_value=0))

    print(f'\n=== By distance bucket ===')
    df['dist_bucket'] = df['dist_pct'].abs().apply(lambda x:
        '3.0-3.5%' if x < 3.5 else
        '3.5-4.5%' if x < 4.5 else
        '4.5-6.0%')
    print(df.groupby(['instrument','dist_bucket']).agg(
        n=('date','count'),
        median_spike_x=('spike_x','median'),
        median_oi_lakh=('oi_lakh','median'),
    ))


if __name__ == "__main__":
    main()
