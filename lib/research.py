"""Research / Analytics layer — a consensus screener over Rohan's stock universe,
built on signals WE compute (cc_signals: RSI/MACD/trend/breakout + CC verdict +
bull/bear momentum), plus per-symbol deep-links into his paid services.

Deliberately NOT a scraper of paid login-walled sites (Moneycontrol/Market Mojo/
Tejimandi/ET Prime/Screener have no retail API + ban automated access). Instead:
  • own-compute screener  → genuinely replaces the tab-hopping judgement
  • per-symbol launchers   → one click opens each paid site pre-loaded to the stock
  • news                   → free Google-News RSS, filtered to the name (lib/news)

Universe = CC holdings ∪ an editable watchlist (data/watchlist.json).
"""
from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "data" / "watchlist.json"

# Per-symbol deep links into the user's paid/free services (he's already logged in).
# {sym} is replaced with the NSE symbol. Public URL patterns only.
LAUNCHERS = [
    {"key": "screener",   "label": "Screener",     "url": "https://www.screener.in/company/{sym}/"},
    {"key": "tradingview","label": "TradingView",  "url": "https://www.tradingview.com/chart/?symbol=NSE:{sym}"},
    {"key": "moneycontrol","label": "Moneycontrol", "url": "https://www.moneycontrol.com/india/stockpricequote/search/?searchStr={sym}"},
    {"key": "marketmojo", "label": "Market Mojo",  "url": "https://www.marketsmojo.com/mojo/search?q={sym}"},
    {"key": "tijori",     "label": "Tijori",       "url": "https://www.tijorifinance.com/search/?q={sym}"},
    {"key": "etmarkets",  "label": "ET Markets",   "url": "https://economictimes.indiatimes.com/markets/stocks/news?query={sym}"},
    {"key": "tejimandi",  "label": "TejiMandi",    "url": "https://www.tejimandi.com/search?q={sym}"},
    {"key": "sensibull",  "label": "Sensibull",    "url": "https://web.sensibull.com/option-chain?tradingsymbol={sym}"},
]


def launchers(symbol: str) -> list[dict]:
    s = (symbol or "").upper().strip()
    return [{"key": l["key"], "label": l["label"], "url": l["url"].replace("{sym}", s)} for l in LAUNCHERS]


def _load_watchlist() -> list[str]:
    try:
        return json.loads(WATCHLIST.read_text()) if WATCHLIST.exists() else []
    except Exception:
        return []


def _save_watchlist(syms: list[str]) -> None:
    WATCHLIST.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST.write_text(json.dumps(sorted(set(syms)), indent=2))


def add_watch(symbol: str) -> list[str]:
    s = (symbol or "").upper().strip()
    if not s:
        return _load_watchlist()
    wl = _load_watchlist()
    if s not in wl:
        wl.append(s)
        _save_watchlist(wl)
    return _load_watchlist()


def remove_watch(symbol: str) -> list[str]:
    s = (symbol or "").upper().strip()
    wl = [x for x in _load_watchlist() if x != s]
    _save_watchlist(wl)
    return wl


def universe() -> list[str]:
    """CC holdings ∪ watchlist."""
    syms = set(_load_watchlist())
    try:
        from lib import cc_holdings as CCH
        for h in CCH.merged_holdings():
            if h.get("symbol"):
                syms.add(h["symbol"].upper())
    except Exception:
        pass
    return sorted(syms)


def _use_case(snap: dict) -> str:
    """Tag each name with what it's good FOR, from our own signals:
    sell_calls (bearish/range), momentum (strong up → S2 buy-write), avoid (near high / breakout)."""
    if not snap or snap.get("error"):
        return "unknown"
    bull = snap.get("bullish_score", 0); bear = snap.get("bearish_score", 0)
    trend = snap.get("trend_state", "")
    bo = snap.get("breakout_state")
    pct_off_high = snap.get("pct_off_high") or 0
    if bo == "confirmed" or pct_off_high > -0.03:
        return "avoid"          # near high / breaking out — bad to sell calls
    if bull >= 40 and trend in ("bullish", "weak_bullish"):
        return "momentum"       # strong up — S2 buy-write candidate
    if bear >= 25 or trend in ("bearish", "weak_bearish", "sideways"):
        return "sell_calls"     # range/bearish — safe to sell calls (S1/S2A)
    return "neutral"


def screen(symbols: list[str] | None = None, leg: str = "CE") -> list[dict]:
    """Run the own-compute screener over the universe. Returns one row per symbol
    with our technical verdict + momentum + use-case tag + launchers."""
    from lib import cc_signals as CS
    from concurrent.futures import ThreadPoolExecutor, as_completed
    syms = [s.upper() for s in (symbols or universe())]
    rows = {}
    if not syms:
        return []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(CS.verdict, s, leg): s for s in syms}
        for f in as_completed(futs):
            s = futs[f]
            try:
                v = f.result()
            except Exception as e:
                v = {"symbol": s, "error": str(e)[:60]}
            rows[s] = v
    out = []
    for s in syms:
        v = rows.get(s, {})
        out.append({
            "symbol": s,
            "spot": v.get("spot"),
            "verdict": v.get("verdict", "UNKNOWN"),
            "trend": v.get("trend_state"),
            "rsi_d": v.get("rsi_d"), "rsi_w": v.get("rsi_w"),
            "macd_d": v.get("macd_d_state"),
            "breakout": v.get("breakout_state"),
            "bull": v.get("bullish_score"), "bear": v.get("bearish_score"),
            "pct_off_high": v.get("pct_off_high"),
            "use_case": _use_case(v),
            "error": v.get("error"),
            "launchers": launchers(s),
        })
    # rank: sell-call candidates first (greenest), then by bear score
    order = {"sell_calls": 0, "neutral": 1, "momentum": 2, "avoid": 3, "unknown": 4}
    out.sort(key=lambda r: (order.get(r["use_case"], 5), -(r.get("bear") or 0)))
    return out
