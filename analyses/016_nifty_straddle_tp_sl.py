"""
ANALYSIS 016 — NIFTY Straddle/Strangle SELL with intraday TP/SL exits

Builds on 015 (which showed ATM straddle has fat left tail: median +₹20-50K/Cr
but worst day −₹850K/Cr). Rohan can't tolerate ₹1L/Cr loss. He wants TP/SL
exits + OTM variants tested. Key concern: fake stops.

Grid:
  - DTE buckets: E-1, E-2, E-3, E-4
  - Entry times: 09:30, 10:00, 10:30
  - Strike variants: ATM straddle, ATM±100 strangle, ATM±200 strangle
  - TP grid (₹/share drop in combined premium): [2, 3, 5, 8, 10, 15, 20]
  - SL grid (₹/share rise in combined premium): [5, 8, 10, 12, 15, 20, 25, 30]
  - SL confirmation modes: instant, confirm_3m, confirm_5m, intrabar_high

Sizing: 43 lots/Cr × 75 = 3,225 shares/Cr → ₹/share × 3,225 = ₹/Cr.
  ₹10K/Cr ≈ ₹3.10/share ; ₹25K/Cr ≈ ₹7.75/share ; ₹40K/Cr ≈ ₹12.40/share

Outputs (results/016_nifty_straddle_tp_sl/):
  per_trade_results.csv
  grid_summary.csv
  best_per_dte.csv
  fake_stop_comparison.csv
  tail_days_with_sl.csv
  summary.md
  heatmap_tpsl.png + ev_vs_tail.png
"""
from __future__ import annotations
from datetime import date, time, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sys, os
_env_root = os.environ.get("BACKTEST_ROOT")
if _env_root:
    ROOT = Path(_env_root)
else:
    try:
        ROOT = Path(__file__).resolve().parent.parent
    except NameError:
        ROOT = Path(os.getcwd())
sys.path.insert(0, str(ROOT))
from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, is_trading_day

STORE = ROOT / "data" / "parquet" / "instrument=NIFTY"
OUT   = ROOT / "results" / "016_nifty_straddle_tp_sl"
OUT.mkdir(parents=True, exist_ok=True)

# ─── Config ───────────────────────────────────────────────────────────────────
ENTRY_TIMES   = [time(9, 30), time(10, 0), time(10, 30)]
TIME_STOP     = time(15, 20)        # hard exit if neither TP/SL hit
SIM_END       = time(15, 25)        # last bar to read
DTE_BUCKETS   = [1, 2, 3, 4]
GRID          = 50                  # NIFTY strike grid

# Strike variants: offset (call_offset, put_offset) from ATM
# ATM straddle    → CE@ATM, PE@ATM
# 100pt strangle  → CE@ATM+100, PE@ATM-100
# 200pt strangle  → CE@ATM+200, PE@ATM-200
VARIANTS = {
    "ATM":      (0,   0),
    "OTM_100":  (100, -100),
    "OTM_200":  (200, -200),
}

TP_GRID = [2, 3, 5, 8, 10, 15, 20]                  # ₹/share drop
SL_GRID = [5, 8, 10, 12, 15, 20, 25, 30]            # ₹/share rise
CONFIRM_MODES = ["instant", "confirm_3m", "confirm_5m", "intrabar_high"]

NIFTY_LOT               = 75
NIFTY_E0_MARGIN_PER_LOT = 235_000
LOTS_PER_CR             = 1_00_00_000 / NIFTY_E0_MARGIN_PER_LOT  # ≈ 42.553
SHARES_PER_CR_USER      = 43 * NIFTY_LOT                          # 3,225

# Friction: default ₹100/lot/leg × 2 legs = ₹200/lot total per trade
FRICTION_DEFAULT_PER_LEG_RS  = 100
FRICTION_PER_LOT_AXIS        = 6
FRICTION_PER_LOT_MONARCH     = 10

# ─── Helpers ──────────────────────────────────────────────────────────────────
con = duckdb.connect()
PATH_GLOB = str(STORE / "**" / "*.parquet")


def trading_days_until_expiry(d: date, exp: date) -> int:
    cur = d
    n = 0
    while cur < exp:
        cur = date.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            n += 1
    return n


def nearest_weekly(d: date) -> date | None:
    for e in NIFTY_WEEKLY_EXPIRIES:
        if e >= d:
            return e
    return None


def load_fut_all() -> pd.DataFrame:
    df = con.execute(f"""
        WITH ranked AS (
            SELECT timestamp, expiry, open, high, low, close,
                   ROW_NUMBER() OVER (PARTITION BY timestamp ORDER BY expiry ASC) AS rn
            FROM read_parquet('{PATH_GLOB}', union_by_name=True)
            WHERE option_type='FUT'
        )
        SELECT timestamp, expiry, open, high, low, close FROM ranked WHERE rn = 1
    """).fetchdf()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["date"] = df["timestamp"].dt.date
    df["time"] = df["timestamp"].dt.time
    df = df[df["timestamp"].dt.weekday < 5]
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def load_day_options_window(d: date, exp: date, lo: int, hi: int) -> pd.DataFrame:
    """Pull full intraday OHLC + OI for all strikes in window — one query per day."""
    df = con.execute(f"""
        SELECT timestamp, strike, option_type, open, high, low, close, oi
        FROM read_parquet('{PATH_GLOB}', union_by_name=True)
        WHERE option_type IN ('CE','PE')
          AND expiry = DATE '{exp.isoformat()}'
          AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) = DATE '{d.isoformat()}'
          AND strike BETWEEN {lo} AND {hi}
    """).fetchdf()
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["time"] = df["timestamp"].dt.time
    df["strike"] = df["strike"].astype(int)
    return df.sort_values("timestamp").reset_index(drop=True)


def realized_vol_proxy_vix(fut_daily: pd.DataFrame, d: date, lookback: int = 20) -> float | None:
    prior = fut_daily[fut_daily["date"] < d].tail(lookback)
    if len(prior) < 5:
        return None
    rets = np.log(prior["close"] / prior["close"].shift(1)).dropna()
    if len(rets) < 5:
        return None
    return float(rets.std() * np.sqrt(252) * 100)


def build_minute_series(chain: pd.DataFrame, ce_strike: int, pe_strike: int,
                        entry_t: time) -> pd.DataFrame | None:
    """Build forward-filled 1-min combined-premium series for CE+PE legs
    from entry_t to SIM_END. Returns DataFrame with columns:
       time, ce_close, pe_close, ce_high, pe_high, combined_close, combined_high.
    Returns None if either leg has no data at all.
    """
    ce = chain[(chain.option_type == "CE") & (chain.strike == ce_strike)] \
            [["timestamp", "time", "open", "high", "low", "close"]] \
            .sort_values("timestamp").reset_index(drop=True)
    pe = chain[(chain.option_type == "PE") & (chain.strike == pe_strike)] \
            [["timestamp", "time", "open", "high", "low", "close"]] \
            .sort_values("timestamp").reset_index(drop=True)
    if ce.empty or pe.empty:
        return None

    # Parquet bars are stamped at end-of-minute (HH:MM:59). Floor to HH:MM:00 so
    # they line up with the minute index built below.
    ce["timestamp"] = ce["timestamp"].dt.floor("min")
    pe["timestamp"] = pe["timestamp"].dt.floor("min")
    ce["time"] = ce["timestamp"].dt.time
    pe["time"] = pe["timestamp"].dt.time

    # Filter to entry_t..SIM_END
    ce = ce[(ce["time"] >= entry_t) & (ce["time"] <= SIM_END)].copy()
    pe = pe[(pe["time"] >= entry_t) & (pe["time"] <= SIM_END)].copy()
    if ce.empty or pe.empty:
        return None

    # Build a master minute index from min of both first timestamps to SIM_END
    start_ts = max(ce["timestamp"].min(), pe["timestamp"].min())
    # already floored, but keep for safety
    start_ts = start_ts.floor("min")
    # end_ts = same day at SIM_END
    day = start_ts.date()
    end_ts = pd.Timestamp.combine(day, SIM_END).tz_localize(start_ts.tz)
    idx = pd.date_range(start=start_ts, end=end_ts, freq="1min")
    if len(idx) == 0:
        return None

    ce_i = ce.set_index("timestamp").reindex(idx).ffill()
    pe_i = pe.set_index("timestamp").reindex(idx).ffill()
    if ce_i["close"].isna().all() or pe_i["close"].isna().all():
        return None

    out = pd.DataFrame({
        "timestamp": idx,
        "ce_close":  ce_i["close"].values,
        "pe_close":  pe_i["close"].values,
        "ce_high":   ce_i["high"].values,
        "pe_high":   pe_i["high"].values,
    })
    out["time"] = pd.Series(idx).dt.time.values
    out["combined_close"] = out["ce_close"] + out["pe_close"]
    out["combined_high"]  = out["ce_high"]  + out["pe_high"]
    # Drop any rows where either close is still NaN (early-day gaps before first bar)
    out = out.dropna(subset=["combined_close"]).reset_index(drop=True)
    if out.empty:
        return None
    return out


def prepare_arrays(series: pd.DataFrame) -> dict:
    """Pre-extract numpy arrays + truncation mask once per context — avoids
    re-doing the iloc/Series lookups per (tp,sl,confirm) cell."""
    times = series["time"].values
    # mask for bars at or before TIME_STOP
    valid = np.array([t <= TIME_STOP for t in times])
    last_idx = int(np.where(valid)[0].max()) if valid.any() else 0
    closes = series["combined_close"].values.astype(float)
    highs  = series["combined_high"].values.astype(float)
    return {
        "closes":   closes,
        "highs":    highs,
        "times":    times,
        "last_idx": last_idx,
    }


def simulate_tpsl_fast(arrs: dict, entry_combined: float,
                       tp_rs: float, sl_rs: float, confirm: str) -> dict:
    """Vectorized walk. SL precedes TP on same bar (conservative).
    Returns dict with exit_reason, exit_time, exit_combined, hold_minutes.
    """
    closes   = arrs["closes"]
    highs    = arrs["highs"]
    times    = arrs["times"]
    last_idx = arrs["last_idx"]

    tp_thresh = entry_combined - tp_rs
    sl_thresh = entry_combined + sl_rs

    # Restrict to (1 .. last_idx) inclusive
    if last_idx < 1:
        return None
    c = closes[1:last_idx+1]
    h = highs[1:last_idx+1]
    t = times[1:last_idx+1]

    # Compute SL-trigger index per confirm mode
    sl_trigger_idx = None
    sl_fill_value  = None
    if confirm == "intrabar_high":
        h_safe = np.where(np.isnan(h), -np.inf, h)
        mask = h_safe >= sl_thresh
        if mask.any():
            sl_trigger_idx = int(mask.argmax())   # first True
            sl_fill_value  = sl_thresh             # pessimistic
    else:
        need = {"instant": 1, "confirm_3m": 3, "confirm_5m": 5}.get(confirm, 1)
        cond = c >= sl_thresh
        if need == 1:
            if cond.any():
                sl_trigger_idx = int(cond.argmax())
                sl_fill_value  = float(c[sl_trigger_idx])
        else:
            # find first index where `need` consecutive bars all True ending at that index
            # rolling sum trick: cumsum reset on False
            # build run-length where cond[i] resets to 0 on False
            run = np.zeros_like(cond, dtype=int)
            run[0] = 1 if cond[0] else 0
            for i in range(1, len(cond)):
                run[i] = run[i-1] + 1 if cond[i] else 0
            hits = np.where(run >= need)[0]
            if len(hits) > 0:
                sl_trigger_idx = int(hits[0])
                sl_fill_value  = float(c[sl_trigger_idx])

    # Compute TP-trigger index (first close at/below tp_thresh)
    tp_mask = c <= tp_thresh
    tp_trigger_idx = int(tp_mask.argmax()) if tp_mask.any() else None

    # Decide winner (SL precedes TP on tie)
    sl_t = sl_trigger_idx if sl_trigger_idx is not None else np.inf
    tp_t = tp_trigger_idx if tp_trigger_idx is not None else np.inf

    if sl_t <= tp_t and sl_t != np.inf:
        idx = sl_trigger_idx
        return {
            "exit_reason":   "SL",
            "exit_time":     t[idx].strftime("%H:%M"),
            "exit_combined": float(sl_fill_value),
            "hold_minutes":  idx + 1,
        }
    if tp_t < sl_t and tp_t != np.inf:
        idx = tp_trigger_idx
        return {
            "exit_reason":   "TP",
            "exit_time":     t[idx].strftime("%H:%M"),
            "exit_combined": float(c[idx]),
            "hold_minutes":  idx + 1,
        }

    # Time stop = last eligible bar
    return {
        "exit_reason":   "TIME",
        "exit_time":     t[-1].strftime("%H:%M"),
        "exit_combined": float(c[-1]),
        "hold_minutes":  len(c),
    }


# ─── Main ─────────────────────────────────────────────────────────────────────
def run():
    print("[1/6] Loading FUT data ...")
    fut = load_fut_all()
    fut_daily = fut.groupby("date").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).reset_index().sort_values("date").reset_index(drop=True)
    fut_daily["prev_close"] = fut_daily["close"].shift(1)
    print(f"  → {len(fut_daily)} trading days in FUT data")

    # ── Build day list per DTE bucket ──
    print("[2/6] Bucketing days by DTE ...")
    days_by_dte: dict[int, list[tuple[date, date]]] = {k: [] for k in DTE_BUCKETS}
    for d in sorted(fut["date"].unique()):
        nxt = nearest_weekly(d)
        if not nxt or nxt == d:
            continue
        n = trading_days_until_expiry(d, nxt)
        if n in DTE_BUCKETS:
            days_by_dte[n].append((d, nxt))
    for k, v in days_by_dte.items():
        print(f"  E-{k}: {len(v)} days")

    # ── Build the minute series ONCE per (date, entry, variant) ──
    # Cache structure: per_day_series[(date, entry_time, variant_name)] = {series, entry_combined, ...}
    print("[3/6] Building minute series per (day × entry × variant) ...")
    per_day_records = []  # base context: date, dte, entry, variant, entry_combined, series, day-feats
    skipped = []
    days_total = sum(len(v) for v in days_by_dte.values())
    days_done = 0

    for dte_n in DTE_BUCKETS:
        for d, exp in days_by_dte[dte_n]:
            days_done += 1
            day_fut = fut[fut["date"] == d]
            if day_fut.empty:
                skipped.append((d, exp, dte_n, "no_fut"))
                continue
            prev_row = fut_daily[fut_daily["date"] == d].iloc[0]
            prev_close = prev_row["prev_close"]
            day_open = float(day_fut.iloc[0]["open"])
            gap_pct = ((day_open - prev_close) / prev_close * 100) \
                        if prev_close and not np.isnan(prev_close) else np.nan

            until_1030 = day_fut[day_fut["time"] <= time(10, 30)]
            if len(until_1030) >= 2:
                op = float(until_1030.iloc[0]["open"])
                cl_1030 = float(until_1030.iloc[-1]["close"])
                intraday_pct_1030 = abs(cl_1030 - op) / op * 100 if op else np.nan
            else:
                intraday_pct_1030 = np.nan

            vix_proxy = realized_vol_proxy_vix(fut_daily, d)

            spot_open = float(day_fut.iloc[0]["close"])
            atm_open  = int(round(spot_open / GRID) * GRID)
            # Cover ATM ± 10 strikes (= ±500pts), sufficient for variants up to 200pt OTM
            # plus drift through the day
            chain = load_day_options_window(d, exp, atm_open - 10*GRID, atm_open + 10*GRID)
            if chain.empty:
                skipped.append((d, exp, dte_n, "no_chain"))
                continue

            for entry_t in ENTRY_TIMES:
                # ATM at entry time
                ent_fut = day_fut[day_fut["time"] >= entry_t]
                if ent_fut.empty:
                    continue
                spot_at_entry = float(ent_fut.iloc[0]["close"])
                atm_strike = int(round(spot_at_entry / GRID) * GRID)

                for variant, (ce_off, pe_off) in VARIANTS.items():
                    ce_strike = atm_strike + ce_off
                    pe_strike = atm_strike + pe_off
                    series = build_minute_series(chain, ce_strike, pe_strike, entry_t)
                    if series is None or len(series) < 2:
                        continue
                    entry_row = series.iloc[0]
                    entry_combined = float(entry_row["combined_close"])
                    if entry_combined <= 0 or np.isnan(entry_combined):
                        continue
                    per_day_records.append({
                        "date":       d,
                        "expiry":     exp,
                        "dte":        dte_n,
                        "weekday":    pd.Timestamp(d).day_name(),
                        "entry_time": entry_t.strftime("%H:%M"),
                        "variant":    variant,
                        "atm_strike": atm_strike,
                        "ce_strike":  ce_strike,
                        "pe_strike":  pe_strike,
                        "spot_entry": round(spot_at_entry, 2),
                        "entry_combined": round(entry_combined, 2),
                        "gap_pct":    round(gap_pct, 3) if not np.isnan(gap_pct) else np.nan,
                        "intraday_pct_to_1030": round(intraday_pct_1030, 3)
                                                    if not np.isnan(intraday_pct_1030) else np.nan,
                        "vix_proxy":  round(vix_proxy, 2) if vix_proxy is not None else np.nan,
                        "series":     series,
                    })
        print(f"  E-{dte_n} done [{days_done}/{days_total}]: contexts so far = {len(per_day_records)}")

    print(f"  → {len(per_day_records)} (day × entry × variant) contexts built")

    # ── Run TP × SL × confirm grid on each context ──
    print("[4/6] Running TP×SL×confirm grid ...")
    rows = []
    total_cells = len(per_day_records) * len(TP_GRID) * len(SL_GRID) * len(CONFIRM_MODES)
    print(f"  total sim cells: {total_cells:,}")
    cell = 0
    for ctx in per_day_records:
        series = ctx["series"]
        ent    = ctx["entry_combined"]
        arrs   = prepare_arrays(series)
        for tp in TP_GRID:
            for sl in SL_GRID:
                for cm in CONFIRM_MODES:
                    cell += 1
                    res = simulate_tpsl_fast(arrs, ent, tp, sl, cm)
                    if res is None:
                        continue
                    pnl_per_share = ent - res["exit_combined"]
                    pnl_per_lot_gross  = pnl_per_share * NIFTY_LOT
                    friction_default   = 2 * FRICTION_DEFAULT_PER_LEG_RS
                    pnl_per_lot_net    = pnl_per_lot_gross - friction_default
                    rs_per_cr          = pnl_per_lot_net * LOTS_PER_CR
                    # Axis / Monarch variants
                    pnl_per_lot_net_axis    = pnl_per_lot_gross - 2 * FRICTION_PER_LOT_AXIS
                    pnl_per_lot_net_monarch = pnl_per_lot_gross - 2 * FRICTION_PER_LOT_MONARCH
                    rs_per_cr_axis    = pnl_per_lot_net_axis * LOTS_PER_CR
                    rs_per_cr_monarch = pnl_per_lot_net_monarch * LOTS_PER_CR

                    rows.append({
                        "date":       ctx["date"],
                        "dte":        ctx["dte"],
                        "weekday":    ctx["weekday"],
                        "entry_time": ctx["entry_time"],
                        "variant":    ctx["variant"],
                        "tp_rs":      tp,
                        "sl_rs":      sl,
                        "confirm":    cm,
                        "atm_strike": ctx["atm_strike"],
                        "ce_strike":  ctx["ce_strike"],
                        "pe_strike":  ctx["pe_strike"],
                        "entry_combined": ctx["entry_combined"],
                        "exit_combined":  round(res["exit_combined"], 2),
                        "exit_reason":    res["exit_reason"],
                        "exit_time":      res["exit_time"],
                        "hold_minutes":   res["hold_minutes"],
                        "pnl_per_share":  round(pnl_per_share, 2),
                        "pnl_per_lot_net": round(pnl_per_lot_net, 0),
                        "rs_per_cr":         round(rs_per_cr, 0),
                        "rs_per_cr_axis":    round(rs_per_cr_axis, 0),
                        "rs_per_cr_monarch": round(rs_per_cr_monarch, 0),
                        "gap_pct":    ctx["gap_pct"],
                        "intraday_pct_to_1030": ctx["intraday_pct_to_1030"],
                        "vix_proxy":  ctx["vix_proxy"],
                    })
        if cell % 200_000 < 16:
            print(f"   ... {cell:,}/{total_cells:,} cells done — rows={len(rows):,}")

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "per_trade_results.csv", index=False)
    print(f"  → per_trade_results.csv ({len(df):,} rows)")

    # ── Grid summary ──
    print("[5/6] Aggregating grid summary ...")
    def agg_grp(sub: pd.DataFrame) -> pd.Series:
        n = len(sub)
        if n == 0:
            return pd.Series(dtype=float)
        rs = sub["rs_per_cr"].astype(float)
        ev = rs.mean()
        std = rs.std(ddof=0) if n > 1 else np.nan
        sharpe = (ev / std) if std and std > 0 else np.nan
        return pd.Series({
            "n":           n,
            "tp_hit_pct":  round((sub["exit_reason"] == "TP").mean() * 100, 1),
            "sl_hit_pct":  round((sub["exit_reason"] == "SL").mean() * 100, 1),
            "time_pct":    round((sub["exit_reason"] == "TIME").mean() * 100, 1),
            "mean_rs_cr":  round(ev, 0),
            "median_rs_cr":round(rs.median(), 0),
            "p10":         round(rs.quantile(0.10), 0),
            "p25":         round(rs.quantile(0.25), 0),
            "p75":         round(rs.quantile(0.75), 0),
            "p90":         round(rs.quantile(0.90), 0),
            "worst":       round(rs.min(), 0),
            "best":        round(rs.max(), 0),
            "expected_value": round(ev, 0),
            "sharpe_per_trade": round(sharpe, 3) if not np.isnan(sharpe) else np.nan,
            "pct_loss_worse_than_neg_25K": round((rs <= -25_000).mean() * 100, 1),
            "pct_loss_worse_than_neg_50K": round((rs <= -50_000).mean() * 100, 1),
            "pct_loss_worse_than_neg_100K":round((rs <= -100_000).mean() * 100, 1),
        })

    grid = df.groupby(["dte", "variant", "tp_rs", "sl_rs", "confirm"]) \
             .apply(agg_grp).reset_index()
    grid.to_csv(OUT / "grid_summary.csv", index=False)
    print(f"  → grid_summary.csv ({len(grid):,} rows)")

    # ── Best per DTE: rank by mean ₹/Cr, subject to constraint ──
    print("[6/6] Building best_per_dte, fake_stop_comparison, tail_days, charts, summary ...")
    constraint = (grid["pct_loss_worse_than_neg_50K"] <= 5.0) & (grid["n"] >= 20)
    best_rows = []
    for dte_n in DTE_BUCKETS:
        sub = grid[(grid["dte"] == dte_n) & constraint].copy()
        if sub.empty:
            continue
        sub = sub.sort_values("mean_rs_cr", ascending=False)
        for _, r in sub.head(10).iterrows():
            best_rows.append(r)
    best_per_dte = pd.DataFrame(best_rows)
    best_per_dte.to_csv(OUT / "best_per_dte.csv", index=False)
    print(f"  → best_per_dte.csv ({len(best_per_dte)} rows)")

    # ── Fake-stop comparison ──
    # For each (dte, variant, tp, sl), compare the 4 confirm modes side-by-side
    fs_rows = []
    keys = grid[["dte", "variant", "tp_rs", "sl_rs"]].drop_duplicates()
    # Restrict to the more interesting subset — those with at least one
    # confirm mode achieving mean_rs_cr ≥ 5,000 and n ≥ 20
    interesting = grid[(grid["mean_rs_cr"] >= 5_000) & (grid["n"] >= 20)]
    int_keys = interesting[["dte", "variant", "tp_rs", "sl_rs"]].drop_duplicates()
    for _, k in int_keys.iterrows():
        sub = grid[(grid["dte"] == k["dte"]) & (grid["variant"] == k["variant"]) &
                   (grid["tp_rs"] == k["tp_rs"]) & (grid["sl_rs"] == k["sl_rs"])]
        if sub.empty:
            continue
        out = {"dte": int(k["dte"]), "variant": k["variant"],
               "tp_rs": int(k["tp_rs"]), "sl_rs": int(k["sl_rs"])}
        for cm in CONFIRM_MODES:
            r = sub[sub["confirm"] == cm]
            if r.empty:
                continue
            r0 = r.iloc[0]
            out[f"{cm}_n"]          = int(r0["n"])
            out[f"{cm}_mean"]       = int(r0["mean_rs_cr"])
            out[f"{cm}_sl_hit_pct"] = float(r0["sl_hit_pct"])
            out[f"{cm}_tp_hit_pct"] = float(r0["tp_hit_pct"])
            out[f"{cm}_worst"]      = int(r0["worst"])
            out[f"{cm}_pct_neg50K"] = float(r0["pct_loss_worse_than_neg_50K"])
        fs_rows.append(out)
    fs_df = pd.DataFrame(fs_rows)
    if not fs_df.empty:
        fs_df = fs_df.sort_values(["dte", "variant", "tp_rs", "sl_rs"])
    fs_df.to_csv(OUT / "fake_stop_comparison.csv", index=False)
    print(f"  → fake_stop_comparison.csv ({len(fs_df)} rows)")

    # ── Tail days at the recommended rule(s) ──
    # Pick top rule per DTE (best mean_rs_cr under constraint). For that rule,
    # list every day with rs_per_cr < -40_000.
    tail_rows = []
    for dte_n in DTE_BUCKETS:
        sub = grid[(grid["dte"] == dte_n) & constraint]
        if sub.empty:
            continue
        top = sub.sort_values("mean_rs_cr", ascending=False).iloc[0]
        trade_sub = df[(df["dte"] == dte_n) &
                       (df["variant"] == top["variant"]) &
                       (df["tp_rs"]   == top["tp_rs"]) &
                       (df["sl_rs"]   == top["sl_rs"]) &
                       (df["confirm"] == top["confirm"])]
        bad = trade_sub[trade_sub["rs_per_cr"] < -40_000].copy()
        bad["recommended_rule"] = f"E-{dte_n} {top['variant']} TP{int(top['tp_rs'])}/SL{int(top['sl_rs'])}/{top['confirm']}"
        tail_rows.append(bad)
    if tail_rows:
        tail_df = pd.concat(tail_rows, ignore_index=True)
        tail_df = tail_df.sort_values("rs_per_cr")
    else:
        tail_df = pd.DataFrame()
    tail_df.to_csv(OUT / "tail_days_with_sl.csv", index=False)
    print(f"  → tail_days_with_sl.csv ({len(tail_df)} rows)")

    # ── Charts ──
    # (a) Heatmap of mean ₹/Cr across TP × SL for recommended (variant, DTE, confirm)
    # Pick the dte with the strongest mean_rs_cr top candidate
    if not best_per_dte.empty:
        head = best_per_dte.sort_values("mean_rs_cr", ascending=False).iloc[0]
        rec_dte     = int(head["dte"])
        rec_variant = head["variant"]
        rec_confirm = head["confirm"]
    else:
        rec_dte = 1; rec_variant = "ATM"; rec_confirm = "confirm_3m"

    pivot_data = grid[(grid["dte"] == rec_dte) & (grid["variant"] == rec_variant) &
                      (grid["confirm"] == rec_confirm)].copy()
    if not pivot_data.empty:
        piv = pivot_data.pivot(index="sl_rs", columns="tp_rs", values="mean_rs_cr")
        fig, ax = plt.subplots(figsize=(10, 7))
        im = ax.imshow(piv.values, cmap="RdYlGn", aspect="auto",
                       vmin=-40_000, vmax=40_000)
        ax.set_xticks(range(len(piv.columns)))
        ax.set_xticklabels([f"₹{int(c)}" for c in piv.columns])
        ax.set_yticks(range(len(piv.index)))
        ax.set_yticklabels([f"₹{int(r)}" for r in piv.index])
        ax.set_xlabel("Take Profit (₹/share drop)")
        ax.set_ylabel("Stop Loss (₹/share rise)")
        ax.set_title(f"Mean ₹/Cr · E-{rec_dte} · {rec_variant} · {rec_confirm}")
        for i in range(len(piv.index)):
            for j in range(len(piv.columns)):
                v = piv.values[i, j]
                if not np.isnan(v):
                    ax.text(j, i, f"{int(v):+,}", ha="center", va="center",
                            fontsize=8, color="black")
        fig.colorbar(im, ax=ax, label="₹/Cr")
        fig.tight_layout()
        fig.savefig(OUT / "heatmap_tpsl.png", dpi=140)
        plt.close(fig)

    # (b) EV vs tail-risk scatter (one point per grid cell, x=worst, y=mean, color=variant)
    fig, ax = plt.subplots(figsize=(11, 7))
    colors = {"ATM": "#ef4444", "OTM_100": "#3b82f6", "OTM_200": "#10b981"}
    for variant, c in colors.items():
        sub = grid[(grid["variant"] == variant) & (grid["n"] >= 20)]
        ax.scatter(sub["worst"], sub["mean_rs_cr"], s=8, alpha=0.25,
                   c=c, label=f"{variant} (n={len(sub):,} cells)")
    ax.axhline(0, color="black", lw=0.5)
    ax.axvline(-50_000, color="red", ls="--", lw=1.0, label="−₹50K/Cr worst")
    ax.axvline(-100_000, color="darkred", ls="--", lw=1.0, label="−₹1L/Cr worst")
    ax.set_xlabel("Worst day ₹/Cr (left tail)")
    ax.set_ylabel("Mean ₹/Cr (EV)")
    ax.set_title("EV vs Tail Risk · each point = one (TP, SL, confirm, DTE) cell")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "ev_vs_tail.png", dpi=140)
    plt.close(fig)

    # ── Summary.md ──
    md = build_summary(df, grid, best_per_dte, fs_df, tail_df, days_by_dte,
                       rec_dte, rec_variant, rec_confirm)
    (OUT / "summary.md").write_text(md)
    print(f"\nDone. Results in {OUT}")
    return df, grid, best_per_dte, fs_df, tail_df


def build_summary(df, grid, best_per_dte, fs_df, tail_df, days_by_dte,
                  rec_dte, rec_variant, rec_confirm) -> str:
    L = []
    L.append("# 016 — NIFTY Straddle/Strangle SELL · with TP/SL exits + fake-stop study\n")
    L.append(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M IST')}_\n")
    L.append("## Question (Rohan, 2026-05-11)\n")
    L.append("> \"I can't tolerate ₹1L/Cr loss. Stop at 25-40K/Cr. Test 100/200pt OTM straddles. Fake stops are real — confirm modes needed.\"\n")
    L.append("## Methodology\n")
    L.append("- **Data:** NIFTY 1-min OHLC parquet store (front-month FUT + CE/PE chain, ±10 strikes around ATM, ATM±500pts window).")
    L.append("- **Sizing:** ₹2.35L NIFTY E-0 margin/lot → 42.55 lots/Cr → **3,225 shares/Cr** (user-spec 43 lots × 75).")
    L.append(f"- **Friction (headline):** ₹{FRICTION_DEFAULT_PER_LEG_RS}/leg × 2 legs = ₹{2*FRICTION_DEFAULT_PER_LEG_RS}/lot. Also reported per-trade in CSV: Axis ₹{2*FRICTION_PER_LOT_AXIS}/lot, Monarch ₹{2*FRICTION_PER_LOT_MONARCH}/lot.")
    L.append(f"- **₹/share ↔ ₹/Cr (gross):** 1 ₹/share ≈ ₹3,225/Cr. ₹10K/Cr ≈ ₹3.10/share. ₹25K/Cr ≈ ₹7.75. ₹40K/Cr ≈ ₹12.40. ₹50K/Cr ≈ ₹15.50.")
    L.append("- **Strike variants (3):**")
    L.append("  - **ATM** straddle: CE@ATM, PE@ATM (ATM = round-to-50 of FUT close at entry).")
    L.append("  - **OTM_100** strangle: CE@ATM+100, PE@ATM−100.")
    L.append("  - **OTM_200** strangle: CE@ATM+200, PE@ATM−200.")
    L.append("- **Entry times:** 09:30, 10:00, 10:30.")
    L.append("- **DTE buckets:** E-1, E-2, E-3, E-4 (trading-day distance to next NIFTY weekly using `lib/expiry_calendar.NIFTY_WEEKLY_EXPIRIES`).")
    L.append(f"- **TP grid (₹/share drop):** {TP_GRID} → ₹{[round(x*SHARES_PER_CR_USER/1000) for x in TP_GRID]}K/Cr (gross).")
    L.append(f"- **SL grid (₹/share rise):** {SL_GRID} → ₹{[round(x*SHARES_PER_CR_USER/1000) for x in SL_GRID]}K/Cr (gross).")
    L.append("- **SL confirmation modes:**")
    L.append("  - `instant` — trigger on first 1-min close at/above SL.")
    L.append("  - `confirm_3m` — need 3 consecutive 1-min closes at/above SL.")
    L.append("  - `confirm_5m` — need 5 consecutive.")
    L.append("  - `intrabar_high` — pessimistic: trigger on any 1-min HIGH at/above SL, filled AT the SL line.")
    L.append("- **TP/SL precedence:** if both fire same bar, SL wins (conservative).")
    L.append("- **Time stop:** 15:20 if neither TP nor SL fires.")
    L.append("- **Forward-fill** within day for missing minute bars on either leg.")
    L.append("- **Slippage:** none beyond the intrabar-high pessimistic mode. 1-min bar close used otherwise.\n")

    L.append("## Sample size\n")
    L.append("| DTE | Trading days |")
    L.append("|-----|--------------|")
    for k in DTE_BUCKETS:
        L.append(f"| E-{k} | {len(days_by_dte[k])} |")
    L.append("")

    # ── Headline: best (variant, TP, SL, confirm) per (DTE × variant)
    L.append("## Headline — best (TP, SL, confirm) per (DTE × variant)\n")
    L.append("Constraint: `n ≥ 20` AND `%loss_days_worse_than_neg_50K ≤ 5%`.")
    L.append("Ranking: by **mean ₹/Cr** (expected value, since the user's pain is left tail not median).\n")
    L.append("| DTE | variant | TP ₹ | SL ₹ | confirm | n | tp% | sl% | time% | mean ₹/Cr | median | worst | %≤-50K |")
    L.append("|-----|---------|------|------|---------|---|-----|-----|-------|-----------|--------|-------|--------|")
    constraint_mask = (grid["pct_loss_worse_than_neg_50K"] <= 5.0) & (grid["n"] >= 20)
    for dte_n in DTE_BUCKETS:
        for variant in VARIANTS:
            sub = grid[(grid["dte"] == dte_n) & (grid["variant"] == variant) & constraint_mask]
            if sub.empty:
                L.append(f"| E-{dte_n} | {variant} | — | — | — | — | — | — | — | _(no cell meets constraint)_ | — | — | — |")
                continue
            top = sub.sort_values("mean_rs_cr", ascending=False).iloc[0]
            L.append(f"| E-{dte_n} | {variant} | {int(top['tp_rs'])} | {int(top['sl_rs'])} | {top['confirm']} | {int(top['n'])} | {top['tp_hit_pct']}% | {top['sl_hit_pct']}% | {top['time_pct']}% | {int(top['mean_rs_cr']):+,} | {int(top['median_rs_cr']):+,} | {int(top['worst']):+,} | {top['pct_loss_worse_than_neg_50K']}% |")
    L.append("")

    # ── Top 10 across ALL (DTE × variant)
    L.append("## Top 10 overall (highest mean ₹/Cr, tail-cap satisfied)\n")
    L.append("| DTE | variant | TP | SL | confirm | n | mean ₹/Cr | worst | %≤-50K | sl_hit% |")
    L.append("|-----|---------|----|----|---------|---|-----------|-------|--------|---------|")
    top10 = grid[constraint_mask].sort_values("mean_rs_cr", ascending=False).head(10)
    for _, r in top10.iterrows():
        L.append(f"| E-{int(r['dte'])} | {r['variant']} | {int(r['tp_rs'])} | {int(r['sl_rs'])} | {r['confirm']} | {int(r['n'])} | {int(r['mean_rs_cr']):+,} | {int(r['worst']):+,} | {r['pct_loss_worse_than_neg_50K']}% | {r['sl_hit_pct']}% |")
    L.append("")

    # ── Direct answer
    L.append("## Direct answer — best per variant\n")
    L.append("For each strike variant, the single best (TP, SL, confirm) by mean ₹/Cr under the tail-cap constraint:\n")
    L.append("| variant | DTE | TP | SL | confirm | n | mean ₹/Cr | median | worst | sl_hit% | tp_hit% |")
    L.append("|---------|-----|----|----|---------|---|-----------|--------|-------|---------|---------|")
    for variant in VARIANTS:
        sub = grid[(grid["variant"] == variant) & constraint_mask]
        if sub.empty:
            L.append(f"| {variant} | — | — | — | — | — | _(no cell meets constraint — see unconstrained below)_ | — | — | — | — |")
            continue
        top = sub.sort_values("mean_rs_cr", ascending=False).iloc[0]
        L.append(f"| {variant} | E-{int(top['dte'])} | {int(top['tp_rs'])} | {int(top['sl_rs'])} | {top['confirm']} | {int(top['n'])} | {int(top['mean_rs_cr']):+,} | {int(top['median_rs_cr']):+,} | {int(top['worst']):+,} | {top['sl_hit_pct']}% | {top['tp_hit_pct']}% |")
    L.append("")

    # If any variant has NO row meeting constraint, also show its unconstrained best to keep the picture honest
    L.append("Unconstrained best per variant (no tail cap — for reference):\n")
    L.append("| variant | DTE | TP | SL | confirm | n | mean ₹/Cr | median | worst | %≤-50K |")
    L.append("|---------|-----|----|----|---------|---|-----------|--------|-------|--------|")
    for variant in VARIANTS:
        sub = grid[(grid["variant"] == variant) & (grid["n"] >= 20)]
        if sub.empty:
            continue
        top = sub.sort_values("mean_rs_cr", ascending=False).iloc[0]
        L.append(f"| {variant} | E-{int(top['dte'])} | {int(top['tp_rs'])} | {int(top['sl_rs'])} | {top['confirm']} | {int(top['n'])} | {int(top['mean_rs_cr']):+,} | {int(top['median_rs_cr']):+,} | {int(top['worst']):+,} | {top['pct_loss_worse_than_neg_50K']}% |")
    L.append("")

    # ── Fake stop analysis
    L.append("## Fake-stop analysis — does waiting help?\n")
    L.append("For each (DTE × variant × TP × SL) cell where at least one confirm mode produced mean ₹/Cr ≥ 5K (n ≥ 20), compare the 4 confirm modes side-by-side.")
    L.append("Look for cells where `confirm_3m` or `confirm_5m` IMPROVES mean ₹/Cr vs `instant`, AND reduces sl_hit%.\n")
    if not fs_df.empty:
        # Compute confirm_3m vs instant delta on each row
        fs_show = fs_df.dropna(subset=["instant_mean", "confirm_3m_mean", "confirm_5m_mean"]).copy()
        fs_show["delta_3m_vs_instant"] = fs_show["confirm_3m_mean"] - fs_show["instant_mean"]
        fs_show["delta_5m_vs_instant"] = fs_show["confirm_5m_mean"] - fs_show["instant_mean"]
        fs_show["delta_intrabar_vs_instant"] = fs_show["intrabar_high_mean"] - fs_show["instant_mean"]
        # Top 15 cases where 3m beats instant by the most
        fs_top = fs_show.sort_values("delta_3m_vs_instant", ascending=False).head(15)
        L.append("**Top 15 cells where `confirm_3m` BEATS `instant` (Δ mean ₹/Cr):**\n")
        L.append("| DTE | variant | TP | SL | instant mean | 3m mean | Δ 3m | 5m mean | Δ 5m | intrabar | instant sl% | 3m sl% |")
        L.append("|-----|---------|----|----|--------------|---------|------|---------|------|----------|-------------|--------|")
        for _, r in fs_top.iterrows():
            L.append(f"| E-{int(r['dte'])} | {r['variant']} | {int(r['tp_rs'])} | {int(r['sl_rs'])} | "
                     f"{int(r['instant_mean']):+,} | {int(r['confirm_3m_mean']):+,} | {int(r['delta_3m_vs_instant']):+,} | "
                     f"{int(r['confirm_5m_mean']):+,} | {int(r['delta_5m_vs_instant']):+,} | "
                     f"{int(r['intrabar_high_mean']):+,} | {r['instant_sl_hit_pct']:.1f}% | {r['confirm_3m_sl_hit_pct']:.1f}% |")
        L.append("")
        avg_delta_3m = fs_show["delta_3m_vs_instant"].mean()
        avg_delta_5m = fs_show["delta_5m_vs_instant"].mean()
        avg_delta_intra = fs_show["delta_intrabar_vs_instant"].mean()
        avg_sl_instant = fs_show["instant_sl_hit_pct"].mean()
        avg_sl_3m      = fs_show["confirm_3m_sl_hit_pct"].mean()
        avg_sl_5m      = fs_show["confirm_5m_sl_hit_pct"].mean()
        avg_sl_intra   = fs_show["intrabar_high_sl_hit_pct"].mean()
        L.append("**Across all interesting cells (avg of {} cells):**\n".format(len(fs_show)))
        L.append("| confirm mode | avg Δ mean ₹/Cr vs instant | avg sl_hit% |")
        L.append("|--------------|---------------------------|-------------|")
        L.append(f"| instant       | (baseline)                | {avg_sl_instant:.1f}% |")
        L.append(f"| confirm_3m    | {int(avg_delta_3m):+,}    | {avg_sl_3m:.1f}% |")
        L.append(f"| confirm_5m    | {int(avg_delta_5m):+,}    | {avg_sl_5m:.1f}% |")
        L.append(f"| intrabar_high | {int(avg_delta_intra):+,} | {avg_sl_intra:.1f}% |")
        L.append("")
        # Verdict text — fill in numerically
        verdict_3m = "HELPS" if avg_delta_3m > 1_000 else ("HURTS" if avg_delta_3m < -1_000 else "NEUTRAL")
        verdict_5m = "HELPS" if avg_delta_5m > 1_000 else ("HURTS" if avg_delta_5m < -1_000 else "NEUTRAL")
        L.append(f"**Verdict on fake-stop confirmation:** `confirm_3m` is **{verdict_3m}** on average (Δ ≈ ₹{int(avg_delta_3m):+,}/Cr, sl% {avg_sl_instant - avg_sl_3m:+.1f}pp). `confirm_5m` is **{verdict_5m}** (Δ ≈ ₹{int(avg_delta_5m):+,}/Cr).")
        L.append(f"`intrabar_high` (pessimistic) underperforms `instant` by ₹{int(-avg_delta_intra):,}/Cr on average — the pessimistic fill assumption costs us that much; treat instant numbers as moderately optimistic, intrabar as pessimistic.\n")
    else:
        L.append("_No interesting cells (no cell achieved ≥₹5K/Cr mean with n≥20)._\n")

    # ── Tail days at recommended rule
    L.append(f"## Tail days at the recommended rule ({rec_variant}, E-{rec_dte}, {rec_confirm})\n")
    L.append("These are days where SL did NOT save us — loss exceeded ₹40K/Cr even with the stop. The cause is usually a one-bar gap right through the stop, or a slow drift where the stop fires late.\n")
    if not tail_df.empty:
        show = tail_df.head(25)
        L.append("| date | DTE | variant | weekday | entry | exit reason | exit time | hold min | entry comb | exit comb | ₹/share | ₹/Cr | gap% | intra-1030% | VIX-proxy |")
        L.append("|------|-----|---------|---------|-------|-------------|-----------|----------|-----------|-----------|---------|------|------|-------------|-----------|")
        for _, r in show.iterrows():
            vix = f"{r['vix_proxy']:.1f}" if pd.notna(r['vix_proxy']) else "n/a"
            gap = f"{r['gap_pct']:+.2f}" if pd.notna(r['gap_pct']) else "n/a"
            intra = f"{r['intraday_pct_to_1030']:.2f}" if pd.notna(r['intraday_pct_to_1030']) else "n/a"
            L.append(f"| {r['date']} | E-{int(r['dte'])} | {r['variant']} | {r['weekday']} | {r['entry_time']} | {r['exit_reason']} | {r['exit_time']} | {int(r['hold_minutes'])} | {r['entry_combined']} | {r['exit_combined']} | {r['pnl_per_share']} | {int(r['rs_per_cr']):+,} | {gap} | {intra} | {vix} |")
        L.append("")
    else:
        L.append("_No days worse than −₹40K/Cr at the recommended rule._\n")

    # ── Recommendation
    L.append("## Recommendation for ≥₹5Cr live deployment\n")
    if not best_per_dte.empty:
        head = best_per_dte.sort_values("mean_rs_cr", ascending=False).iloc[0]
        L.append(f"**Best risk-adjusted setup:** E-{int(head['dte'])} {head['variant']} · TP ₹{int(head['tp_rs'])}/share · SL ₹{int(head['sl_rs'])}/share · `{head['confirm']}` confirmation.")
        L.append(f"- Mean EV: **₹{int(head['mean_rs_cr']):+,}/Cr**, median ₹{int(head['median_rs_cr']):+,}/Cr.")
        L.append(f"- Worst single day: **₹{int(head['worst']):+,}/Cr** ({head['pct_loss_worse_than_neg_50K']}% of days worse than −₹50K).")
        L.append(f"- TP fires {head['tp_hit_pct']}% · SL fires {head['sl_hit_pct']}% · time-stop {head['time_pct']}%. ")
        L.append(f"- Sample: {int(head['n'])} (day × entry) trades — about {int(head['n']) / 3 / (253/12):.1f} opportunities per month per entry slot.")
        L.append("")
        L.append("Practical guidance:")
        L.append("- Position SL orders **at broker level** (not mental). 1-min bar closes used here; in live trading use the broker's stop trigger logic.")
        L.append(f"- The `{head['confirm']}` mode means: ")
        if head['confirm'] == "instant":
            L.append("  > exit immediately on first 1-min bar close at SL — simplest, fastest.")
        elif head['confirm'] == "confirm_3m":
            L.append("  > wait for 3 consecutive 1-min closes above SL before exiting — reduces fake stops.")
        elif head['confirm'] == "confirm_5m":
            L.append("  > wait for 5 consecutive 1-min closes above SL — slowest, but most resistant to noise.")
        else:
            L.append("  > stop fills at first 1-min high touching SL line — most pessimistic, treat as live worst-case.")
        L.append("- Sizing at ₹5Cr: 215 lots (5 × 43). Margin block ≈ ₹11.75L (5 × ₹2.35L).")
        L.append("- Friction at Axis ₹6/lot/leg: net P&L per lot improves by ₹188 → ~₹8K/Cr boost vs the default ₹100/leg headline.")
    L.append("")
    L.append("## Caveats\n")
    L.append("- **1-min bar slippage**: the simulator uses bar closes (and high for intrabar mode). Real fills may be worse on spikes — use intrabar mode as the realistic worst case.")
    L.append("- **No transaction-by-transaction order book**: STT, exchange, GST not separately modeled here — only ₹/lot/leg cost. Friction CSV columns show Axis ₹12/lot and Monarch ₹20/lot variants alongside default ₹200/lot.")
    L.append("- **VIX proxy used elsewhere; not used in TP/SL grid filter** — the grid is unconditional. Future work: re-run conditional on VIX regime.")
    L.append("- **Friction sensitivity** is large: ₹100/leg default vs ₹6/leg Axis = ₹188/lot per trade ≈ ₹8K/Cr per trade. At 200 lots and 100 trading days = ~₹16L/yr just from friction choice.")
    L.append("- **Forward-fill within day**: if a leg goes 5-10min without prints (common on deep-OTM), the simulator uses last seen close. Doesn't affect ATM much, can mute extremes on OTM_200.")
    L.append("- **Sample size**: 195 trading days per DTE max. E-3, E-4 have fewer days. Tail estimates wide.")
    L.append("- **TP/SL precedence**: SL wins on tie-bar. Conservative.\n")

    L.append("## Files\n")
    L.append("- `per_trade_results.csv` — every (date, DTE, entry, variant, TP, SL, confirm) simulated trade")
    L.append("- `grid_summary.csv` — aggregated stats per (dte, variant, tp_rs, sl_rs, confirm)")
    L.append("- `best_per_dte.csv` — top 10 per DTE under tail-cap constraint")
    L.append("- `fake_stop_comparison.csv` — confirm-mode side-by-side")
    L.append("- `tail_days_with_sl.csv` — days worse than −₹40K/Cr at recommended rules")
    L.append("- `heatmap_tpsl.png` · `ev_vs_tail.png`")

    return "\n".join(L)


if __name__ == "__main__":
    run()
