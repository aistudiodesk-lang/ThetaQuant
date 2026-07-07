"""
lib/live_monitor.py — ALWAYS-ON live alerting that runs server-side (independent
of any open browser tab). Detects what the browser-only RGB monitor + the
schedule-only Telegram bot could not:

  1. Air-pocket / trend-break  — a fast directional move after a tight range
     (today's case: range ~0.25% → NIFTY −1%). Per-tick move + move-from-open.
  2. Range expansion           — intraday range blows past a threshold.
  3. Position danger           — any OPEN short option whose sold strike the
     spot is now near/through (would have caught the 23950 PE as NIFTY fell).

evaluate() is pure: give it a snapshot + open legs + prior state, it returns
(alerts, new_state). The /api/live_alerts endpoint + the Telegram bot loop call it.
Each alert carries a stable `key` so consumers dedup (fire once per condition).
"""
from __future__ import annotations

# thresholds (%)
FAST_MOVE_TICK = 0.30     # move since last tick (~1 min) → fast move
MOVE_FROM_OPEN_WARN = 0.6
MOVE_FROM_OPEN_CRIT = 1.0
RANGE_EXPAND_WARN = 0.8   # day range beyond this = no longer rangebound
NEAR_STRIKE_WARN = 1.0    # spot within this % of a sold strike
NEAR_STRIKE_CRIT = 0.4
PT_CAPTURE_PCT = 70       # analysis 022 PT_70: premium decayed ≥70% → it's done its job
PT_STRONG_PCT = 85        # ≥85% captured → only crumbs left, tail risk not worth it


def _sev_rank(s):
    return {"INFO": 0, "WARN": 1, "CRITICAL": 2}.get(s, 0)


def evaluate(snapshot: dict, open_legs: list[dict], state: dict | None = None):
    """snapshot: /api/snapshot dict (per-inst spot/open/high/low/day_range_pct).
    open_legs: [{instrument, strike, side, qty, name}] — short legs (qty<0).
    state: {inst: {last_spot}} carried between ticks. Returns (alerts, state)."""
    state = dict(state or {})
    alerts = []

    for inst in ("NIFTY", "SENSEX"):
        s = snapshot.get(inst)
        if not s or not s.get("spot"):
            continue
        spot = s["spot"]; opn = s.get("open"); rng = s.get("day_range_pct") or 0
        prev = (state.get(inst) or {}).get("last_spot")

        # 1) fast per-tick move
        if prev:
            mv = (spot - prev) / prev * 100
            if abs(mv) >= FAST_MOVE_TICK:
                arrow = "▼" if mv < 0 else "▲"
                alerts.append({"key": f"fast_{inst}_{round(spot)}", "severity": "CRITICAL",
                    "title": f"{inst} fast move {arrow} {mv:+.2f}%",
                    "body": f"{inst} {spot:,.0f} — moved {mv:+.2f}% since last check. Range now {rng:.2f}%."})

        # 2) move from open (air-pocket after tight range)
        if opn:
            chg = (spot - opn) / opn * 100
            if abs(chg) >= MOVE_FROM_OPEN_CRIT:
                arrow = "▼" if chg < 0 else "▲"
                alerts.append({"key": f"fromopen_{inst}_{int(abs(chg)*2)}", "severity": "CRITICAL",
                    "title": f"{inst} {arrow} {chg:+.2f}% from open",
                    "body": f"{inst} {spot:,.0f}, {chg:+.2f}% from open ({opn:,.0f}). Day range {rng:.2f}% — trend, not chop."})
            elif abs(chg) >= MOVE_FROM_OPEN_WARN:
                alerts.append({"key": f"fromopen_{inst}_w", "severity": "WARN",
                    "title": f"{inst} {chg:+.2f}% from open",
                    "body": f"{inst} {spot:,.0f}, {chg:+.2f}% from open — watch for follow-through."})

        # 3) range expansion
        if rng >= RANGE_EXPAND_WARN:
            alerts.append({"key": f"range_{inst}", "severity": "WARN",
                "title": f"{inst} range expanded to {rng:.2f}%",
                "body": f"{inst} {spot:,.0f} day range {rng:.2f}% — no longer rangebound."})

        state[inst] = {"last_spot": spot}

    # 4) open short positions near/through their sold strike
    for l in open_legs or []:
        inst = (l.get("instrument") or "").upper()
        s = snapshot.get(inst)
        if not s or not s.get("spot"):
            continue
        spot = s["spot"]; strike = l.get("strike"); side = (l.get("side") or "").upper()
        if not strike or side not in ("CE", "PE") or (l.get("qty") or 0) >= 0:
            continue
        dist = abs(strike - spot) / spot * 100
        itm = (side == "PE" and spot < strike) or (side == "CE" and spot > strike)
        if itm:
            sev, tag = "CRITICAL", "ITM"
        elif dist <= NEAR_STRIKE_CRIT:
            sev, tag = "CRITICAL", f"{dist:.2f}% away"
        elif dist <= NEAR_STRIKE_WARN:
            sev, tag = "WARN", f"{dist:.2f}% away"
        else:
            continue
        nm = l.get("name") or inst
        alerts.append({"key": f"near_{inst}_{strike}_{side}", "severity": sev,
            "title": f"{strike}{side} {tag} — {nm}",
            "body": f"{inst} {spot:,.0f} vs your sold {strike}{side} ({tag}). {'IN THE MONEY.' if itm else 'Closing in.'}"})

    # 5) SL / TP triggers + PROACTIVE take-profit (the tool suggests, even with no SL/TP set).
    #    Needs entry_price (sold premium) + ltp (current premium) per short leg.
    for l in open_legs or []:
        if (l.get("qty") or 0) >= 0:
            continue
        side = (l.get("side") or "").upper()
        if side not in ("CE", "PE"):
            continue
        entry = l.get("entry_price"); ltp = l.get("ltp")
        strike = l.get("strike"); nm = l.get("name") or (l.get("instrument") or "")
        tag = f"{strike}{side}"
        # user-set SL (premium ran UP against the short) — hard alert
        sl = l.get("sl")
        if sl and ltp is not None and ltp >= sl:
            alerts.append({"key": f"sl_{tag}", "severity": "CRITICAL",
                "title": f"🛑 SL HIT — {tag}",
                "body": f"{nm}: premium ₹{ltp} ≥ your SL ₹{sl}. Manage the leg."})
        # user-set TP (premium decayed to your target) — lock it
        tp = l.get("tp")
        if tp and ltp is not None and ltp <= tp:
            alerts.append({"key": f"tp_{tag}", "severity": "WARN",
                "title": f"🎯 TP HIT — {tag}",
                "body": f"{nm}: premium ₹{ltp} ≤ your TP ₹{tp}. Buy back to lock the gain."})
        # PROACTIVE take-profit — fires regardless of whether an SL/TP was set
        if entry and ltp is not None and entry > 0:
            captured = (entry - ltp) / entry * 100
            if captured >= PT_STRONG_PCT and not tp:
                alerts.append({"key": f"pt_{tag}", "severity": "WARN",
                    "title": f"✅ TAKE PROFIT — {tag} captured {captured:.0f}%",
                    "body": f"{nm}: ₹{entry}→₹{ltp}, {captured:.0f}% of premium banked. Only crumbs left — "
                            f"the tail risk of holding outweighs it. Buy back to lock."})
            elif captured >= PT_CAPTURE_PCT and not tp:
                alerts.append({"key": f"pt_{tag}", "severity": "INFO",
                    "title": f"💡 {tag} has done its job — {captured:.0f}% captured",
                    "body": f"{nm}: ₹{entry}→₹{ltp}. {captured:.0f}% of the premium is banked (PT_70 rule). "
                            f"Consider locking it — limited left to gain, real risk if it reverses."})

    alerts.sort(key=lambda a: -_sev_rank(a["severity"]))
    return alerts, state
