"""
lib/news.py — risk-event awareness for bot + web. Single source of truth.

Three layers:
  1. EVENT_CALENDAR — hardcoded scheduled macro events (RBI MPC, Fed FOMC, budget).
     Update yearly; these are the events that matter for expiry trading.
  2. crude_check() — Brent proxy move via free API (no key) with graceful failure.
  3. headlines() — free NewsAPI-compatible fetch (key optional, set in ~/.config/news_api.json
     as {"provider": "newsapi", "api_key": "..."} ). Falls back to Google News RSS (no key).

All functions return plain dicts; bot renders markdown, web renders JSON.
"""
from __future__ import annotations
import json
import ssl
import urllib.request as ureq
import urllib.parse as uparse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

CFG_PATH = Path.home() / ".config" / "news_api.json"

# ─── 1. Scheduled macro events (update yearly) ─────────────────────────────────
# Format: (date, label, severity)  severity: HIGH = hard-exclusion day, MED = caution
EVENT_CALENDAR = [
    # RBI MPC 2026 (bi-monthly; announcement day)
    (date(2026, 6, 5),  "RBI MPC decision", "HIGH"),
    (date(2026, 8, 6),  "RBI MPC decision", "HIGH"),
    (date(2026, 10, 1), "RBI MPC decision", "HIGH"),
    (date(2026, 12, 4), "RBI MPC decision", "HIGH"),
    # Fed FOMC 2026 (decision day, IST impact next morning)
    (date(2026, 6, 17), "Fed FOMC decision (US evening — impacts next IST open)", "HIGH"),
    (date(2026, 7, 29), "Fed FOMC decision", "HIGH"),
    (date(2026, 9, 16), "Fed FOMC decision", "HIGH"),
    (date(2026, 11, 4), "Fed FOMC decision", "HIGH"),
    (date(2026, 12, 16),"Fed FOMC decision", "HIGH"),
    # India events
    (date(2027, 2, 1),  "Union Budget", "HIGH"),
]


def upcoming_events(days_ahead: int = 3) -> list[dict]:
    """Events today or within N days. The 24h-window ones are hard-exclusion flags."""
    today = datetime.now(IST).date()
    out = []
    for d, label, sev in EVENT_CALENDAR:
        delta = (d - today).days
        if 0 <= delta <= days_ahead:
            out.append({"date": d.isoformat(), "days_away": delta, "label": label,
                        "severity": sev, "exclusion_flag": delta <= 1 and sev == "HIGH"})
    return out


# ─── 2. Crude oil check (free, no key) ─────────────────────────────────────────
def crude_check() -> dict:
    """Brent via Yahoo Finance chart API (free, no key; BZ=F).
    Returns {price, change_pct_1d, flag} — flag True if |move| > 3%."""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/BZ%3DF?interval=1d&range=5d"
        req = ureq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with ureq.urlopen(req, timeout=10, context=SSL_CTX) as r:
            d = json.load(r)
        res = d["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            return {"available": False}
        prev, last = closes[-2], closes[-1]
        chg = (last - prev) / prev * 100
        return {"available": True, "price": round(last, 2),
                "change_pct_1d": round(chg, 2), "flag": abs(chg) > 3.0}
    except Exception:
        return {"available": False}


# ─── 3. Headlines (free) ───────────────────────────────────────────────────────
_KEYWORDS = "india OR nifty OR sensex OR RBI OR fed OR crude OR geopolitical OR war"


def google_news(query: str, max_items: int = 10) -> list[dict]:
    """Free Google News RSS (no key, KITE-INDEPENDENT) → [{title, url, source, at}]."""
    import re
    try:
        q = uparse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        with ureq.urlopen(url, timeout=10, context=SSL_CTX) as r:
            xml = r.read().decode("utf-8", errors="ignore")
        out = []
        for it in re.findall(r"<item>(.*?)</item>", xml, re.S)[:max_items]:
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            l = re.search(r"<link>(.*?)</link>", it, re.S)
            p = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
            s = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
            title = (t.group(1) if t else "").replace("<![CDATA[", "").replace("]]>", "").strip()
            if not title:
                continue
            out.append({"title": title, "url": (l.group(1).strip() if l else ""),
                        "source": (s.group(1).strip() if s else "Google News"),
                        "at": (p.group(1)[:22] if p else "")})
        return out
    except Exception:
        return []


def stock_news(symbol: str, max_items: int = 8) -> list[dict]:
    """Per-stock news (free) — for the Research drill-down. No broker session needed."""
    sym = (symbol or "").strip().upper()
    return google_news(f"{sym} share NSE", max_items)


def headlines(max_items: int = 6) -> list[dict]:
    """Try configured provider (newsapi key), else Google News RSS (no key)."""
    cfg = {}
    if CFG_PATH.exists():
        try: cfg = json.loads(CFG_PATH.read_text())
        except Exception: pass

    if cfg.get("provider") == "newsapi" and cfg.get("api_key"):
        try:
            q = uparse.quote("(india market) OR nifty OR sensex OR RBI OR crude oil")
            url = (f"https://newsapi.org/v2/everything?q={q}&language=en"
                   f"&sortBy=publishedAt&pageSize={max_items}&apiKey={cfg['api_key']}")
            with ureq.urlopen(url, timeout=10, context=SSL_CTX) as r:
                arts = json.load(r).get("articles", [])
            return [{"title": a["title"], "source": a["source"]["name"],
                     "at": a["publishedAt"][:16]} for a in arts[:max_items]]
        except Exception:
            pass

    # Fallback: Google News RSS — free, no key (now with URLs)
    return google_news("nifty OR sensex OR RBI OR 'india stock market' when:1d", max_items)


def risk_brief() -> dict:
    """Composite: events + crude + headlines. Used by bot 08:30 + /news + web."""
    ev = upcoming_events()
    cr = crude_check()
    hl = headlines()
    flags = [e["label"] for e in ev if e["exclusion_flag"]]
    if cr.get("flag"):
        flags.append(f"Brent moved {cr['change_pct_1d']}% in 24h (>3%)")
    return {"generated_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M"),
            "events": ev, "crude": cr, "headlines": hl,
            "exclusion_flags": flags, "any_exclusion": len(flags) > 0}
