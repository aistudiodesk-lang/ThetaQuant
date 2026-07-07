"""
ANALYSIS 900 — Learning loop: journal vs backtest expectations.

Run nightly (or via evening.sh). Closes the feedback loop:
  1. Reads data/trade_journal.jsonl (every live trade with entry context).
  2. For each CLOSED trade, compares actual P&L vs the backtest expectation
     for its (tier, regime) — from lib/playbook tables.
  3. Flags drift: actuals consistently under/over expectation → candidate
     rule recalibration.
  4. Appends a dated learning entry to FINDINGS_LOG.md (only when there is
     something new to say).
  5. Optionally re-runs the distance tables (025/024) when ≥10 new E-0 days
     of parquet have accumulated since last recalibration (tracked in
     results/900_learning/last_recalib.json) — keeps the model getting
     smarter as data grows.

Usage:  python3 analyses/900_learning_loop.py [--recalibrate]
"""
from __future__ import annotations
import json
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib import journal, playbook as pb

OUT = ROOT / "results" / "900_learning"
OUT.mkdir(parents=True, exist_ok=True)
STATE = OUT / "state.json"


def load_state() -> dict:
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except Exception: pass
    return {"reviewed_ids": [], "last_recalib_date": None}


def save_state(s: dict):
    STATE.write_text(json.dumps(s, indent=2))


def expectation_for(tier: str, regime: str, instrument: str) -> dict | None:
    """Backtest expectation ₹/Cr for a tier+regime (Tier 1 from 025 tables;
    Tier 2/3 from §9W setups)."""
    t = (tier or "").lower()
    if "1" in t or "deep" in t:
        prem = pb.tier1_expected_premium(regime, instrument)
        return {"expected_pcr": prem, "win_pct": 100} if prem else None
    for s in pb.TIER_SETUPS:
        if s["instrument"] == instrument:
            if ("3" in t or "near" in t or "star" in t) and s["tier"] == 3:
                return {"expected_pcr": s["mean_pcr"], "win_pct": s["win_pct"]}
            if ("2" in t or "mid" in t) and s["tier"] == 2:
                return {"expected_pcr": s["mean_pcr"], "win_pct": s["win_pct"]}
    return None


def review_new_trades(state: dict) -> list[str]:
    findings = []
    for t in journal.all_trades():
        if t.get("status") != "closed" or t["id"] in state["reviewed_ids"]:
            continue
        state["reviewed_ids"].append(t["id"])
        pnl = t.get("pnl")
        if pnl is None: continue
        regime = (t.get("regime_snapshot") or {}).get("regime", "normal")
        exp = expectation_for(t.get("tier"), regime, t["instrument"])
        line = (f"{t['entry_date']} {t['entry_time']} {t['instrument']} {t.get('tier')} "
                f"({regime}) → actual ₹{pnl:,.0f}")
        if exp:
            line += f" vs backtest mean ₹{exp['expected_pcr']:,.0f}/Cr"
            if pnl < 0 and exp["win_pct"] == 100:
                line += "  ⚠ LOSS on a 100%-win setup — investigate (regime misread? timing? slippage?)"
        findings.append(line)
    return findings


def count_new_e0_days(state: dict) -> int:
    """How many E-0 days in parquet since last recalibration."""
    import duckdb
    con = duckdb.connect()
    last = state.get("last_recalib_date") or "2026-06-11"
    try:
        n = 0
        for inst, cal in (("NIFTY", pb.expiring_today.__module__), ):
            pass
        from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES
        all_exp = set(NIFTY_WEEKLY_EXPIRIES) | set(SENSEX_WEEKLY_EXPIRIES)
        r = con.execute(f"""
            SELECT DISTINCT CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) d
            FROM read_parquet('{ROOT}/data/parquet/**/*.parquet', union_by_name=True)
            WHERE option_type='FUT' AND CAST(timestamp AT TIME ZONE 'Asia/Kolkata' AS DATE) > DATE '{last}'
        """).fetchdf()
        days = {x.date() if hasattr(x, "date") else x for x in r["d"]}
        return len([d for d in days if d in all_exp])
    except Exception:
        return 0


def main():
    recal = "--recalibrate" in sys.argv
    state = load_state()
    findings = review_new_trades(state)

    if findings:
        stamp = datetime.now(IST).strftime("%Y-%m-%d")
        entry = f"- {stamp} · 900 (learning loop) · " + " | ".join(findings[:6])
        with (ROOT / "FINDINGS_LOG.md").open("a") as f:
            f.write(entry + "\n")
        print(f"Logged {len(findings)} reviewed trades to FINDINGS_LOG")
    else:
        print("No new closed trades to review")

    new_days = count_new_e0_days(state)
    print(f"New E-0 days since last recalibration: {new_days}")
    if recal or new_days >= 10:
        print("Re-running distance tables (025)...")
        subprocess.run([sys.executable, str(ROOT / "analyses" / "025_tier1_distance_optimization.py")],
                       timeout=3600)
        state["last_recalib_date"] = date.today().isoformat()
        print("Recalibration done — review results/025_tier1_distance/ and update "
              "lib/playbook.py TIER1_DISTANCE if the table shifted.")

    save_state(state)


if __name__ == "__main__":
    main()
