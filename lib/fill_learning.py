"""Closing the learning loop — score each actual fill against the day's premium path.

Reads the fill-timing feed (data/fill_timing.jsonl, written on every fill) and joins
it to the minute-bar option history (data/parquet, index only) to answer:
  "On this fill, what % of the day's PEAK premium did I capture, and how many minutes
   before the peak did I sell?"  → the 30-40% premium-timing leak, measured per fill.

For a SELL (short premium) higher premium = better, so capture% = fill / day-peak.
Index (NIFTY/SENSEX) fills have minute data; stock fills are skipped (no intraday store).
Bulk DuckDB query per fill-day (no per-row loops over parquet).
"""
from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
FILLS = ROOT / "data" / "fill_timing.jsonl"


def _read_fills() -> list[dict]:
    if not FILLS.exists():
        return []
    out = []
    for line in FILLS.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _mins(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _score_one(con, fill: dict) -> dict:
    inst = (fill.get("instrument") or "").upper()
    date = fill.get("date") or (fill.get("ts") or "")[:10]
    ce_k, pe_k = fill.get("ce_strike"), fill.get("pe_strike")
    fill_prem = fill.get("combined")
    base = {"date": date, "time": fill.get("time_ist") or (fill.get("ts") or "")[11:16],
            "instrument": inst, "name": fill.get("name"), "ce_strike": ce_k, "pe_strike": pe_k,
            "fill_premium": fill_prem, "capture_pct": None, "day_peak": None,
            "peak_time": None, "mins_early": None, "note": ""}
    strikes = [int(s) for s in (ce_k, pe_k) if s]
    if inst not in ("NIFTY", "SENSEX") or not strikes or fill_prem in (None, ""):
        base["note"] = "no intraday data (stock or incomplete fill)"
        return base
    g = ROOT / "data" / "parquet" / f"instrument={inst}" / "**" / "*.parquet"
    try:
        df = con.execute(f"""
            SELECT timestamp, strike, option_type, close, dte
            FROM read_parquet('{g}', union_by_name=True)
            WHERE option_type IN ('CE','PE') AND strike IN ({','.join(map(str, strikes))})
              AND CAST(timestamp AS DATE) = DATE '{date}'
        """).df()
    except Exception as e:
        base["note"] = f"query failed: {str(e)[:50]}"
        return base
    if df.empty:
        base["note"] = "no minute data for that day"
        return base
    df = df[df["dte"] == df["dte"].min()]                       # nearest expiry
    df["t"] = df["timestamp"].dt.strftime("%H:%M")
    ce = df[(df["strike"] == ce_k) & (df["option_type"] == "CE")].groupby("t")["close"].last() if ce_k else None
    pe = df[(df["strike"] == pe_k) & (df["option_type"] == "PE")].groupby("t")["close"].last() if pe_k else None
    if ce is not None and pe is not None:
        combined = ce.add(pe, fill_value=0)
    else:
        combined = ce if ce is not None else pe
    if combined is None or combined.empty:
        base["note"] = "no matching strike series"
        return base
    peak = float(combined.max()); peak_t = str(combined.idxmax())
    base["day_peak"] = round(peak, 2)
    base["peak_time"] = peak_t
    base["capture_pct"] = round(fill_prem / peak * 100, 1) if peak else None
    ft, pt = _mins(base["time"]), _mins(peak_t)
    if ft is not None and pt is not None:
        base["mins_early"] = pt - ft        # +ve = peak came AFTER you sold (sold too early)
    base["left_on_table"] = round(peak - fill_prem, 2) if peak >= fill_prem else 0
    return base


def score_fills(limit: int = 200) -> dict:
    fills = _read_fills()
    if not fills:
        return {"scored": [], "summary": {"n": 0}, "note": "No fills logged yet — they appear as you mark trades filled."}
    try:
        import duckdb
        con = duckdb.connect()
    except Exception as e:
        return {"scored": [], "summary": {"n": 0}, "note": f"duckdb unavailable: {e}"}
    scored = [_score_one(con, f) for f in fills[-limit:]]
    scored.reverse()
    rated = [s for s in scored if s.get("capture_pct") is not None]
    summ = {"n": len(scored), "n_rated": len(rated)}
    if rated:
        summ["avg_capture_pct"] = round(sum(s["capture_pct"] for s in rated) / len(rated), 1)
        early = [s["mins_early"] for s in rated if s.get("mins_early") is not None]
        if early:
            summ["avg_mins_early"] = round(sum(early) / len(early))
            summ["sold_early_pct"] = round(sum(1 for m in early if m > 0) / len(early) * 100)
    return {"scored": scored, "summary": summ}
