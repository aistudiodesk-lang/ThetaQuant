"""030 · Fill-timing scorecard — close the learning loop.

Scores every real fill (data/fill_timing.jsonl) against the day's minute-bar premium
path: what % of the day's PEAK premium did we capture, and how many minutes before the
peak did we sell? This quantifies the 30-40% premium-timing leak (analyses 027/028) on
ACTUAL trades, so the tool keeps learning from what we really did.

Usage:  python3.11 analyses/030_fill_timing_scorecard.py
Output: results/030_fill_timing_scorecard/scored.csv  + one line appended to FINDINGS_LOG.md
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib import fill_learning as FL  # noqa: E402

IST = pytz.timezone("Asia/Kolkata")
OUT = ROOT / "results" / "030_fill_timing_scorecard"


def main():
    res = FL.score_fills(limit=10000)
    scored, summ = res.get("scored", []), res.get("summary", {})
    OUT.mkdir(parents=True, exist_ok=True)
    if scored:
        import pandas as pd
        df = pd.DataFrame(scored)
        df.to_csv(OUT / "scored.csv", index=False)
        print(df.head(10).to_string(index=False))
    print("\nSUMMARY:", summ, res.get("note", ""))

    # Append a dated finding (only when there is data to learn from)
    if summ.get("n_rated"):
        stamp = datetime.now(IST).strftime("%Y-%m-%d")
        cap = summ.get("avg_capture_pct")
        early = summ.get("avg_mins_early")
        epct = summ.get("sold_early_pct")
        line = (f"- {stamp} · 030 (fill-timing scorecard) · {summ['n_rated']} real fills scored vs "
                f"minute-bar premium path · avg capture {cap}% of day-peak · sold {early} min before peak "
                f"on avg · {epct}% of fills were too early. Quantifies the 027/028 timing leak on ACTUAL trades.")
        fl = ROOT / "FINDINGS_LOG.md"
        existing = fl.read_text() if fl.exists() else ""
        if line.split(" · ")[2] not in existing or stamp not in existing:
            with fl.open("a") as f:
                f.write("\n" + line + "\n")
            print("\nAppended finding to FINDINGS_LOG.md")
    else:
        print("\nNo rated fills yet — scorecard populates as index fills are marked filled.")


if __name__ == "__main__":
    main()
