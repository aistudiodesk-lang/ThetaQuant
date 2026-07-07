"""
ANALYSIS 013 — Late-day fake-spike detection on E-0 days

Hypothesis: institutional desks sometimes drive UP deep-OTM premium late in the
session (after 14:00) without commensurate spot movement — to hunt stops,
trigger short-cover, or manipulate pin levels.

Detection criteria for a "fake spike":
  1. Time window: 14:00 - 15:20 (within last 1.5 hr to expiry)
  2. Combined CE+PE @ 2.5% OTM jumps ≥ 50% over 5-min window
  3. Spot moves < 0.20% during that 5-min window (not delta-driven)
  4. Spike persists ≥ 3 minutes (not single-bar artifact)

Output: dated list of fake spikes per instrument with magnitude + spot drift
"""
from __future__ import annotations
from datetime import date, time, timedelta
from pathlib import Path
import sys

import duckdb, numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "013_fake_spike_detector"
OUT.mkdir(parents=True, exist_ok=True)

CFG = {
    "NIFTY":  {"grid": 50,  "store": ROOT / "data/parquet/instrument=NIFTY"},
    "SENSEX": {"grid": 100, "store": ROOT / "data/parquet/instrument=SENSEX"},
}

con = duckdb.connect()


def detect_for_instrument(instrument):
    cfg = CFG[instrument]
    expiries = NIFTY_WEEKLY_EXPIRIES if instrument == 'NIFTY' else SENSEX_WEEKLY_EXPIRIES
    p = str(cfg["store"] / "**" / "*.parquet")
    spikes = []
    for d in expiries:
        # Pull spot/FUT for the day
        spot_df = con.execute(f"""
          SELECT timestamp, close FROM read_parquet('{p}', union_by_name=True)
          WHERE option_type IN ('SPOT','FUT')
            AND CAST(timestamp AS DATE) = DATE '{d.isoformat()}'
        """).fetchdf()
        if spot_df.empty: continue
        spot_df['timestamp'] = pd.to_datetime(spot_df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        spot_df = spot_df.sort_values('timestamp').drop_duplicates('timestamp', keep='first')
        spot_df['t'] = spot_df['timestamp'].dt.strftime('%H:%M')

        # spot at 14:00
        s14 = spot_df[spot_df['t'] >= '14:00']
        if s14.empty: continue
        spot_1400 = float(s14['close'].iloc[0])
        ce_s = round(spot_1400 * 1.025 / cfg['grid']) * cfg['grid']
        pe_s = round(spot_1400 * 0.975 / cfg['grid']) * cfg['grid']

        # Pull combined premium
        opts = con.execute(f"""
          SELECT timestamp, strike, option_type, close
          FROM read_parquet('{p}', union_by_name=True)
          WHERE expiry = DATE '{d.isoformat()}'
            AND CAST(timestamp AS DATE) = DATE '{d.isoformat()}'
            AND strike IN ({int(ce_s)},{int(pe_s)})
            AND option_type IN ('CE','PE')
        """).fetchdf()
        if opts.empty: continue
        opts['timestamp'] = pd.to_datetime(opts['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        opts['t'] = opts['timestamp'].dt.strftime('%H:%M')
        ce = opts[(opts.option_type=='CE') & (opts.strike==ce_s)].sort_values('timestamp').drop_duplicates('timestamp')
        pe = opts[(opts.option_type=='PE') & (opts.strike==pe_s)].sort_values('timestamp').drop_duplicates('timestamp')
        if ce.empty or pe.empty: continue
        m = pd.merge(ce[['timestamp','t','close']].rename(columns={'close':'ce'}),
                     pe[['timestamp','t','close']].rename(columns={'close':'pe'}),
                     on=['timestamp','t'], how='inner')
        m = pd.merge(m, spot_df[['timestamp','close']].rename(columns={'close':'spot'}), on='timestamp', how='inner')
        m['combined'] = m['ce'] + m['pe']
        m = m[m['t'] >= '14:00'].copy().reset_index(drop=True)
        if len(m) < 10: continue

        # Look for 5-min windows where combined jumps ≥50% while spot moves <0.2%
        for i in range(5, len(m)):
            prem_then = m['combined'].iloc[i-5]
            prem_now = m['combined'].iloc[i]
            if prem_then < 0.5: continue   # ignore tiny bases
            spike_pct = (prem_now / prem_then - 1) * 100 if prem_then > 0 else 0
            spot_then = m['spot'].iloc[i-5]
            spot_now = m['spot'].iloc[i]
            spot_move_pct = abs(spot_now - spot_then) / spot_then * 100

            if spike_pct >= 50 and spot_move_pct < 0.20:
                # Check persistence: ≥3 minutes elevated
                window_after = m.iloc[i:min(i+3, len(m))]
                if (window_after['combined'] >= prem_now * 0.85).all():
                    spikes.append({
                        'instrument': instrument,
                        'date': d,
                        't_start': m['t'].iloc[i-5],
                        't_peak': m['t'].iloc[i],
                        'ce_strike': ce_s, 'pe_strike': pe_s,
                        'spot_start': round(spot_then, 1),
                        'spot_peak': round(spot_now, 1),
                        'spot_move_pct': round(spot_move_pct, 3),
                        'prem_start': round(prem_then, 2),
                        'prem_peak': round(prem_now, 2),
                        'spike_pct': round(spike_pct, 0),
                        'ce_then': round(m['ce'].iloc[i-5], 2),
                        'ce_now': round(m['ce'].iloc[i], 2),
                        'pe_then': round(m['pe'].iloc[i-5], 2),
                        'pe_now': round(m['pe'].iloc[i], 2),
                    })
    return pd.DataFrame(spikes)


def main():
    all_spikes = []
    for inst in ['NIFTY', 'SENSEX']:
        print(f'Scanning {inst} ...')
        s = detect_for_instrument(inst)
        if not s.empty:
            print(f'  Found {len(s)} fake-spike instances')
        all_spikes.append(s)
    df = pd.concat(all_spikes, ignore_index=True) if all_spikes else pd.DataFrame()
    if df.empty:
        print('\nNO fake spikes found by these criteria.')
        return
    df = df.sort_values(['date','t_peak'])
    df.to_csv(OUT / 'fake_spikes.csv', index=False)
    print(f'\n=== ALL DETECTED FAKE SPIKES ===')
    print(df.to_string(index=False))
    print(f'\n=== Summary by instrument ===')
    print(df.groupby('instrument').agg(
        n_spikes=('date','count'),
        n_unique_days=('date','nunique'),
        median_spike_pct=('spike_pct','median'),
        max_spike_pct=('spike_pct','max'),
    ))
    # Time-of-day distribution
    print(f'\n=== Time of day distribution ===')
    df['hour'] = df['t_peak'].str[:2]
    print(df.groupby('hour').size())


if __name__ == "__main__":
    main()
