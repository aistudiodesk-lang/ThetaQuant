#!/usr/bin/env python3.11
"""
smoke_test.py — functional check of the whole Theta Quant platform.

Runs against a live dashboard (default http://127.0.0.1:8000). Checks every page
renders (200), every API returns valid JSON without 5xx, and core lib functions
work. Exit code 0 = all pass, 1 = failures. The QA agent runs this after any change.

Usage:
  python3.11 scripts/smoke_test.py            # against localhost:8000
  python3.11 scripts/smoke_test.py --base http://192.168.1.233:8000
"""
from __future__ import annotations
import sys, json, time, argparse, urllib.request, urllib.error

BASE = "http://127.0.0.1:8000"
PASS, FAIL = [], []


def _get(path, method="GET", expect_json=False, timeout=20):
    url = BASE + path
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = r.status
            body = r.read()
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read()
    except Exception as e:
        return None, str(e)
    if expect_json:
        try:
            return code, json.loads(body)
        except Exception as e:
            return code, f"BAD JSON: {e}"
    return code, body


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail and not ok else ''}")


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    print(f"Smoke test → {BASE}\n")

    # ── 1. Pages render (200) ──
    print("PAGES")
    pages = ["/overview", "/playbook", "/manipulation/SENSEX", "/chain/SENSEX",
             "/report", "/report-full", "/index/monthly", "/index/long",
             "/cc/investment", "/cc/otm", "/cc/itm", "/commodity", "/dummy", "/alerts",
             "/margin", "/recommend/NIFTY", "/builder", "/expiry/timing"]
    for p in pages:
        code, _ = _get(p)
        check(f"GET {p}", code == 200, f"got {code}")

    # ── 2. APIs return JSON without 5xx ──
    print("\nAPIs")
    apis = [
        "/api/report-full", "/api/cc/holdings", "/api/cc/stockwise", "/api/cc/monitor", "/api/cc/suggestions",
        "/api/futures/m2m", "/api/dummy/list", "/api/reporting/days",
        "/api/live_alerts", "/api/maxpain",
        "/api/recommendations/NIFTY", "/api/recommend/NIFTY",
        "/api/playbook/recommendations?instrument=NIFTY",
        "/api/playbook/regime?instrument=NIFTY", "/api/playbook/next_action?instrument=NIFTY",
        "/api/expiry/timing", "/api/margin-ledger",
        "/api/journal", "/api/import/types", "/api/health",
        "/api/desk/positions?group=Index",
        "/api/cc/levels?current=2900&high52=3050&lot=300",
        "/api/covered-call/payoff?fut_entry=3000&strike=3100&premium=40&spot=3050&lots=10&lot_size=200",
    ]
    for a in apis:
        code, data = _get(a, expect_json=True, timeout=45)
        ok = code == 200 and isinstance(data, (dict, list))
        check(f"GET {a.split('?')[0]}", ok, f"got {code}: {str(data)[:60]}")

    # ── 3. Monitor endpoints (POST) ──
    print("\nMONITORS")
    for a in ["/api/dummy/check"]:
        code, data = _get(a, method="POST", expect_json=True)
        check(f"POST {a}", code == 200 and isinstance(data, dict), f"got {code}")

    # ── 3b. Reporting day-view + edit round-trip ──
    # Regression guard: a journal trade tagged with a NON-INDEX instrument
    # (e.g. 'NIFTY50' mistag, covered-call equity, commodity) must NOT 500 the
    # day-view. _expired_worthless()/is_e0() raised ValueError on unknown
    # instruments → whole /report page failed to load → "edit trades not working".
    print("\nREPORTING / EDIT")
    code, days = _get("/api/reporting/days", expect_json=True)
    check("GET /api/reporting/days", code == 200 and isinstance(days, dict))
    day_list = (days or {}).get("days", []) if isinstance(days, dict) else []
    # every day that has trades must render its day-view without a 5xx
    bad_days = []
    for dd in day_list:
        dt = dd.get("date")
        c, _ = _get(f"/api/reporting/day?date={dt}", expect_json=True, timeout=45)
        if c != 200:
            bad_days.append(f"{dt}:{c}")
    check("GET /api/reporting/day (every day, no 5xx)", not bad_days,
          "5xx on: " + ", ".join(bad_days[:6]))
    # non-index instrument must not blow up the day-view (synthetic, self-cleaning)
    import json as _json, urllib.request as _u
    def _post_json(path, payload):
        req = _u.Request(BASE + path, method="POST",
                         data=_json.dumps(payload).encode(),
                         headers={"Content-Type": "application/json"})
        try:
            with _u.urlopen(req, timeout=20) as r:
                return r.status, _json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return None, str(e)
    tid = None
    try:
        sc, sr = _post_json("/api/journal/trade", {
            "instrument": "NIFTY50", "tier": "?", "strategy_group": "Expiry",
            "strategy_name": "SMOKE_NONINDEX_DELETE", "entry_date": "2098-12-05",
            "legs": [{"strike": 24000, "side": "CE", "qty": -50, "price": 1.0}]})
        tid = (sr or {}).get("trade", {}).get("id") if isinstance(sr, dict) else None
        c, _ = _get("/api/reporting/day?date=2098-12-05", expect_json=True)
        check("day-view survives non-index instrument", c == 200, f"got {c}")
        # amend (edit) round-trip on that synthetic trade
        if tid:
            ac, ar = _post_json("/api/reporting/amend", {
                "id": tid, "legs": [{"strike": 25000, "side": "CE", "qty": -75,
                                     "price": 2.0, "demat": ""}],
                "meta": {"strategy_name": "SMOKE_EDITED", "tier": "Tier 1"}})
            ok = ac == 200 and isinstance(ar, dict) and ar.get("saved")
            check("POST /api/reporting/amend (edit persists)", ok, f"got {ac}: {str(ar)[:60]}")
            c2, d2 = _get("/api/reporting/day?date=2098-12-05", expect_json=True)
            edited = any(s.get("strategy_name") == "SMOKE_EDITED"
                         for s in (d2 or {}).get("strategies", [])) if isinstance(d2, dict) else False
            check("edit reflected in day-view", edited)
    finally:
        if tid:
            _u.urlopen(_u.Request(BASE + f"/api/reporting/strategy/{tid}",
                                  method="DELETE"), timeout=20).read()

    # ── 4. Core lib functions ──
    print("\nLIBS")
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from lib import journal, dummy, covered_call, holdings, full_report, expiry_calendar
        check("lib.journal.all_trades", isinstance(journal.all_trades(), list))
        check("lib.holdings.load_holdings", isinstance(holdings.load_holdings(), list))
        check("lib.full_report.load_report", "trades" in full_report.load_report())
        from datetime import date
        check("expiry_calendar.is_e0", expiry_calendar.is_e0(date(2026, 6, 16), "NIFTY") is True)
        cb = covered_call.payoff_buy_write(3000, 3100, 40, 3050, 10, 200)
        check("covered_call.payoff", cb["max_profit"] == 280000)
        check("dummy.grid_strikes", dummy.grid_strikes("NIFTY", 24000, 2, "strangle")["ce_strike"] == 24500)
    except Exception as e:
        check("lib import", False, str(e))

    # ── summary ──
    print(f"\n{'='*46}")
    print(f"PASS {len(PASS)} · FAIL {len(FAIL)}")
    if FAIL:
        print("FAILURES:", ", ".join(FAIL))
        sys.exit(1)
    print("✓ all green")
    sys.exit(0)


if __name__ == "__main__":
    main()
