#!/usr/bin/env python3
"""
telegram_bot.py — Theta Quant Telegram bot daemon.

Runs continuously, doing TWO things:

1. SCHEDULED SENDS (IST times):
   - 08:30  Pre-market report  (news + levels + ideal strikes)
   - 09:20  Morning regime briefing (suggestions for all 3 tiers)
   - 12:00, 13:00, 14:00, 15:00  Hourly premium status
   - 15:30  Day-end summary

2. INCOMING (polled every 8s via Telegram getUpdates):
   - Photo → OCR (tesseract) → parse trade → save to dashboard
   - Text command:
        /status        — current regime + tier qualification
        /positions     — list saved positions per portfolio
        /triggers      — show active triggers
        /sample <type> — preview a message type
        /pause         — stop scheduled sends until /resume
        /resume        — resume

Usage:
   python3 scripts/telegram_bot.py
   (or via launchd plist for 24/7 operation)
"""
from __future__ import annotations
import json
import os
import ssl
import time
import urllib.request as ureq
import urllib.parse as uparse
from datetime import datetime, time as dtime, date
from pathlib import Path
from zoneinfo import ZoneInfo

# ─── Config ─────────────────────────────────────────────────────────────────────
ROOT = Path("/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)")
CFG_PATH = Path.home() / ".config" / "telegram_bot.json"
STATE_PATH = Path.home() / ".config" / "telegram_bot_state.json"
LOG_PATH = ROOT / "results" / "telegram_bot.log"
DASHBOARD = "http://127.0.0.1:8000"
IST = ZoneInfo("Asia/Kolkata")
import re

def inr(v) -> str:
    """Indian money format: ₹X.XXCr / ₹X.XXL / ₹X,XX,XXX (lakhs-crores, never millions)."""
    try:
        v = float(v or 0)
    except Exception:
        return "₹0"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e7:
        return f"{sign}₹{a/1e7:.2f}Cr"
    if a >= 1e5:
        return f"{sign}₹{a/1e5:.2f}L"
    # Indian grouping
    s = f"{int(round(a)):,}"  # US grouping first
    # convert 1,234,567 -> 12,34,567
    parts = s.replace(",", "")
    if len(parts) > 3:
        head, tail = parts[:-3], parts[-3:]
        head = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", head)
        s = head + "," + tail
    return f"{sign}₹{s}"


SCHEDULE = [
    # (hh, mm, message_type)
    (8, 30,  "strike_recs"),       # Pre-market strike preview
    (9, 20,  "morning_regime"),    # Regime brief + tier qualification
    (9, 30,  "strike_recs"),       # Strikes after open (with live LTPs)
    (11, 0,  "strike_recs"),       # Mid-morning strikes (pre-decision window)
    (12, 0,  "midday_max"),        # brief: total + max profit so far
    (13, 0,  "hourly_premium"),
    (14, 0,  "hourly_premium"),
    (15, 0,  "hourly_premium"),
    (15, 45, "day_end"),           # FINAL — after close + manual entries + auto-settle
]

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def log(msg: str):
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def load_cfg() -> dict:
    if not CFG_PATH.exists():
        return {}
    return json.loads(CFG_PATH.read_text())


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"last_sent": {}, "paused": False, "last_update_id": 0}
    return json.loads(STATE_PATH.read_text())


def save_state(s: dict):
    STATE_PATH.write_text(json.dumps(s, indent=2))
    try:                       # holds the authorized chat-id whitelist — keep it private
        STATE_PATH.chmod(0o600)
    except Exception:
        pass


# ─── Telegram I/O ───────────────────────────────────────────────────────────────
def tg_request(method: str, params: dict = None, data: dict = None) -> dict:
    cfg = load_cfg()
    if not cfg.get("bot_token"):
        return {"ok": False, "error": "bot not configured"}
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/{method}"
    if params:
        url += "?" + uparse.urlencode(params)
    try:
        if data:
            data_enc = uparse.urlencode(data).encode()
            req = ureq.Request(url, data=data_enc)
        else:
            req = ureq.Request(url)
        with ureq.urlopen(req, timeout=15, context=SSL_CTX) as r:
            return json.load(r)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tg_send(text: str, parse_mode: str = "Markdown", chat_id: int | str = None) -> bool:
    """Send to a specific chat_id, or default to admin if not provided."""
    cfg = load_cfg()
    target = chat_id if chat_id is not None else cfg.get("chat_id")
    if not target:
        log(f"send skipped: no chat_id")
        return False
    res = tg_request("sendMessage", data={
        "chat_id": target, "text": text, "parse_mode": parse_mode
    })
    if not res.get("ok"):
        log(f"send failed (target {target}): {res.get('error', res.get('description'))}")
        return False
    return True


def is_authorized(chat_id: int, state: dict) -> tuple[bool, str]:
    """Check if a chat_id is allowed to use the bot. Returns (allowed, role)."""
    cfg = load_cfg()
    if chat_id == cfg.get("chat_id"):
        return True, "admin"
    whitelist = state.get("whitelist", {})
    info = whitelist.get(str(chat_id))
    if info:
        return True, info.get("role", "team")
    return False, "unauthorized"


def tg_get_updates(offset: int = 0) -> list:
    res = tg_request("getUpdates", params={"offset": offset, "timeout": 5})
    if res.get("ok"):
        return res.get("result", [])
    return []


def tg_get_file(file_id: str) -> bytes | None:
    cfg = load_cfg()
    res = tg_request("getFile", params={"file_id": file_id})
    if not res.get("ok"):
        return None
    file_path = res["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{cfg['bot_token']}/{file_path}"
    try:
        with ureq.urlopen(url, timeout=30, context=SSL_CTX) as r:
            return r.read()
    except Exception as e:
        log(f"file download failed: {e}")
        return None


# ─── Dashboard data fetchers ────────────────────────────────────────────────────
def fetch_snapshot() -> dict:
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/snapshot", timeout=10) as r:
            return json.load(r)
    except Exception as e:
        log(f"snapshot fetch failed: {e}")
        return {}


def fetch_live_alerts() -> dict:
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/live_alerts", timeout=12) as r:
            return json.load(r)
    except Exception as e:
        log(f"live_alerts fetch failed: {e}")
        return {}


def fetch_next_action() -> dict:
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/playbook/next_action", timeout=10) as r:
            return json.load(r)
    except Exception:
        return {}


def fetch_regime() -> dict:
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/playbook/regime", timeout=10) as r:
            return json.load(r)
    except Exception as e:
        log(f"regime fetch failed: {e}")
        return {}


def fetch_snapshot_persisted(date_str: str) -> dict:
    """Load saved positions from dashboard snapshot."""
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/snapshot/{date_str}", timeout=10) as r:
            return json.load(r)
    except Exception:
        return {}


# ─── Message builders ───────────────────────────────────────────────────────────
def build_pre_market() -> str:
    """8:30 IST — before market opens. Today is mostly previous close + calendar."""
    today = datetime.now(IST)
    weekday = today.strftime("%A")
    msg = f"☀️ *PRE-MARKET — {today.strftime('%a %d %b')}*\n\n"

    # Determine which expiry today is
    from sys import path as _p
    _p.insert(0, str(ROOT))
    try:
        from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES
        td = today.date()
        nifty_e0 = td in NIFTY_WEEKLY_EXPIRIES
        sensex_e0 = td in SENSEX_WEEKLY_EXPIRIES
        next_nifty = next((e for e in NIFTY_WEEKLY_EXPIRIES if e >= td), None)
        next_sensex = next((e for e in SENSEX_WEEKLY_EXPIRIES if e >= td), None)
        msg += "📅 *Expiry calendar*\n"
        msg += f"   NIFTY next: {next_nifty} ({(next_nifty - td).days}d)\n" if next_nifty else ""
        msg += f"   SENSEX next: {next_sensex} ({(next_sensex - td).days}d)\n" if next_sensex else ""
        if nifty_e0: msg += "   ⭐ *NIFTY E-0 today*\n"
        if sensex_e0: msg += "   ⭐ *SENSEX E-0 today*\n"
    except Exception:
        pass

    # Live risk brief (events + crude + headlines)
    try:
        from lib.news import risk_brief
        rb = risk_brief()
        msg += "\n🌍 *Overnight risk brief*\n"
        cr = rb["crude"]
        if cr.get("available"):
            flag = " ⚠ >3% (HARD EXCLUSION)" if cr["flag"] else ""
            msg += f"   Brent: ${cr['price']} ({cr['change_pct_1d']:+}% 1d){flag}\n"
        for e in rb["events"]:
            tag = "⛔" if e["exclusion_flag"] else "📅"
            msg += f"   {tag} {e['label']} — {e['date']} ({e['days_away']}d)\n"
        if not rb["events"]:
            msg += "   📅 No scheduled macro events in next 3 days\n"
        for h in rb["headlines"][:4]:
            msg += f"   • {h['title'][:75]}\n"
        if rb["any_exclusion"]:
            msg += "\n⛔ *EXCLUSION FLAGS ACTIVE — Tier 1 only today:*\n"
            for f in rb["exclusion_flags"]:
                msg += f"   • {f}\n"
        msg += "\n"
    except Exception as e:
        msg += f"\n🌍 (risk brief unavailable: {e})\n\n"

    msg += "📊 *Watch at open*\n"
    msg += "   • Gap % (>0.7% = SKIP near/mid OTM)\n"
    msg += "   • VIX direction\n"
    msg += "   • 9:15-10:30 range\n\n"

    msg += "💡 *Today's plan*\n"
    msg += "   Apply the 7-flag STOP test at 10:30.\n"
    msg += "   If all clear → cascade Tier 3 → Tier 2 → Tier 1.\n"
    msg += "   Else → Tier 1 deep OTM (≥2.5%) only.\n"
    return msg


def fetch_reporting_day() -> dict:
    try:
        d = datetime.now(IST).strftime("%Y-%m-%d")
        with ureq.urlopen(f"{DASHBOARD}/api/reporting/day?date={d}", timeout=15) as r:
            return __import__("json").load(r)
    except Exception as e:
        log(f"reporting fetch failed: {e}")
        return {}


NL = chr(10)


def build_midday_max() -> str:
    """12:00 — one-glance book status. Final numbers at 15:45."""
    d = fetch_reporting_day()
    a = d.get("dashboard") or {}
    n = len(d.get("strategies") or [])
    if not n:
        return "⏱ 12:00 — _no strategies in journal yet today_"
    line2 = f"P&L *{inr(a.get('total') or 0)}* · max profit {inr(a.get('max_profit') or 0)}"
    if a.get("margin_used"):
        line2 += f" on ₹{a['margin_used']/1e7:.1f}Cr"
    return NL.join([f"⏱ *12:00* · {n} strategies", line2,
                    "_final at 15:45 · /detail day_"])


def build_morning_regime_brief() -> str:
    """Scheduled 9:20 — 4 lines max. Full version: /detail regime."""
    r = fetch_regime()
    if not r or "snapshot" not in r:
        return "⚠️ regime fetch failed — dashboard down?"
    sn = r["snapshot"]
    na = fetch_next_action()
    excl = r.get("hard_exclusions", [])
    lines = [f"🌅 *{(r.get('regime') or sn.get('regime') or '?').upper()}* · VIX {sn.get('vix')}",
             f"NF `{sn['NIFTY']['spot']}` ({sn['NIFTY'].get('gap_pct')}%) · SX `{sn['SENSEX']['spot']}` ({sn['SENSEX'].get('gap_pct')}%)"]
    if na.get("headline"):
        lines.append(f"➡️ {na['headline']}")
    lines.append(f"⛔ {len(excl)} exclusions — Tier 1 only" if excl else "✅ all clear")
    lines.append("_/detail regime · /detail recs_")
    return NL.join(lines)


def build_strike_recs_brief() -> str:
    """Scheduled strikes — one line per tier (Recommended option). Full: /detail recs."""
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/playbook/recommendations", timeout=15) as r:
            d = __import__("json").load(r)
    except Exception as e:
        return f"⚠️ recs fetch failed: {e}"
    if d.get("error"):
        return f"⚠️ {d['error']}"
    lines = [f"🎯 *{d.get('instrument', '?')}* {datetime.now(IST).strftime('%H:%M')}"]
    for t in d.get("tiers") or []:
        o = (t.get("options") or [{}])[0]
        if not o.get("pe_strike"):
            continue
        name = t.get("tier", "?").split("—")[0].strip()
        per_cr = f" ₹{o['per_cr']/1000:.1f}K/Cr" if o.get("per_cr") else ""
        pop = f" · PoP {o['pop_pct']}%" if o.get("pop_pct") else ""
        lines.append(f"*{name}* {o['pe_strike']}PE+{o['ce_strike']}CE{per_cr} · {o.get('entry_time','')}{pop}")
    lines.append("_/detail recs for both options + logic_")
    return NL.join(lines)


def build_morning_regime() -> str:
    """9:20 IST — just after open. Show snapshot + tier qualification."""
    today = datetime.now(IST)
    r = fetch_regime()
    if not r or "snapshot" not in r:
        return f"⚠️ Could not fetch regime at {today.strftime('%H:%M')}. Dashboard down?"

    sn = r["snapshot"]
    excl = r.get("hard_exclusions", [])

    na = fetch_next_action()
    msg = f"🌅 *MORNING REGIME — {today.strftime('%a %d %b %H:%M')}*\n"
    if na.get("headline"):
        msg += f"➡️ *{na['headline']}*\n"
        if na.get("detail"): msg += f"_{na['detail']}_\n"
    msg += "\n"
    msg += f"NIFTY    `{sn['NIFTY']['spot']}`  gap `{sn['NIFTY'].get('gap_pct')}%`\n"
    msg += f"SENSEX   `{sn['SENSEX']['spot']}`  gap `{sn['SENSEX'].get('gap_pct')}%`\n"
    msg += f"VIX      `{sn['vix']}`  ({sn['vix_status']})\n\n"

    if excl:
        msg += f"🚦 *Hard Exclusions: {len(excl)} RED*\n"
        for e in excl: msg += f"   ⛔ {e}\n"
        msg += "\n📌 *Verdict: TIER 1 DEEP OTM ONLY today*\n"
        msg += "   No near/mid OTM qualifies.\n"
    else:
        msg += "🚦 *Hard Exclusions: ALL CLEAR* ✓\n\n"
        tiers = r.get("tiers", [])
        qual = [t for t in tiers if t.get("qualifies")]
        if qual:
            msg += "✅ *Tier qualification (live):*\n"
            for t in qual:
                star = "⭐ " if t.get("is_star") else ""
                msg += f"{star}*{t['tier']}*\n"
                msg += f"   premium floor ₹{t['premium_floor_per_cr']//1000}K/Cr\n"
                msg += f"   backtest: +₹{t['mean_pcr']//1000}K mean, {t['win_pct']}% win\n\n"
        else:
            msg += "⏳ No tier qualifies yet — re-check at 10:30\n"

    msg += "\n_Next update: 12:00 (premium status)_"
    return msg


def build_hourly_premium() -> str:
    """12:00 / 13:00 / 14:00 / 15:00 — premium decay status."""
    today = datetime.now(IST)
    snap = fetch_snapshot_persisted(today.strftime("%Y-%m-%d"))
    positions = snap.get("positions", []) if snap else []
    analysis = snap.get("analysis", []) if snap else []

    msg = f"⏰ *PREMIUM STATUS — {today.strftime('%H:%M')}*\n\n"

    if not positions:
        sn = fetch_snapshot()
        msg += "_(no positions saved to dashboard today)_\n\n"
        msg += "📊 *Live state*\n"
        if sn.get("NIFTY"):
            msg += f"   NIFTY  `{sn['NIFTY']['spot']}`  Δ `{sn['NIFTY'].get('change_pct')}%`\n"
        if sn.get("SENSEX"):
            msg += f"   SENSEX `{sn['SENSEX']['spot']}`  Δ `{sn['SENSEX'].get('change_pct')}%`\n"
        msg += f"   VIX   `{sn.get('vix')}`\n"
        return msg

    # Bucket positions by tier
    sn = fetch_snapshot()
    spot_nifty = sn.get("NIFTY", {}).get("spot")
    spot_sensex = sn.get("SENSEX", {}).get("spot")

    tier_buckets = {"Tier 1 (≥2.5%)": [], "Tier 2 (1.25-2%)": [], "Tier 3 (0.5-1%)": [], "Other": []}

    for p in positions:
        inst = p.get("instrument", "")
        strike = float(p.get("strike", 0))
        spot = spot_nifty if inst == "NIFTY" else (spot_sensex if inst == "SENSEX" else None)
        if not spot:
            tier_buckets["Other"].append(p)
            continue
        dist_pct = abs(strike - spot) / spot * 100
        if dist_pct >= 2.5: tier_buckets["Tier 1 (≥2.5%)"].append((p, dist_pct))
        elif dist_pct >= 1.25: tier_buckets["Tier 2 (1.25-2%)"].append((p, dist_pct))
        elif dist_pct >= 0.5: tier_buckets["Tier 3 (0.5-1%)"].append((p, dist_pct))
        else: tier_buckets["Other"].append((p, dist_pct))

    for tier_name, items in tier_buckets.items():
        if not items: continue
        sold_total = 0
        remaining_total = 0
        for entry in items:
            if isinstance(entry, tuple): p, dist = entry
            else: p = entry; dist = None
            qty = abs(int(p.get("qty", 0)))
            avg = float(p.get("avg_price", 0))
            sold_total += qty * avg
            # Estimated remaining: use LTP from analysis if available
            a = next((x for x in analysis if x.get("strike") == p.get("strike") and x.get("side") == p.get("side")), None)
            ltp = a.get("ltp") if a else None
            if ltp is None:
                ltp = avg * 0.3  # placeholder
            remaining_total += qty * ltp
        decay = sold_total - remaining_total
        msg += f"📦 *{tier_name}*\n"
        msg += f"   Sold: {inr(sold_total)}  Remaining: {inr(remaining_total)} ({remaining_total/sold_total*100:.0f}%)\n"
        msg += f"   Decay so far: {inr(decay)} {'✓' if decay > 0 else '⚠'}\n\n"

    return msg


def build_day_end() -> str:
    """15:30 IST — day end summary. Reads /api/reporting/day (the journal),
    so Telegram, web report and learning loop all show the SAME numbers."""
    today = datetime.now(IST)
    msg = f"🏁 *DAY END SUMMARY — {today.strftime('%a %d %b')}*\n\n"
    # First: auto-settle expired-worthless legs at ₹0 (intrinsic if ITM).
    # No-op on non-expiry days; idempotent on already-booked legs.
    try:
        req = ureq.Request(f"{DASHBOARD}/api/reporting/settle_expired", data=b"", method="POST")
        with ureq.urlopen(req, timeout=20) as r:
            st = __import__("json").load(r)
        if st.get("settled"):
            msg += f"🧹 auto-settled {st['settled']} expired-worthless legs at ₹0 — strategies closed\n"
        for p in st.get("itm_pending") or []:
            msg += (f"⚠️ *{p['strike']} {p['side']} ended ITM* (intrinsic ₹{p['intrinsic']}) — "
                    f"enter your actual sq-off price in /report → ✎ Edit → Sq off\n")
        if st.get("settled") or st.get("itm_pending"):
            msg += "\n"
    except Exception as e:
        log(f"auto-settle failed: {e}")
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/reporting/day?date={today.strftime('%Y-%m-%d')}",
                          timeout=15) as r:
            d = __import__("json").load(r)
    except Exception as e:
        log(f"day_end reporting fetch failed: {e}")
        return msg + "_(could not reach reporting — is the dashboard up?)_"

    strategies = d.get("strategies") or []
    if not strategies:
        return msg + "_(no strategies in the journal today — add them at /report)_"
    agg = d.get("agg") or d.get("dashboard") or {}

    total = agg.get("total") or 0
    booked = agg.get("booked") or 0
    unbooked = agg.get("unbooked") or 0
    msg += f"📈 *Total P&L: {inr(total)}*\n"
    msg += f"   ✅ booked {inr(booked)} · ⏳ unbooked {inr(unbooked)}\n"
    if agg.get("costs"):
        msg += f"   💸 net {inr(agg.get('net_total') or 0)} after {inr(agg['costs'])} brokerage\n"
    if agg.get("max_profit"):
        msg += f"   🎯 max profit {inr(agg['max_profit'])}"
        if agg.get("margin_used"):
            msg += f" on ₹{agg['margin_used']/1e7:.2f}Cr margin ({agg.get('yield_on_margin_pct') or 0}%)"
        msg += "\n"
    msg += "\n*By tier:*\n"
    for k, v in sorted((agg.get("by_tier") or {}).items()):
        msg += f"   {k}:  {inr(v.get('pnl', 0))}  ({v.get('n', 0)})\n"
    msg += "\n*By broker:*\n"
    for k, v in sorted((agg.get("by_broker") or {}).items(), key=lambda x: -x[1].get("pnl", 0)):
        msg += f"   {k}:  {inr(v.get('pnl', 0))}  ({v.get('n', 0)})\n"
    open_n = sum(1 for s in strategies if s.get("status") == "open")
    if open_n:
        msg += f"\n⚠️ {open_n} strategies still OPEN — square off or they expire at settlement."
    return msg


def fetch_chain(instrument: str) -> list:
    """Fetch full option chain via dashboard."""
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/chain/{instrument.upper()}", timeout=10) as r:
            d = __import__("json").load(r)
        return d.get("rows") or d.get("chain") or []
    except Exception as e:
        log(f"chain fetch {instrument} failed: {e}")
        return []


def find_strike(chain: list, target_strike: int, opt: str) -> dict | None:
    """Find row for given strike + return ltp."""
    for r in chain:
        if r.get("strike") == target_strike:
            key = "ce_ltp" if opt == "CE" else "pe_ltp"
            ltp = r.get(key)
            return {"strike": target_strike, "ltp": ltp} if ltp is not None else None
    return None


def nearest_strike(spot: float, otm_pct: float, side: str, grid: int) -> int:
    """Round to nearest strike grid (PE below spot, CE above)."""
    if side == "PE":
        target = spot * (1 - otm_pct / 100)
    else:
        target = spot * (1 + otm_pct / 100)
    return int(round(target / grid) * grid)


def handle_trade_command(text: str, state: dict) -> str:
    """Parse: /trade <tier> [nifty|sensex] <strike1>pe@<price> <strike2>ce@<price>
    Examples:
      /trade tier3 nifty 22300pe@2 24400ce@3
      /trade tier1 71000pe@2.5 76000ce@3      (instrument inferred from strike size)
      /trade mid 22950pe@3.45 23400ce@3.55

    Uses lib.playbook.compute_triggers for all level calculations
    (same source as the web /api/playbook/triggers endpoint).
    """
    import sys as _s; _s.path.insert(0, str(ROOT))
    from lib import playbook as _pb
    import re
    # Strip command
    body = text.split(maxsplit=1)[1] if " " in text else ""
    if not body:
        return ("*Usage:*\n"
                "`/trade <tier> [<instrument>] <strike>pe@<price> <strike>ce@<price>`\n\n"
                "*Examples:*\n"
                "`/trade tier3 nifty 22300pe@2 24400ce@3`\n"
                "`/trade tier1 71000pe@2.5 76000ce@3` (SENSEX inferred from strike)\n"
                "`/trade mid 22950pe@3.45 23400ce@3.55`\n\n"
                "I'll compute exact spot levels for:\n"
                "• Yellow PE / Yellow CE (close 1 leg)\n"
                "• Red PE / Red CE (close both)\n"
                "• Profit-take combined target\n"
                "• Watch zone for the next 30-min")

    body_lc = body.lower()
    # Parse tier
    tier = None
    if "tier1" in body_lc or "tier-1" in body_lc or " deep " in f" {body_lc} " or body_lc.startswith("deep"): tier = "Tier 1"
    elif "tier2" in body_lc or "tier-2" in body_lc or " mid " in f" {body_lc} " or body_lc.startswith("mid"): tier = "Tier 2"
    elif "tier3" in body_lc or "tier-3" in body_lc or " near " in f" {body_lc} " or body_lc.startswith("near") or "star" in body_lc: tier = "Tier 3"

    # Parse instrument
    inst = None
    if "nifty" in body_lc and "bank" not in body_lc: inst = "NIFTY"
    elif "sensex" in body_lc: inst = "SENSEX"
    elif "bank" in body_lc: inst = "BANKNIFTY"

    # Parse strikes & prices: '22300pe@2' or '22300 pe @ 2'
    legs = re.findall(r"(\d{4,6})\s*(pe|ce)\s*@\s*(\d+(?:\.\d+)?)", body_lc)
    if len(legs) < 2:
        return ("⚠ Could not parse 2 legs. Format: `22300pe@2 24400ce@3`\n\n"
                "What I saw: " + (", ".join([f"{s}{t.upper()}@{p}" for s,t,p in legs]) or "nothing"))

    pe_strike = ce_strike = pe_entry = ce_entry = None
    for strike_s, side, price_s in legs:
        if side == "pe":
            pe_strike = int(strike_s); pe_entry = float(price_s)
        else:
            ce_strike = int(strike_s); ce_entry = float(price_s)

    if pe_strike is None or ce_strike is None:
        return "⚠ Need both a PE and CE leg."

    # Infer instrument from strike if not specified
    if inst is None:
        if max(pe_strike, ce_strike) >= 50000: inst = "SENSEX"
        elif max(pe_strike, ce_strike) >= 18000: inst = "NIFTY"
        else: inst = "NIFTY"  # default

    # Get current spot
    snap = fetch_snapshot()
    spot = snap.get(inst, {}).get("spot")
    if not spot:
        return f"⚠ No live spot for {inst}. Try again later."

    # Use shared playbook logic (one source of truth)
    core = _pb.compute_triggers(inst, spot, pe_strike, ce_strike,
                                pe_entry=pe_entry, ce_entry=ce_entry)
    pe_buffer = core["pe_buffer_pts"]
    ce_buffer = core["ce_buffer_pts"]
    combined_entry = core["combined_entry"]
    big_move_pts = core["big_move_pts"]
    yellow_pe_spot = core["yellow_pe_spot"]
    yellow_ce_spot = core["yellow_ce_spot"]
    red_pe_spot = core["red_pe_spot"]
    red_ce_spot = core["red_ce_spot"]
    profit_take_combined = core["profit_take_combined"]
    per_cr_gross = core["combined_per_cr"]

    # Save to state
    trade_id = len(state.setdefault("active_trades", [])) + 1
    state["active_trades"].append({
        "id": trade_id,
        "added_at": datetime.now(IST).isoformat(),
        "tier": tier or "?",
        "instrument": inst,
        "entry_spot": spot,
        "pe_strike": pe_strike, "pe_entry": pe_entry,
        "ce_strike": ce_strike, "ce_entry": ce_entry,
        "yellow_pe_spot": yellow_pe_spot, "yellow_ce_spot": yellow_ce_spot,
        "red_pe_spot": red_pe_spot, "red_ce_spot": red_ce_spot,
        "profit_take_combined": profit_take_combined,
    })
    save_state(state)

    msg = f"🎯 *TRADE LEVELS — {inst} {tier or ''}*\n\n"
    msg += f"*Entry:*\n"
    msg += f"  PE `{pe_strike}` @ ₹{pe_entry}  (buffer {int(pe_buffer)} pts, {pe_buffer/spot*100:.2f}%)\n"
    msg += f"  CE `{ce_strike}` @ ₹{ce_entry}  (buffer {int(ce_buffer)} pts, {ce_buffer/spot*100:.2f}%)\n"
    msg += f"  Combined: ₹{combined_entry:.2f}/share = ₹{per_cr_gross/1000:.1f}K/Cr gross\n"
    msg += f"  Spot now: `{spot}`\n\n"
    msg += f"🟡 *YELLOW PE — close PE only*\n"
    msg += f"  Spot ≤ `{yellow_pe_spot}` AND 30-min drop ≥ {big_move_pts} pts\n\n"
    msg += f"🟡 *YELLOW CE — close CE only*\n"
    msg += f"  Spot ≥ `{yellow_ce_spot}` AND 30-min rise ≥ {big_move_pts} pts\n\n"
    msg += f"🔴 *RED — close BOTH at market*\n"
    msg += f"  Spot ≤ `{red_pe_spot}`  OR  Spot ≥ `{red_ce_spot}`\n"
    msg += f"  (or spot touches either strike)\n\n"
    msg += f"💰 *PROFIT TAKE*\n"
    msg += f"  When combined LTP ≤ ₹{profit_take_combined}\n"
    msg += f"  (= 30% of entry, locks in 70% decay)\n\n"
    msg += f"_Tracked as trade #{trade_id}. /mytrades to list. /forget {trade_id} to remove._"
    return msg


def expiring_today() -> list[str]:
    """Delegates to lib.playbook for single source of truth."""
    import sys as _s; _s.path.insert(0, str(ROOT))
    from lib import playbook as _pb
    return _pb.expiring_today()


def build_strike_recs(filter_tier: str = None, filter_inst: str = None) -> str:
    """Build tier-wise strike recommendations.

    filter_tier: None | "1" | "2" | "3"  (None = all)
    filter_inst: None | "NIFTY" | "SENSEX" | "BANKNIFTY"  (None = auto by expiry)

    If filter_inst is None, auto-restricts to instruments expiring today.
    Explicit filter overrides (lets you ask about non-expiring index too).
    """
    now = datetime.now(IST)
    today = now.date()
    snap = fetch_snapshot()
    if not snap or not snap.get("NIFTY") or not snap.get("SENSEX"):
        return "⚠ Live data unavailable — Kite may be down. Try /status to confirm."

    nifty_spot = snap["NIFTY"]["spot"]
    sensex_spot = snap["SENSEX"]["spot"]
    vix = snap.get("vix") or 0

    # Determine which instruments expire today
    expiring = expiring_today()
    auto_filter = filter_inst is None  # we're auto-filtering by expiry

    if auto_filter and not expiring:
        # No expiry today — show next expiry info instead of recs
        import sys as _s
        _s.path.insert(0, str(ROOT))
        from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES
        from datetime import timedelta
        next_n = next((e for e in NIFTY_WEEKLY_EXPIRIES if e >= today), None)
        next_s = next((e for e in SENSEX_WEEKLY_EXPIRIES if e >= today), None)
        msg = f"📅 *No expiry today* ({today.strftime('%A')})\n\n"
        msg += f"NIFTY `{nifty_spot}` · SENSEX `{sensex_spot}` · VIX `{vix}`\n\n"
        msg += "*Upcoming expiries:*\n"
        if next_n: msg += f"  NIFTY  → {next_n} ({(next_n - today).days}d, {next_n.strftime('%A')})\n"
        if next_s: msg += f"  SENSEX → {next_s} ({(next_s - today).days}d, {next_s.strftime('%A')})\n"
        msg += "\n_Strike recs auto-fire only on expiry days._\n"
        msg += "_To force-check anyway, use:_\n"
        msg += "  `/recommend nifty` or `/recommend sensex`\n"
        return msg

    # Schedule which instruments to show
    instruments_to_show = []
    candidates = [filter_inst] if filter_inst else expiring
    for inst_name in candidates:
        if inst_name == "NIFTY":
            instruments_to_show.append(("NIFTY", nifty_spot, 50, 75, 43))
        elif inst_name == "SENSEX":
            instruments_to_show.append(("SENSEX", sensex_spot, 100, 20, 40))
        elif inst_name == "BANKNIFTY":
            # Placeholder: BANKNIFTY data not yet in store
            pass  # handled below
    has_banknifty = "BANKNIFTY" in (candidates or [])

    # Regime classification + Tier 1 distance recommendation (analysis 025)
    import sys as _s; _s.path.insert(0, str(ROOT))
    from lib import playbook as _pb
    snapshot_for_regime = {
        "vix": vix,
        "NIFTY":  {"gap_pct": snap.get("NIFTY", {}).get("gap_pct"),
                   "pre_range_pct": snap.get("NIFTY", {}).get("day_range_pct")},
        "SENSEX": {"gap_pct": snap.get("SENSEX", {}).get("gap_pct"),
                   "pre_range_pct": snap.get("SENSEX", {}).get("day_range_pct")},
    }
    regime = _pb.classify_regime(snapshot_for_regime)
    regime_emoji = {"calm_green":"🟢", "normal":"🟢", "moderate":"🟡", "high_risk":"🔴"}.get(regime, "⚪")

    # Header
    na = fetch_next_action()
    e0_tag = f" — *E-0 ({', '.join(expiring)})*" if expiring else ""
    msg = f"📊 *STRIKE RECOMMENDATIONS — {now.strftime('%H:%M IST')}*{e0_tag}\n"
    if na.get("headline"):
        msg += f"➡️ *{na['headline']}*\n"
    msg += f"NIFTY `{nifty_spot}` · SENSEX `{sensex_spot}` · VIX `{vix}`\n"
    msg += f"{regime_emoji} *Regime: {regime}*  →  "
    # Tier 1 distance recommendation
    rec_lines = []
    for inst_name, _, _, _, _ in instruments_to_show:
        d = _pb.tier1_distance(regime, inst_name)
        p = _pb.tier1_expected_premium(regime, inst_name)
        rec_lines.append(f"{inst_name} {d}% (₹{int(p/1000)}K/Cr)")
    msg += "Tier 1: " + " · ".join(rec_lines) + "\n\n"

    # Pre-fetch chains
    chains = {}
    for inst, _, _, _, _ in instruments_to_show:
        chains[inst] = fetch_chain(inst)

    def format_pair(inst, spot, grid, lot, lots_per_cr, otm_pct):
        """Return one line: 'X% → PE @ ₹A + CE @ ₹B = ₹C/share (~₹DK/Cr)'"""
        pe_strike = nearest_strike(spot, otm_pct, "PE", grid)
        ce_strike = nearest_strike(spot, otm_pct, "CE", grid)
        chain = chains.get(inst, [])
        pe = find_strike(chain, pe_strike, "PE")
        ce = find_strike(chain, ce_strike, "CE")
        if not pe or not ce or pe["ltp"] is None or ce["ltp"] is None:
            return f"  {otm_pct}% → `{pe_strike} PE` + `{ce_strike} CE`  (no live LTP)"
        combined = pe["ltp"] + ce["ltp"]
        per_cr = combined * lot * lots_per_cr
        return (f"  {otm_pct}% → `{pe_strike} PE` @ ₹{pe['ltp']} + `{ce_strike} CE` @ ₹{ce['ltp']}"
                f" = ₹{combined:.2f} (₹{per_cr/1000:.1f}K/Cr)")

    # TIER 1 — Deep OTM (analysis 025: 2.0-2.25% safe floor, 100% backtest win)
    if filter_tier in (None, "1"):
        msg += "🟢 *TIER 1 — Deep OTM (75% of book)*\n_Floor: 2.0% OTM. 100% backtest win + 0% ITM at recommended distances._\n\n"
        for inst, spot, grid, lot, lpc in instruments_to_show:
            rec_d = _pb.tier1_distance(regime, inst)
            msg += f"*{inst}* spot `{spot}` — recommended **{rec_d}%**:\n"
            # Show the recommended one + a wider safety option
            distances_to_show = sorted({rec_d, rec_d + 0.25, 3.0})
            for otm in distances_to_show:
                tag = " ⭐ recommended" if abs(otm - rec_d) < 0.01 else ""
                msg += format_pair(inst, spot, grid, lot, lpc, otm) + tag + "\n"
            msg += "\n"

    # TIER 2 — Mid OTM (1.25-2%)
    if filter_tier in (None, "2"):
        msg += "🟡 *TIER 2 — Mid OTM*\n_Floor: ₹12.5K/Cr · Pre-range ≤ 0.7-0.8%_\n\n"
        for inst, spot, grid, lot, lpc in instruments_to_show:
            msg += f"*{inst}*:\n"
            for otm in [1.25, 1.5, 1.75, 2.0]:
                msg += format_pair(inst, spot, grid, lot, lpc, otm) + "\n"
            msg += "\n"

    # TIER 3 — Near OTM (0.5-1%)
    if filter_tier in (None, "3"):
        msg += "🔴 *TIER 3 — Near OTM*\n_Strict filters apply — see playbook_\n\n"
        for inst, spot, grid, lot, lpc in instruments_to_show:
            msg += f"*{inst}*:\n"
            star = " ⭐STAR" if inst == "SENSEX" else ""
            for otm in [0.5, 0.7, 1.0]:
                tag = star if otm == 1.0 and inst == "SENSEX" else ""
                msg += format_pair(inst, spot, grid, lot, lpc, otm) + tag + "\n"
            msg += "\n"

    if has_banknifty:
        msg += "⚠ *BANK NIFTY data not yet in store.* Add ingest in future.\n\n"

    msg += "_To ask anytime:_\n"
    msg += "  `/recommend` — auto (today's expiring index)\n"
    msg += "  `/recommend tier1` — Tier 1 only\n"
    msg += "  `/recommend tier3` — Tier 3 only\n"
    msg += "  `/recommend nifty` — force NIFTY (any day)\n"
    msg += "  `/recommend sensex tier3` — SENSEX Tier 3\n"
    return msg


def build_message(msg_type: str) -> str:
    builders = {
        "pre_market": build_pre_market,
        "morning_regime": build_morning_regime_brief,   # full: /detail regime
        "hourly_premium": build_hourly_premium,
        "midday_max": build_midday_max,
        "day_end": build_day_end,
        "strike_recs": build_strike_recs_brief,         # full: /detail recs
    }
    fn = builders.get(msg_type)
    if not fn:
        return f"Unknown message type: {msg_type}"
    try:
        return fn()
    except Exception as e:
        log(f"builder {msg_type} failed: {e}")
        return f"⚠️ Builder {msg_type} failed: {e}"


# ─── OCR (screenshot → trade) ───────────────────────────────────────────────────
def ocr_screenshot(img_bytes: bytes) -> str:
    """OCR via macOS Vision framework (lib/ocr) — accurate on app screenshots."""
    try:
        import sys as _s; _s.path.insert(0, str(ROOT))
        from lib import ocr as _o
        text = _o.ocr_image_bytes(img_bytes)
        return text if text.strip() else "[OCR produced no text — try a sharper screenshot]"
    except Exception as e:
        return f"[OCR failed: {e}]"


def _parse_amount(s: str) -> float:
    """Parse Sensibull amount string: '+45,575', '+1.88L', '-2.50K', '0', '+3.15L'."""
    if s is None: return 0.0
    s = str(s).strip().replace(",", "").replace("+", "").replace(" ", "")
    if not s or s == "-": return 0.0
    mult = 1.0
    if s.endswith("L"):
        mult = 100_000  # 1 Lakh = 100,000
        s = s[:-1]
    elif s.endswith("K") or s.endswith("k"):
        mult = 1_000
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def parse_sensibull_portfolio(text: str) -> dict:
    """Parse Sensibull portfolio screenshot OCR text.

    Recognizes formats like:
        Header: "4th June Sensex Deep OTM (Axis)"
                "12th May Nifty ATM (11th May Trade) M."
        Counter:"3 of 3 Positions"
        Totals: "Total P&L +3.15L", "Booked P&L +1.33L", "Unbooked P&L 0"
        Spot:   "SENSEX 73524.26 -0.97%"
        Rows:   "04th Jun 72000 PE E 0 0.00 0.05 +45,575 0 +45,575"
                "12th May 22700 PE -5980 1.20 0.65 +3,289 +3,289 0"

    Returns dict with portfolio_name, instrument, broker, tier, positions[], totals{}.
    """
    import re
    result = {
        "portfolio_name": None, "instrument": None, "broker": None, "tier": None,
        "spot": None, "spot_change_pct": None,
        "n_positions": None,
        "totals": {"total_pnl": 0.0, "booked_pnl": 0.0, "unbooked_pnl": 0.0},
        "positions": [],
        "raw_text_preview": text[:300],
    }

    # ── Header: "Nth Month Instrument Tier (Broker)" or "M." suffix ──
    # Capture: day, month, instrument, tier text, broker
    header_re = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+"
        r"(Sensex|Nifty)\s+(.+?)\s*(?:\((Axis|Monarch|Zerodha|HDFC|ICICI|Kotak)\)|([A-Z])\s*\.|\s*$)",
        re.IGNORECASE
    )
    for line in text.splitlines():
        m = header_re.search(line.strip())
        if m:
            day, mo, inst, tier_raw, broker, broker_init = m.groups()
            result["portfolio_name"] = line.strip()
            result["instrument"] = inst.upper()
            if broker:
                result["broker"] = broker
            elif broker_init:
                result["broker"] = {"M": "Monarch", "A": "Axis", "Z": "Zerodha"}.get(broker_init.upper())
            tier_raw = tier_raw.strip()
            # Tier matching
            t_low = tier_raw.lower()
            if "deep" in t_low or "deepest" in t_low:
                result["tier"] = "Deep OTM"
            elif "mid" in t_low and "risk" in t_low:
                result["tier"] = "Mid Risk"
            elif "high" in t_low and "risk" in t_low:
                result["tier"] = "High Risk"
            elif "atm" in t_low or "straddle" in t_low:
                result["tier"] = "ATM"
            else:
                result["tier"] = tier_raw
            break

    # ── Position counter: "3 of 3 Positions" ──
    m = re.search(r"(\d+)\s+of\s+(\d+)\s+Positions?", text, re.IGNORECASE)
    if m: result["n_positions"] = int(m.group(2))

    # ── Totals ──
    for key, label in [("total_pnl", r"Total\s*P[&&]?L"),
                       ("booked_pnl", r"Booked\s*P[&&]?L"),
                       ("unbooked_pnl", r"Unbooked\s*P[&&]?L")]:
        m = re.search(label + r"\s*([+\-]?[\d,]+(?:\.\d+)?[LKk]?|\-+)", text)
        if m: result["totals"][key] = _parse_amount(m.group(1))

    # ── Spot: "SENSEX 73524.26 -0.97%" or "NIFTY 23123.45 -1.04%" ──
    m = re.search(r"(SENSEX|NIFTY)\s+([\d,]+\.?\d*)\s*([+\-][\d.]+%)?", text)
    if m:
        result["spot"] = float(m.group(2).replace(",", ""))
        if m.group(3): result["spot_change_pct"] = float(m.group(3).replace("%", ""))

    # ── Position rows ──
    # Pattern: "04th Jun 72000 PE [E] qty avg ltp total_pnl unbooked booked"
    # Each numeric col can be: "0", "0.00", "0.05", "+45,575", "+1.88L", "+5,980"
    # qty can also be "-5980" or "-5,980" (negative for short)
    row_re = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w{3})\s+(\d{4,6})\s+(PE|CE)\s*"
        r"(?:\[?E\]?\s+)?"                                 # optional [E] expired marker
        r"([+\-]?[\d,]+)\s+"                                # qty
        r"([\d,]+\.?\d*)\s+"                                # avg
        r"([\d,]+\.?\d*)\s+"                                # ltp
        r"([+\-]?[\d,]+(?:\.\d+)?[LKk]?)\s+"               # total_pnl
        r"([+\-]?[\d,]+(?:\.\d+)?[LKk]?)\s+"               # unbooked
        r"([+\-]?[\d,]+(?:\.\d+)?[LKk]?)"                  # booked
    )
    for line in text.splitlines():
        m = row_re.search(line)
        if not m: continue
        day, mo, strike, side, qty, avg, ltp, tp, ub, bk = m.groups()
        result["positions"].append({
            "expiry_day": int(day), "expiry_month": mo,
            "instrument": result["instrument"],
            "strike": int(strike),
            "side": side,
            "qty": int(qty.replace(",", "")),
            "avg_price": float(avg.replace(",", "")),
            "ltp": float(ltp.replace(",", "")),
            "total_pnl": _parse_amount(tp),
            "unbooked_pnl": _parse_amount(ub),
            "booked_pnl": _parse_amount(bk),
            "broker": result.get("broker"),
            "tier": result.get("tier"),
        })

    # Reconcile broker via inverse: if found in positions, infer broker
    if not result["broker"] and result["positions"]:
        pass  # could add: ask user to confirm

    return result


def format_ingestion_reply(parsed: dict) -> str:
    """Format the parsed Sensibull data as a Telegram reply."""
    if not parsed.get("positions"):
        return (f"📷 *OCR ran but couldn't extract positions.*\n\n"
                f"Header detected: `{parsed.get('portfolio_name', 'n/a')}`\n"
                f"Spot detected: `{parsed.get('spot', 'n/a')}`\n\n"
                f"Raw OCR (first 300 chars):\n```\n{parsed['raw_text_preview']}\n```\n\n"
                f"Please send a clearer screenshot or paste trade details as text.")

    portfolio = parsed.get("portfolio_name", "—")
    broker = parsed.get("broker") or "?"
    tier = parsed.get("tier") or "?"
    inst = parsed.get("instrument") or "?"
    spot = parsed.get("spot")
    n = len(parsed["positions"])

    msg = f"📷 *Trade screenshot parsed*\n\n"
    msg += f"*Portfolio:* `{portfolio}`\n"
    msg += f"*Broker:* {broker} · *Tier:* {tier} · *Instrument:* {inst}\n"
    if spot: msg += f"*Spot at capture:* {spot}\n"
    msg += f"\n*{n} position{'s' if n != 1 else ''}:*\n"
    for i, p in enumerate(parsed["positions"], 1):
        sign = "S" if p["qty"] < 0 else "B"
        qty_disp = f"{abs(p['qty']):,}"
        msg += (f"{i}. {sign} {qty_disp} × {p['expiry_day']:02d}-{p['expiry_month']} "
                f"{p['strike']} {p['side']}  @ ₹{p['avg_price']:.2f}\n")
        msg += f"   LTP ₹{p['ltp']:.2f} · P&L {inr(p['total_pnl'])}\n"

    t = parsed["totals"]
    msg += f"\n*Totals:*\n"
    msg += f"   Total: {inr(t['total_pnl'])}\n"
    if t["booked_pnl"]: msg += f"   Booked: {inr(t['booked_pnl'])}\n"
    if t["unbooked_pnl"]: msg += f"   Unbooked: {inr(t['unbooked_pnl'])}\n"

    msg += f"\nReply `/yes` to save to dashboard, `/no` to discard, or send another screenshot."
    return msg


# ─── Command handlers (text messages) ───────────────────────────────────────────
def handle_command(text: str, state: dict, chat_id: int, sender_name: str, role: str) -> str:
    text = text.strip()
    text_lc = text.lower()

    # ── ADMIN-ONLY commands ──
    if role == "admin":
        if text_lc.startswith("/whitelist_add"):
            # /whitelist_add 1234567 ravi
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                return "Usage: `/whitelist_add <chat_id> [name]`"
            try:
                add_id = int(parts[1])
            except ValueError:
                return "chat_id must be integer"
            name = parts[2] if len(parts) > 2 else f"user_{add_id}"
            state.setdefault("whitelist", {})[str(add_id)] = {
                "name": name, "role": "team",
                "added_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
            }
            save_state(state)
            # Notify the new user
            tg_send(f"👋 You've been authorized to use Theta Quant Bot by Rohan.\n\nSend /help to see commands.", chat_id=add_id)
            return f"✓ Added chat_id `{add_id}` as `{name}` (role: team)"

        if text_lc.startswith("/whitelist_remove"):
            parts = text.split()
            if len(parts) != 2: return "Usage: `/whitelist_remove <chat_id>`"
            wl = state.get("whitelist", {})
            removed = wl.pop(parts[1], None)
            save_state(state)
            return f"✓ Removed `{parts[1]}`" if removed else f"Not in whitelist"

        if text_lc == "/whitelist":
            wl = state.get("whitelist", {})
            if not wl: return "_no team members. Add via /whitelist_add <chat_id> <name>_"
            out = "👥 *Whitelist:*\n"
            for cid, info in wl.items():
                out += f"  `{cid}` — {info.get('name','?')} ({info.get('role','team')}, added {info.get('added_at','?')})\n"
            return out

        if text_lc == "/pause":
            state["paused"] = True; save_state(state)
            return "⏸ Scheduled sends paused. /resume to enable."
        if text_lc == "/resume":
            state["paused"] = False; save_state(state)
            return "▶️ Scheduled sends resumed."

    # ── COMMON commands (all authorized users) ──
    if text_lc in ("/status", "/regime"):
        return build_morning_regime()
    if text_lc == "/positions":
        snap = fetch_snapshot_persisted(datetime.now(IST).strftime("%Y-%m-%d"))
        ps = snap.get("positions", []) if snap else []
        if not ps: return "_no positions saved today_"
        out = f"📋 *{len(ps)} positions saved today*\n"
        for p in ps[:20]:
            out += f"  {p.get('side')} {p.get('strike')} {p.get('instrument','?')} qty {p.get('qty')} @ ₹{p.get('avg_price')}\n"
        return out
    if text_lc.startswith("/detail"):
        parts = text_lc.split()
        what = parts[1] if len(parts) > 1 else "day"
        if what in ("day", "report", "pnl"):
            return build_day_end()
        if what in ("recs", "strikes", "rec"):
            return build_strike_recs()
        if what in ("regime", "morning"):
            return build_morning_regime()
        if what in ("premium", "decay"):
            return build_hourly_premium()
        return "Usage: `/detail day` `/detail recs` `/detail regime` `/detail premium`"
    if text_lc.startswith("/sample"):
        parts = text_lc.split()
        if len(parts) == 2:
            return build_message(parts[1])
        return "Usage: `/sample morning_regime` `/sample strike_recs` `/sample hourly_premium` `/sample day_end`"
    if text_lc.startswith("/recommend") or text_lc.startswith("/rec"):
        parts = text_lc.replace(",", " ").split()[1:]   # tokens after the command
        tier_filter = None; inst_filter = None
        for t in parts:
            t = t.strip().lower()
            if t in ("1", "tier1", "tier-1", "deep", "deep-otm"): tier_filter = "1"
            elif t in ("2", "tier2", "tier-2", "mid"): tier_filter = "2"
            elif t in ("3", "tier3", "tier-3", "near", "star"): tier_filter = "3"
            elif t in ("nifty", "nf", "n"): inst_filter = "NIFTY"
            elif t in ("sensex", "sx", "s"): inst_filter = "SENSEX"
            elif t in ("banknifty", "bnf", "bn"): inst_filter = "BANKNIFTY"
        return build_strike_recs(filter_tier=tier_filter, filter_inst=inst_filter)
    if text_lc.startswith("/trade") or text_lc.startswith("/levels") or text_lc.startswith("/triggers"):
        return handle_trade_command(text, state)
    if text_lc.startswith("/mytrades") or text_lc.startswith("/active"):
        trades = state.get("active_trades", [])
        if not trades:
            return "_no active trades. Add one with: /trade tier3 nifty 22300pe@2 24400ce@3_"
        out = f"📋 *Active trades you're tracking ({len(trades)}):*\n\n"
        for i, t in enumerate(trades, 1):
            out += f"{i}. {t['tier']} {t['instrument']} {t['pe_strike']}PE@₹{t['pe_entry']} / {t['ce_strike']}CE@₹{t['ce_entry']}\n"
        out += "\n/triggers <id> to re-show levels · /forget <id> to remove"
        return out
    if text_lc.startswith("/news") or text_lc.startswith("/risk"):
        try:
            import sys as _s; _s.path.insert(0, str(ROOT))
            from lib.news import risk_brief
            rb = risk_brief()
            out = f"🌍 *RISK BRIEF — {rb['generated_at']}*\n\n"
            cr = rb["crude"]
            if cr.get("available"):
                flag = " ⚠ HARD EXCLUSION" if cr["flag"] else ""
                out += f"*Brent:* ${cr['price']} ({cr['change_pct_1d']:+}% 1d){flag}\n\n"
            out += "*Scheduled events (3d):*\n"
            if rb["events"]:
                for e in rb["events"]:
                    tag = "⛔" if e["exclusion_flag"] else "📅"
                    out += f"{tag} {e['label']} — {e['date']}\n"
            else:
                out += "none\n"
            out += "\n*Headlines:*\n"
            for h in rb["headlines"]:
                out += f"• {h['title'][:80]}\n"
            if rb["any_exclusion"]:
                out += "\n⛔ *EXCLUSION ACTIVE — Tier 1 only*"
            return out
        except Exception as e:
            return f"⚠ risk brief failed: {e}"
    if text_lc.startswith("/forget"):
        parts = text.split()
        if len(parts) < 2: return "Usage: /forget <id> (see /mytrades)"
        try:
            i = int(parts[1]) - 1
            if 0 <= i < len(state.get("active_trades", [])):
                removed = state["active_trades"].pop(i)
                save_state(state)
                return f"🗑 Forgot trade: {removed.get('pe_strike')}PE / {removed.get('ce_strike')}CE"
        except Exception: pass
        return "Couldn't parse id. Try /mytrades first."
    if text_lc == "/yes":
        return save_pending_screenshot(state, sender_name)
    if text_lc == "/no":
        state.get("pending_screenshots", {}).pop(sender_name, None); save_state(state)
        return "🗑 Discarded. Send another screenshot when ready."
    if text_lc == "/myid":
        return f"Your chat_id: `{chat_id}`\nName: {sender_name}\nRole: {role}\n\nTo get added to whitelist, share this chat_id with Rohan."
    if text_lc.startswith("/help") or text_lc == "/start":
        admin_cmds = ""
        if role == "admin":
            admin_cmds = ("\n*Admin only:*\n"
                          "/whitelist — list team members\n"
                          "/whitelist_add <chat_id> <name> — add team member\n"
                          "/whitelist_remove <chat_id> — revoke access\n"
                          "/pause /resume — toggle scheduled sends\n")
        return (f"👋 *Theta Quant Bot* (role: `{role}`)\n\n"
                "*🎯 Strike recommendations (anytime):*\n"
                "/recommend — auto (today's expiring index)\n"
                "/recommend tier1 (or tier2/tier3)\n"
                "/recommend nifty (or sensex)\n"
                "_Auto-fires daily: 08:30, 09:30, 11:00 IST_\n\n"
                "*📍 Track your trade levels:*\n"
                "/trade tier3 nifty 22300pe@2 24400ce@3\n"
                "  → returns Yellow/Red/profit-take levels\n"
                "/mytrades — list trades you're tracking\n"
                "/forget <id> — remove from tracking\n\n"
                "*Live data:*\n"
                "/status — current regime + tier qualification\n"
                "/positions — saved trades today\n"
                "/myid — your chat_id\n\n"
                "*Full details (scheduled msgs are brief):*\n"
                "/detail day — strategy-wise P&L report\n"
                "/detail recs — both options per tier + logic\n"
                "/detail regime — full regime + tier qualification\n"
                "/detail premium — premium decay status\n\n"
                "*Previews:*\n"
                "/sample morning_regime\n"
                "/sample strike_recs\n"
                "/sample hourly_premium\n"
                "/sample day_end\n\n"
                "*Screenshot ingestion:*\n"
                "Send a Sensibull/Kite screenshot. I'll OCR + parse.\n"
                "Reply /yes to save to dashboard, /no to discard."
                + admin_cmds)
    return f"Unknown: `{text}`. Try /help"


def handle_photo(msg: dict, state: dict, sender_name: str = "admin") -> str:
    photo = msg.get("photo", [])
    if not photo:
        return "no photo in message"
    # Use largest version (highest resolution)
    file_id = max(photo, key=lambda p: p.get("file_size", 0))["file_id"]
    img = tg_get_file(file_id)
    if not img:
        return "⚠️ couldn't download photo"
    text = ocr_screenshot(img)
    if text.startswith("[") and text.endswith("]") and len(text) < 500:
        # Error marker (OCR not installed, etc.)
        return text
    import sys as _s; _s.path.insert(0, str(ROOT))
    from lib import ocr as _o
    parsed_v = _o.parse_sensibull(text)
    parsed = parsed_v if parsed_v.get("positions") else parse_sensibull_portfolio(text)
    # Store as pending — keyed by sender so concurrent team uploads don't collide
    state.setdefault("pending_screenshots", {})[sender_name] = {
        "parsed": parsed,
        "timestamp": datetime.now(IST).isoformat(),
    }
    save_state(state)
    return format_ingestion_reply(parsed)


def save_pending_screenshot(state: dict, sender_name: str = "admin") -> str:
    """Persist the pending parsed screenshot to dashboard via /api/snapshot/save."""
    # Per-sender pending so concurrent team uploads don't collide
    pending = state.get("pending_screenshots", {}).get(sender_name)
    if not pending:
        return "_no pending screenshot to save (your last upload may have expired)_"
    parsed = pending["parsed"]
    positions_to_save = []
    for p in parsed["positions"]:
        positions_to_save.append({
            "instrument": p["instrument"],
            "strike": p["strike"],
            "side": p["side"],
            "qty": p["qty"],
            "avg_price": p["avg_price"],
            "broker": p.get("broker") or parsed.get("broker"),
            "demat": "",
            "expiry": "",  # could be inferred from expiry_day+month
            "note": f"{parsed.get('tier','')} (from screenshot {pending['timestamp'][:10]})",
        })
    # Get today's date in IST
    today = datetime.now(IST).strftime("%Y-%m-%d")
    # Fetch existing positions for today (to APPEND, not overwrite)
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/snapshot/{today}", timeout=10) as r:
            existing = json.load(r)
        existing_positions = existing.get("positions", []) or []
    except Exception:
        existing_positions = []

    # Build new full position list (append parsed to existing)
    merged = existing_positions + positions_to_save

    # POST to dashboard
    try:
        body = json.dumps({
            "date": today, "positions": merged,
            "note": f"+{len(positions_to_save)} from {sender_name} screenshot ({parsed.get('portfolio_name','')})",
        }).encode()
        req = ureq.Request(
            f"{DASHBOARD}/api/snapshot/save",
            data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with ureq.urlopen(req, timeout=15) as r:
            res = json.load(r)
        # Clear this sender's pending
        state.get("pending_screenshots", {}).pop(sender_name, None)
        save_state(state)

        # Notify admin if save was from a team member
        cfg = load_cfg()
        admin_id = cfg.get("chat_id")
        if sender_name != "admin" and admin_id:
            admin_msg = (f"📥 *Team member ingest*\n\n"
                         f"From: *{sender_name}*\n"
                         f"Portfolio: `{parsed.get('portfolio_name','—')}`\n"
                         f"Saved {len(positions_to_save)} positions, total {len(merged)} today.\n"
                         f"Total P&L parsed: ₹{parsed.get('totals',{}).get('total_pnl',0):,.0f}")
            tg_send(admin_msg, chat_id=admin_id)

        return (f"✅ *Saved {len(positions_to_save)} positions* to dashboard for {today}\n"
                f"Total positions today: {len(merged)}\n"
                f"Total MTM: {inr(res.get('total_mtm', 0))}\n\n"
                f"Open http://127.0.0.1:8000/report to view")
    except Exception as e:
        return f"⚠️ save failed: {e}"


# ─── Main loop ──────────────────────────────────────────────────────────────────
def should_send(hh: int, mm: int, msg_type: str, state: dict) -> bool:
    """Check if it's time to fire this scheduled message and we haven't already today."""
    now = datetime.now(IST)
    # Weekday only (Mon-Fri)
    if now.weekday() >= 5: return False
    # Within 90s of scheduled time
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if abs((now - target).total_seconds()) > 90: return False
    # Not already sent today
    today_key = now.strftime("%Y-%m-%d")
    last = state.get("last_sent", {}).get(f"{today_key}__{hh:02d}{mm:02d}__{msg_type}")
    return last is None


def check_position_triggers(state: dict) -> None:
    """Poll dashboard positions + /trade-tracked positions. Fire Yellow/Red alerts
    when spot crosses trigger levels. Throttled 5-min per (position, kind)."""
    now = datetime.now(IST)
    snap = fetch_snapshot()
    if not snap: return
    spots = {"NIFTY": snap.get("NIFTY", {}).get("spot"),
             "SENSEX": snap.get("SENSEX", {}).get("spot")}
    if not any(spots.values()): return

    today = now.strftime("%Y-%m-%d")
    dashboard_positions = []
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/snapshot/{today}", timeout=8) as r:
            d = __import__("json").load(r)
            dashboard_positions = d.get("positions", []) or []
    except Exception:
        pass

    fired = state.setdefault("trigger_fired", {})

    def should_fire(key: str) -> bool:
        last = fired.get(key)
        if not last: return True
        try: return (now - datetime.fromisoformat(last)).total_seconds() > 300
        except Exception: return True

    # Dashboard-imported positions: use current strike distance as risk proxy
    for p in dashboard_positions:
        inst = (p.get("instrument") or "").upper()
        strike = p.get("strike"); side = p.get("side")
        if not (inst in spots and spots[inst] and strike and side): continue
        spot = spots[inst]
        if side == "PE":
            buffer_pts = spot - strike
        else:
            buffer_pts = strike - spot
        if buffer_pts == 0: continue
        pct = abs(buffer_pts) / spot * 100

        broker = p.get("broker", "?")
        note = p.get("note", "")
        qty = abs(int(p.get("qty", 0)))

        if buffer_pts < 0:
            key = f"red_itm:{broker}-{strike}-{side}"
            if should_fire(key):
                tg_send(f"🔴 *RED — {inst} {strike} {side} IS ITM*\nSpot {spot} crossed {strike}\nBroker: {broker} · Qty: {qty:,}\nNote: {note}\n→ Close if still holding")
                fired[key] = now.isoformat()
        elif pct < 0.15:
            key = f"red_near:{broker}-{strike}-{side}"
            if should_fire(key):
                tg_send(f"🔴 *RED ZONE — {inst} {strike} {side}*\nSpot {spot}, strike {strike}, buffer {int(abs(buffer_pts))} pts ({pct:.2f}%)\nBroker: {broker} · Qty: {qty:,}\n→ Consider closing")
                fired[key] = now.isoformat()
        elif pct < 0.40:
            key = f"yellow:{broker}-{strike}-{side}"
            if should_fire(key):
                tg_send(f"🟡 *YELLOW WATCH — {inst} {strike} {side}*\nSpot {spot}, buffer {int(abs(buffer_pts))} pts ({pct:.2f}%)\nBroker: {broker} · Qty: {qty:,}\nNote: {note}\n→ Monitor for 30-min directional move")
                fired[key] = now.isoformat()

    # /trade-tracked positions: precise entry-buffer triggers
    for t in state.get("active_trades", []) or []:
        inst = t.get("instrument"); spot = spots.get(inst) if inst else None
        if not spot: continue
        tid = t.get("id")
        if spot <= t.get("yellow_pe_spot", 0):
            key = f"yellow_pe_t:{tid}"
            if should_fire(key):
                tg_send(f"🟡 *YELLOW PE — trade #{tid}*\n{inst} {t['pe_strike']}PE @ ₹{t['pe_entry']}\nSpot {spot} ≤ {t['yellow_pe_spot']}\n→ If 30-min drop ≥0.4%, close PE leg only")
                fired[key] = now.isoformat()
        if spot >= t.get("yellow_ce_spot", 99e9):
            key = f"yellow_ce_t:{tid}"
            if should_fire(key):
                tg_send(f"🟡 *YELLOW CE — trade #{tid}*\n{inst} {t['ce_strike']}CE @ ₹{t['ce_entry']}\nSpot {spot} ≥ {t['yellow_ce_spot']}\n→ If 30-min rise ≥0.4%, close CE leg only")
                fired[key] = now.isoformat()
        if spot <= t.get("red_pe_spot", 0) or spot >= t.get("red_ce_spot", 99e9):
            key = f"red_t:{tid}"
            if should_fire(key):
                tg_send(f"🔴 *RED — trade #{tid}*\n{inst} {t['pe_strike']}PE / {t['ce_strike']}CE\nSpot {spot} crossed 85% buffer\n→ CLOSE BOTH AT MARKET")
                fired[key] = now.isoformat()

    # Journal trades: relay server-computed PROFIT_TAKE / ORANGE / RED
    try:
        with ureq.urlopen(f"{DASHBOARD}/api/monitor/status", timeout=10) as r:
            mon = json.load(r)
        for m in mon.get("items", []):
            lvl = m.get("overall")
            if lvl not in ("PROFIT_TAKE", "ORANGE", "RED", "RED_ITM"):
                continue
            key = f"jr:{m['id']}:{lvl}"
            if not should_fire(key):
                continue
            name = m.get("strategy_name") or f"{m.get('instrument')} {m.get('tier','')}"
            if lvl == "PROFIT_TAKE":
                tg_send(f"💰 *TAKE PROFIT — {name}*\nCombined ₹{m.get('combined_entry')} → ₹{m.get('combined_now')} ({m.get('decay_pct')}% decayed)\n→ Book both legs, lock the win.")
            elif lvl == "ORANGE":
                legs_txt = "\n".join(
                    f"{l['strike']} {l['side']}: {l['buffer_pts']}pts · 🔴 exit if spot {l.get('direction','')} {l.get('red_at',0):,}"
                    for l in m.get("legs", []) if l.get("level") != "SAFE")
                tg_send(f"🟠 *APPROACHING TRIGGER — {name}*\n{legs_txt}\n→ Prepare exit; if 30-min move confirms, close threatened leg.")
            else:
                tg_send(f"🔴 *RED — {name}*\n→ CLOSE BOTH LEGS AT MARKET. No re-entry today.")
            fired[key] = now.isoformat()
    except Exception as e:
        log(f"journal monitor relay error: {e}")

    state["trigger_fired"] = fired


def main():
    log("=== telegram_bot.py START ===")
    cfg = load_cfg()
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        log("✗ bot not configured — exiting")
        return

    log(f"connected as @{cfg.get('bot_username')} chat_id {cfg.get('chat_id')}")

    state = load_state()
    last_trigger_check = 0
    last_alert_check = 0

    while True:
        try:
            now = datetime.now(IST)

            # 1. Check scheduled sends
            if not state.get("paused", False):
                for hh, mm, mtype in SCHEDULE:
                    if should_send(hh, mm, mtype, state):
                        msg = build_message(mtype)
                        if tg_send(msg):
                            today_key = now.strftime("%Y-%m-%d")
                            state.setdefault("last_sent", {})[f"{today_key}__{hh:02d}{mm:02d}__{mtype}"] = now.isoformat()
                            save_state(state)
                            log(f"✓ sent {mtype}")

            # 1b. ALWAYS-ON live alert monitor — air-pocket / trend-break / position-near-strike.
            # Pushes the moment a condition fires; deduped once per (day, key).
            if not state.get("paused", False) and (time.time() - last_alert_check >= 60):
                last_alert_check = time.time()
                try:
                    la = fetch_live_alerts()
                    if la.get("market_open"):
                        today_key = now.strftime("%Y-%m-%d")
                        sent = state.setdefault("alerted", {})
                        # drop yesterday's keys to keep state small
                        for k in [k for k in sent if not k.startswith(today_key)]:
                            sent.pop(k, None)
                        for a in la.get("alerts", []):
                            if a.get("severity") not in ("WARN", "CRITICAL"):
                                continue
                            key = f"{today_key}__{a.get('key','')}"
                            if key in sent:
                                continue
                            icon = "🔴" if a["severity"] == "CRITICAL" else "🟠"
                            if tg_send(f"{icon} *{a.get('title','Alert')}*\n{a.get('body','')}"):
                                sent[key] = now.isoformat(); save_state(state)
                                log(f"✓ live alert: {a.get('key')}")
                except Exception as e:
                    log(f"live-alert tick err: {e}")

            # 2. Poll incoming
            updates = tg_get_updates(state.get("last_update_id", 0) + 1)
            for upd in updates:
                state["last_update_id"] = max(state.get("last_update_id", 0), upd.get("update_id", 0))
                msg = upd.get("message") or {}
                if not msg: continue

                # Identify sender
                from_user = msg.get("from", {})
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                if not chat_id: continue
                sender_name = (from_user.get("first_name", "") + " " + from_user.get("last_name", "")).strip() or from_user.get("username", f"user_{chat_id}")

                # Auth check
                allowed, role = is_authorized(chat_id, state)
                if not allowed:
                    log(f"unauthorized: chat_id={chat_id} name={sender_name} text={msg.get('text','<photo>')[:50]}")
                    # Tell user politely (and notify admin)
                    tg_send(
                        f"🔒 You're not authorized to use this bot.\n\nYour chat_id is `{chat_id}`. Share this with Rohan to be added.",
                        chat_id=chat_id,
                    )
                    cfg = load_cfg()
                    admin_id = cfg.get("chat_id")
                    if admin_id and chat_id != admin_id:
                        tg_send(
                            f"🔒 *Unauthorized access attempt*\n\n"
                            f"Name: {sender_name}\nchat_id: `{chat_id}`\n"
                            f"Sent: _{msg.get('text','<photo>')[:80]}_\n\n"
                            f"To authorize: `/whitelist_add {chat_id} {sender_name.split()[0] if sender_name else 'name'}`",
                            chat_id=admin_id,
                        )
                    continue

                # Handle
                if msg.get("text"):
                    reply = handle_command(msg["text"], state, chat_id, sender_name, role)
                    tg_send(reply, chat_id=chat_id)
                elif msg.get("photo"):
                    reply = handle_photo(msg, state, sender_name)
                    tg_send(reply, chat_id=chat_id)
            save_state(state)

            # 3. Live position trigger check every ~60 sec during market hours
            if time.time() - last_trigger_check >= 60:
                if now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 25):
                    if not state.get("paused", False):
                        try:
                            check_position_triggers(state)
                            save_state(state)
                        except Exception as e:
                            log(f"trigger check error: {e}")
                last_trigger_check = time.time()

        except KeyboardInterrupt:
            log("=== KeyboardInterrupt ===")
            break
        except Exception as e:
            log(f"main loop error: {e}")

        time.sleep(8)


if __name__ == "__main__":
    main()
