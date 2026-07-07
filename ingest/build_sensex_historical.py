"""
Ingest SENSEX historical data (GFDL format, BFO contracts + BSE indices spot)
located at: /Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data/SENSEX

Folder layout:
  SENSEX/
    Sensex(Future and Options)_Contractwise/
      <YEAR>/<MONTH_YEAR>/BFO_CONTRACT_<DDMMYYYY>.zip   ← daily option/FUT zips
    Sensex(Spot)/
      <YEAR>/<MONTH_YEAR>.zip                            ← monthly spot zip (one CSV per day inside)

Ticker format inside option zips:
  SENSEX06MAY2569100PE.BFO     → SENSEX, expiry 6-May-2025, strike 69100, PE
  SENSEX06MAY25FUT.BFO         → SENSEX, expiry 6-May-2025, FUT

Spot ticker:
  SENSEX.BSE_IDX               → SENSEX index spot

Output: appends to data/parquet/instrument=SENSEX/year=YYYY/month=MM/<hash>.parquet
        in same canonical schema as existing NIFTY data.
        Tracks ingested files in data/manifest.parquet (sha256 dedup).

Run: python3 ingest/build_sensex_historical.py [--dry-run] [--max-files N]
"""
from __future__ import annotations
import argparse, hashlib, io, re, sys, zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ingest.common import CANONICAL_COLS, empty_row, sha256_of

DATA_ROOT = ROOT / "data" / "parquet"
MANIFEST = ROOT / "data" / "manifest.parquet"
REJECTS = ROOT / "results" / "ingestion_rejects.csv"

SOURCE = Path("/Users/rohanshah/Desktop/AI Instructions/Trading Developments/Options Data/SENSEX")
FNO = SOURCE / "Sensex(Future and Options)_Contractwise"
SPOT = SOURCE / "Sensex(Spot)"

_MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
           "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}


def parse_sensex_ticker(t: str) -> dict:
    """Parse 'SENSEX06MAY2569100PE.BFO' or 'SENSEX06MAY25FUT.BFO'."""
    t = t.strip().upper()
    if t.endswith(".BFO"): t = t[:-4]
    if t.endswith(".NFO"): t = t[:-4]
    m = re.match(r"^(SENSEX|BSX)(\d{2})([A-Z]{3})(\d{2})(\d+)?([CP]E|FUT)$", t)
    if not m:
        raise ValueError(f"unparseable SENSEX ticker: {t}")
    _, dd, mmm, yy, strike, optype = m.groups()
    year = 2000 + int(yy)
    if mmm not in _MONTHS: raise ValueError(f"bad month {mmm} in {t}")
    exp = date(year, _MONTHS[mmm], int(dd))
    return {"instrument": "SENSEX", "expiry": exp,
            "strike": int(strike) if strike else None,
            "option_type": "FUT" if optype == "FUT" else optype}


def parse_options_csv(content: bytes, source_label: str) -> tuple[list, list]:
    rejects = []
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        return [], [f"{source_label}: read error — {e}"]
    cols = {"Ticker","Date","Time","Open","High","Low","Close","Volume"}
    if not cols.issubset(df.columns):
        return [], [f"{source_label}: missing cols {cols - set(df.columns)}"]
    rows = []
    oi_col = "Open Interest" if "Open Interest" in df.columns else None
    for i, rec in enumerate(df.itertuples(index=False)):
        try:
            meta = parse_sensex_ticker(rec.Ticker)
            ts = pd.to_datetime(f"{rec.Date} {rec.Time}",
                                 format="%d/%m/%Y %H:%M:%S").tz_localize("Asia/Kolkata")
            r = empty_row()
            r.update(meta)
            r["timestamp"] = ts
            r["source"] = "GFDL_HISTORICAL"
            r["open"] = float(rec.Open); r["high"] = float(rec.High)
            r["low"] = float(rec.Low); r["close"] = float(rec.Close)
            r["volume"] = int(rec.Volume)
            if oi_col:
                try: r["oi"] = int(df.iloc[i][oi_col])
                except: r["oi"] = None
            r["bar_minutes"] = 1
            if r["expiry"]: r["dte"] = (r["expiry"] - ts.date()).days
            rows.append(r)
        except Exception as e:
            rejects.append(f"{source_label}: ticker={rec.Ticker} — {e}")
    return rows, rejects


def parse_spot_csv(content: bytes, source_label: str) -> tuple[list, list]:
    """Parse BSE_INDICES_*.csv — extract only SENSEX.BSE_IDX rows."""
    rejects = []
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        return [], [f"{source_label}: read error — {e}"]
    df = df[df["Ticker"] == "SENSEX.BSE_IDX"]
    if df.empty:
        return [], [f"{source_label}: no SENSEX.BSE_IDX rows"]
    rows = []
    for rec in df.itertuples(index=False):
        try:
            ts = pd.to_datetime(f"{rec.Date} {rec.Time}",
                                 format="%d/%m/%Y %H:%M:%S").tz_localize("Asia/Kolkata")
            r = empty_row()
            r.update({
                "timestamp": ts, "source": "GFDL_HISTORICAL",
                "instrument": "SENSEX", "expiry": None, "strike": None,
                "option_type": "SPOT",
                "open": float(rec.Open), "high": float(rec.High),
                "low": float(rec.Low), "close": float(rec.Close),
                "volume": int(rec.Volume), "oi": None, "bar_minutes": 1,
                "dte": None,
            })
            rows.append(r)
        except Exception as e:
            rejects.append(f"{source_label}: parse — {e}")
    return rows, rejects


def load_manifest() -> set[str]:
    if not MANIFEST.exists(): return set()
    return set(pd.read_parquet(MANIFEST)["sha256"].tolist())


def append_manifest(sha: str, src: str, n: int):
    new = pd.DataFrame([{"sha256": sha, "source_path": src, "rows": n,
                          "ingested_at": datetime.now().isoformat()}])
    if MANIFEST.exists():
        old = pd.read_parquet(MANIFEST)
        new = pd.concat([old, new], ignore_index=True)
    new.to_parquet(MANIFEST, index=False)


def write_partition(rows: list, instrument: str, year: int, month: int, src_label: str):
    if not rows: return 0
    df = pd.DataFrame(rows, columns=CANONICAL_COLS)
    part = DATA_ROOT / f"instrument={instrument}" / f"year={year}" / f"month={month:02d}"
    part.mkdir(parents=True, exist_ok=True)
    file_id = hashlib.md5(src_label.encode()).hexdigest()[:12]
    out = part / f"sensex_hist_{file_id}.parquet"
    if out.exists():
        old = pd.read_parquet(out)
        df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp","instrument","expiry","strike","option_type"],
                                  keep="last")
    df.to_parquet(out, index=False)
    return len(df)


def main(dry: bool, max_files: int | None):
    print(f"[start] SENSEX historical ingest from {SOURCE}")
    seen = load_manifest()
    print(f"[manifest] {len(seen)} files already ingested")

    all_rejects = []
    files_processed = 0
    total_rows = 0

    # ── Options/FUT zips ──
    print(f"\n[OPTIONS] walking {FNO}")
    for z in sorted(FNO.rglob("BFO_CONTRACT_*.zip")):
        if max_files and files_processed >= max_files:
            print(f"  [stop] hit --max-files {max_files}"); break
        sha = sha256_of(z)
        if sha in seen:
            print(f"  [skip] already in manifest: {z.name}"); continue
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".csv"): continue
                content = zf.read(name)
                src_label = f"{z.relative_to(SOURCE)}/{name}"
                rows, rej = parse_options_csv(content, src_label)
                all_rejects.extend(rej)
                if rows:
                    # Group rows by year/month of timestamp
                    df = pd.DataFrame(rows)
                    df["_ts_dt"] = pd.to_datetime(df["timestamp"])
                    for (yr, mo), grp in df.groupby([df["_ts_dt"].dt.year, df["_ts_dt"].dt.month]):
                        rows_grp = grp.drop(columns=["_ts_dt"]).to_dict("records")
                        if not dry:
                            n = write_partition(rows_grp, "SENSEX", int(yr), int(mo), src_label)
                            total_rows += len(rows_grp)
                            print(f"  [+] {z.name}/{name} → {len(rows_grp):,} rows · part {yr}/{mo:02d}")
                        else:
                            print(f"  [DRY] {z.name}/{name} → would write {len(rows_grp):,} rows")
        if not dry:
            append_manifest(sha, str(z), 0)   # rows summed across the zip
        files_processed += 1

    # ── Spot zips ──
    print(f"\n[SPOT] walking {SPOT}")
    for z in sorted(SPOT.rglob("*.zip")):
        if max_files and files_processed >= max_files: break
        sha = sha256_of(z)
        if sha in seen:
            print(f"  [skip] already in manifest: {z.name}"); continue
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".csv"): continue
                content = zf.read(name)
                src_label = f"{z.relative_to(SOURCE)}/{name}"
                rows, rej = parse_spot_csv(content, src_label)
                all_rejects.extend(rej)
                if rows:
                    df = pd.DataFrame(rows)
                    df["_ts_dt"] = pd.to_datetime(df["timestamp"])
                    for (yr, mo), grp in df.groupby([df["_ts_dt"].dt.year, df["_ts_dt"].dt.month]):
                        rows_grp = grp.drop(columns=["_ts_dt"]).to_dict("records")
                        if not dry:
                            n = write_partition(rows_grp, "SENSEX", int(yr), int(mo), src_label)
                            total_rows += len(rows_grp)
                            print(f"  [+] {z.name}/{name} → {len(rows_grp):,} rows · part {yr}/{mo:02d}")
        if not dry:
            append_manifest(sha, str(z), 0)
        files_processed += 1

    # Save rejects
    if all_rejects:
        REJECTS.parent.mkdir(parents=True, exist_ok=True)
        with REJECTS.open("a") as f:
            for r in all_rejects[:1000]:
                f.write(r + "\n")
        print(f"\n[reject] {len(all_rejects)} rejected records (first 1000 saved to {REJECTS})")

    print(f"\n[done] processed {files_processed} files · added {total_rows:,} rows")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-files", type=int, default=None)
    args = ap.parse_args()
    main(args.dry_run, args.max_files)
