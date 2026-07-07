"""Expiry-day SELL TIMING — surfaces the locked timing rules (analyses 027/028/009)
as a live, do-this-now panel, so the desk stops leaving 30-40% of premium on the table.

The whole point: the rule already exists in lib/playbook (TIER1_ENTRY_TIME + SPIKE_LIMIT_RULE);
it was just buried. This turns it into "what do I do at this minute?" on expiry day.

Core logic (locked):
  • Sell ~65% of size in the entry window (regime/instrument-specific).
  • Immediately place standing SELL-LIMITs on the SAME strikes at 1.3x your fill for the
    other ~35% — spikes fill ~14% of days at ~1.7x (027). 96-97% of spikes fade by 15:00 (028),
    so you can NOT wait-and-watch; the limits catch the peak without delaying the core.
  • Market-fill any unfilled reserve at 11:30 (waiting past 11:30 is -EV).
"""
from __future__ import annotations
from datetime import date, datetime, time

from lib import playbook
from lib import expiry_calendar as cal

# Spike base-rates from analysis 028 (1.3x of 09:30 quote, Tier-1 2.0% strikes)
SPIKE_BASE_RATE = {"SENSEX": 0.60, "NIFTY": 0.45}
RESERVE_DEADLINE = time(11, 30)
MKT_OPEN = time(9, 15)
MKT_CLOSE = time(15, 30)


def _parse_window(w: str) -> tuple[time, time]:
    """'09:25-09:35' -> (09:25, 09:35); single '10:00' -> (10:00, 10:05)."""
    def t(s):
        h, m = s.strip().split(":")
        return time(int(h), int(m))
    if "-" in w:
        a, b = w.split("-", 1)
        return t(a), t(b)
    start = t(w)
    end = time(start.hour, min(start.minute + 5, 59))
    return start, end


def timing_plan(instrument: str, now: datetime, snapshot: dict | None = None,
                fill_price: float | None = None, current_premium: float | None = None) -> dict:
    """Live timing guidance for one instrument at time `now` (IST-aware datetime)."""
    inst = (instrument or "SENSEX").upper()
    today = now.date()
    nowt = now.time()
    is_e0 = cal.is_e0(today, inst)
    regime = playbook.classify_regime(snapshot or {}) if snapshot else "normal"
    window = playbook.tier1_entry_time(regime, inst)
    w_start, w_end = _parse_window(window)

    # phase of the day
    if nowt < w_start:
        phase, headline, tone = "pre", f"Get strikes ready — core SELL window opens {w_start.strftime('%H:%M')}", "wait"
    elif w_start <= nowt <= w_end:
        phase, headline, tone = "window", f"🟢 SELL CORE ~65% NOW — window {w_start.strftime('%H:%M')}–{w_end.strftime('%H:%M')}", "go"
    elif nowt < RESERVE_DEADLINE:
        phase, headline, tone = "limits_working", "Core done → keep SELL-LIMITs at 1.3× working; market-fill reserve at 11:30", "hold"
    elif nowt <= MKT_CLOSE:
        phase, headline, tone = "deadline", "⏰ Market-fill any UNFILLED reserve now — waiting past 11:30 is −EV", "act"
    else:
        phase, headline, tone = "closed", "Market closed", "wait"

    spike_target = round(fill_price * 1.3, 2) if fill_price else None
    spike_hit = (current_premium is not None and spike_target is not None and current_premium >= spike_target)
    spike_mult = round(current_premium / fill_price, 2) if (current_premium and fill_price) else None

    # Monthly expiry → carry a BIGGER reserve (more event risk + bigger spike potential
    # on trend days; the leak hurts most here). Last weekly expiry of the calendar month.
    is_monthly = _is_monthly(today, inst)
    reserve_pct = 50 if is_monthly else 35
    core_pct = 100 - reserve_pct

    # SPIKE OVERRIDE — when premium is at/over 1.3× the fill, this is THE moment the
    # reserve exists for. Shout it, above the normal phase headline (during market hours).
    if is_e0 and spike_hit and nowt <= MKT_CLOSE:
        headline = (f"🚨 SPIKE — premium at {spike_mult}× your fill — SELL YOUR RESERVE NOW. "
                    "96–97% of spikes fade to ≤1.1× by 15:00, so don't wait for higher.")
        tone = "act"

    steps = [
        {"n": 1, "do": f"Sell ~{core_pct}% of planned size in the {window} window", "done": nowt > w_end},
        {"n": 2, "do": f"Immediately place standing SELL-LIMITs on the SAME strikes at 1.3× your fill (the other ~{reserve_pct}%)", "done": nowt > w_end},
        {"n": 3, "do": "Market-fill any unfilled reserve at 11:30", "done": nowt >= RESERVE_DEADLINE},
    ]

    return {
        "instrument": inst,
        "is_expiry": is_e0,
        "now": now.strftime("%H:%M"),
        "regime": regime,
        "window": window,
        "phase": phase, "headline": headline, "tone": tone,
        "reserve_deadline": RESERVE_DEADLINE.strftime("%H:%M"),
        "mins_to_deadline": _mins_between(nowt, RESERVE_DEADLINE) if nowt < RESERVE_DEADLINE else 0,
        "spike_base_rate": SPIKE_BASE_RATE.get(inst, 0.5),
        "spike_target": spike_target, "spike_hit": spike_hit, "spike_mult": spike_mult,
        "is_monthly": is_monthly, "reserve_pct": reserve_pct, "core_pct": core_pct,
        "current_premium": current_premium, "fill_price": fill_price,
        "steps": steps,
        "rule": playbook.SPIKE_LIMIT_RULE,
        "why": ("On median/calm days premium is highest at the open (sell early). On trend days it "
                "inflates later — but 96-97% of spikes fade by 15:00, so you can't wait-and-watch. "
                f"{inst} spikes ≥1.3× on ~{int(SPIKE_BASE_RATE.get(inst,0.5)*100)}% of days. The 1.3× "
                "limit orders catch the peak without delaying the core. (analyses 027/028/009)"),
    }


def _is_monthly(today, inst: str) -> bool:
    """True if today's E-0 is the LAST weekly expiry of its calendar month (= monthly)."""
    try:
        exps = (cal.NIFTY_WEEKLY_EXPIRIES if inst == "NIFTY" else cal.SENSEX_WEEKLY_EXPIRIES)
        same_month = [e for e in exps if e.year == today.year and e.month == today.month]
        return bool(same_month) and today == max(same_month)
    except Exception:
        return False


def _mins_between(a: time, b: time) -> int:
    return (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)
