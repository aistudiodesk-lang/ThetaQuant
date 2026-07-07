"""Covered-call eligibility signals — technical-indicator engine + verdict.

Ported & adapted from the Design-folder "Covered Call Analyzer" (Streamlit) into
Theta Quant's flow. Pure-pandas indicator math is copied verbatim (it is generic);
the DATA FETCH is rewired to our own Kite layer (lib/kite_historical) instead of
yfinance, and results feed the CC desk eligibility column.

Public entry points:
    snapshot(symbol)                  -> dict of indicators + bull/bear bias
    eligibility(symbol, leg='CE')     -> ('GREEN'|'YELLOW'|'RED'|'REJECT'|'UNKNOWN', [reasons])
    verdict(symbol, leg='CE')         -> {**snapshot, 'verdict':..., 'reasons':[...]}

All cached in-process (daily candles move slowly) so a desk scan of ~30 names is
cheap. Never writes anywhere; read-only Kite history.
"""
from __future__ import annotations
import time
import threading
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- indicators
# (copied from Design/.../src/indicators/technical.py — generic OHLCV math)

def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    roll_up = up.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    roll_dn = down.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = roll_up / roll_dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return line, sig, hist


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def dma(close: pd.Series, length: int) -> pd.Series:
    return close.rolling(length).mean()


def find_pivots(df: pd.DataFrame, left: int = 5, right: int = 5):
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    n = len(df)
    for i in range(left, n - right):
        if h[i] == max(h[i - left:i + right + 1]):
            highs.append((i, float(h[i])))
        if l[i] == min(l[i - left:i + right + 1]):
            lows.append((i, float(l[i])))
    return highs, lows


def fit_trendline(pivots, min_touches: int = 2, tolerance_pct: float = 0.015):
    if len(pivots) < min_touches:
        return None
    best = None
    for i in range(len(pivots)):
        for j in range(i + 1, len(pivots)):
            x1, y1 = pivots[i]; x2, y2 = pivots[j]
            if x2 == x1:
                continue
            slope = (y2 - y1) / (x2 - x1)
            intercept = y1 - slope * x1
            touches = 0
            for x, y in pivots:
                proj = slope * x + intercept
                if proj <= 0:
                    continue
                if abs(y - proj) / proj <= tolerance_pct:
                    touches += 1
            if touches >= min_touches and (best is None or touches > best[2]):
                best = (slope, intercept, touches, x2)
    return best


def trend_state(df: pd.DataFrame) -> dict:
    if len(df) < 60:
        return {"state": "unknown", "dma_stack": "n/a", "slope": None, "distance_pct": None}
    c = df["Close"]
    d20 = dma(c, 20).iloc[-1]
    d50 = dma(c, 50).iloc[-1]
    d200 = dma(c, 200).iloc[-1] if len(df) >= 200 else None
    last = float(c.iloc[-1])
    stack = []
    if pd.notna(d20): stack.append(("20", d20))
    if pd.notna(d50): stack.append(("50", d50))
    if d200 is not None and pd.notna(d200): stack.append(("200", d200))
    stack_sorted_desc = all(stack[i][1] >= stack[i + 1][1] for i in range(len(stack) - 1)) if len(stack) > 1 else False
    stack_sorted_asc = all(stack[i][1] <= stack[i + 1][1] for i in range(len(stack) - 1)) if len(stack) > 1 else False
    above_all = all(last > v for _, v in stack)
    below_all = all(last < v for _, v in stack)
    state = "sideways"
    if above_all and stack_sorted_desc:
        state = "bullish"
    elif below_all and stack_sorted_asc:
        state = "bearish"
    elif above_all:
        state = "weak_bullish"
    elif below_all:
        state = "weak_bearish"
    highs, lows = find_pivots(df.tail(120).reset_index(drop=True))
    n = len(df.tail(120))
    line = None
    if state in ("bullish", "weak_bullish"):
        line = fit_trendline(lows)
    elif state in ("bearish", "weak_bearish"):
        line = fit_trendline(highs)
    slope, intercept, touches = (line[0], line[1], line[2]) if line else (None, None, None)
    dist_pct = None
    if slope is not None:
        proj = slope * (n - 1) + intercept
        if proj > 0:
            dist_pct = float((last - proj) / proj)
    return {"state": state, "dma_stack": "/".join(f"{lbl}:{int(v)}" for lbl, v in stack),
            "slope": slope, "distance_pct": dist_pct, "trendline_touches": touches}


def gap_analysis(df: pd.DataFrame, min_pct: float = 0.005, lookback: int = 60) -> dict:
    if len(df) < 5:
        return {"recent_gap": None, "unfilled_up_below": False, "breakaway_recent": False}
    sub = df.tail(lookback).copy()
    sub["prev_close"] = sub["Close"].shift(1)
    sub["gap_pct"] = (sub["Open"] - sub["prev_close"]) / sub["prev_close"]
    sub = sub.dropna()
    last_close = float(df["Close"].iloc[-1])
    gaps = []
    for idx, row in sub.iterrows():
        if abs(row["gap_pct"]) < min_pct:
            continue
        filled = False
        post = sub.loc[sub.index > idx]
        if row["gap_pct"] > 0 and (post["Low"] <= row["prev_close"]).any():
            filled = True
        if row["gap_pct"] < 0 and (post["High"] >= row["prev_close"]).any():
            filled = True
        gaps.append({"date": idx, "pct": float(row["gap_pct"]), "filled": filled,
                     "open": float(row["Open"]), "prev_close": float(row["prev_close"])})
    most_recent = gaps[-1] if gaps else None
    unfilled_up_below = any(g["pct"] > 0 and not g["filled"] and g["prev_close"] < last_close for g in gaps)
    unfilled_down_above = any(g["pct"] < 0 and not g["filled"] and g["prev_close"] > last_close for g in gaps)
    breakaway_up = False
    breakaway_down = False
    if gaps:
        v_avg = df["Volume"].tail(20).mean()
        for g in gaps[-10:]:
            if abs(g["pct"]) < 0.015:
                continue
            vol = df.loc[g["date"], "Volume"] if g["date"] in df.index else None
            if vol is None or not v_avg or vol <= 1.5 * v_avg:
                continue
            if g["pct"] > 0: breakaway_up = True
            else: breakaway_down = True
    return {"recent_gap": most_recent,
            "unfilled_up_below": unfilled_up_below,
            "unfilled_down_above": unfilled_down_above,
            "breakaway_up_recent": breakaway_up,
            "breakaway_down_recent": breakaway_down,
            "breakaway_recent": breakaway_up or breakaway_down,
            "gap_count_60d": len(gaps)}


def breakout_state(df: pd.DataFrame) -> dict:
    if len(df) < 60:
        return {"state": "none", "level": None, "failed": False}
    c = df["Close"]
    last = float(c.iloc[-1])
    high20 = df["High"].rolling(20).max().shift(1)
    last_high20 = float(high20.iloc[-1]) if pd.notna(high20.iloc[-1]) else None
    vol = df["Volume"]
    v_avg = float(vol.tail(20).mean())
    last_vol = float(vol.iloc[-1])
    recent = df.tail(5)
    confirmed = False
    level = None
    for i in range(len(recent)):
        idx = recent.index[i]
        h20_i = high20.loc[idx] if idx in high20.index else None
        if h20_i is not None and pd.notna(h20_i) and recent["Close"].iloc[i] > h20_i:
            v_i = recent["Volume"].iloc[i]
            if v_avg and v_i > 1.5 * v_avg:
                confirmed = True
                level = float(h20_i)
                break
    failed = False
    last10 = df.tail(15)
    for i in range(len(last10) - 3):
        idx = last10.index[i]
        h20_i = high20.loc[idx] if idx in high20.index else None
        if h20_i is None or pd.isna(h20_i):
            continue
        if last10["Close"].iloc[i] > h20_i:
            tail3 = last10.iloc[i + 1:i + 4]
            if (tail3["Close"] < h20_i).any():
                failed = True
                break
    state = "confirmed" if confirmed else ("testing" if last_high20 and last >= 0.99 * last_high20 else "none")
    return {"state": state, "level": level or last_high20, "failed": failed,
            "vol_ratio": (last_vol / v_avg) if v_avg else None}


def divergence(close: pd.Series, ind: pd.Series, lookback: int = 20) -> str:
    if len(close) < lookback or len(ind) < lookback:
        return "none"
    c_tail = close.tail(lookback); i_tail = ind.tail(lookback)
    c_max_idx = c_tail.idxmax(); i_max_idx = i_tail.idxmax()
    c_min_idx = c_tail.idxmin(); i_min_idx = i_tail.idxmin()
    if c_max_idx == c_tail.index[-1] and i_max_idx != i_tail.index[-1]:
        if c_tail.iloc[-1] > c_tail.iloc[0] and i_tail.iloc[-1] < i_tail.max():
            return "bearish"
    if c_min_idx == c_tail.index[-1] and i_min_idx != i_tail.index[-1]:
        if c_tail.iloc[-1] < c_tail.iloc[0] and i_tail.iloc[-1] > i_tail.min():
            return "bullish"
    return "none"


def macd_state(line, sig, hist) -> str:
    if len(hist) < 5 or pd.isna(hist.iloc[-1]):
        return "unknown"
    h_now = hist.iloc[-1]; h_prev = hist.iloc[-2]
    crossed_up = line.iloc[-1] > sig.iloc[-1] and line.iloc[-2] <= sig.iloc[-2]
    crossed_dn = line.iloc[-1] < sig.iloc[-1] and line.iloc[-2] >= sig.iloc[-2]
    if crossed_up: return "fresh_bull_cross"
    if crossed_dn: return "fresh_bear_cross"
    if h_now > 0 and h_now > h_prev: return "bull_expanding"
    if h_now > 0 and h_now < h_prev: return "bull_fading"
    if h_now < 0 and h_now < h_prev: return "bear_expanding"
    if h_now < 0 and h_now > h_prev: return "bear_fading"
    return "neutral"


# ---------------------------------------------------------------- data fetch
# Rewired from the analyzer's yfinance layer to OUR Kite history.

_LOCK = threading.Lock()
_INSTR_CACHE = {"ts": 0.0, "map": {}}      # tradingsymbol -> instrument_token (NSE EQ)
_CANDLE_CACHE = {}                          # symbol -> (ts, DataFrame)
_INSTR_TTL = 12 * 3600
_CANDLE_TTL = 4 * 3600


def _rk():
    """Lazy RateLimitedKite — raises if no Kite session on disk."""
    from lib.kite_historical import RateLimitedKite
    return RateLimitedKite()


def _nse_token_map(rk) -> dict:
    now = time.time()
    with _LOCK:
        if _INSTR_CACHE["map"] and now - _INSTR_CACHE["ts"] < _INSTR_TTL:
            return _INSTR_CACHE["map"]
    instr = rk.instruments("NSE")
    m = {}
    for row in instr:
        if row.get("instrument_type") == "EQ" and row.get("segment") == "NSE":
            m[row["tradingsymbol"].upper()] = row["instrument_token"]
    with _LOCK:
        _INSTR_CACHE["map"] = m
        _INSTR_CACHE["ts"] = now
    return m


def _clean_symbol(symbol: str) -> str:
    """Strip exchange prefix / common suffixes so 'NSE:GRASIM' -> 'GRASIM'."""
    s = (symbol or "").upper().strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s.replace(" ", "")


def _yahoo_ohlcv(symbol: str, years: int = 2) -> pd.DataFrame:
    """Free, KITE-INDEPENDENT daily OHLC from Yahoo Finance (NSE = symbol.NS).
    Technicals/news must work regardless of any broker session."""
    import urllib.request as _ur, json as _json, ssl as _ssl
    sym = _clean_symbol(symbol)
    rng = "5y" if years > 2 else "2y"
    for suffix in (".NS", ".BO"):                    # NSE then BSE fallback
        try:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}{suffix}"
                   f"?interval=1d&range={rng}")
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False; ctx.verify_mode = _ssl.CERT_NONE
            with _ur.urlopen(req, timeout=12, context=ctx) as r:
                j = _json.loads(r.read())
            res = (j.get("chart", {}).get("result") or [None])[0]
            if not res:
                continue
            ts = res.get("timestamp") or []
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            if not ts or not q.get("close"):
                continue
            df = pd.DataFrame({
                "Date": pd.to_datetime(ts, unit="s"),
                "Open": q.get("open"), "High": q.get("high"), "Low": q.get("low"),
                "Close": q.get("close"), "Volume": q.get("volume"),
            }).dropna(subset=["Close"])
            return df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            continue
    return pd.DataFrame()


def _kite_ohlcv(symbol: str, years: int = 2) -> pd.DataFrame:
    sym = _clean_symbol(symbol)
    rk = _rk()
    tok = _nse_token_map(rk).get(sym)
    if not tok:
        return pd.DataFrame()
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=int(365.25 * years) + 10)
    rows = rk.historical(tok, from_dt, to_dt, interval="day", oi=False)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={"open": "Open", "high": "High", "low": "Low",
                                            "close": "Close", "volume": "Volume", "date": "Date"})
    df["Date"] = pd.to_datetime(df["Date"])
    return df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]


def daily_ohlcv(symbol: str, years: int = 2) -> pd.DataFrame:
    """Daily OHLCV — FREE Yahoo first (no Kite needed), Kite as fallback. Cached."""
    sym = _clean_symbol(symbol)
    now = time.time()
    cached = _CANDLE_CACHE.get(sym)
    if cached and now - cached[0] < _CANDLE_TTL:
        return cached[1]
    df = _yahoo_ohlcv(sym, years)
    if df.empty:                                     # only touch Kite if Yahoo failed
        try:
            df = _kite_ohlcv(sym, years)
        except Exception:
            df = pd.DataFrame()
    _CANDLE_CACHE[sym] = (now, df)
    return df


def weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    return daily.resample("W-FRI").agg(agg).dropna()


# ---------------------------------------------------------------- snapshot
# (bias-scoring logic ported from src/indicators/snapshot.py)

def _safe_last(series, default=None):
    if series is None or len(series) == 0:
        return default
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def _build_snapshot(daily: pd.DataFrame) -> dict:
    weekly = weekly_from_daily(daily)
    rsi_d = rsi(daily["Close"]); rsi_w = rsi(weekly["Close"]) if not weekly.empty else None
    md_l, md_s, md_h = macd(daily["Close"])
    mw_l, mw_s, mw_h = (macd(weekly["Close"]) if not weekly.empty else (None, None, None))
    atr_d = atr(daily, 14)
    d20 = dma(daily["Close"], 20); d50 = dma(daily["Close"], 50)
    d200 = dma(daily["Close"], 200) if len(daily) >= 200 else None
    last = float(daily["Close"].iloc[-1])
    high_52w = float(daily["High"].tail(252).max())
    low_52w = float(daily["Low"].tail(252).min())
    trend = trend_state(daily)
    gaps = gap_analysis(daily)
    bo = breakout_state(daily)
    rsi_div = divergence(daily["Close"], rsi_d)
    macd_d_st = macd_state(md_l, md_s, md_h)
    macd_w_st = macd_state(mw_l, mw_s, mw_h) if mw_l is not None else "unknown"

    bullish = 0; bearish = 0
    rd = _safe_last(rsi_d, 50)
    rw = _safe_last(rsi_w, 50) if rsi_w is not None else 50
    if rd > 60: bullish += 12
    elif rd > 50: bullish += 6
    if rd < 40: bearish += 12
    elif rd < 50: bearish += 6
    if rw > 60: bullish += 10
    elif rw > 50: bullish += 5
    if rw < 40: bearish += 10
    elif rw < 50: bearish += 5
    if macd_d_st in ("fresh_bull_cross", "bull_expanding"): bullish += 12
    if macd_d_st in ("fresh_bear_cross", "bear_expanding"): bearish += 12
    if macd_w_st in ("fresh_bull_cross", "bull_expanding"): bullish += 10
    if macd_w_st in ("fresh_bear_cross", "bear_expanding"): bearish += 10
    if trend["state"] == "bullish": bullish += 18
    elif trend["state"] == "weak_bullish": bullish += 9
    elif trend["state"] == "bearish": bearish += 18
    elif trend["state"] == "weak_bearish": bearish += 9
    if bo["state"] == "confirmed": bullish += 18
    elif bo["state"] == "testing": bullish += 8
    if bo["failed"]: bearish += 12
    if gaps.get("breakaway_up_recent"): bullish += 10
    if gaps.get("breakaway_down_recent"): bearish += 10
    if gaps.get("unfilled_up_below"): bullish += 6
    if gaps.get("unfilled_down_above"): bearish += 6
    if rsi_div == "bearish": bearish += 8
    if rsi_div == "bullish": bullish += 8
    pct_off_high = (last - high_52w) / high_52w if high_52w else 0
    if pct_off_high < -0.20: bearish += 8
    elif pct_off_high < -0.10: bearish += 4
    if pct_off_high > -0.03: bullish += 6

    return {
        "spot": last, "high_52w": high_52w, "low_52w": low_52w, "pct_off_high": pct_off_high,
        "rsi_d": rd, "rsi_w": rw, "rsi_divergence": rsi_div,
        "macd_d_state": macd_d_st, "macd_w_state": macd_w_st, "macd_d_hist": _safe_last(md_h),
        "atr_d": _safe_last(atr_d), "atr_pct": (_safe_last(atr_d) / last) if last and _safe_last(atr_d) else None,
        "dma20": _safe_last(d20), "dma50": _safe_last(d50),
        "dma200": _safe_last(d200) if d200 is not None else None,
        "trend_state": trend["state"], "trend_distance_pct": trend.get("distance_pct"),
        "trend_touches": trend.get("trendline_touches"),
        "breakout_state": bo["state"], "breakout_failed": bo["failed"], "breakout_level": bo.get("level"),
        "gap_unfilled_up_below": gaps.get("unfilled_up_below"),
        "gap_unfilled_down_above": gaps.get("unfilled_down_above"),
        "gap_breakaway_up_recent": gaps.get("breakaway_up_recent"),
        "gap_breakaway_down_recent": gaps.get("breakaway_down_recent"),
        "bullish_score": bullish, "bearish_score": bearish,
    }


def snapshot(symbol: str) -> dict:
    """Full indicator snapshot for an NSE equity symbol. {'error':...} on failure."""
    try:
        daily = daily_ohlcv(symbol)
    except Exception as e:
        return {"error": str(e)[:120]}
    if daily.empty:
        return {"error": "no_price_data"}
    snap = _build_snapshot(daily)
    snap["symbol"] = _clean_symbol(symbol)
    return snap


# ---------------------------------------------------------------- eligibility
# (verdict logic ported from src/scoring/eligibility.py)

def evaluate_eligibility(snap: dict, leg: str = "CE") -> tuple[str, list]:
    if not snap or snap.get("error"):
        return "UNKNOWN", [snap.get("error", "no_data") if snap else "no_data"]
    vetoes = []
    bull = snap.get("bullish_score", 0); bear = snap.get("bearish_score", 0)
    rsi_d = snap.get("rsi_d") or 50; rsi_w = snap.get("rsi_w") or 50
    bo = snap.get("breakout_state")
    macd_d = snap.get("macd_d_state", ""); macd_w = snap.get("macd_w_state", "")
    pct_off_high = snap.get("pct_off_high") or 0

    if leg == "CE":
        if bo == "confirmed": vetoes.append("confirmed breakout in last 5 sessions")
        if rsi_w > 70: vetoes.append(f"weekly RSI {rsi_w:.0f} > 70")
        if rsi_d > 75: vetoes.append(f"daily RSI {rsi_d:.0f} > 75")
        if macd_w in ("fresh_bull_cross", "bull_expanding") and macd_d in ("fresh_bull_cross", "bull_expanding"):
            vetoes.append("MACD bullish on both timeframes")
        if pct_off_high > -0.03: vetoes.append("within 3% of 52w high")
        if snap.get("gap_breakaway_up_recent"): vetoes.append("recent breakaway gap up")
        if vetoes:
            return "REJECT", vetoes
        if bear >= 40 and bull <= 25: return "GREEN", _ce_reasons(snap, bull, bear)
        if bear >= 25 and bull <= 35: return "YELLOW", _ce_reasons(snap, bull, bear)
        return "RED", _ce_reasons(snap, bull, bear)
    else:  # PE
        if rsi_w < 30: vetoes.append(f"weekly RSI {rsi_w:.0f} < 30 (oversold breakdown risk)")
        if macd_d in ("fresh_bear_cross", "bear_expanding") and macd_w in ("fresh_bear_cross", "bear_expanding"):
            vetoes.append("MACD bearish on both timeframes")
        if snap.get("trend_state") in ("bearish", "weak_bearish"):
            vetoes.append("trend bearish — wrong side for short put")
        if vetoes:
            return "REJECT", vetoes
        if bull >= 40 and bear <= 25: return "GREEN", _pe_reasons(snap, bull, bear)
        if bull >= 25 and bear <= 35: return "YELLOW", _pe_reasons(snap, bull, bear)
        return "RED", _pe_reasons(snap, bull, bear)


def _ce_reasons(snap, bull, bear):
    out = [f"bias bear={bear} bull={bull}", f"trend={snap.get('trend_state')}",
           f"breakout={snap.get('breakout_state')}",
           f"RSI d={snap.get('rsi_d', 0):.0f}/w={snap.get('rsi_w', 0):.0f}",
           f"MACD d={snap.get('macd_d_state')}/w={snap.get('macd_w_state')}"]
    if snap.get("gap_unfilled_up_below"):
        out.append("⚠ unfilled gap-up below price")
    return out


def _pe_reasons(snap, bull, bear):
    return [f"bias bull={bull} bear={bear}", f"trend={snap.get('trend_state')}",
            f"RSI d={snap.get('rsi_d', 0):.0f}/w={snap.get('rsi_w', 0):.0f}",
            f"MACD d={snap.get('macd_d_state')}/w={snap.get('macd_w_state')}"]


def verdict(symbol: str, leg: str = "CE") -> dict:
    """One-call: snapshot + eligibility verdict, flattened for the desk UI."""
    snap = snapshot(symbol)
    v, reasons = evaluate_eligibility(snap, leg)
    out = dict(snap)
    out["symbol"] = _clean_symbol(symbol)
    out["leg"] = leg
    out["verdict"] = v
    out["reasons"] = reasons
    return out
