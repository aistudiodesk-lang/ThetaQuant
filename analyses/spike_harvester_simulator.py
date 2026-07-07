"""
SPIKE HARVESTER SIMULATOR — SENSEX expiry day deep-OTM ₹0.05-0.10 lottery buys
+ sell-limit ladder.

Reusable, parameterised. Returns:
  - events df:    one row per (expiry, strike, side) candidate at entry time
  - per_expiry:   one row per expiry with summary
  - basket_pnl:   per (basket_size, brokerage_rt) net P&L distribution
  - ladder_pnl:   per (ladder_config, scenario) net P&L

Run modes:
  python analyses/spike_harvester_simulator.py             # full backtest
  python analyses/spike_harvester_simulator.py --strategy 1
  python analyses/spike_harvester_simulator.py --strategy 2

Notes:
  - SENSEX lot size = 20 (post Jan 2025).
  - Brokerage RT = round-trip total (entry+exit) per lot.
      Axis = ₹12 RT  · Monarch = ₹20 RT
  - Sell-limit ladder: assume 0.5% of available margin held back per limit;
    'fill probability' = % of strikes that printed >= ladder_level for >= 60s.
  - Spot reference for distance: use SPOT close at entry time (14:00 by default).
"""
from __future__ import annotations
from datetime import date, time, datetime
from pathlib import Path
import argparse, json, sys

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import SENSEX_WEEKLY_EXPIRIES

OUT = ROOT / "results" / "spike_harvester_v1"
OUT.mkdir(parents=True, exist_ok=True)

PARQUET_GLOB = str(ROOT / "data/parquet/instrument=SENSEX/**/*.parquet")
SENSEX_LOT = 20

# ── parameters ──────────────────────────────────────────────────────────
DEFAULT_PARAMS = {
    "entry_time_hhmm": "14:00",          # default candidate entry time
    "spike_window_start": "14:45",       # window when a spike must print
    "spike_window_end":   "15:30",
    # Entry premium band:
    #   user spec says ₹0.05-0.10 (live experiment) but minimum tick + low-OI strikes
    #   often quote ₹0.10-0.30 at 14:00. We use ₹0.05-0.30 as the harvest-buy band
    #   and tag separately by sub-band for analysis.
    "entry_premium_min":  0.05,
    "entry_premium_max":  0.30,
    "min_oi":             0,             # absolute OI floor
    "axis_rt_per_lot":    12,
    "monarch_rt_per_lot": 20,
    "lot_size":           SENSEX_LOT,
    "lots_per_strike":    1,             # for basket sims (₹2K outlay per strike if avg ₹0.10)
    "outlay_per_strike_inr": 2500,       # ₹2.5K per strike (algorithm spec)
    "lots_min_per_strike": 5,            # floor per algorithm spec
    "lots_max_per_strike": 100,          # cap per algorithm spec
    # For Strategy 2 sell-limits we need a much wider chain — separate band:
    "s2_dist_pct_min":    1.5,
    "s2_dist_pct_max":    5.0,
    "s2_entry_premium_max": 0.50,
}

# ── helpers ─────────────────────────────────────────────────────────────
def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h)*60 + int(m)

def fmt_minute(t):
    return t.strftime('%H:%M')


def load_expiry_day(con, exp: date, entry_min: int, win_start: int, win_end: int) -> tuple[pd.DataFrame, float, float, float]:
    """
    Returns:
      chain_df with columns:
        strike, side, oi_open, oi_at_entry, prem_at_entry,
        prem_max_in_window, prem_max_minute, prem_high_60s_above_threshold (added later),
        prem_close_eod
      spot_at_open, spot_at_entry, spot_at_eod
    """
    p = PARQUET_GLOB

    # spot path for the day
    spot = con.execute(f"""
        SELECT timestamp, close
        FROM read_parquet('{p}', union_by_name=True)
        WHERE option_type = 'SPOT'
          AND CAST(timestamp AS DATE) = DATE '{exp.isoformat()}'
        ORDER BY timestamp
    """).fetchdf()
    if spot.empty:
        return pd.DataFrame(), None, None, None
    spot['timestamp'] = pd.to_datetime(spot['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
    spot['min_of_day'] = spot['timestamp'].dt.hour*60 + spot['timestamp'].dt.minute
    spot_open = float(spot.iloc[0]['close'])
    spot_at_entry_row = spot[spot['min_of_day'] >= entry_min]
    if spot_at_entry_row.empty:
        return pd.DataFrame(), None, None, None
    spot_at_entry = float(spot_at_entry_row.iloc[0]['close'])
    spot_eod_row = spot[spot['min_of_day'] >= 15*60+25]
    spot_eod = float(spot_eod_row.iloc[0]['close']) if not spot_eod_row.empty else float(spot.iloc[-1]['close'])

    # full options bars on expiry day
    opts = con.execute(f"""
        SELECT timestamp, strike, option_type, open, high, low, close, volume, oi
        FROM read_parquet('{p}', union_by_name=True)
        WHERE expiry = DATE '{exp.isoformat()}'
          AND CAST(timestamp AS DATE) = DATE '{exp.isoformat()}'
          AND option_type IN ('CE','PE')
    """).fetchdf()
    if opts.empty:
        return pd.DataFrame(), spot_open, spot_at_entry, spot_eod
    opts['timestamp'] = pd.to_datetime(opts['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
    opts['min_of_day'] = opts['timestamp'].dt.hour*60 + opts['timestamp'].dt.minute

    # OI at 9:15 (representative of "morning OI before manipulation")
    oi_morning = (opts[opts['min_of_day'] <= 9*60+30]
                  .sort_values('min_of_day')
                  .groupby(['strike','option_type'], as_index=False)
                  .agg(oi_morning=('oi','first')))
    oi_morning = oi_morning.rename(columns={'option_type':'side'})

    # Snapshot at entry
    entry_snap = opts[opts['min_of_day'] == entry_min].copy()
    if entry_snap.empty:
        # fallback: nearest available minute >= entry_min
        candidates = opts[opts['min_of_day'] >= entry_min].copy()
        if candidates.empty:
            return pd.DataFrame(), spot_open, spot_at_entry, spot_eod
        target_min = candidates['min_of_day'].min()
        entry_snap = opts[opts['min_of_day'] == target_min].copy()
    entry_snap = (entry_snap.sort_values(['strike','option_type','min_of_day'])
                  .drop_duplicates(['strike','option_type'], keep='first'))
    entry_snap = entry_snap.rename(columns={'option_type':'side',
                                            'close':'prem_at_entry',
                                            'oi':'oi_at_entry'})
    entry_snap = entry_snap[['strike','side','prem_at_entry','oi_at_entry']]

    # Window stats: max high during 14:45-15:30 + sustained price proxies
    win = opts[(opts['min_of_day'] >= win_start) & (opts['min_of_day'] <= win_end)].copy()
    if win.empty:
        return pd.DataFrame(), spot_open, spot_at_entry, spot_eod

    # peak high per strike in window
    win_stats = (win.groupby(['strike','option_type'], as_index=False)
                 .agg(prem_max_high=('high','max'),
                      prem_max_close=('close','max'),
                      prem_min_low=('low','min'),
                      bars_above_1=('high', lambda x: (x >= 1.0).sum()),
                      bars_above_1_5=('high', lambda x: (x >= 1.5).sum()),
                      bars_above_2=('high', lambda x: (x >= 2.0).sum()),
                      bars_above_3=('high', lambda x: (x >= 3.0).sum()),
                      # sustained ≥60s = at least one bar where LOW also held above
                      sustained_above_1=('low', lambda x: (x >= 1.0).sum()),
                      sustained_above_1_5=('low', lambda x: (x >= 1.5).sum()),
                      sustained_above_2=('low', lambda x: (x >= 2.0).sum()),
                      sustained_above_3=('low', lambda x: (x >= 3.0).sum()),
                      # vwap-like: close at peak minute
                      ))
    win_stats = win_stats.rename(columns={'option_type':'side'})

    # EOD close per strike (15:25-15:30)
    eod = (opts[opts['min_of_day'] >= 15*60+25]
           .sort_values('min_of_day')
           .drop_duplicates(['strike','option_type'], keep='last'))
    eod = eod.rename(columns={'option_type':'side','close':'prem_eod'})
    eod = eod[['strike','side','prem_eod']]

    chain = entry_snap.merge(win_stats, on=['strike','side'], how='inner')
    chain = chain.merge(oi_morning, on=['strike','side'], how='left')
    chain = chain.merge(eod, on=['strike','side'], how='left')

    chain['expiry'] = exp
    chain['spot_at_entry'] = spot_at_entry
    chain['spot_open'] = spot_open
    chain['spot_eod'] = spot_eod
    # signed distance % at entry
    chain['signed_dist_pct'] = (chain['strike'] - spot_at_entry) / spot_at_entry * 100
    chain['abs_dist_pct'] = chain['signed_dist_pct'].abs()
    # Direction: strike on the far side of spot path
    return chain, spot_open, spot_at_entry, spot_eod


def classify_distance(d):
    """absolute distance % bucket"""
    if d < 1.0: return "<1%"
    if d < 1.5: return "1-1.5%"
    if d < 2.0: return "1.5-2%"
    if d < 2.5: return "2-2.5%"
    if d < 3.0: return "2.5-3%"
    if d < 4.0: return "3-4%"
    if d < 5.0: return "4-5%"
    return "5%+"


def classify_oi(oi, q_low, q_high):
    if pd.isna(oi): return "unknown"
    if oi <= q_low: return "low"
    if oi <= q_high: return "mid"
    return "high"


def build_master(params=DEFAULT_PARAMS, expiries=None,
                 entry_premium_min=None, entry_premium_max=None,
                 keep_full_chain=False) -> pd.DataFrame:
    """Master per-(expiry,strike,side) candidate dataset for entry-time = entry_time_hhmm.

    keep_full_chain=True ignores premium filter (used for Strategy 2 sell-limits).
    """
    con = duckdb.connect()
    expiries = expiries or [d for d in SENSEX_WEEKLY_EXPIRIES if d <= date(2026,5,1)]
    entry_min = hhmm_to_min(params['entry_time_hhmm'])
    win_start = hhmm_to_min(params['spike_window_start'])
    win_end   = hhmm_to_min(params['spike_window_end'])
    pmin = entry_premium_min if entry_premium_min is not None else params['entry_premium_min']
    pmax = entry_premium_max if entry_premium_max is not None else params['entry_premium_max']

    rows = []
    for exp in expiries:
        try:
            df, sopen, sentry, seod = load_expiry_day(con, exp, entry_min, win_start, win_end)
        except Exception as e:
            print(f"  ! {exp}: {e}")
            continue
        if df.empty:
            continue
        # Only deep OTM (CE > spot, PE < spot)
        df = df[((df['side']=='CE') & (df['signed_dist_pct'] > 0)) |
                ((df['side']=='PE') & (df['signed_dist_pct'] < 0))]
        if not keep_full_chain:
            df = df[(df['prem_at_entry'] >= pmin) &
                    (df['prem_at_entry'] <= pmax)].copy()
        if df.empty:
            continue
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)

    # bucket columns
    out['dist_bucket'] = out['abs_dist_pct'].apply(classify_distance)
    # OI quartiles per expiry per side (handles regime shifts)
    out['oi_morning'] = out['oi_morning'].fillna(0)
    def _safe_qcut(x):
        try:
            return pd.qcut(x.rank(method='first'), q=4,
                           labels=['Q1_low','Q2','Q3','Q4_high'])
        except ValueError:
            # too few unique values — bucket by raw rank
            return pd.Series(['Q2'] * len(x), index=x.index)
    out['oi_quartile'] = (out.groupby(['expiry','side'])['oi_morning']
                          .transform(_safe_qcut))
    # spot direction (open → entry)
    out['spot_move_open_to_entry_pct'] = (out['spot_at_entry'] - out['spot_open']) / out['spot_open'] * 100
    out['day_direction'] = np.where(out['spot_move_open_to_entry_pct'] > 0.3, 'up',
                            np.where(out['spot_move_open_to_entry_pct'] < -0.3, 'down', 'flat'))

    # binary outcomes
    out['hit_1']   = (out['prem_max_high'] >= 1.0).astype(int)
    out['hit_1_5'] = (out['prem_max_high'] >= 1.5).astype(int)
    out['hit_2']   = (out['prem_max_high'] >= 2.0).astype(int)
    out['hit_3']   = (out['prem_max_high'] >= 3.0).astype(int)
    out['sustained_1']   = (out['sustained_above_1']   >= 1).astype(int)
    out['sustained_1_5'] = (out['sustained_above_1_5'] >= 1).astype(int)
    out['sustained_2']   = (out['sustained_above_2']   >= 1).astype(int)
    out['sustained_3']   = (out['sustained_above_3']   >= 1).astype(int)
    return out


# ── Strategy 1: Lottery Buys ─────────────────────────────────────────────
def strategy1_per_expiry(master: pd.DataFrame, params=DEFAULT_PARAMS,
                         basket_size: int = 5,
                         exit_target: float = 1.0,
                         brokerage_rt_per_lot: float = 12.0,
                         selection_rule=None) -> pd.DataFrame:
    """Per-expiry net P&L if we bought `basket_size` candidate strikes meeting `selection_rule`.

    selection_rule(df_at_expiry) -> ranked df  (top N taken).
    Default selection: best-performing buckets first by historical hit rate (use random/uniform if None).

    Lots-per-strike calibrated so each strike outlay ≈ params['outlay_per_strike_inr'].
    """
    lot_size = params['lot_size']
    outlay_target = params['outlay_per_strike_inr']
    rows = []
    for exp, grp in master.groupby('expiry'):
        cand = grp.copy()
        if selection_rule is not None:
            cand = selection_rule(cand)
        # take top N (deterministic order — selection rule should rank)
        cand = cand.head(basket_size)
        if cand.empty:
            rows.append(dict(expiry=exp, basket_size=0, picked=0, gross=0, brok=0, net=0))
            continue
        gross = 0.0
        brok = 0.0
        picked = []
        lots_min = params.get('lots_min_per_strike', 5)
        lots_max = params.get('lots_max_per_strike', 100)
        for _, r in cand.iterrows():
            entry_prem = float(r['prem_at_entry'])
            # qty_lots = floor(budget/(ltp*lot_size)) capped [lots_min, lots_max]
            raw = int(outlay_target // max(entry_prem*lot_size, 0.01))
            lots = max(lots_min, min(lots_max, raw))
            # Exit: target ≥ exit_target (use prem_max_high), else expire at prem_eod (≈0.05)
            if r['prem_max_high'] >= exit_target:
                exit_prem = exit_target  # conservative — sell at the ladder, not the peak
            else:
                exit_prem = float(r['prem_eod']) if not pd.isna(r['prem_eod']) else 0.05
            pnl = (exit_prem - entry_prem) * lots * lot_size
            brok_inr = brokerage_rt_per_lot * lots
            gross += pnl
            brok += brok_inr
            picked.append({
                'strike': int(r['strike']), 'side': r['side'],
                'dist_bucket': r['dist_bucket'], 'entry': entry_prem,
                'peak_high': r['prem_max_high'],
                'eod': r['prem_eod'], 'lots': lots, 'pnl_pre_brok': pnl,
                'oi_quartile': r['oi_quartile'],
            })
        net = gross - brok
        rows.append(dict(expiry=exp, basket_size=len(picked), picked=picked,
                         gross=gross, brok=brok, net=net))
    return pd.DataFrame(rows)


def hit_rate_table(master: pd.DataFrame, by_cols, threshold_col='hit_1') -> pd.DataFrame:
    g = (master.groupby(by_cols)
         .agg(n_candidates=('expiry','count'),
              n_expiries=('expiry','nunique'),
              hit_rate=(threshold_col,'mean'),
              median_peak=('prem_max_high','median'),
              p75_peak=('prem_max_high', lambda x: np.percentile(x,75)),
              p90_peak=('prem_max_high', lambda x: np.percentile(x,90)),
              max_peak=('prem_max_high','max'),
              )
         .reset_index())
    g['hit_pct'] = (g['hit_rate']*100).round(1)
    return g


# ── Strategy 2: Sell-limit ladder ────────────────────────────────────────
def strategy2_sell_limits(master: pd.DataFrame,
                          ladder: list[tuple[float, float]],   # [(level, capital_fraction), ...]
                          bucket_a_close_inr_per_cr: float = 300,
                          book_size_cr: float = 10.0,
                          margin_per_lot_inr: float = 250000,    # SENSEX deep OTM ≈ 40 lots/Cr
                          brokerage_rt_per_lot: float = 12.0,
                          entry_premium_min: float = 0.05,
                          entry_premium_max: float = 0.30,
                          ) -> pd.DataFrame:
    """Each level (price, cap_frac): place sell limit at `level`, capital fraction of book.
    Hit if strike printed sustained (low ≥ level for ≥ 60s) anywhere 14:45-15:30.
    Net per expiry = sum_over_levels(level * lots_at_level * lot_size * P_fill)
                     - bucket_a_close_inr_per_cr * book_size_cr
                     - brokerage on filled portions.
    Probability uses cross-section across ALL deep-OTM strikes that day (we 'win' if
    ANY strike printed sustained ≥ level).
    """
    lot_size = SENSEX_LOT
    # widen entry filter for sell limits (deep OTM, any low premium ≤0.30)
    deep = master[(master['abs_dist_pct'] >= 1.0) & (master['abs_dist_pct'] <= 5.0)]

    rows = []
    for exp, grp in deep.groupby('expiry'):
        # For each level we need probability that AT LEAST ONE strike sustained >= level
        # In practice, you'd put N limits per level — model: per limit, expected fill =
        # min(N_limits, strikes_that_sustained) / strikes_that_sustained_overall_or_N
        # We approximate: per strike, prob of sustained ≥ level = sustained_X column;
        # # of strikes available with sustained ≥ level on this day
        avail = {
            1.0:  int((grp['sustained_above_1']   >= 1).sum()),
            1.5:  int((grp['sustained_above_1_5'] >= 1).sum()),
            2.0:  int((grp['sustained_above_2']   >= 1).sum()),
            3.0:  int((grp['sustained_above_3']   >= 1).sum()),
        }
        gross_pnl = 0.0
        n_filled  = 0
        legs_total = 0
        leg_details = []
        for level, cap_frac in ladder:
            cap_inr = cap_frac * book_size_cr * 1e7
            lots_per_limit = max(1, int(cap_inr / margin_per_lot_inr))
            n_limits_at_level = 1   # cap_frac already represents "this much capital at THIS level"
            # actually — interpret cap_frac as fraction of book at this level; spread across N limits:
            # default: each ladder entry = 1 limit. Multiple limits/level are passed as repeated entries.
            avail_at_level = avail.get(level, 0)
            filled = min(n_limits_at_level, avail_at_level)
            if filled > 0:
                # premium captured per lot = level * lot_size
                gross_per_limit = level * lots_per_limit * lot_size
                gross_pnl += gross_per_limit * filled
                brok_legs = lots_per_limit * 1   # only entry sold; settlement = 0 cost (let it expire) typically
                n_filled += filled
            legs_total += n_limits_at_level
            leg_details.append({
                'level': level, 'cap_frac': cap_frac,
                'lots_per_limit': lots_per_limit,
                'avail_at_level': avail_at_level,
                'filled': filled,
            })
        # Brokerage on filled portions only — sells expiring → entry brokerage only
        # Round-trip used because user defines 'RT' per lot; we use HALF for sell-only
        brok_total = 0.0
        for det in leg_details:
            brok_total += det['filled'] * det['lots_per_limit'] * (brokerage_rt_per_lot / 2)

        bucket_a_close_cost = bucket_a_close_inr_per_cr * book_size_cr   # ₹ for entire book

        net = gross_pnl - brok_total - bucket_a_close_cost
        rows.append({
            'expiry': exp,
            'gross': gross_pnl,
            'brok': brok_total,
            'bucket_a_close_cost': bucket_a_close_cost,
            'net': net,
            'n_filled': n_filled,
            'legs_total': legs_total,
            'avail_1':   avail.get(1.0,0),
            'avail_1_5': avail.get(1.5,0),
            'avail_2':   avail.get(2.0,0),
            'avail_3':   avail.get(3.0,0),
            'leg_details': leg_details,
        })
    return pd.DataFrame(rows)


# ── Skip-day filters ─────────────────────────────────────────────────────
def add_skip_day_features(master: pd.DataFrame, con) -> pd.DataFrame:
    """Add per-expiry-day features used for skip filters."""
    # Spot move 9:15 → 14:30
    feats = []
    for exp in master['expiry'].unique():
        spot = con.execute(f"""
            SELECT timestamp, close
            FROM read_parquet('{PARQUET_GLOB}', union_by_name=True)
            WHERE option_type='SPOT' AND CAST(timestamp AS DATE) = DATE '{exp.isoformat()}'
            ORDER BY timestamp
        """).fetchdf()
        if spot.empty: continue
        spot['timestamp'] = pd.to_datetime(spot['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        spot['min_of_day'] = spot['timestamp'].dt.hour*60 + spot['timestamp'].dt.minute
        s_open = float(spot.iloc[0]['close'])
        s_1430 = spot[spot['min_of_day'] >= 14*60+30]
        s_1430 = float(s_1430.iloc[0]['close']) if not s_1430.empty else s_open
        # daily range until 14:30
        until_1430 = spot[spot['min_of_day'] <= 14*60+30]
        rng = (until_1430['close'].max() - until_1430['close'].min()) / s_open * 100 if not until_1430.empty else 0
        move_open_to_1430 = (s_1430 - s_open) / s_open * 100
        feats.append({
            'expiry': exp,
            'spot_open': s_open,
            'spot_1430': s_1430,
            'move_open_to_1430_pct': move_open_to_1430,
            'abs_move_pct': abs(move_open_to_1430),
            'range_until_1430_pct': rng,
        })
    return pd.DataFrame(feats)


def compute_bucket_a_close_cost(con, expiries, entry_dist_pct=2.5,
                                 reference_times=('14:30','14:45','15:00')):
    """For a 2.5-3% OTM short strangle entered at 09:30, compute buy-back cost
    at each reference time. Per ₹1Cr book = (CE+PE close)*lot_size*lots_per_cr.
    """
    rows = []
    for exp in expiries:
        spot = con.execute(f"""
            SELECT timestamp, close FROM read_parquet('{PARQUET_GLOB}', union_by_name=True)
            WHERE option_type='SPOT' AND CAST(timestamp AS DATE)=DATE '{exp.isoformat()}'
            ORDER BY timestamp
        """).fetchdf()
        if spot.empty: continue
        spot['timestamp'] = pd.to_datetime(spot['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        spot['min_of_day'] = spot['timestamp'].dt.hour*60 + spot['timestamp'].dt.minute

        s_open = float(spot.iloc[0]['close'])
        # find spot at 09:30
        s_930 = spot[spot['min_of_day']>=9*60+30]
        s_930 = float(s_930.iloc[0]['close']) if not s_930.empty else s_open
        # target strike (round to 100)
        ce_target = round(s_930 * (1 + entry_dist_pct/100) / 100) * 100
        pe_target = round(s_930 * (1 - entry_dist_pct/100) / 100) * 100

        chain = con.execute(f"""
            SELECT timestamp, strike, option_type, close
            FROM read_parquet('{PARQUET_GLOB}', union_by_name=True)
            WHERE expiry=DATE '{exp.isoformat()}'
              AND CAST(timestamp AS DATE)=DATE '{exp.isoformat()}'
              AND option_type IN ('CE','PE')
              AND strike IN ({ce_target}, {pe_target})
        """).fetchdf()
        if chain.empty: continue
        chain['timestamp'] = pd.to_datetime(chain['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        chain['min_of_day'] = chain['timestamp'].dt.hour*60 + chain['timestamp'].dt.minute

        # entry premium at 09:30 — STRICTLY filter by strike + option_type
        def pick(df, strike, opt):
            r = df[(df['strike']==strike) & (df['option_type']==opt)]
            return float(r['close'].iloc[0]) if not r.empty else None

        entry = chain[chain['min_of_day']==9*60+30]
        if entry.empty:
            entry = chain[chain['min_of_day']>=9*60+30].sort_values('min_of_day')
        ce_entry = pick(entry, ce_target, 'CE')
        pe_entry = pick(entry, pe_target, 'PE')
        rec = {'expiry': exp, 'spot_930': s_930,
               'ce_strike': ce_target, 'pe_strike': pe_target,
               'ce_entry': ce_entry, 'pe_entry': pe_entry,
               'combined_entry': (ce_entry or 0)+(pe_entry or 0)}
        for tlabel in reference_times:
            tmin = hhmm_to_min(tlabel)
            snap = chain[chain['min_of_day']==tmin]
            if snap.empty:
                snap = chain[chain['min_of_day']>=tmin].sort_values('min_of_day')
            ce_now = pick(snap, ce_target, 'CE')
            pe_now = pick(snap, pe_target, 'PE')
            comb = (ce_now or 0) + (pe_now or 0)
            # cost per Cr (40 lots/Cr × lot 20 × ₹combined)
            rec[f'ce_{tlabel}'] = ce_now
            rec[f'pe_{tlabel}'] = pe_now
            rec[f'combined_{tlabel}'] = comb
            rec[f'cost_per_cr_{tlabel}'] = comb * SENSEX_LOT * 40   # 40 lots/Cr
        rows.append(rec)
    return pd.DataFrame(rows)


def find_tail_risk_events(con, expiries):
    """Flag expiries where deep OTM strikes ended ITM (real spot move past 2.5% strike)."""
    rows = []
    for exp in expiries:
        spot = con.execute(f"""
            SELECT timestamp, close FROM read_parquet('{PARQUET_GLOB}', union_by_name=True)
            WHERE option_type='SPOT' AND CAST(timestamp AS DATE)=DATE '{exp.isoformat()}'
            ORDER BY timestamp
        """).fetchdf()
        if spot.empty: continue
        spot['timestamp'] = pd.to_datetime(spot['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
        spot['min_of_day'] = spot['timestamp'].dt.hour*60 + spot['timestamp'].dt.minute
        s_open = float(spot.iloc[0]['close'])
        s_eod = spot[spot['min_of_day']>=15*60+25]
        s_eod = float(s_eod.iloc[0]['close']) if not s_eod.empty else float(spot.iloc[-1]['close'])
        rng_pct = (spot['close'].max() - spot['close'].min()) / s_open * 100
        max_move = (spot['close'].max() - s_open) / s_open * 100
        min_move = (spot['close'].min() - s_open) / s_open * 100
        rows.append({
            'expiry': exp, 'spot_open': s_open, 'spot_eod': s_eod,
            'spot_high': float(spot['close'].max()),
            'spot_low': float(spot['close'].min()),
            'eod_move_pct': (s_eod - s_open)/s_open*100,
            'range_pct': rng_pct,
            'max_up_move_pct': max_move,
            'max_down_move_pct': min_move,
            'tail_event_2_5pct': abs((s_eod - s_open)/s_open*100) >= 2.5,
            'tail_event_2pct': abs((s_eod - s_open)/s_open*100) >= 2.0,
            'tail_event_1pct': abs((s_eod - s_open)/s_open*100) >= 1.0,
        })
    return pd.DataFrame(rows)


def main(args):
    print("=" * 70)
    print("SPIKE HARVESTER SIMULATOR — SENSEX")
    print("=" * 70)

    print("\n[1/8] Building master_buy (Strategy 1: prem 0.05-0.30, entry 14:00)...")
    master_buy = build_master()
    print(f"   master_buy rows: {len(master_buy)}  ·  expiries: {master_buy['expiry'].nunique()}")
    master_buy.to_csv(OUT / "master_buy_candidates.csv", index=False)

    # Sub-band tag
    master_buy['entry_band'] = pd.cut(master_buy['prem_at_entry'],
        bins=[-0.001, 0.10, 0.20, 0.30, 0.50],
        labels=['0.05-0.10','0.10-0.20','0.20-0.30','0.30-0.50'])

    print("\n[2/8] Strategy 1 hit-rate by buckets")
    by_dist = hit_rate_table(master_buy, ['dist_bucket'], 'hit_1').sort_values('dist_bucket')
    by_dist_side = hit_rate_table(master_buy, ['side','dist_bucket'], 'hit_1').sort_values(['side','dist_bucket'])
    by_dist_band = hit_rate_table(master_buy, ['dist_bucket','entry_band'], 'hit_1')
    by_oi = hit_rate_table(master_buy, ['dist_bucket','oi_quartile'], 'hit_1')
    by_dir = hit_rate_table(master_buy, ['side','dist_bucket','day_direction'], 'hit_1')
    by_dist.to_csv(OUT/"hitrate_by_distance.csv", index=False)
    by_dist_side.to_csv(OUT/"hitrate_by_distance_side.csv", index=False)
    by_dist_band.to_csv(OUT/"hitrate_by_distance_band.csv", index=False)
    by_oi.to_csv(OUT/"hitrate_by_distance_oi.csv", index=False)
    by_dir.to_csv(OUT/"hitrate_by_distance_side_direction.csv", index=False)
    print("\nBy distance bucket (hit ≥ ₹1):")
    print(by_dist.to_string(index=False))
    print("\nBy side+distance (hit ≥ ₹1):")
    print(by_dist_side.to_string(index=False))
    print("\nBy distance × OI (hit ≥ ₹1):")
    print(by_oi.to_string(index=False))
    print("\nBy side+distance+direction (hit ≥ ₹1):")
    print(by_dir.to_string(index=False))

    # MAGNITUDE distribution among hits
    hits = master_buy[master_buy['hit_1']==1]
    print(f"\n   {len(hits)} candidates printed ≥ ₹1; distribution of peak (₹):")
    if len(hits)>0:
        print(f"      median {hits['prem_max_high'].median():.2f}  p75 {hits['prem_max_high'].quantile(0.75):.2f}"
              f"  p90 {hits['prem_max_high'].quantile(0.90):.2f}  max {hits['prem_max_high'].max():.2f}")

    # Entry-time sweep
    print("\n[3/8] Strategy 1 — entry-time sweep")
    times_master = {}
    for hhmm in ['13:00','13:30','14:00','14:30','15:00']:
        params = dict(DEFAULT_PARAMS); params['entry_time_hhmm'] = hhmm
        m_t = build_master(params)
        m_t['entry_time'] = hhmm
        times_master[hhmm] = m_t
    sweep = pd.concat(times_master.values(), ignore_index=True) if times_master else pd.DataFrame()
    if not sweep.empty:
        sweep_sum = (sweep.groupby('entry_time')
                     .agg(n_candidates=('expiry','count'),
                          n_expiries=('expiry','nunique'),
                          hit_1_pct=('hit_1', lambda x: round(x.mean()*100, 1)),
                          hit_1_5_pct=('hit_1_5', lambda x: round(x.mean()*100, 1)),
                          hit_2_pct=('hit_2', lambda x: round(x.mean()*100, 1)),
                          median_entry_prem=('prem_at_entry','median'),
                          )
                     .reset_index())
        print(sweep_sum.to_string(index=False))
        sweep_sum.to_csv(OUT/"entry_time_sweep.csv", index=False)

    # Basket sweep — multiple selection rules
    print("\n[4/8] Strategy 1 — basket sweep with selection rules")
    selection_rules = {
        'deepest_first': lambda df, n: df.assign(rank=df['abs_dist_pct']).sort_values('rank', ascending=False).head(n),
        'cheapest_first': lambda df, n: df.sort_values('prem_at_entry').head(n),
        'low_oi_5pct_OTM': lambda df, n: (df[(df['abs_dist_pct']>=5.0) & (df['oi_morning']<=df['oi_morning'].quantile(0.5))]
                                          .sort_values('abs_dist_pct', ascending=False).head(n)),
        'CE_only_deepest': lambda df, n: (df[df['side']=='CE'].sort_values('abs_dist_pct', ascending=False).head(n)),
        'PE_only_deepest': lambda df, n: (df[df['side']=='PE'].sort_values('abs_dist_pct', ascending=False).head(n)),
        'mixed_5_8pct': lambda df, n: (df[(df['abs_dist_pct']>=5.0) & (df['abs_dist_pct']<=8.0)]
                                       .sort_values('prem_at_entry').head(n)),
    }
    basket_results = []
    all_picks = []
    for n in [3, 5, 8, 12]:
        for rule_name, rule_fn in selection_rules.items():
            for brok, lbl in [(12, 'axis'), (20, 'monarch')]:
                def pick(df, n=n, fn=rule_fn): return fn(df, n)
                res = strategy1_per_expiry(master_buy, basket_size=n, brokerage_rt_per_lot=brok,
                                           selection_rule=pick)
                res['basket_size'] = n
                res['rule'] = rule_name
                res['brokerage'] = lbl
                basket_results.append(res)
    basket = pd.concat(basket_results, ignore_index=True)
    basket_sum = (basket.groupby(['rule','basket_size','brokerage'])
                  .agg(expiries=('expiry','nunique'),
                       wins=('net', lambda x: int((x>0).sum())),
                       win_pct=('net', lambda x: round((x>0).mean()*100, 1)),
                       median_net=('net','median'),
                       mean_net=('net','mean'),
                       p75_net=('net', lambda x: np.percentile(x, 75)),
                       max_net=('net','max'),
                       min_net=('net','min'),
                       sum_net=('net','sum'),
                  ).reset_index())
    print("\nTop 20 by mean_net:")
    print(basket_sum.sort_values('mean_net', ascending=False).head(20).to_string(index=False))
    basket_sum.to_csv(OUT/"basket_brokerage_sweep.csv", index=False)
    basket.drop(columns='picked', errors='ignore').to_csv(OUT/"basket_per_expiry.csv", index=False)

    # Build wider chain for Strategy 2 (any premium)
    print("\n[5/8] Building master_sell (Strategy 2: full chain at 14:00, all OTM)...")
    master_sell = build_master(keep_full_chain=True)
    # restrict to 1.5-5% OTM
    master_sell = master_sell[(master_sell['abs_dist_pct']>=1.5) & (master_sell['abs_dist_pct']<=5.0)]
    print(f"   master_sell rows: {len(master_sell)}  ·  expiries: {master_sell['expiry'].nunique()}")
    master_sell.to_csv(OUT/"master_sell_candidates.csv", index=False)

    # Sell-limit fill probability (cross-strike per expiry)
    print("\n[6/8] Strategy 2 — sell-limit fill probability per level")
    fill_summary = (master_sell.groupby('expiry')
                    .agg(strikes_avail=('strike','count'),
                         strikes_sustained_1=('sustained_above_1', lambda x: int((x>=1).sum())),
                         strikes_sustained_1_5=('sustained_above_1_5', lambda x: int((x>=1).sum())),
                         strikes_sustained_2=('sustained_above_2', lambda x: int((x>=1).sum())),
                         strikes_sustained_3=('sustained_above_3', lambda x: int((x>=1).sum())),
                         strikes_high_above_1=('hit_1', lambda x: int((x>=1).sum())),
                         strikes_high_above_2=('hit_2', lambda x: int((x>=1).sum())),
                    ).reset_index())
    fill_summary['any_sustained_1']   = (fill_summary['strikes_sustained_1']>0).astype(int)
    fill_summary['any_sustained_1_5'] = (fill_summary['strikes_sustained_1_5']>0).astype(int)
    fill_summary['any_sustained_2']   = (fill_summary['strikes_sustained_2']>0).astype(int)
    fill_summary['any_sustained_3']   = (fill_summary['strikes_sustained_3']>0).astype(int)
    fill_summary['any_hit_1']         = (fill_summary['strikes_high_above_1']>0).astype(int)
    fill_summary['any_hit_2']         = (fill_summary['strikes_high_above_2']>0).astype(int)
    fill_summary.to_csv(OUT/"strategy2_fill_summary.csv", index=False)
    print(f"   Per-expiry coverage: {len(fill_summary)} expiries")
    print(f"   % expiries any strike sustained ≥ ₹1: {fill_summary['any_sustained_1'].mean()*100:.1f}%")
    print(f"   % expiries any strike sustained ≥ ₹1.5: {fill_summary['any_sustained_1_5'].mean()*100:.1f}%")
    print(f"   % expiries any strike sustained ≥ ₹2: {fill_summary['any_sustained_2'].mean()*100:.1f}%")
    print(f"   % expiries any strike sustained ≥ ₹3: {fill_summary['any_sustained_3'].mean()*100:.1f}%")
    print(f"\n   Mean strikes available per expiry (sustained ≥ level):")
    print(f"     ≥₹1   = {fill_summary['strikes_sustained_1'].mean():.1f}")
    print(f"     ≥₹1.5 = {fill_summary['strikes_sustained_1_5'].mean():.1f}")
    print(f"     ≥₹2   = {fill_summary['strikes_sustained_2'].mean():.1f}")
    print(f"     ≥₹3   = {fill_summary['strikes_sustained_3'].mean():.1f}")

    # Bucket A close cost
    print("\n[7/8] Bucket A close-back cost distribution (2.5% OTM, 09:30 entry)")
    con = duckdb.connect()
    expiries_for_ba = sorted(master_sell['expiry'].unique())
    ba = compute_bucket_a_close_cost(con, expiries_for_ba, entry_dist_pct=2.5)
    if not ba.empty:
        for tlabel in ['14:30','14:45','15:00']:
            col = f'cost_per_cr_{tlabel}'
            if col in ba.columns:
                vals = ba[col].dropna()
                print(f"   At {tlabel}: median ₹{vals.median():,.0f}/cr · p25 ₹{vals.quantile(0.25):,.0f} · p75 ₹{vals.quantile(0.75):,.0f}"
                      f" · max ₹{vals.max():,.0f}  (n={len(vals)})")
    ba.to_csv(OUT/"bucket_a_close_cost.csv", index=False)

    # Ladder optimisation using actual fill data
    print("\n[8/8] Strategy 2 — ladder × Bucket-A-cost × broker matrix")
    ladders = {
        '5x@2.0': [(2.0, 0.20)] * 5,
        '8x@1.5': [(1.5, 0.125)] * 8,
        '3x@3.0': [(3.0, 0.33)] * 3,
        '10x@1.0': [(1.0, 0.10)] * 10,
        'Mix-2-15-1': [(2.0,0.10),(2.0,0.10),(1.5,0.15),(1.5,0.15),(1.0,0.20),(1.0,0.20)],
        '5x@1.0': [(1.0, 0.20)] * 5,
        '5x@1.5': [(1.5, 0.20)] * 5,
        '4x@2-2x@3': [(2.0,0.20),(2.0,0.20),(2.0,0.20),(2.0,0.20),(3.0,0.10),(3.0,0.10)],
    }
    ladder_results = []
    for name, ld in ladders.items():
        for ba_cost in [100, 200, 300, 500, 750]:
            for brok, lbl in [(12,'axis'), (20,'monarch')]:
                df = strategy2_sell_limits(master_sell, ld,
                                           bucket_a_close_inr_per_cr=ba_cost,
                                           book_size_cr=10,
                                           brokerage_rt_per_lot=brok)
                summary = {
                    'ladder': name,
                    'ba_close_per_cr': ba_cost,
                    'brokerage': lbl,
                    'expiries': df['expiry'].nunique(),
                    'mean_net': df['net'].mean(),
                    'median_net': df['net'].median(),
                    'win_pct': round((df['net']>0).mean()*100, 1),
                    'sum_net': df['net'].sum(),
                    'min_net': df['net'].min(),
                    'max_net': df['net'].max(),
                    'mean_n_filled': df['n_filled'].mean(),
                    'mean_avail_1': df['avail_1'].mean(),
                    'mean_avail_1_5': df['avail_1_5'].mean(),
                    'mean_avail_2': df['avail_2'].mean(),
                    'mean_avail_3': df['avail_3'].mean(),
                }
                ladder_results.append(summary)
    ldf = pd.DataFrame(ladder_results)
    ldf.to_csv(OUT/"ladder_results.csv", index=False)
    print("\nTop 25 ladder configs by mean_net (across all BA-cost/broker combos):")
    print(ldf.sort_values('mean_net', ascending=False).head(25).to_string(index=False))

    # Tail-risk events
    print("\n[+1] Tail-risk events — large EOD spot moves")
    tail = find_tail_risk_events(con, expiries_for_ba)
    print(f"  Total expiries: {len(tail)}")
    print(f"  ≥1% EOD move:   {int(tail['tail_event_1pct'].sum())}  ({tail['tail_event_1pct'].mean()*100:.0f}%)")
    print(f"  ≥2% EOD move:   {int(tail['tail_event_2pct'].sum())}  ({tail['tail_event_2pct'].mean()*100:.0f}%)")
    print(f"  ≥2.5% EOD move: {int(tail['tail_event_2_5pct'].sum())}  ({tail['tail_event_2_5pct'].mean()*100:.0f}%)")
    if tail['tail_event_2pct'].any():
        print("\n  ≥2% expiries:")
        print(tail[tail['tail_event_2pct']][['expiry','spot_open','spot_eod','eod_move_pct','range_pct']].to_string(index=False))
    tail.to_csv(OUT/"tail_risk_events.csv", index=False)

    # Skip-day filter analysis
    print("\n[+2] Skip-day filters")
    feats = add_skip_day_features(master_buy, con)
    per_exp_spike = (master_sell.groupby('expiry')
                     .agg(any_sustained_1=('sustained_above_1', lambda x: int((x>=1).any())),
                          any_sustained_2=('sustained_above_2', lambda x: int((x>=1).any())),
                          any_hit_1=('hit_1', lambda x: int((x>=1).any())),
                          any_hit_2=('hit_2', lambda x: int((x>=1).any())),
                          n_strikes_hit_1=('hit_1','sum'),
                          n_strikes_hit_2=('hit_2','sum'),
                          ).reset_index())
    j = feats.merge(per_exp_spike, on='expiry', how='inner')
    print(f"   Total expiries with both data: {len(j)}")
    print(f"   Base rate any_hit_1={j['any_hit_1'].mean()*100:.1f}%  any_hit_2={j['any_hit_2'].mean()*100:.1f}%  any_sustained_1={j['any_sustained_1'].mean()*100:.1f}%")
    for thr in [0.4, 0.6, 0.8, 1.0, 1.5]:
        sub = j[j['abs_move_pct'] <= thr]
        if len(sub):
            print(f"   abs_move ≤ {thr}%  · n={len(sub)} · spike rate any_hit_1={sub['any_hit_1'].mean()*100:.1f}%  any_sustained_1={sub['any_sustained_1'].mean()*100:.1f}%")
    j.to_csv(OUT/"skip_day_features.csv", index=False)

    print(f"\nAll outputs to: {OUT}")
    return master_buy, master_sell, basket_sum, ldf, j, ba, tail, fill_summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=int, default=0,
                        help="0=full both, 1=Strategy 1 only, 2=Strategy 2 only")
    args = parser.parse_args()
    main(args)
