"""
Trading Dashboard — Phase 1 MVP

Local FastAPI server. Run with:
    python3 -m uvicorn dashboard.server:app --reload --port 8000

Then open http://localhost:8000 in your browser.

Pulls live data via lib/kite_live.py — needs valid Kite session.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from pathlib import Path
import sys
from functools import lru_cache
import time as time_mod

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import json
from lib import playbook as pb

# ── App setup ────────────────────────────────────────────────────────────
app = FastAPI(title="Theta Quant", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Disable Jinja2 template cache to avoid hashable-key bug in jinja2 3.1.6
templates.env.cache = None

# ── Web access password (HTTP Basic) ─────────────────────────────────────
# Localhost (the Telegram bot + local scripts) is exempt so it keeps working.
# Any LAN / remote browser must enter the team password. Credentials live in
# ~/.config/thetadesk_web.json — change the password there, no code edit needed.
import base64 as _b64, secrets as _secrets, urllib.parse as _urllib_parse
from starlette.responses import Response as _Response, RedirectResponse as _RedirectResponse
_WEB_CRED_FILE = Path.home() / ".config" / "thetadesk_web.json"


def _web_creds() -> dict:
    # Env-var override: set TG_WEB_USERNAME + TG_WEB_PASSWORD in Vercel (or any env)
    _env_u = _os.environ.get("TG_WEB_USERNAME", "")
    _env_p = _os.environ.get("TG_WEB_PASSWORD", "")
    if _env_u and _env_p:
        return {"username": _env_u, "password": _env_p}
    if _WEB_CRED_FILE.exists():
        try:
            return json.loads(_WEB_CRED_FILE.read_text())
        except Exception:
            pass
    # first run: generate a RANDOM legacy master password (never a hardcoded default
    # in source). Printed once to the console so the operator can note it; the real
    # accounts are the per-user store. Rotate/delete this file to reset.
    import secrets as _sec
    default = {"username": "team", "password": _sec.token_urlsafe(18)}
    try:
        _WEB_CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _WEB_CRED_FILE.write_text(json.dumps(default, indent=2))
        try:
            _WEB_CRED_FILE.chmod(0o600)
        except Exception:
            pass
        print(f"[thetadesk] generated legacy master login → user 'team', "
              f"password '{default['password']}' (stored at {_WEB_CRED_FILE}); "
              f"prefer the per-user accounts.")
    except Exception:
        pass  # read-only fs (e.g. Vercel serverless) — run without legacy file
    return default


# ── Cookie session (form login) ───────────────────────────────────────────
# HTTP Basic still works (API clients, curl, loopback), but a WebView/phone shows
# an ugly native Basic popup. So we also accept a signed-cookie session set by the
# /login form. Signed with a per-install secret in ~/.config (never in git).
import hmac as _hmac, hashlib as _hashlib, time as _time_sess
_SESSION_FILE = Path.home() / ".config" / "thetadesk_session_secret"
_SESSION_COOKIE = "tg_session"
_SESSION_TTL = 30 * 24 * 3600         # 30 days


def _session_secret() -> bytes:
    try:
        if _SESSION_FILE.exists():
            return bytes.fromhex(_SESSION_FILE.read_text().strip())
    except Exception:
        pass
    sec = _secrets.token_bytes(32)
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(sec.hex())
        _SESSION_FILE.chmod(0o600)
    except Exception:
        pass
    return sec


def _make_session(username: str) -> str:
    exp = str(int(_time_sess.time()) + _SESSION_TTL)
    body = f"{username}|{exp}"
    sig = _hmac.new(_session_secret(), body.encode(), _hashlib.sha256).hexdigest()
    return _b64.urlsafe_b64encode(f"{body}|{sig}".encode()).decode()


def _verify_session(token: str) -> str | None:
    """Return the username if the cookie is valid + unexpired, else None."""
    try:
        raw = _b64.urlsafe_b64decode(token.encode()).decode()
        username, exp, sig = raw.rsplit("|", 2)
        expect = _hmac.new(_session_secret(), f"{username}|{exp}".encode(), _hashlib.sha256).hexdigest()
        if not _secrets.compare_digest(sig, expect):
            return None
        if int(exp) < int(_time_sess.time()):
            return None
        return username
    except Exception:
        return None


def _user_from_cookie(request: Request):
    """Resolve a logged-in user_obj from the session cookie, or None."""
    from lib import access as _access
    tok = request.cookies.get(_SESSION_COOKIE)
    if not tok:
        return None
    username = _verify_session(tok)
    if not username:
        return None
    u = _access.get_user(username)
    if u:
        return u
    # legacy master account (the 'team' login) → full admin
    if username == _web_creds().get("username", ""):
        return _access.ADMIN
    return None


# Requests we let through WITHOUT auth so the login page + app shell can load.
_PUBLIC_PATHS = ("/login", "/logout", "/manifest.json", "/api/health")


@app.middleware("http")
async def _no_cache(request: Request, call_next):
    """Never let the browser serve a stale page — fixes 'I can't see my changes'."""
    resp = await call_next(request)
    p = request.url.path
    if not p.startswith("/static") and "." not in p.rsplit("/", 1)[-1]:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


import ipaddress as _ipaddr
import os as _os
_TRUSTED_NETS = None

def _trusted_nets():
    global _TRUSTED_NETS
    if _TRUSTED_NETS is None:
        _TRUSTED_NETS = []
        for _h in (_os.environ.get("TG_TRUSTED_HOSTS", "") or "").split(","):
            _h = _h.strip()
            if _h:
                try:
                    _TRUSTED_NETS.append(_ipaddr.ip_network(_h, strict=False))
                except Exception:
                    pass
    return _TRUSTED_NETS


def _host_trusted(client: str) -> bool:
    if client in ("127.0.0.1", "::1", "localhost"):
        return True
    try:
        ip = _ipaddr.ip_address(client)
        return any(ip in net for net in _trusted_nets())
    except Exception:
        return False


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    client = (request.client.host if request.client else "") or ""
    # A request is REMOTE if it arrived through a proxy/tunnel (Cloudflare, etc.) —
    # those add forwarding headers. cloudflared connects FROM 127.0.0.1, so trusting
    # the client IP alone would let every tunnel visitor bypass login. So: true-local
    # = loopback IP AND no proxy headers → auto-admin on this computer; anything via
    # a tunnel or the LAN must log in.
    via_proxy = bool(request.headers.get("cf-connecting-ip")
                     or request.headers.get("x-forwarded-for")
                     or request.headers.get("x-forwarded-host")
                     or request.headers.get("x-real-ip"))
    # Trusted hosts auto-admin (no login) — loopback always, plus any IP/CIDR in
    # TG_TRUSTED_HOSTS (e.g. "192.168.1.0/24" for your home WiFi, or this Mac's IP).
    # NEVER trusted when the request came via a proxy/tunnel (Cloudflare) — those log in.
    from lib import access as _access
    path = request.url.path
    # Anti DNS-rebinding: loopback auto-admin only if the Host header is itself a
    # loopback name. A malicious site that rebinds its domain to 127.0.0.1 sends its
    # own domain in Host → it will NOT get auto-admin (falls through to login).
    _host = (request.headers.get("host") or "").split(":")[0].lower()
    host_is_local = _host in ("localhost", "127.0.0.1", "[::1]", "::1", "")
    is_loopback = (client in ("127.0.0.1", "::1")) and not via_proxy and host_is_local
    is_trusted_lan = (not via_proxy) and _host_trusted(client) and not is_loopback

    def _admit(user_obj):
        # Attach identity to the request and run path-level gates for non-admins.
        request.state.user_obj = user_obj
        request.state.role = user_obj.get("role")
        request.state.user = user_obj.get("username")
        request.state.scopes = user_obj.get("scopes")
        if not _access.is_admin(user_obj):
            # Admin-only areas
            if path.startswith("/admin") or path.startswith("/api/admin"):
                return _Response(status_code=403, content="Forbidden: admin only")
            # Write-lock for viewers
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and not _access.can_write(user_obj):
                # allow operational, non-mutating POSTs (Kite login, chat assistant)
                if not (path.startswith("/api/kite-exchange") or path.startswith("/api/chat")):
                    return _Response(status_code=403, content="Forbidden: read-only account")
            # Strategy-desk page gate
            strat = _access.route_strategy(path)
            if strat and not _access.allows(user_obj, "strategies", strat):
                return _Response(status_code=403, content=f"Forbidden: no access to {strat}")
        return None

    # Loopback (host console / Rohan's own Mac) → always admin. The Kite callback
    # runs on the separate :5000 listener, so no /callback route exists here; only
    # honour it from a genuine loopback client (never a tunnel).
    if is_loopback or (path.startswith("/callback") and client in ("127.0.0.1", "::1") and not via_proxy):
        return (_admit(_access.ADMIN)) or await call_next(request)
    # Office/LAN auto-admin ONLY until real user accounts exist; after that, log in.
    if is_trusted_lan and not _access.users_defined():
        return (_admit(_access.ADMIN)) or await call_next(request)

    # Public shell: the login page, its assets, manifest + health check load without auth.
    if path in _PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)

    # Everything else → require login (per-user store, with legacy team cred as master admin).
    cred = _web_creds()
    hdr = request.headers.get("authorization", "")
    if hdr.startswith("Basic "):
        try:
            user, _, pw = _b64.b64decode(hdr[6:]).decode("utf-8").partition(":")
            u = _access.authenticate(user, pw)
            if u:
                return (_admit(u)) or await call_next(request)
            # legacy master account → full admin (never lock yourself out)
            _cred = cred if isinstance(cred, dict) else {}
            if (_secrets.compare_digest(user, _cred.get("username", "")) and
                    _secrets.compare_digest(pw, _cred.get("password", ""))):
                return (_admit(_access.ADMIN)) or await call_next(request)
        except Exception:
            pass

    # Cookie session (set by the /login form) — the app/phone path (no Basic popup).
    su = _user_from_cookie(request)
    if su:
        return (_admit(su)) or await call_next(request)

    # Unauthenticated. Send a browser/WebView GET to the login page; APIs get 401.
    accept = request.headers.get("accept", "")
    if request.method == "GET" and "text/html" in accept:
        nxt = _urllib_parse.quote(request.url.path + ("?" + request.url.query if request.url.query else ""))
        return _RedirectResponse(url=f"/login?next={nxt}", status_code=303)
    return _Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Theta Quant"'})


# ── Login / logout (cookie session; nicer than the Basic popup in the app) ──
def _safe_next(nxt: str) -> str:
    """Only allow same-site relative redirects (block open-redirect)."""
    nxt = nxt or "/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        return "/"
    return nxt


@app.get("/login", response_class=HTMLResponse)
def page_login(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "error": None})


@app.post("/login")
def do_login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    from lib import access as _access
    dest = _safe_next(next)
    u = _access.authenticate(username, password)
    cred = _web_creds()
    _cred = cred if isinstance(cred, dict) else {}
    ok_legacy = (_secrets.compare_digest(username, _cred.get("username", "")) and
                 _secrets.compare_digest(password, _cred.get("password", "")))
    if not u and not ok_legacy:
        return templates.TemplateResponse(
            request, "login.html",
            {"next": dest, "error": "Wrong username or password."}, status_code=401)
    who = username if u else _cred.get("username", "team")
    resp = _RedirectResponse(url=dest, status_code=303)
    # Secure cookie: HttpOnly (JS can't read it), SameSite=Lax, 30-day. Secure flag on
    # so it only rides HTTPS (the tunnel) — loopback http still works since Secure is
    # advisory for localhost in practice; set via forwarded-proto.
    secure = bool(request.headers.get("x-forwarded-proto", "") == "https"
                  or request.url.scheme == "https")
    resp.set_cookie(_SESSION_COOKIE, _make_session(who), max_age=_SESSION_TTL,
                    httponly=True, samesite="lax", secure=secure, path="/")
    return resp


@app.get("/logout")
@app.post("/logout")
def do_logout():
    resp = _RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(_SESSION_COOKIE, path="/")
    return resp


IST = pytz.timezone("Asia/Kolkata")

# ── Trading constants ────────────────────────────────────────────────────
LOT_SIZE = {"NIFTY": 75, "SENSEX": 20}
GRID = {"NIFTY": 50, "SENSEX": 100}
# E-0 margin per lot for deep-OTM strikes (per Rohan's broker reality):
#   SENSEX expiry day: 40-41 lots/Cr → ~₹2.5L/lot
#   NIFTY  expiry day: 42-43 lots/Cr → ~₹2.35L/lot
# We size on E-0 margin (the larger one) so we don't over-size when E-1→E-0.
MARGIN_PER_LOT_E0 = {"NIFTY": 235000, "SENSEX": 250000}
LOTS_PER_CR = {"NIFTY": 43, "SENSEX": 40}
SHARES_PER_CR = {"NIFTY": 43*75, "SENSEX": 40*20}   # 3225 / 800
# Per-Cr capture floors per Navin Group Canonical Rulebook (Section 9T):
# Bucket A — Deep OTM E-0 main shot:
PREM_PER_CR_E0_FLOOR    = 4000     # absolute min — escalate below
PREM_PER_CR_E0_IDEAL    = 5000     # standard target
PREM_PER_CR_E0_FULL_QTY = 6000     # premium override → fire full quantity in one shot
# Bucket B2 — Mid-Deep range:
PREM_PER_CR_B2_MIN      = 10000
PREM_PER_CR_B2_MAX      = 20000
# Bucket B1 — ATM Straddle (opportunistic only):
PREM_PER_CR_B1_MIN      = 50000
# E-1 overnight carry (held to next-day expiry):
PREM_PER_CR_FLOOR_MIN   = 7500     # E-1 carry minimum
PREM_PER_CR_FLOOR_IDEAL = 10000    # E-1 carry ideal
# SL trigger: spot within X pts of strike → manual squareoff (Rulebook 2.2)
SL_DISTANCE_PTS         = {"NIFTY": 150, "SENSEX": 500}
# Discretionary squareoff thresholds (% of spot vs strike)
SL_HARD_CLOSE_PCT       = 0.5      # within 0.5% → hard close
SL_RETHINK_PCT          = 1.0      # within 1.0% → rethink
SL_SPOT_MOVED_PCT       = 1.0      # spot moved >1% from entry → rethink
# Backwards compat
MARGIN_PER_LOT = 175000          # legacy E-1 average; not used in new sizing

# ── Tiny in-memory cache (5-second TTL on heavy calls) ──────────────────
_cache = {}
def cached(key: str, ttl_sec: float, fn):
    now = time_mod.time()
    if key in _cache:
        ts, val = _cache[key]
        if now - ts < ttl_sec:
            return val
    try:
        val = fn()
    except Exception:
        # Serve last-good value on transient upstream failure (e.g. Kite
        # "Too many requests") instead of propagating a 500. Only raise if
        # we have nothing cached yet.
        if key in _cache:
            return _cache[key][1]
        raise
    _cache[key] = (now, val)
    return val


def _kite_alive_uncached():
    try:
        from lib.kite_live import _kite
        _kite().profile()
        return True
    except Exception:
        return False


def kite_alive():
    """Returns True if Kite session looks good. MEMOISED (15s) — this makes a network
    call to Kite, and report/desk endpoints check it per-leg across hundreds of legs;
    without caching that was hundreds of round-trips (20s+) per request."""
    return cached("kite_alive", 15, _kite_alive_uncached)


# ── API Endpoints ────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {
        "kite_alive": kite_alive(),
        "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        "ist_date": datetime.now(IST).strftime("%Y-%m-%d"),
        "weekday": datetime.now(IST).strftime("%A"),
    }


# ── Kite login: one-click flow ──────────────────────────────────────────
def _kite_do_exchange(request_token: str) -> dict:
    """Exchange a Kite request_token → access_token, save session, fire post-login
    sync. Shared by the paste fallback (/api/kite-exchange) AND the seamless callback
    listener. Raises on failure."""
    from kiteconnect import KiteConnect
    import json
    cred = json.loads((Path.home() / ".config" / "kite_credentials.json").read_text())
    k = KiteConnect(api_key=cred["api_key"])
    s = k.generate_session(request_token, api_secret=cred["api_secret"])
    out = {"access_token": s["access_token"], "api_key": cred["api_key"], "user_id": s.get("user_id")}
    sess = Path.home() / ".config" / "kite_session.json"
    sess.write_text(json.dumps(out)); sess.chmod(0o600)
    _cache.clear()
    try:
        import subprocess, sys as _sys
        sync = ROOT / "scripts" / "post_login_sync.py"
        if sync.exists():
            subprocess.Popen([_sys.executable, str(sync)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, cwd=str(ROOT), start_new_session=True)
    except Exception:
        pass
    return {"user_id": s.get("user_id")}


def _start_kite_callback_server():
    """Listen on 127.0.0.1:5000 for Kite's OAuth redirect (the registered redirect_uri).
    Catches /callback?request_token=…, exchanges it server-side, and shows a success
    page — so 'Login' is one seamless round-trip, no copy-paste. Best-effort: if :5000
    is busy (e.g. macOS AirPlay Receiver), the paste fallback still works."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs
    import threading

    def _page(title, msg, ok):
        redirect = ('<script>setTimeout(function(){location.href="http://127.0.0.1:8000/"},1800)</script>'
                    '<p style="color:#6b7c91;font-size:12px">Taking you to Theta Quant…</p>') if ok else ''
        return (f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
                f'<title>Kite login</title><body style="font-family:-apple-system,system-ui,sans-serif;'
                f'background:#0b1220;color:#e5edf5;display:grid;place-items:center;height:100vh;margin:0">'
                f'<div style="text-align:center;max-width:440px;padding:24px">'
                f'<div style="font-size:46px">{"✅" if ok else "⚠️"}</div>'
                f'<h2 style="margin:.35em 0">{title}</h2>'
                f'<p style="color:#9fb0c3;font-size:14px;line-height:1.5">{msg}</p>{redirect}</div></body>').encode()

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            tok = (q.get("request_token") or [""])[0]
            if not tok:
                body, code, ok = _page("No token", "This page only handles the Kite login redirect.", False), 400, False
            else:
                try:
                    res = _kite_do_exchange(tok)
                    body = _page("Login successful", f"Signed in as <b>{res.get('user_id') or 'your Kite account'}</b>. "
                                 "Live data is syncing for everyone — you can close this tab.", True)
                    code, ok = 200, True
                except Exception as e:
                    body, code, ok = _page("Login problem", f"Couldn't complete login: {e}", False), 400, False
            self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

    try:
        srv = HTTPServer(("127.0.0.1", 5000), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print("→ Kite callback listener on http://127.0.0.1:5000/callback")
    except Exception as e:
        print(f"→ Kite callback listener NOT started ({e}); paste-the-link fallback still works.")


@app.on_event("startup")
def _kite_callback_startup():
    _start_kite_callback_server()


@app.get("/api/kite-login-url")
def kite_login_url():
    """Return the Kite login URL for opening in a new tab."""
    try:
        from kiteconnect import KiteConnect
        import json
        cred_path = Path.home() / ".config" / "kite_credentials.json"
        cred = json.loads(cred_path.read_text())
        k = KiteConnect(api_key=cred["api_key"])
        return {"url": k.login_url()}
    except Exception as e:
        raise HTTPException(500, f"Failed to build login URL: {e}")


@app.post("/api/kite-exchange")
async def kite_exchange(request: Request):
    """Exchange request_token (or full redirect URL) for access_token + save session."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = (body.get("request_token") or body.get("url") or "").strip()
    if not raw:
        return JSONResponse({"success": False, "error": "Missing request_token or url"}, status_code=400)
    # Extract token from URL if present
    import re
    m = re.search(r"request_token=([^&\s]+)", raw)
    request_token = m.group(1) if m else raw
    try:
        res = _kite_do_exchange(request_token)
        return {"success": True, "user_id": res.get("user_id"), "post_login_sync": "fired"}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)


@app.get("/api/expiry/timing")
def api_expiry_timing(instrument: str = "SENSEX", fill_price: float = 0, current_premium: float = 0):
    """Live expiry-day SELL-TIMING guidance (lib.expiry_timing) — surfaces the locked
    tranche rule (analyses 027/028) as 'what do I do this minute' so the desk stops
    selling too early and leaving 30-40% premium on the table."""
    from lib import expiry_timing as ET
    snap = None
    try:
        if kite_alive():
            from lib.kite_live import get_vix, get_open, get_prev_close
            vix = get_vix()
            gap = (get_open() - get_prev_close()) / get_prev_close() * 100
            snap = {"vix": vix, "NIFTY": {"gap_pct": gap}}
    except Exception:
        snap = None
    return ET.timing_plan(instrument, datetime.now(IST), snapshot=snap,
                          fill_price=(fill_price or None), current_premium=(current_premium or None))


@app.get("/api/snapshot")
def snapshot():
    """Top-level KPIs for both NIFTY + SENSEX + VIX."""
    if not kite_alive():
        return {"error": "Kite session expired. Run scripts/kite_login.py", "ist_time": datetime.now(IST).strftime("%H:%M:%S")}

    def _pull():
        from lib.kite_live import _kite
        from lib.expiry_calendar import is_e0, is_e1, nearest_weekly_expiry_after, MARKET_HOLIDAYS, is_market_holiday
        k = _kite()
        out = {"ist_time": datetime.now(IST).strftime("%H:%M:%S"),
               "ist_date": datetime.now(IST).strftime("%Y-%m-%d")}
        today = datetime.now(IST).date()
        out["holiday"] = is_market_holiday(today) or ""

        for inst in ["NIFTY", "SENSEX"]:
            sym = "NSE:NIFTY 50" if inst == "NIFTY" else "BSE:SENSEX"
            q = k.quote([sym])[sym]
            spot = q["last_price"]
            prev = q["ohlc"]["close"]
            opn = q["ohlc"]["open"]
            hi = q["ohlc"]["high"]
            lo = q["ohlc"]["low"]
            change_abs = spot - prev
            change_pct = change_abs / prev * 100 if prev else 0
            gap_pct = (opn - prev) / prev * 100 if prev else 0
            day_range_pct = (hi - lo) / opn * 100 if opn else 0
            out[inst] = {
                "spot": round(spot, 2),
                "prev": round(prev, 2),
                "open": round(opn, 2),
                "high": round(hi, 2),
                "low": round(lo, 2),
                "change_pct": round(change_pct, 2),
                "gap_pct": round(gap_pct, 2),
                "day_range_pct": round(day_range_pct, 2),
                "is_e0": is_e0(today, inst),
                "is_e1": is_e1(today, inst),
                "next_expiry": str(nearest_weekly_expiry_after(today, inst) or ""),
            }
        # VIX
        vix = k.quote(["NSE:INDIA VIX"])["NSE:INDIA VIX"]
        out["vix"] = round(vix["last_price"], 2)
        out["vix_change"] = round(vix["last_price"] - vix["ohlc"]["close"], 2)
        return out

    return cached("snapshot", 5, _pull)


_LIVE_MON_STATE = {"state": {}}

@app.get("/api/live_alerts")
def api_live_alerts():
    """Always-on alert feed: air-pocket / trend-break + open short positions near
    their sold strike. Computed server-side from the live snapshot + open journal
    legs, so it works with no browser open (the Telegram bot + in-app bell poll this)."""
    from lib import live_monitor as LM, journal
    mkt = _market_state()
    if not kite_alive():
        return {"alerts": [], "market_open": mkt.get("market_open"), "note": "kite session expired"}
    try:
        sn = snapshot()
    except Exception as e:
        return {"alerts": [], "error": str(e)}
    open_legs = []
    chains = {}
    for t in journal.open_trades():
        inst = (t.get("instrument") or "").upper()
        if inst not in ("NIFTY", "SENSEX"):
            continue
        for l in t.get("legs", []):
            if (l.get("qty") or 0) < 0 and l.get("strike") and (l.get("side") in ("CE", "PE")):
                # live premium for this strike — peek the warm chain cache (no slow fetch)
                if inst not in chains:
                    ck = _cache.get(f"chain_{inst}_5.0")
                    chains[inst] = ({r["strike"]: r for r in ck[1].get("rows", [])}
                                    if (ck and time_mod.time() - ck[0] < 60) else {})
                row = chains[inst].get(l["strike"])
                cur = (row.get("pe_ltp" if l["side"] == "PE" else "ce_ltp") if row else l.get("ltp"))
                open_legs.append({"instrument": inst, "strike": l["strike"], "side": l["side"],
                                  "qty": l["qty"], "name": t.get("strategy_name") or inst,
                                  "entry_price": l.get("price"), "ltp": cur,
                                  "sl": l.get("sl"), "tp": l.get("tp")})
    # only run trend detectors during market hours (avoid stale-tick noise)
    snap_for_eval = sn if mkt.get("market_open") else {}
    alerts, _LIVE_MON_STATE["state"] = LM.evaluate(snap_for_eval, open_legs, _LIVE_MON_STATE["state"])
    # Tier-4 KICKER window nudge (NIFTY E-0, 11:55–12:20) — points at the live card
    try:
        from lib.expiry_calendar import is_e0 as _ie0
        _n = datetime.now(IST)
        if mkt.get("market_open") and _ie0(_n.date(), "NIFTY") and time(11, 55) <= _n.time() <= time(12, 20):
            alerts.append({"key": f"kicker_{_n.date()}", "severity": "INFO",
                "title": "⏱ KICKER window — NIFTY 12:00",
                "body": "Strikes = 1.0× range-so-far · TP 40% capture · SL 2× · size ₹1.5–2 Cr. Live card on /playbook."})
    except Exception:
        pass
    return {"alerts": alerts, "market_open": mkt.get("market_open"),
            "n_positions_watched": len(open_legs),
            "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.post("/api/journal/leg-sltp")
async def api_set_leg_sltp(request: Request):
    """Set SL/TP (premium levels) on one open leg → the live monitor then fires
    SL-HIT / TP-HIT. Body: {trade_id, strike, side, sl?, tp?}. Optional — the
    proactive take-profit suggestion works regardless of whether you set these."""
    from lib import journal
    body = await request.json()
    tid, strike = body.get("trade_id"), body.get("strike")
    side = (body.get("side") or "").upper()
    t = next((x for x in journal.all_trades() if x.get("id") == tid), None)
    if not t:
        return JSONResponse({"error": "trade not found"}, status_code=404)
    legs = [dict(l) for l in t.get("legs", [])]
    hit = False
    for l in legs:
        if l.get("strike") == strike and (l.get("side") or "").upper() == side:
            if "sl" in body: l["sl"] = body.get("sl")
            if "tp" in body: l["tp"] = body.get("tp")
            hit = True
    if not hit:
        return JSONResponse({"error": "leg not found on trade"}, status_code=404)
    journal.amend_trade(tid, legs, note="set sl/tp")
    return {"success": True, "trade_id": tid, "strike": strike, "side": side,
            "sl": body.get("sl"), "tp": body.get("tp")}


@app.get("/api/maxpain")
def api_maxpain():
    """Max-pain 'Pin' + implied EOD range for the NEAREST-expiry index (NIFTY or
    SENSEX, whichever expires sooner). Range = spot ± ATM straddle (market-implied
    move to expiry). Cached 60s — used by the top bar."""
    if not kite_alive():
        return {"error": "kite"}

    def _pull():
        from lib.expiry_calendar import nearest_weekly_expiry_after
        today = datetime.now(IST).date()
        exps = {inst: nearest_weekly_expiry_after(today, inst) for inst in ("NIFTY", "SENSEX")}
        exps = {k: v for k, v in exps.items() if v}
        if not exps:
            return {"error": "no_expiry"}
        inst = min(exps, key=lambda k: exps[k])           # nearest expiry index
        ch = chain(inst, distance_pct=3.0)
        spot = ch.get("spot"); rows = ch.get("rows", [])
        atm = min(rows, key=lambda r: abs(r["strike"] - spot)) if (rows and spot) else None
        straddle = ((atm.get("ce_ltp") or 0) + (atm.get("pe_ltp") or 0)) if atm else None
        return {"instrument": inst, "expiry": str(exps[inst]), "spot": spot,
                "max_pain": ch.get("max_pain"),
                "max_pain_pct_from_spot": ch.get("max_pain_pct_from_spot"),
                "atm_straddle": round(straddle, 2) if straddle else None,
                "range_low": round(spot - straddle) if (spot and straddle) else None,
                "range_high": round(spot + straddle) if (spot and straddle) else None}

    try:
        return cached("maxpain", 60, _pull)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/chain/{instrument}")
def chain(instrument: str, distance_pct: float = 5.0):
    """Option chain ±distance_pct% around spot for nearest weekly expiry."""
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")

    def _pull():
        from lib.kite_live import _kite, _instruments
        from lib.expiry_calendar import nearest_weekly_expiry_after
        k = _kite()
        sym = "NSE:NIFTY 50" if instrument == "NIFTY" else "BSE:SENSEX"
        spot = k.quote([sym])[sym]["last_price"]
        grid = 50 if instrument == "NIFTY" else 100
        today = datetime.now(IST).date()
        next_exp = nearest_weekly_expiry_after(today, instrument)
        if not next_exp:
            return {"error": "no_expiry"}

        seg = "NFO" if instrument == "NIFTY" else "BFO"
        instr_dump = pd.DataFrame(_instruments() if instrument == "NIFTY" else k.instruments(seg))
        # filter chain
        def _td(x):
            if x in (None, '', '1970-01-01'): return None
            try: return pd.to_datetime(x).date()
            except: return None
        instr_dump['expiry'] = instr_dump['expiry'].apply(_td)
        chain_df = instr_dump[(instr_dump['name'] == instrument) &
                              (instr_dump['expiry'] == next_exp) &
                              (instr_dump['instrument_type'].isin(['CE','PE']))]
        # build strikes within ±distance
        lo_strike = round(spot * (1 - distance_pct/100) / grid) * grid
        hi_strike = round(spot * (1 + distance_pct/100) / grid) * grid
        chain_df = chain_df[(chain_df['strike'] >= lo_strike) & (chain_df['strike'] <= hi_strike)]
        # pull quotes in batches
        symbols = [f"{seg}:{r['tradingsymbol']}" for _, r in chain_df.iterrows()]
        prices = {}
        for i in range(0, len(symbols), 250):
            q = k.quote(symbols[i:i+250])
            for ts, v in q.items():
                base = ts.replace(f"{seg}:", "")
                row = chain_df[chain_df['tradingsymbol'] == base]
                if row.empty: continue
                s = int(row.iloc[0]['strike'])
                opt = row.iloc[0]['instrument_type']
                dep = v.get('depth', {}) or {}
                buy0 = (dep.get('buy') or [{}])[0]
                sell0 = (dep.get('sell') or [{}])[0]
                prices[(s, opt)] = {
                    'ltp': v['last_price'],
                    'bid': buy0.get('price'), 'ask': sell0.get('price'),
                    'bid_qty': buy0.get('quantity'), 'ask_qty': sell0.get('quantity'),
                    'oi': v.get('oi', 0),
                    'volume': v.get('volume', 0),
                    'open': v['ohlc']['open'],
                    'high': v['ohlc']['high'],
                    'low': v['ohlc']['low'],
                }
        # consolidate
        strikes = sorted(set(int(s) for s in chain_df['strike']))
        rows = []
        for s in strikes:
            ce = prices.get((s, 'CE'), {})
            pe = prices.get((s, 'PE'), {})
            rows.append({
                'strike': s,
                'dist_pct': round((s - spot) / spot * 100, 2),
                'ce_ltp': ce.get('ltp'), 'ce_bid': ce.get('bid'), 'ce_ask': ce.get('ask'),
                'ce_high': ce.get('high'), 'ce_low': ce.get('low'), 'ce_open': ce.get('open'),
                'ce_oi': ce.get('oi'), 'ce_volume': ce.get('volume'),
                'pe_ltp': pe.get('ltp'), 'pe_bid': pe.get('bid'), 'pe_ask': pe.get('ask'),
                'pe_high': pe.get('high'), 'pe_low': pe.get('low'), 'pe_open': pe.get('open'),
                'pe_oi': pe.get('oi'), 'pe_volume': pe.get('volume'),
            })
        # max pain
        pains = []
        for pin in strikes:
            p = sum((pin - s) * (prices.get((s,'CE'), {}).get('oi') or 0) for s in strikes if s < pin)
            p += sum((s - pin) * (prices.get((s,'PE'), {}).get('oi') or 0) for s in strikes if s > pin)
            pains.append((pin, p))
        max_pain = min(pains, key=lambda x: x[1])[0] if pains else None
        return {
            "instrument": instrument,
            "spot": round(spot, 2),
            "expiry": str(next_exp),
            "max_pain": max_pain,
            "max_pain_pct_from_spot": round((max_pain - spot)/spot*100, 2) if max_pain else None,
            "rows": rows,
        }

    return cached(f"chain_{instrument}_{distance_pct}", 5, _pull)


# ── Helper: market-hours + trade-window verdict ─────────────────────────
def _market_state():
    """Returns dict: market_open, status_label, minutes_to_close (or None)."""
    from datetime import time as _time
    from lib.expiry_calendar import is_market_holiday, is_trading_day
    now = datetime.now(IST)
    today = now.date()
    holiday = is_market_holiday(today)
    weekend = now.weekday() >= 5
    open_t = _time(9, 15)
    close_t = _time(15, 30)
    if holiday:
        return {"market_open": False, "status": "HOLIDAY", "label": f"Market closed — {holiday}", "minutes_to_close": None}
    if weekend:
        return {"market_open": False, "status": "WEEKEND", "label": "Market closed — weekend", "minutes_to_close": None}
    if now.time() < open_t:
        mins_until = (open_t.hour - now.hour)*60 + (open_t.minute - now.minute)
        return {"market_open": False, "status": "PRE_OPEN", "label": f"Pre-market · opens in {mins_until}m", "minutes_to_close": None}
    if now.time() > close_t:
        return {"market_open": False, "status": "AFTER_HOURS", "label": "After-hours · closed at 15:30", "minutes_to_close": None}
    mtc = (close_t.hour - now.hour)*60 + (close_t.minute - now.minute)
    return {"market_open": True, "status": "OPEN", "label": f"Open · {mtc}m to close", "minutes_to_close": mtc}


def _trade_verdict(instrument: str) -> dict:
    """Live answer to 'should I take a sell-strangle trade right now?'

    Returns: verdict, score (0-100), color, label, reason, recommended_action.
    Maps current IST to STRATEGY_LIVE.md windows.
    """
    from datetime import time as _time
    from lib.expiry_calendar import is_e0, is_e1
    ms = _market_state()
    now = datetime.now(IST)
    today = now.date()
    is_e0_today = is_e0(today, instrument)
    is_e1_today = is_e1(today, instrument)

    if not ms["market_open"]:
        return {"verdict": ms["status"], "score": 0, "color": "slate",
                "label": ms["label"], "reason": "Outside market hours",
                "action": "Plan tomorrow. Set alarms for 9:15.",
                "is_e0": is_e0_today, "is_e1": is_e1_today,
                "next_window": _next_trade_window(instrument, today)}

    t = now.time()
    if not (is_e0_today or is_e1_today):
        return {"verdict": "NO_TRADE", "score": 25, "color": "amber",
                "label": "Non-cycle day for this instrument",
                "reason": f"Today is not E-0 or E-1 for {instrument}",
                "action": "No mandate today (strategy fires E-0/E-1 only). Watch.",
                "is_e0": False, "is_e1": False,
                "next_window": _next_trade_window(instrument, today)}

    if is_e1_today:
        # Rohan's updated E-1 rule (post 6-May incident):
        # E-1 entry ONLY after 14:45 (news risk window 9:15-14:45 = no entry).
        # Distance ≥ 4% (was getting whipsawed at 3%). Per-Cr ≥ ₹7.5K still required.
        if t < _time(14, 45):
            return {"verdict": "WAIT", "score": 35, "color": "blue",
                    "label": "E-1 NEWS-RISK window — wait until 14:45",
                    "reason": "Rohan's rule: too much news risk before 14:45 (war/policy spikes whipsaw 3% strikes)",
                    "action": "Don't enter. Watch only. Window opens 14:45.",
                    "is_e0": False, "is_e1": True, "next_window": "14:45 today"}
        if t < _time(15, 15):
            return {"verdict": "GO", "score": 90, "color": "emerald",
                    "label": "E-1 ADVANCE — execute (≥4% OTM, per-Cr ≥ ₹7.5K)",
                    "reason": "14:45-15:15 = Rohan's preferred E-1 window: news digested + theta accelerated + 45 min residual risk",
                    "action": "Sell wide (≥4% OTM). Verify per-Cr ≥ ₹7,500 before placing. Hold overnight to tomorrow's E-0 close.",
                    "is_e0": False, "is_e1": True, "next_window": "now"}
        if t < _time(15, 25):
            return {"verdict": "MARGINAL", "score": 60, "color": "yellow",
                    "label": "E-1 last 10 min — only if premium meets floor",
                    "reason": "Tight window. Premium decayed further but still chargeable.",
                    "action": "Place limit only if per-Cr ≥ ₹7,500. Half-size acceptable.",
                    "is_e0": False, "is_e1": True, "next_window": "tomorrow 9:18 (E-0)"}
        return {"verdict": "SKIP_E1", "score": 15, "color": "slate",
                "label": "E-1 missed — go straight to E-0",
                "reason": "Past 15:25, market closing",
                "action": "Skip. Set alarm for E-0 tomorrow 9:18.",
                "is_e0": False, "is_e1": True, "next_window": "tomorrow 9:18 (E-0)"}

    # is_e0 — main day
    if t < _time(9, 17):
        return {"verdict": "WAIT", "score": 65, "color": "blue",
                "label": "E-0 — bid-ask too wide",
                "reason": "First 2 min open chaos. Section 9F: wait for 9:17.",
                "action": "Hold limits ready. Sweet spot 9:17-9:22.",
                "is_e0": True, "is_e1": False, "next_window": "9:17 today"}
    if t < _time(9, 22):
        return {"verdict": "GO", "score": 100, "color": "emerald",
                "label": "🔥 E-0 SWEET SPOT — execute T1+T2+T3 NOW",
                "reason": "9:17-9:22 = 100% worthless rate in 47-day backtest",
                "action": "Fire all 3 tiers. Limits at LTP × regime mult.",
                "is_e0": True, "is_e1": False, "next_window": "now"}
    if t < _time(9, 35):
        return {"verdict": "GO", "score": 90, "color": "green",
                "label": "E-0 — still within fill window",
                "reason": "9:25-9:35 limit-fill window (Section 9F)",
                "action": "Execute T1+T2+T3. Premium ~10% lower than peak.",
                "is_e0": True, "is_e1": False, "next_window": "now"}
    if t < _time(10, 30):
        return {"verdict": "MARGINAL", "score": 70, "color": "yellow",
                "label": "E-0 past optimal — Bucket A still viable",
                "reason": "Per Rulebook 2.3: full quantity if combined ≥ ₹6K/Cr",
                "action": "Take Bucket A if premium meets target. Bucket B2 9:45-10:15 if not yet placed.",
                "is_e0": True, "is_e1": False, "next_window": "SENSEX 11:00 secondary window"}
    if t < _time(11, 0):
        return {"verdict": "LATE", "score": 55, "color": "orange",
                "label": "E-0 LATE — close B-bucket, prep SENSEX secondary",
                "reason": "Past 10:30 — B-bucket profit booking starts",
                "action": "Close B1/B2. SENSEX secondary window opens at 11:00 (premium spike opportunity).",
                "is_e0": True, "is_e1": False, "next_window": "11:00 SENSEX secondary"}
    if t < _time(12, 0):
        return {"verdict": "SECONDARY_WINDOW", "score": 75, "color": "blue",
                "label": "🟦 SENSEX 11-12 SECONDARY — premium spikes possible",
                "reason": "Rulebook 2.3.3: SENSEX premium can spike with no spot move 11-12",
                "action": "If combined premium > morning levels with no spot move → take remaining Bucket A. ALL B-bucket MUST close by 12:00.",
                "is_e0": True, "is_e1": False, "next_window": "12:00 hard cutoff"}
    if t < _time(13, 0):
        return {"verdict": "POST_NOON", "score": 40, "color": "orange",
                "label": "E-0 post-noon — only Bucket A holds; prep harvest",
                "reason": "Rulebook: All B-bucket closed by 12:00. Only A remains.",
                "action": "Monitor A-bucket SL triggers (within 1% of strike = rethink, 0.5% = hard close). Prep harvest for 14:00.",
                "is_e0": True, "is_e1": False, "next_window": "harvest @ 14:00"}
    if t < _time(14, 0):
        return {"verdict": "EOD", "score": 30, "color": "orange",
                "label": "E-0 EOD — TIGHT strikes only (1-1.5%) hit ≥₹3K/Cr floor",
                "reason": "Sub-₹1 premium at 2%+. Section 9R: never skip — closer strikes still safe with DTE 0 final hour.",
                "action": "Place 1-1.5% strangles. Walls + max-pain pin protect on final hour. Then prep harvest.",
                "is_e0": True, "is_e1": False, "next_window": "harvest @ 14:00"}
    # 14:00+ on E-0 → harvest mode
    if t < _time(15, 25):
        return {"verdict": "HARVEST", "score": 60, "color": "purple",
                "label": "🎯 SWITCH TO HARVEST MODE",
                "reason": "14:00+ on E-0 = manipulation window per Section 9M",
                "action": "Go to /manipulation. Buy 5 deep-OTM × ₹10-15K. Sell-limit at 12×.",
                "is_e0": True, "is_e1": False, "next_window": "now (harvest)"}
    return {"verdict": "CLEANUP", "score": 10, "color": "slate",
            "label": "E-0 — cleanup phase",
            "reason": "15:25+ on expiry; close anything open at market",
            "action": "Cancel unfilled limits. Log results.",
            "is_e0": True, "is_e1": False, "next_window": "tomorrow"}


def _next_trade_window(instrument: str, today: date) -> str:
    """Lookahead: returns label of next E-0/E-1 window."""
    from lib.expiry_calendar import is_e0, is_e1, is_trading_day
    for offset in range(1, 10):
        d = today + timedelta(days=offset)
        if not is_trading_day(d): continue
        if is_e0(d, instrument): return f"{d.strftime('%a %d-%b')} 9:18 (E-0)"
        if is_e1(d, instrument): return f"{d.strftime('%a %d-%b')} 10:00 (E-1)"
    return "—"


# ── Helper: premium rise probability (heuristic) ────────────────────────
def _premium_rise_prob(cushion: float, dist_pct: float, vix_chg_pct: float,
                       hh_mm: int, oi: int, ltp: float, dte: int, side: str,
                       spot_chg_pct: float, market_open: bool) -> int | None:
    """Probability premium will be HIGHER in ~15 min. 2-95.

    Heuristic — not Black-Scholes. Calibrated against intuition:
      - Deep cushion (3+): premium decays, ~5-15% chance of rise
      - Tight cushion (<1): premium volatile, ~50-75% chance
      - VIX expanding intraday: bumps up
      - Spot drifting TOWARD strike: bumps up
      - Manipulation window (E-0 14:00-15:25 + low OI + 4-6% OTM): big bump
      - DTE 0 + cheap (<₹1) + EOD: pin volatility
    Returns None if market closed (number isn't meaningful).
    """
    if not market_open: return None
    p = 35  # baseline — theta tilts slightly against rise

    if cushion >= 3.0: p -= 22
    elif cushion >= 2.0: p -= 12
    elif cushion >= 1.5: p -= 5
    elif cushion < 1.0: p += 15

    p += int(vix_chg_pct * 2.0)

    # Spot drift effect
    if side == "CE":
        if spot_chg_pct > 0.5: p += 12
        elif spot_chg_pct > 0.2: p += 6
        elif spot_chg_pct < -0.5: p -= 8
    else:  # PE
        if spot_chg_pct < -0.5: p += 12
        elif spot_chg_pct < -0.2: p += 6
        elif spot_chg_pct > 0.5: p -= 8

    # Time-of-day theta progression
    if hh_mm < 9*60+30: p += 6
    elif hh_mm < 11*60: p += 0
    elif hh_mm < 14*60: p -= 6
    elif hh_mm > 15*60+15: p -= 12

    # Manipulation window on E-0 with low OI sweet spot
    if dte == 0 and 14*60 <= hh_mm < 15*60+25:
        if 4.0 <= abs(dist_pct) <= 6.0 and oi < 200000: p += 22
        if ltp < 1.0: p += 8

    # End-of-day pin volatility
    if dte == 0 and hh_mm > 15*60 and ltp < 2.0:
        p += 6

    return max(2, min(95, p))


@app.get("/api/timing/{instrument}")
def timing(instrument: str):
    """Live trade-window verdict for the instrument."""
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")
    return {
        "instrument": instrument,
        "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        "ist_date": datetime.now(IST).strftime("%Y-%m-%d"),
        "weekday": datetime.now(IST).strftime("%A"),
        "market": _market_state(),
        "verdict": _trade_verdict(instrument),
    }


# ── Helper: Black-Scholes delta approximation ──────────────────────────
def _bs_delta(spot: float, strike: float, vix_pct: float, dte_days: float, side: str) -> float:
    """Black-Scholes delta. CE: 0-1. PE: -1-0. dte_days can be fractional."""
    import math
    if dte_days <= 0: dte_days = 0.5   # intra-day fallback
    T = dte_days / 365.0
    sigma = max(vix_pct / 100.0, 0.05)
    r = 0.07
    sqrtT = T ** 0.5
    if sigma * sqrtT < 1e-9: return 0.0
    d1 = (math.log(spot / strike) + (r + sigma * sigma / 2) * T) / (sigma * sqrtT)
    cdf = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    return round(cdf if side == "CE" else cdf - 1, 3)


# ── Helper: per-strike "why this strike" reasoning ─────────────────────
def _strike_reasoning(side: str, strike: int, ltp: float, oi: int, dist_pct: float,
                      cushion: float, ce_walls: list, pe_walls: list,
                      max_pain: int, spot: float, vix: float,
                      exp_move_pct: float) -> list[str]:
    """Returns 3-5 bullet reasons specific to this strike."""
    reasons = []
    walls = ce_walls if side == "CE" else pe_walls
    top_wall = walls[0] if walls else None

    # Wall positioning
    if top_wall:
        wall_strike = top_wall["strike"]
        wall_oi_l = top_wall["oi"] / 1e5
        if (side == "CE" and strike >= wall_strike) or (side == "PE" and strike <= wall_strike):
            if strike == wall_strike:
                reasons.append(f"At top {side} wall {wall_strike} ({wall_oi_l:.1f}L OI) — pin magnet")
            else:
                reasons.append(f"Beyond top {side} wall {wall_strike} ({wall_oi_l:.1f}L OI buffer)")
        else:
            reasons.append(f"⚠ Inside top {side} wall {wall_strike} — wall MUST hold")

    # Cushion vs expected move
    if cushion >= 3:
        reasons.append(f"Cushion {cushion} = far outside 1σ band")
    elif cushion >= 2:
        reasons.append(f"Cushion {cushion} = outside 1σ band")
    elif cushion >= 1.5:
        reasons.append(f"Cushion {cushion} = at edge of 1σ band")
    else:
        reasons.append(f"⚠ Cushion {cushion} < 1.5 = inside 1σ band")

    # Max-pain alignment
    if max_pain:
        if side == "CE" and max_pain <= spot:
            reasons.append(f"Max-pain {max_pain} pulls spot DOWN — favors CE side")
        elif side == "PE" and max_pain >= spot:
            reasons.append(f"Max-pain {max_pain} pulls spot UP — favors PE side")

    # VIX regime
    if vix < 14:
        reasons.append(f"VIX {vix} very low — minimal IV expansion risk")
    elif vix < 17:
        reasons.append(f"VIX {vix} cooling — IV crush helps")
    elif vix > 20:
        reasons.append(f"⚠ VIX {vix} elevated — IV expansion possible")

    # OI density
    if oi > 1_000_000:
        reasons.append(f"Strike OI {oi/1e5:.1f}L — well-traded, tight spreads")
    elif oi < 100_000:
        reasons.append(f"⚠ Strike OI {oi/1e5:.1f}L — thin, watch slippage")

    return reasons[:5]


# ── Helper: events calendar (manual stub for now, expand later) ────────
def _events_for_today() -> list[dict]:
    """Return list of today's market-relevant events with impact level."""
    today = datetime.now(IST).date()
    # Hardcoded known events. Expand as needed.
    events = []
    return events or [{"label": "No major events scheduled", "impact": "none"}]


# ── Helper: classical pivots from yesterday's HLC ──────────────────────
def _pivot_levels(prev_high: float, prev_low: float, prev_close: float) -> dict:
    p = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * p - prev_low
    s1 = 2 * p - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s3 = prev_low - 2 * (prev_high - p)
    return {"pivot": round(p, 0), "r1": round(r1, 0), "r2": round(r2, 0), "r3": round(r3, 0),
            "s1": round(s1, 0), "s2": round(s2, 0), "s3": round(s3, 0)}


# ── /api/recommendations — the new tiered recommendation endpoint ──────
# ── Daily snapshot persistence (Report → Historical view) ───────────────
SNAPSHOT_DIR = ROOT / "data" / "dashboard_snapshots"
try:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass  # read-only fs (Vercel serverless)


@app.post("/api/snapshot/save")
async def save_snapshot(request: Request):
    """Persist a daily snapshot for the Report → Historical view.

    Body: {date?: 'YYYY-MM-DD' (default today), positions: [...], note?: '...'}
    Stores the positions plus a snapshot of current market context.
    """
    import json
    try:
        body = await request.json()
    except Exception:
        body = {}
    date_str = body.get("date") or datetime.now(IST).strftime("%Y-%m-%d")
    positions = body.get("positions") or []
    note = body.get("note") or ""

    # Capture current market context if Kite is alive
    market = {}
    try:
        if kite_alive():
            from lib.kite_live import _kite
            k = _kite()
            sn = k.quote(["NSE:NIFTY 50", "BSE:SENSEX", "NSE:INDIA VIX"])
            market = {
                "SENSEX": {
                    "spot": sn["BSE:SENSEX"]["last_price"],
                    "open": sn["BSE:SENSEX"]["ohlc"]["open"],
                    "high": sn["BSE:SENSEX"]["ohlc"]["high"],
                    "low": sn["BSE:SENSEX"]["ohlc"]["low"],
                    "prev_close": sn["BSE:SENSEX"]["ohlc"]["close"],
                },
                "NIFTY": {
                    "spot": sn["NSE:NIFTY 50"]["last_price"],
                    "open": sn["NSE:NIFTY 50"]["ohlc"]["open"],
                    "high": sn["NSE:NIFTY 50"]["ohlc"]["high"],
                    "low": sn["NSE:NIFTY 50"]["ohlc"]["low"],
                    "prev_close": sn["NSE:NIFTY 50"]["ohlc"]["close"],
                },
                "vix": sn["NSE:INDIA VIX"]["last_price"],
            }
    except Exception:
        pass

    # Run position analysis to capture LTPs + MTM at save time
    analysis = []
    summary = {"total_mtm": 0, "total_lots": 0, "n_short": 0, "n_long": 0}
    if positions and kite_alive():
        try:
            class _R:  # mock the request body for analysis
                async def json(self_): return {"positions": positions}
            r = await position_analysis(_R())
            if isinstance(r, dict):
                analysis = r.get("positions", [])
                summary = r.get("summary", summary)
        except Exception:
            pass

    snap = {
        "date": date_str,
        "saved_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": datetime.now(IST).strftime("%A"),
        "note": note,
        "market": market,
        "positions": positions,
        "analysis": analysis,
        "summary": summary,
    }
    fp = SNAPSHOT_DIR / f"{date_str}.json"
    fp.write_text(json.dumps(snap, indent=2, default=str))
    return {"success": True, "date": date_str, "n_positions": len(positions),
            "total_mtm": summary.get("total_mtm", 0), "path": str(fp.relative_to(ROOT))}


@app.get("/api/snapshots")
def list_snapshots():
    """Return list of saved snapshots (date, summary, file size)."""
    import json
    out = []
    for fp in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(fp.read_text())
            out.append({
                "date": d.get("date"),
                "weekday": d.get("weekday", ""),
                "saved_at": d.get("saved_at", ""),
                "note": d.get("note", "")[:100],
                "n_positions": len(d.get("positions") or []),
                "total_mtm": (d.get("summary") or {}).get("total_mtm", 0),
                "total_lots": (d.get("summary") or {}).get("total_lots", 0),
                "size_kb": round(fp.stat().st_size / 1024, 1),
            })
        except Exception as e:
            out.append({"date": fp.stem, "error": str(e)})
    return {"snapshots": out}


import re as _re_date
def _valid_date(date: str) -> bool:
    return bool(_re_date.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""))


@app.get("/api/snapshot/{date}")
def get_snapshot(date: str):
    """Return a specific date's snapshot. Date format YYYY-MM-DD."""
    import json
    if not _valid_date(date):
        raise HTTPException(400, "date must be YYYY-MM-DD")
    fp = SNAPSHOT_DIR / f"{date}.json"
    if not fp.exists():
        raise HTTPException(404, f"No snapshot for {date}")
    return json.loads(fp.read_text())


@app.delete("/api/snapshot/{date}")
def delete_snapshot(date: str):
    if not _valid_date(date):
        raise HTTPException(400, "date must be YYYY-MM-DD")
    fp = SNAPSHOT_DIR / f"{date}.json"
    if fp.exists():
        fp.unlink()
        return {"deleted": date}
    raise HTTPException(404, f"No snapshot for {date}")


# ── Reconciliation (Layer B): contract-note / mProfit ingestion ──────────
@app.post("/api/recon/upload")
async def api_recon_upload(file: UploadFile = File(...), demat: str = Form(""), source: str = Form("")):
    """Ingest a Layer-B file → normalized transactions in the recon store. CSV = mProfit
    export; PDF = broker contract note (decrypted with the demat's stored password — never
    typed here). Feeds the 'Contract-note based' view in Full Reporting."""
    from lib import recon_import as RI, recon_store as RS, demat_creds as DC
    raw = await file.read()
    name = (file.filename or "").lower()
    import tempfile, os
    try:
        if name.endswith(".csv") or source == "mprofit":
            tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
            tmp.write(raw); tmp.close()
            txns = RI.parse_mprofit_csv(tmp.name)
            os.unlink(tmp.name)
            res = RS.add_batch(txns, source="mprofit", demat=demat,
                               meta={"file": file.filename, "batch_key": f"mprofit:{(demat or '').upper()}:{file.filename}"})
            return {"saved": True, "n": res["n"], "source": "mprofit"}
        elif name.endswith(".pdf"):
            pw = DC.get_password(demat) if demat else None
            if not pw:
                return JSONResponse({"error": f"No stored password for demat '{demat}'. Set it up in Settings → Demat accounts."}, status_code=400)
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(raw); tmp.close()
            try:
                parsed = RI.parse_monarch_pdf(tmp.name, password=pw)
            finally:
                os.unlink(tmp.name)
            hdr = parsed.get("header", {})
            res = RS.add_batch(parsed.get("transactions", []), source="contract_note",
                               demat=demat or hdr.get("ucc", ""),
                               meta={"file": file.filename, "broker": "Monarch", "totals": parsed.get("totals", {}),
                                     "trade_date": hdr.get("trade_date"), "contract_no": hdr.get("contract_no"),
                                     "batch_key": f"cn:{(demat or hdr.get('ucc','')).upper()}:{hdr.get('contract_no','')}"})
            return {"saved": True, "n": res["n"], "source": "contract_note", "header": hdr, "totals": parsed.get("totals", {})}
        else:
            return JSONResponse({"error": "Upload a .csv (mProfit) or .pdf (contract note)."}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


@app.get("/api/recon/report")
def api_recon_report():
    """Layer-B (contract-note/mProfit) aggregate for Full Reporting's source toggle."""
    from lib import recon_store as RS
    return RS.report()


# ── Google Sheet ingestion ──────────────────────────────────────────────
@app.post("/api/import/google-sheet")
async def import_google_sheet(request: Request):
    """Import positions from a published Google Sheet CSV.

    Body: {"url": "https://docs.google.com/spreadsheets/d/.../export?format=csv&gid=0"}

    Sheet schema (case-insensitive headers):
      instrument | strike | side | qty   | price | broker  | demat  | time             | note
      SENSEX     | 80000  | CE   | -1064 | 2.41  | Monarch | M-001  | 2026-05-07 09:30 | Bucket A

    Returns: {positions: [...], count: N, errors: [...]}
    """
    import csv as _csv
    from io import StringIO
    try:
        import urllib.request
    except Exception:
        urllib = None

    try:
        body = await request.json()
    except Exception:
        body = {}
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "Missing 'url' in body"}, status_code=400)

    # Coerce common Google Sheet share URLs to CSV export
    if "docs.google.com/spreadsheets" in url and "/export?" not in url:
        # https://docs.google.com/spreadsheets/d/<ID>/edit#gid=<GID>  →  /export?format=csv&gid=<GID>
        import re as _re
        m = _re.search(r"/spreadsheets/d/([^/]+)", url)
        gid_m = _re.search(r"[?#&]gid=(\d+)", url)
        if m:
            sheet_id = m.group(1)
            gid = gid_m.group(1) if gid_m else "0"
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    # SSRF guard: only https to a Google-hosted domain. No file://, no internal
    # hosts, no cloud-metadata IPs. The importer's whole job is Google Sheets.
    import urllib.parse as _up, urllib.error, socket as _sock, ipaddress as _ip
    try:
        pu = _up.urlparse(url)
    except Exception:
        return JSONResponse({"error": "bad url"}, status_code=400)
    host = (pu.hostname or "").lower()
    if pu.scheme != "https" or not (host == "docs.google.com" or host.endswith(".google.com") or host.endswith(".googleusercontent.com")):
        return JSONResponse({"error": "Only https Google Sheet URLs are allowed."}, status_code=400)
    try:  # reject if the host resolves to a private / loopback / link-local address
        for fam, _, _, _, sa in _sock.getaddrinfo(host, 443):
            if _ip.ip_address(sa[0]).is_private or _ip.ip_address(sa[0]).is_loopback or _ip.ip_address(sa[0]).is_link_local:
                return JSONResponse({"error": "blocked host"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "cannot resolve host"}, status_code=400)
    # Follow redirects but re-apply the host allow-list on every hop (a Google URL
    # could 3xx-bounce off-domain otherwise).
    class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, rq, fp, code, msg, headers, newurl):
            h = (_up.urlparse(newurl).hostname or "").lower()
            if not (h == "docs.google.com" or h.endswith(".google.com") or h.endswith(".googleusercontent.com")):
                raise urllib.error.HTTPError(newurl, code, "redirect off allow-list", headers, fp)
            return super().redirect_request(rq, fp, code, msg, headers, newurl)
    try:
        opener = urllib.request.build_opener(_GuardedRedirect)
        req = urllib.request.Request(url, headers={"User-Agent": "Theta Quant/0.2"})
        with opener.open(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return JSONResponse({"error": "Fetch failed"}, status_code=400)

    # Route through the SAME fuzzy importer as file uploads (idempotent uid upsert,
    # full-column capture, options-vs-futures P&L). Previously this endpoint only
    # understood a narrow 9-col schema and NEVER SAVED — "not taking data proper".
    import pandas as pd
    from lib import import_io as IO
    try:
        df = pd.read_csv(StringIO(text))
    except Exception as e:
        return JSONResponse({"error": f"CSV parse failed: {e}"}, status_code=400)
    if df.empty:
        return JSONResponse({"error": "sheet is empty"}, status_code=400)
    cols = {str(c).strip().lower() for c in df.columns}
    if "stock symbol" in cols or "option symbol" in cols:
        # full reporting-sheet schema → real ingest (saves to the journal)
        res = IO._ingest_trades(df, dry_run=bool(body.get("dry_run")))
        return {"saved": not body.get("dry_run"), "mode": "reporting-sheet",
                "fetched_url": url, **res}
    # legacy narrow schema (instrument/strike/side/qty/price) → save as journal trades
    rows = df.to_dict("records")
    def n(d, key, default=None):
        for k in d.keys():
            if str(k).strip().lower() == key:
                v = d[k]
                return v.strip() if isinstance(v, str) else v
        return default
    from lib import journal as _J
    positions, errors, saved = [], [], 0
    for i, row in enumerate(rows, start=2):
        try:
            inst = str(n(row, "instrument") or "").upper()
            if inst not in ("NIFTY", "SENSEX"):
                continue
            strike = int(float(n(row, "strike")))
            side = str(n(row, "side") or "").upper()
            if side not in ("CE", "PE"):
                raise ValueError(f"side must be CE or PE, got {side!r}")
            qty = int(float(n(row, "qty")))
            price = float(n(row, "price") or n(row, "avg_price") or 0)
            if not price:
                raise ValueError("missing price")
            positions.append({"instrument": inst, "strike": strike, "side": side,
                              "qty": qty, "price": price,
                              "broker": str(n(row, "broker") or ""), "demat": str(n(row, "demat") or ""),
                              "note": str(n(row, "note") or "")})
        except Exception as e:
            errors.append({"row": i, "error": str(e)})
    if positions and not body.get("dry_run"):
        # one strategy per (broker, note) group so related legs stay together
        groups = {}
        for p in positions:
            groups.setdefault((p["instrument"], p["broker"], p["note"]), []).append(p)
        for (inst, broker, note), legs in groups.items():
            _J.add_trade(instrument=inst, tier=note or "Google sheet",
                         legs=[{"strike": p["strike"], "side": p["side"], "qty": p["qty"],
                                "price": p["price"], "leg_type": p["side"], "demat": p["demat"]} for p in legs],
                         broker=broker, source="google-sheet", note=note,
                         strategy_name=note or f"{inst} google-sheet")
            saved += 1
    return {"saved": saved > 0, "mode": "positions", "n_strategies_saved": saved,
            "positions": positions, "count": len(positions), "errors": errors,
            "n_rows_in_sheet": len(rows), "fetched_url": url}


@app.post("/api/position-analysis")
async def position_analysis(request: Request):
    """Analyze a list of user-supplied open positions: live LTP, MTM, per-leg recommendation.

    Body: {"positions": [{"instrument":"SENSEX","strike":80000,"side":"CE","qty":-1000,"avg_price":2.55}, ...]}
    Negative qty = SHORT, positive = LONG.
    """
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    try:
        body = await request.json()
    except Exception:
        body = {}
    positions = body.get("positions") or []
    if not positions:
        return {"positions": [], "summary": {"total_mtm": 0, "total_qty": 0, "n": 0}}

    # Group by (instrument, expiry) so we fetch the RIGHT chain per group.
    # Positions without expiry default to next weekly (legacy behavior).
    from lib.expiry_calendar import nearest_weekly_expiry_after as _next_wkly
    today_d = datetime.now(IST).date()
    by_group = {}     # {(inst, expiry_str): [positions...]}
    for p in positions:
        inst = (p.get("instrument") or "SENSEX").upper()
        exp = p.get("expiry") or ""
        if not exp:
            # Legacy: default to next weekly for the instrument
            nx = _next_wkly(today_d, inst)
            exp = nx.isoformat() if nx else ""
        by_group.setdefault((inst, exp), []).append(p)

    # Per-(instrument, expiry) chain fetch — but skip already-expired groups
    # (no point hitting the API for an expiry that's past).
    contexts = {}     # {(inst, expiry_str): chain_dict}
    for (inst, exp_str) in by_group:
        if inst not in ("NIFTY", "SENSEX"): continue
        try:
            from datetime import datetime as _dt
            exp_d = _dt.strptime(exp_str, "%Y-%m-%d").date() if exp_str else None
        except Exception:
            exp_d = None
        if exp_d and exp_d < today_d:
            # Past expiry — assume worthless; build minimal context
            contexts[(inst, exp_str)] = {
                "expired": True,
                "expiry": exp_str,
                "spot": 0,
                "vix": 0,
                "max_pain": None,
                "max_pain_pct_from_spot": 0,
                "dte": (exp_d - today_d).days,
                "is_e0": False,
                "is_e1": False,
                "prices": {},
                "ce_walls": [],
                "pe_walls": [],
                "spot_chg_pct": 0,
                "vix_chg_pct": 0,
            }
            continue
        try:
            d = _fetch_chain_full(inst, distance_pct=8.0, expiry=exp_str or None)
            if "error" not in d:
                contexts[(inst, exp_str)] = d
        except Exception:
            pass

    out = []
    total_mtm = 0
    for p in positions:
        inst = (p.get("instrument") or "SENSEX").upper()
        strike = int(p.get("strike", 0))
        side = (p.get("side") or "CE").upper()
        qty = int(p.get("qty", 0))
        avg_price = float(p.get("avg_price", 0))
        if not strike or not qty: continue

        # Resolve expiry for this position
        pos_exp_str = p.get("expiry") or ""
        if not pos_exp_str:
            nx = _next_wkly(today_d, inst)
            pos_exp_str = nx.isoformat() if nx else ""
        ctx = contexts.get((inst, pos_exp_str))

        if not ctx:
            out.append({"instrument": inst, "strike": strike, "side": side, "qty": qty,
                        "avg_price": avg_price, "expiry": pos_exp_str,
                        "error": "no live context for this instrument/expiry"})
            continue

        # ── Past-expiry: assume worthless, compute realized P&L ──
        if ctx.get("expired"):
            ltp = 0.0
            if qty < 0:
                mtm_per_share = avg_price       # SHORT kept full premium
            else:
                mtm_per_share = -avg_price      # LONG lost full premium
            mtm_total = round(mtm_per_share * abs(qty), 2)
            total_mtm += mtm_total
            lot_size = LOT_SIZE[inst]
            qty_lots = abs(qty) // lot_size
            margin_used = 0   # no live margin on settled positions
            max_profit_at_expiry = round(avg_price * abs(qty), 2) if qty < 0 else 0
            premium_paid = round(avg_price * qty, 2) if qty > 0 else 0
            out.append({
                "instrument": inst, "strike": strike, "side": side, "qty": qty,
                "qty_lots": qty_lots, "avg_price": avg_price, "ltp": 0.0,
                "expiry": pos_exp_str, "is_expired": True, "is_settled": True,
                "spot": 0, "dist_pts": 0, "dist_pct": 0, "cushion": 0,
                "mtm_per_share": round(mtm_per_share, 2), "mtm_total": mtm_total,
                "margin_used": 0,
                "max_profit_at_expiry": max_profit_at_expiry,
                "premium_paid": premium_paid,
                "recommendation": "EXPIRED",
                "rec_reason": f"Expired {pos_exp_str} — assumed worthless",
                "exit_suggestion": None, "max_pain": None,
                "dte": ctx["dte"], "is_stale": False, "stale_reason": "",
                "broker": p.get("broker") or "", "demat": p.get("demat") or "",
                "time": p.get("time") or "", "note": p.get("note") or "",
            })
            continue

        prices = ctx["prices"]
        spot = ctx["spot"]
        max_pain = ctx["max_pain"]
        dte = ctx["dte"]
        is_e0 = ctx["is_e0"]
        ltp = (prices.get((strike, side), {}) or {}).get("ltp")
        if ltp is None:
            out.append({"instrument": inst, "strike": strike, "side": side, "qty": qty,
                        "avg_price": avg_price, "expiry": pos_exp_str,
                        "error": "strike not in chain (out of fetched range)"})
            continue
        # MTM. SHORT (qty<0): profit = (avg - ltp) * |qty|
        if qty < 0:
            mtm_per_share = avg_price - ltp
        else:
            mtm_per_share = ltp - avg_price
        mtm_total = round(mtm_per_share * abs(qty), 2)
        total_mtm += mtm_total
        # Distance & cushion
        dist_pts = strike - spot
        dist_pct = dist_pts / spot * 100
        from lib.deep_otm import expected_move, cushion_ratio
        exp_mv = expected_move(spot, ctx["vix"], dte if dte > 0 else 1)
        cushion = round(cushion_ratio(abs(dist_pts), exp_mv), 2)
        # Determine recommendation
        # SHORT options recommendations:
        #   HOLD = cushion ok, theta winning OR theta will win
        #   WATCH = cushion thin, spot drifting toward strike, set mental stop
        #   CUT_PARTIAL = cushion < 0.5σ AND spot drifting toward strike
        #   CUT_ALL = ITM or near-ITM (cushion < 0.2σ)
        spot_chg = ctx.get("spot_chg_pct", 0)
        recommendation = "HOLD"
        rec_reason = []
        if qty < 0:  # SHORT (the typical case)
            # CANONICAL RULEBOOK 2.2: SL trigger = spot within X pts of strike
            sl_pts = SL_DISTANCE_PTS.get(inst, 500)
            spot_strike_dist_pts = abs(spot - strike)
            spot_strike_dist_pct = abs(dist_pct)
            # Hard close trigger: within 0.5% of strike
            if spot_strike_dist_pct <= SL_HARD_CLOSE_PCT:
                recommendation = "CUT_ALL"
                rec_reason.append(f"⚠ Rulebook 2.2: spot within {SL_HARD_CLOSE_PCT}% of strike ({int(spot_strike_dist_pts)} pts < {sl_pts} pts) — HARD CLOSE")
            elif (side == "CE" and spot >= strike) or (side == "PE" and spot <= strike):
                recommendation = "CUT_ALL"
                rec_reason.append(f"Strike ITM — cut at market")
            elif spot_strike_dist_pct <= SL_RETHINK_PCT:
                # Within 1% of strike → rethink (per Rulebook discretionary squareoff)
                recommendation = "CUT_PARTIAL" if spot_chg * (1 if side == "CE" else -1) > 0 else "WATCH"
                rec_reason.append(f"Rulebook: spot within 1% of strike ({int(spot_strike_dist_pts)} pts) — rethink. Confirm spike is real before SL.")
            elif cushion < 0.2:
                recommendation = "CUT_ALL"
                rec_reason.append(f"Cushion {cushion}σ — near ITM, cut at market")
            elif cushion < 0.5 and ((side == "CE" and spot_chg > 0.3) or (side == "PE" and spot_chg < -0.3)):
                recommendation = "CUT_PARTIAL"
                rec_reason.append(f"Cushion {cushion}σ + spot drifting your way ({spot_chg:+.2f}%) — cut 50%")
            elif cushion < 1.0 and ((side == "CE" and spot_chg > 0.3) or (side == "PE" and spot_chg < -0.3)):
                recommendation = "WATCH"
                rec_reason.append(f"Cushion {cushion}σ thin — set mental stop. Confirm spike is REAL before squareoff.")
            elif cushion >= 1.5 and ltp <= avg_price * 0.5:
                recommendation = "HOLD"
                rec_reason.append(f"Theta winning — premium decayed >50% from entry")
            elif cushion >= 1.0:
                recommendation = "HOLD"
                rec_reason.append(f"Cushion {cushion}σ ok per Rulebook")
            else:
                recommendation = "WATCH"
                rec_reason.append(f"Cushion {cushion}σ — monitor spot direction")
            # Add max-pain context
            if max_pain:
                if (side == "CE" and max_pain < spot) or (side == "PE" and max_pain > spot):
                    rec_reason.append("max-pain pull in your favor")
                elif (side == "CE" and max_pain > spot + 100) or (side == "PE" and max_pain < spot - 100):
                    rec_reason.append("⚠ max-pain pulls against you")
            # DTE-0 theta callout
            if dte == 0:
                if cushion >= 1.0 and ltp > 1:
                    rec_reason.append(f"DTE 0 — theta acceleration; expect ₹{round(ltp*0.3,2)}-{round(ltp*0.5,2)} in 30 min")
        else:  # LONG (e.g. lottery harvest buys)
            # For longs, recommendation is about whether to set sell-limit
            target_12x = round(avg_price * 12, 2)
            target_8x = round(avg_price * 8, 2)
            if ltp >= avg_price * 5:
                recommendation = "BOOK"
                rec_reason.append(f"Up {round(ltp/avg_price,1)}× from entry — book profit")
            elif ltp >= avg_price * 1.5:
                recommendation = "WATCH"
                rec_reason.append(f"Up {round(ltp/avg_price,1)}× — set sell-limit at {target_8x}-{target_12x}")
            else:
                recommendation = "HOLD"
                rec_reason.append(f"Lottery hold; sell-limit GTT at {target_12x} (12×)")

        # Suggested exit price
        exit_suggestion = None
        if recommendation in ("CUT_PARTIAL", "CUT_ALL"):
            exit_suggestion = round(ltp * 1.02, 2)   # tiny over LTP for fast fill
        elif recommendation == "BOOK":
            exit_suggestion = round(ltp * 0.95, 2)

        # ── Staleness detection ──
        # If LTP is wildly different from avg (10× or more), the position is
        # likely from a past expiry being mispriced against the next weekly chain.
        # Mark as STALE so the UI can prompt user to clear.
        is_stale = False
        stale_reason = ""
        if avg_price > 0 and ltp is not None:
            ratio = ltp / avg_price if avg_price > 0 else 1
            if ratio >= 10 or ratio <= 0.05:
                is_stale = True
                stale_reason = f"LTP ₹{ltp} vs avg ₹{avg_price} — likely past-expiry position priced against new chain"

        # Margin / max profit / premium paid (for portfolio-level metrics)
        lot_size = LOT_SIZE[inst]
        margin_per_lot = MARGIN_PER_LOT_E0[inst]
        qty_lots = abs(qty) // lot_size
        if qty < 0:
            margin_used = qty_lots * margin_per_lot
            max_profit_at_expiry = round(avg_price * abs(qty), 2)   # full credit if expires worthless
            premium_paid = 0
        else:
            margin_used = 0   # long premium positions don't lock margin
            max_profit_at_expiry = None   # theoretical unlimited
            premium_paid = round(avg_price * qty, 2)

        out.append({
            "instrument": inst,
            "strike": strike,
            "side": side,
            "qty": qty,
            "qty_lots": qty_lots,
            "avg_price": avg_price,
            "ltp": ltp,
            "spot": spot,
            "dist_pts": int(dist_pts),
            "dist_pct": round(dist_pct, 2),
            "cushion": cushion,
            "mtm_per_share": round(mtm_per_share, 2),
            "mtm_total": mtm_total,
            "margin_used": margin_used,
            "max_profit_at_expiry": max_profit_at_expiry,
            "premium_paid": premium_paid,
            "recommendation": recommendation,
            "rec_reason": "; ".join(rec_reason),
            "exit_suggestion": exit_suggestion,
            "max_pain": max_pain,
            "dte": dte,
            "is_stale": is_stale,
            "stale_reason": stale_reason,
            "expiry": pos_exp_str,
            "is_expired": False,
            "is_settled": False,
            # Optional fill-level metadata (passed through from input):
            "broker": p.get("broker") or "",
            "demat":  p.get("demat") or "",
            "time":   p.get("time") or "",
            "note":   p.get("note") or "",
        })

    # Summary
    total_lots = sum(p.get("qty_lots", 0) for p in out)
    n_long = sum(1 for p in out if p.get("qty", 0) > 0)
    n_short = sum(1 for p in out if p.get("qty", 0) < 0)
    cuts = sum(1 for p in out if p.get("recommendation", "").startswith("CUT"))
    total_max_profit = sum((p.get("max_profit_at_expiry") or 0) for p in out)
    total_premium_paid = sum(p.get("premium_paid", 0) or 0 for p in out)

    # Margin accounting: brokers apply SPAN strangle offset — only one side can
    # be ITM at expiry, so they charge MAX(CE shorts, PE shorts) per instrument,
    # not the naked sum. We compute both for transparency.
    naked_margin_by_inst = {}      # {inst: {'CE': ₹, 'PE': ₹}}
    for p in out:
        if p.get("qty", 0) >= 0: continue   # only shorts use margin
        inst = p["instrument"]
        side = p["side"]
        m = p.get("margin_used", 0) or 0
        naked_margin_by_inst.setdefault(inst, {"CE": 0, "PE": 0})
        naked_margin_by_inst[inst][side] += m

    total_margin_naked = sum(v["CE"] + v["PE"] for v in naked_margin_by_inst.values())
    total_margin_netted = sum(max(v["CE"], v["PE"]) for v in naked_margin_by_inst.values())
    # Apply small additional E-0 expiry-day SPAN reduction (~10%) — empirical,
    # matches Rohan's broker-shown ₹83 Cr on 7-May vs my ₹93 Cr max-side estimate.
    total_margin = round(total_margin_netted * 0.90, 2)

    # ── Aggregate same-strike fills (combine multiple brokers/dematS into one logical position) ──
    # Group by (instrument, strike, side, sign(qty))
    from collections import defaultdict
    agg_buckets = defaultdict(list)
    for p in out:
        if "error" in p: continue
        sign = "SHORT" if p.get("qty", 0) < 0 else "LONG"
        key = (p["instrument"], p["strike"], p["side"], sign)
        agg_buckets[key].append(p)

    aggregated = []
    rec_priority = {"CUT_ALL": 0, "CUT_PARTIAL": 1, "WATCH": 2, "BOOK": 3, "HOLD": 4}
    for (inst, strike, side, sign), fills in agg_buckets.items():
        total_qty = sum(f["qty"] for f in fills)
        total_qty_abs = sum(abs(f["qty"]) for f in fills)
        weighted_avg = (sum(abs(f["qty"]) * f["avg_price"] for f in fills) / total_qty_abs) if total_qty_abs else 0
        worst_rec = min((f.get("recommendation", "HOLD") for f in fills), key=lambda r: rec_priority.get(r, 5))
        # Dedupe reasons; keep most informative
        reasons = list({f.get("rec_reason", "") for f in fills if f.get("rec_reason")})
        agg_row = {
            "instrument": inst, "strike": strike, "side": side,
            "qty": total_qty,
            "qty_lots": abs(total_qty) // LOT_SIZE[inst],
            "n_fills": len(fills),
            "n_brokers": len({f.get("broker", "") for f in fills if f.get("broker")}),
            "avg_price": round(weighted_avg, 4),
            "ltp": fills[0].get("ltp"),                 # same strike → same LTP
            "spot": fills[0].get("spot"),
            "dist_pts": fills[0].get("dist_pts"),
            "dist_pct": fills[0].get("dist_pct"),
            "cushion": fills[0].get("cushion"),
            "mtm_per_share": round((weighted_avg - (fills[0].get("ltp") or 0)) * (1 if sign == "SHORT" else -1), 2),
            "mtm_total": round(sum(f.get("mtm_total", 0) or 0 for f in fills), 2),
            "margin_used": sum(f.get("margin_used", 0) or 0 for f in fills),
            "max_profit_at_expiry": sum((f.get("max_profit_at_expiry") or 0) for f in fills),
            "premium_paid": sum(f.get("premium_paid", 0) or 0 for f in fills),
            "recommendation": worst_rec,
            "rec_reason": " · ".join(reasons[:2]),
            "max_pain": fills[0].get("max_pain"),
            "dte": fills[0].get("dte"),
            "fills": fills,                              # drill-down details
        }
        aggregated.append(agg_row)
    aggregated.sort(key=lambda r: -abs(r.get("mtm_total", 0)))

    # Yield per Cr — Rohan's primary KPI (₹5K/Cr target avg, ₹3K floor)
    yield_per_cr_now    = round(total_mtm        / total_margin * 1e7) if total_margin > 0 else 0
    yield_per_cr_at_exp = round(total_max_profit / total_margin * 1e7) if total_margin > 0 else 0

    n_stale = sum(1 for p in out if p.get("is_stale"))
    n_expired = sum(1 for p in out if p.get("is_expired"))
    return {
        "positions": out,
        "aggregated": aggregated,
        "summary": {
            "total_mtm": round(total_mtm, 2),
            "total_lots": total_lots,
            "n_short": n_short,
            "n_long": n_long,
            "n_action_needed": cuts,
            "n_stale": n_stale,
            "n_expired": n_expired,
            "total_margin": total_margin,                          # ← netted (broker-realistic)
            "total_margin_netted": round(total_margin_netted, 2),  # max-side estimate
            "total_margin_naked": round(total_margin_naked, 2),    # naked sum (worst-case)
            "total_max_profit": round(total_max_profit, 2),
            "total_premium_paid": round(total_premium_paid, 2),
            "yield_per_cr": yield_per_cr_now,            # ← live yield (target ₹5K/Cr avg)
            "yield_per_cr_at_expiry": yield_per_cr_at_exp,  # if all worthless
            "margin_per_cr": round(total_margin / 1e7, 2) if total_margin > 0 else 0,
            "margin_breakdown": naked_margin_by_inst,              # {inst: {CE: ₹, PE: ₹}}
        },
        "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
    }


@app.get("/api/recommendations/{instrument}")
def recommendations(instrument: str, capital_cr: float = 100.0):
    """Layered LOW / MID / HIGH risk strangle recommendations with reasoning,
    deltas, technicals, walls, events, and full context — designed to drive the
    redesigned 3-card UI."""
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")

    def _build():
        from lib.deep_otm import expected_move, cushion_ratio
        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        spot = d["spot"]; vix = d["vix"]; dte = d["dte"]; max_pain = d["max_pain"]
        prices = d["prices"]; strikes = d["strikes"]; grid = d["grid"]
        regime = vix_regime(vix)
        lot = LOT_SIZE[instrument]
        margin_lot = MARGIN_PER_LOT_E0[instrument]
        shares_per_cr = SHARES_PER_CR[instrument]
        exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

        # Determine bias
        bias_signals = []
        mp_pct = (max_pain - spot) / spot * 100 if max_pain else 0
        if mp_pct > 0.2: bias = "bullish"; bias_signals.append(f"max-pain +{mp_pct:.2f}%")
        elif mp_pct < -0.2: bias = "bearish"; bias_signals.append(f"max-pain {mp_pct:.2f}%")
        else: bias = "neutral"

        # Technicals from yesterday's snapshot — get prev OHLC from spot quote
        try:
            from lib.kite_live import _kite
            sym = "NSE:NIFTY 50" if instrument == "NIFTY" else "BSE:SENSEX"
            q = _kite().quote([sym])[sym]
            prev_high = q["ohlc"]["high"]
            prev_low = q["ohlc"]["low"]
            prev_close = q["ohlc"]["close"]
            today_open = q["ohlc"]["open"]
        except Exception:
            prev_high = prev_low = prev_close = today_open = spot
        # Pivots are based on previous day H/L/C; we have today's only — use as proxy
        pivots = _pivot_levels(prev_high, prev_low, prev_close)

        # Tier definitions per Navin Group Canonical Rulebook (Section 9T):
        #   Bucket A — Deep OTM strangles, 95% capital, 2.5%+ OTM, target ₹5K/Cr (min ₹4K)
        #   Bucket B2 — Mid-deep, 5% capital, mid-far OTM, ₹10-20K/Cr
        #   Bucket B1 — ATM straddle (opportunistic only on calm days), > ₹50K/Cr
        TIER_DEFS = [
            {"id": "A",  "label": "🛡️ Bucket A — Deep OTM",   "subtitle": "Ultra-safe · 2.5%+ (3%+ ideal) · 95% cap",
             "dist_target": 3.0, "dist_min": 2.5, "capital_pct": 95, "hit_floor": 0.96},
            {"id": "B2", "label": "⚖️ Bucket B2 — Mid-Deep",  "subtitle": "Default B · 5% cap · ₹10-20K/Cr target",
             "dist_target": 1.5, "dist_min": 1.0, "capital_pct": 5,  "hit_floor": 0.80},
            {"id": "B1", "label": "🎯 Bucket B1 — ATM Straddle (opportunistic)", "subtitle": "Calm days only · ₹50K+/Cr · 9:45-10:15 only · close 12:00",
             "dist_target": 0.3, "dist_min": 0.0, "capital_pct": 5,  "hit_floor": 0.55},
        ]
        # E-0 floors (per Section 9R)
        if d.get("is_e1"):
            floor_min, floor_ideal = PREM_PER_CR_FLOOR_MIN, PREM_PER_CR_FLOOR_IDEAL
        else:
            floor_min, floor_ideal = PREM_PER_CR_E0_FLOOR, PREM_PER_CR_E0_IDEAL

        def pick_strike(side: str, target_dist_pct: float, min_dist_pct: float) -> int | None:
            target_strike = spot * (1 + target_dist_pct/100) if side == "CE" else spot * (1 - target_dist_pct/100)
            min_strike    = spot * (1 + min_dist_pct/100)    if side == "CE" else spot * (1 - min_dist_pct/100)
            if side == "CE":
                cands = [s for s in strikes if s >= min_strike]
            else:
                cands = [s for s in strikes if s <= min_strike]
            if not cands: return None
            return min(cands, key=lambda s: abs(s - target_strike))

        tiers_out = []
        for td in TIER_DEFS:
            ce_strike = pick_strike("CE", td["dist_target"], td["dist_min"])
            pe_strike = pick_strike("PE", td["dist_target"], td["dist_min"])
            if not ce_strike or not pe_strike: continue
            ce_p = prices.get((ce_strike, "CE"), {})
            pe_p = prices.get((pe_strike, "PE"), {})
            ce_ltp = ce_p.get("ltp")
            pe_ltp = pe_p.get("ltp")
            if not ce_ltp or not pe_ltp: continue
            ce_dist_pts = ce_strike - spot
            pe_dist_pts = pe_strike - spot
            ce_dist_pct = ce_dist_pts / spot * 100
            pe_dist_pct = pe_dist_pts / spot * 100
            ce_cushion = round(cushion_ratio(abs(ce_dist_pts), exp_mv), 2)
            pe_cushion = round(cushion_ratio(abs(pe_dist_pts), exp_mv), 2)
            ce_delta = _bs_delta(spot, ce_strike, vix, max(dte, 0.5), "CE")
            pe_delta = _bs_delta(spot, pe_strike, vix, max(dte, 0.5), "PE")
            combined = round(ce_ltp + pe_ltp, 2)
            per_cr = round(combined * shares_per_cr)
            cap_inr = capital_cr * 1e7 * (td["capital_pct"] / 100)
            lots = int(cap_inr / margin_lot)
            max_profit = int(lots * lot * combined)
            limit_mult = regime["limit_mult"]
            ce_limit = round(ce_ltp * limit_mult, 2)
            pe_limit = round(pe_ltp * limit_mult, 2)
            ce_oi = int(ce_p.get("oi", 0) or 0)
            pe_oi = int(pe_p.get("oi", 0) or 0)
            # Status
            if per_cr >= floor_ideal: status = "IDEAL"
            elif per_cr >= floor_min: status = "MIN_MET"
            elif per_cr >= floor_min * 0.7: status = "CLOSE"
            else: status = "BELOW"
            # Reasoning per leg
            ce_reasons = _strike_reasoning("CE", ce_strike, ce_ltp, ce_oi, ce_dist_pct,
                                           ce_cushion, d["ce_walls"], d["pe_walls"],
                                           max_pain, spot, vix, exp_mv / spot * 100)
            pe_reasons = _strike_reasoning("PE", pe_strike, pe_ltp, pe_oi, pe_dist_pct,
                                           pe_cushion, d["ce_walls"], d["pe_walls"],
                                           max_pain, spot, vix, exp_mv / spot * 100)
            tiers_out.append({
                "id": td["id"], "label": td["label"], "subtitle": td["subtitle"],
                "capital_pct": td["capital_pct"], "lots": lots,
                "capital_inr": int(cap_inr),
                "ce": {
                    "strike": ce_strike, "delta": ce_delta, "ltp": ce_ltp, "limit": ce_limit,
                    "dist_pts": int(ce_dist_pts), "dist_pct": round(ce_dist_pct, 2),
                    "oi": ce_oi, "cushion": ce_cushion, "reasons": ce_reasons,
                },
                "pe": {
                    "strike": pe_strike, "delta": pe_delta, "ltp": pe_ltp, "limit": pe_limit,
                    "dist_pts": int(pe_dist_pts), "dist_pct": round(pe_dist_pct, 2),
                    "oi": pe_oi, "cushion": pe_cushion, "reasons": pe_reasons,
                },
                "combined_premium": combined,
                "per_cr_inr": per_cr,
                "status": status,
                "hit_rate": td["hit_floor"],
                "max_profit_inr": max_profit,
                "ticket": f"SELL CE {ce_strike} × {lots} lots @ ₹{ce_limit}\nSELL PE {pe_strike} × {lots} lots @ ₹{pe_limit}",
            })

        return {
            "context": {
                "instrument": instrument,
                "spot": spot,
                "spot_chg_pct": d.get("spot_chg_pct", 0),
                "today_open": today_open,
                "today_high": prev_high,   # actually today's
                "today_low": prev_low,
                "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
                "expiry": d["expiry"],
                "dte": dte,
                "is_e0": d["is_e0"],
                "is_e1": d["is_e1"],
                "max_pain": max_pain,
                "max_pain_pct": round(mp_pct, 2),
                "vix": vix,
                "vix_chg_pct": d.get("vix_chg_pct", 0),
                "regime": regime,
                "expected_move_pct": round(exp_mv / spot * 100, 2),
                "expected_move_pts": int(exp_mv),
                "oi_pcr": d["oi_pcr"],
                "bias": bias, "bias_signals": bias_signals,
                "ce_walls": d["ce_walls"],
                "pe_walls": d["pe_walls"],
                "pivots": pivots,
                "events": _events_for_today(),
                "lot_size": lot,
                "margin_per_lot": margin_lot,
                "lots_per_cr": LOTS_PER_CR[instrument],
                "shares_per_cr": shares_per_cr,
                "floor_min": floor_min, "floor_ideal": floor_ideal,
                "capital_cr": capital_cr,
            },
            "verdict": _trade_verdict(instrument),
            "tiers": tiers_out,
        }

    return cached(f"recos_{instrument}_{capital_cr}", 5, _build)


# ── Helper: classify VIX regime ─────────────────────────────────────────
def vix_regime(vix: float) -> dict:
    """Returns regime dict: name, action, distance_adj, skip_t3, halve_t2, premium_mult."""
    if vix < 13:
        return {"name": "VERY_LOW", "label": "Very low vol", "distance_adj": -0.25,
                "skip_t3": False, "halve_t2": False, "limit_mult": 1.05,
                "action": "Tighten 0.25% on tiers — premiums small"}
    if vix < 16:
        return {"name": "LOW", "label": "Low vol — default regime", "distance_adj": 0,
                "skip_t3": False, "halve_t2": False, "limit_mult": 1.05,
                "action": "Standard distances. Limit at LTP×1.05"}
    if vix < 18:
        return {"name": "ELEVATED", "label": "Elevated vol", "distance_adj": 0.25,
                "skip_t3": False, "halve_t2": False, "limit_mult": 1.10,
                "action": "+0.25% to T1, T2. Limit LTP×1.10"}
    if vix < 22:
        return {"name": "HIGH", "label": "High vol", "distance_adj": 0.5,
                "skip_t3": True, "halve_t2": False, "limit_mult": 1.15,
                "action": "+0.5%; SKIP T3. Limit LTP×1.15. Delay T1 to 10:30"}
    return {"name": "EXTREME", "label": "Extreme vol", "distance_adj": 1.0,
            "skip_t3": True, "halve_t2": True, "limit_mult": 1.25,
            "action": "+1%; HALVE T2; SKIP T3. Limit LTP×1.25. Delay T1 to 11:00"}


# ── Helper: fetch chain in wider range with all data ────────────────────
def _fetch_chain_full(instrument: str, distance_pct: float = 8.0, expiry=None) -> dict:
    """Returns dict with spot, expiry, vix, max_pain, oi_pcr, walls, prices map.

    If `expiry` is given (date or 'YYYY-MM-DD' string), pulls chain for THAT
    specific expiry. Otherwise defaults to nearest weekly after today.
    """
    from lib.kite_live import _kite, _instruments
    from lib.expiry_calendar import nearest_weekly_expiry_after, is_e0, is_e1
    k = _kite()
    sym = "NSE:NIFTY 50" if instrument == "NIFTY" else "BSE:SENSEX"
    spot_q = k.quote([sym])[sym]
    spot = spot_q["last_price"]
    spot_prev = spot_q["ohlc"]["close"]
    spot_chg_pct = (spot - spot_prev) / spot_prev * 100 if spot_prev else 0
    vix_q = k.quote(["NSE:INDIA VIX"])["NSE:INDIA VIX"]
    vix = vix_q["last_price"]
    vix_prev = vix_q["ohlc"]["close"]
    vix_chg_pct = (vix - vix_prev) / vix_prev * 100 if vix_prev else 0
    grid = GRID[instrument]
    today = datetime.now(IST).date()
    # Resolve expiry: explicit > nearest weekly default
    if expiry:
        if isinstance(expiry, str):
            try:
                from datetime import datetime as _dt
                next_exp = _dt.strptime(expiry, "%Y-%m-%d").date()
            except Exception:
                next_exp = nearest_weekly_expiry_after(today, instrument)
        else:
            next_exp = expiry
    else:
        next_exp = nearest_weekly_expiry_after(today, instrument)
    if not next_exp:
        return {"error": "no_expiry"}
    dte = (next_exp - today).days   # may be negative for past expiries

    seg = "NFO" if instrument == "NIFTY" else "BFO"
    instr_dump = pd.DataFrame(_instruments() if instrument == "NIFTY" else k.instruments(seg))
    def _td(x):
        if x in (None, '', '1970-01-01'): return None
        try: return pd.to_datetime(x).date()
        except: return None
    instr_dump['expiry'] = instr_dump['expiry'].apply(_td)
    chain_df = instr_dump[(instr_dump['name'] == instrument) &
                          (instr_dump['expiry'] == next_exp) &
                          (instr_dump['instrument_type'].isin(['CE','PE']))]
    lo_strike = round(spot * (1 - distance_pct/100) / grid) * grid
    hi_strike = round(spot * (1 + distance_pct/100) / grid) * grid
    chain_df = chain_df[(chain_df['strike'] >= lo_strike) & (chain_df['strike'] <= hi_strike)]
    symbols = [f"{seg}:{r['tradingsymbol']}" for _, r in chain_df.iterrows()]
    prices = {}
    for i in range(0, len(symbols), 250):
        q = k.quote(symbols[i:i+250])
        for ts, v in q.items():
            base = ts.replace(f"{seg}:", "")
            row = chain_df[chain_df['tradingsymbol'] == base]
            if row.empty: continue
            s = int(row.iloc[0]['strike'])
            opt = row.iloc[0]['instrument_type']
            prices[(s, opt)] = {
                'ltp': v['last_price'],
                'oi': v.get('oi', 0) or 0,
                'volume': v.get('volume', 0) or 0,
            }
    strikes = sorted(set(int(s) for s in chain_df['strike']))
    # max pain
    pains = []
    for pin in strikes:
        p = sum((pin - s) * (prices.get((s,'CE'), {}).get('oi') or 0) for s in strikes if s < pin)
        p += sum((s - pin) * (prices.get((s,'PE'), {}).get('oi') or 0) for s in strikes if s > pin)
        pains.append((pin, p))
    max_pain = min(pains, key=lambda x: x[1])[0] if pains else None
    # PCR
    total_pe_oi = sum(prices.get((s, 'PE'), {}).get('oi', 0) or 0 for s in strikes)
    total_ce_oi = sum(prices.get((s, 'CE'), {}).get('oi', 0) or 0 for s in strikes)
    oi_pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else None
    # OI walls (top 3 each side)
    ce_walls = sorted([(s, prices.get((s,'CE'), {}).get('oi', 0) or 0) for s in strikes],
                      key=lambda x: x[1], reverse=True)[:3]
    pe_walls = sorted([(s, prices.get((s,'PE'), {}).get('oi', 0) or 0) for s in strikes],
                      key=lambda x: x[1], reverse=True)[:3]
    return {
        "instrument": instrument,
        "spot": round(spot, 2),
        "spot_chg_pct": round(spot_chg_pct, 2),
        "expiry": str(next_exp),
        "dte": dte,
        "is_e0": is_e0(today, instrument),
        "is_e1": is_e1(today, instrument),
        "vix": round(vix, 2),
        "vix_chg_pct": round(vix_chg_pct, 2),
        "max_pain": max_pain,
        "max_pain_pct_from_spot": round((max_pain - spot)/spot*100, 2) if max_pain else None,
        "oi_pcr": oi_pcr,
        "ce_walls": [{"strike": s, "oi": int(o)} for s, o in ce_walls],
        "pe_walls": [{"strike": s, "oi": int(o)} for s, o in pe_walls],
        "strikes": strikes,
        "prices": prices,
        "grid": grid,
    }


@app.get("/api/strategy/{instrument}")
def strategy(instrument: str, risk: str = "default", capital_cr: float = 100.0, bias: str = "neutral"):
    """Strike suggester with tier classification, cushion ratio, OI walls, recommended limits.

    risk: conservative | default | aggressive
    capital_cr: capital in ₹ Cr
    bias: neutral | bullish | bearish (shifts asymmetric distances)
    """
    # "/api/strategy/custom" is a sibling route registered later — this dynamic route
    # would shadow it, so delegate here rather than 400 on instrument="custom".
    if instrument.lower() == "custom":
        from lib import custom_strategies as cs
        return {"strategies": cs.all_custom(), "kinds": cs.KINDS}
    if not kite_alive():
        raise HTTPException(401, "Kite session expired — run scripts/kite_login.py")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")
    risk = risk.lower()
    bias = bias.lower()

    def _build():
        from lib.deep_otm import expected_move, cushion_ratio, classify_tier, Tier, TIER_LABELS

        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        spot = d["spot"]; vix = d["vix"]; dte = d["dte"]; max_pain = d["max_pain"]
        prices = d["prices"]; strikes = d["strikes"]; grid = d["grid"]
        regime = vix_regime(vix)
        lot = LOT_SIZE[instrument]
        margin_lot = MARGIN_PER_LOT_E0[instrument]
        shares_per_cr = SHARES_PER_CR[instrument]
        ms = _market_state()
        verdict = _trade_verdict(instrument)
        now_ist = datetime.now(IST)
        hh_mm = now_ist.hour * 60 + now_ist.minute

        # Capital deployment per tier (per Section 9 + strategy_live)
        # T1 80%, T2 12%, T3 3%, E-1 5%
        capital_total = capital_cr * 1e7
        tier_capital_split = {"T1": 0.80, "T2": 0.12, "T3": 0.03, "E1": 0.05}

        # Risk profile overrides (stricter/looser cushion)
        # cushion thresholds: ALMOST_SURE ≥3, VERY_DEEP ≥2, BALANCED ≥1.5, AGGRESSIVE ≥1
        if risk == "conservative":
            allowed_tiers = {"T1", "T2"}     # skip T3 entirely
            min_cushion = {"T1": 3.5, "T2": 2.5}
        elif risk == "aggressive":
            allowed_tiers = {"T1", "T2", "T3"}
            min_cushion = {"T1": 2.5, "T2": 1.7, "T3": 1.2}
        else:  # default
            allowed_tiers = {"T1", "T2", "T3"}
            min_cushion = {"T1": 3.0, "T2": 2.0, "T3": 1.5}
        if regime["skip_t3"] and "T3" in allowed_tiers:
            allowed_tiers.discard("T3")

        # Distance adjustments per regime + bias
        dist_adj = regime["distance_adj"]
        ce_bias = 0.0; pe_bias = 0.0
        if bias == "bullish":  # spot drifting up → push CE further, pull PE closer
            ce_bias = 0.5; pe_bias = -0.25
        elif bias == "bearish":
            pe_bias = 0.5; ce_bias = -0.25

        # Expected move band
        exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

        # Tier base distances — Rohan's CORRECTED rules (post 7-May calibration):
        #
        #   E-0 NON-EVENT DAY (default):
        #     T1 ULTRA-SAFE  — 2.5% (floor) to 3.0% (default)
        #     T2 BALANCED    — 2.0%
        #     T3 AGGRESSIVE  — 1.5%
        #   E-0 EVENT DAY (Fed/RBI/Budget/wars/elections): wider via dist_adj.
        #
        #   E-1 OVERNIGHT carry: min 3.5% / target 4.0% (per-Cr ≥ ₹7.5K floor).
        #
        # The v2.0 doc said T1=3.0/T2=2.5/T3=2.0; Rohan's non-event tightens
        # T1 floor to 2.5% (more premium captured).
        # VIX regime adds dist_adj on volatile days (16-18 +0.25, 18-22 +0.5, 22+ +1).
        def tier_target_dist(tier_name: str, side: str) -> float:
            base = {"T1": 2.5, "T2": 2.0, "T3": 1.5, "E1": 4.0}[tier_name]
            return base + dist_adj + (ce_bias if side == "CE" else pe_bias)
        E1_MIN_DIST_PCT = 3.5   # E-1 overnight carry floor
        T1_MIN_DIST_PCT = 2.5   # E-0 ultra-safe floor (non-event)

        # OI wall lookup (top 1 each side as mandatory respect)
        top_ce_wall = d["ce_walls"][0]["strike"] if d["ce_walls"] else None
        top_pe_wall = d["pe_walls"][0]["strike"] if d["pe_walls"] else None

        # Build candidate rows: every strike on each side gets an analysis
        def analyze(strike: int, side: str) -> dict | None:
            p = prices.get((strike, side), {})
            ltp = p.get("ltp")
            oi = p.get("oi", 0) or 0
            vol = p.get("volume", 0) or 0
            if ltp is None: return None
            dist_pts = abs(strike - spot)
            dist_pct = (strike - spot) / spot * 100
            if side == "CE" and dist_pct < 0: return None  # ITM — skip
            if side == "PE" and dist_pct > 0: return None
            cush = cushion_ratio(dist_pts, exp_mv)
            tier_obj = classify_tier(cush)
            # Map deep_otm tiers → our T1/T2/T3 strategy tiers
            tier_tag = None
            if tier_obj == Tier.ALMOST_SURE: tier_tag = "T1"
            elif tier_obj == Tier.VERY_DEEP: tier_tag = "T2"
            elif tier_obj == Tier.BALANCED: tier_tag = "T3"

            # Max-pain alignment (for SELLING far-OTM)
            #   PE far below spot: prefer if pin pulls UP (max_pain > spot)
            #   CE far above spot: prefer if pin pulls DOWN (max_pain < spot)
            mp_aligned = None
            if max_pain:
                if side == "PE" and max_pain >= spot: mp_aligned = True
                elif side == "CE" and max_pain <= spot: mp_aligned = True
                else: mp_aligned = False

            # OI wall flag (top wall = strong support/resistance — selling near gets risky)
            is_wall = strike == (top_ce_wall if side == "CE" else top_pe_wall)

            # Manipulation risk for SELLING (selling at low-OI 4-6% strikes is trap-prone)
            mr = "LOW"
            if 4.0 <= abs(dist_pct) <= 6.0 and oi < 200000:
                mr = "HIGH"
            elif 3.5 <= abs(dist_pct) <= 6.5 and oi < 500000:
                mr = "MED"

            # Recommended limit price (for SELL)
            limit_price = round(ltp * regime["limit_mult"], 2)

            # Margin & sizing  (E-0 deep-OTM margin)
            premium_per_lot = ltp * lot
            return_per_cr = round(ltp * shares_per_cr, 0)  # ₹ captured if worthless, per Cr E-0 margin

            # Breakeven
            breakeven = strike + ltp if side == "CE" else strike - ltp

            # Backtest hit rate hint (rough mapping)
            hit_rate = None
            if cush >= 3.0: hit_rate = 0.96
            elif cush >= 2.0: hit_rate = 0.92
            elif cush >= 1.5: hit_rate = 0.85
            elif cush >= 1.0: hit_rate = 0.72

            # Premium rise probability (heuristic, only meaningful when market open)
            prem_rise = _premium_rise_prob(
                cushion=cush, dist_pct=dist_pct, vix_chg_pct=d.get("vix_chg_pct", 0),
                hh_mm=hh_mm, oi=oi, ltp=ltp, dte=dte, side=side,
                spot_chg_pct=d.get("spot_chg_pct", 0), market_open=ms["market_open"],
            )

            return {
                "strike": strike,
                "side": side,
                "ltp": ltp,
                "limit_price": limit_price,
                "oi": oi,
                "volume": vol,
                "dist_pct": round(dist_pct, 2),
                "dist_abs_pct": round(abs(dist_pct), 2),
                "cushion": round(cush, 2),
                "tier": tier_tag,
                "tier_label": TIER_LABELS[tier_obj] if tier_obj else None,
                "max_pain_aligned": mp_aligned,
                "is_top_wall": is_wall,
                "manipulation_risk": mr,
                "premium_per_lot": round(premium_per_lot, 0),
                "return_per_cr": return_per_cr,
                "breakeven": round(breakeven, 1),
                "hit_rate": hit_rate,
                "premium_rise_prob": prem_rise,
                "lot_size": lot,
            }

        all_rows = []
        for s in strikes:
            for side in ("CE", "PE"):
                r = analyze(s, side)
                if r: all_rows.append(r)

        # Group into tiers per side, then for each tier pick the best candidate near target distance
        def best_in_tier(tier: str, side: str) -> list[dict]:
            target = tier_target_dist(tier, side)
            min_c = min_cushion.get(tier, 1.0)
            # Filter: same tier OR cushion meets min
            cands = [r for r in all_rows if r["side"] == side and r["cushion"] >= min_c]
            if not cands: return []
            # Sort by closeness to target distance
            cands.sort(key=lambda r: abs(r["dist_abs_pct"] - target))
            # Top 3 candidates
            return cands[:3]

        tiers_out = {}
        for tier in ["T1", "T2", "T3"]:
            if tier not in allowed_tiers:
                tiers_out[tier] = {"skipped": True, "reason": f"VIX regime {regime['name']}" if tier == "T3" and regime["skip_t3"] else f"Risk profile {risk}"}
                continue
            cap_alloc = capital_total * tier_capital_split[tier]
            lots_budget = int(cap_alloc / margin_lot)
            ce_cands = best_in_tier(tier, "CE")
            pe_cands = best_in_tier(tier, "PE")
            for r in ce_cands + pe_cands:
                r["lots_in_tier_budget"] = lots_budget
                r["tier_capital_inr"] = int(cap_alloc)
                r["expected_pnl_at_expiry"] = int(r["premium_per_lot"] * lots_budget)
            tiers_out[tier] = {
                "skipped": False,
                "label": {"T1": "Tier 1 — Ultra-safe (80%)", "T2": "Tier 2 — Balanced (12%)", "T3": "Tier 3 — Aggressive (3%)"}[tier],
                "target_dist_ce_pct": round(tier_target_dist(tier, "CE"), 2),
                "target_dist_pe_pct": round(tier_target_dist(tier, "PE"), 2),
                "min_cushion": min_c if (min_c := min_cushion.get(tier)) else None,
                "capital_allocated_inr": int(cap_alloc),
                "lots_budget": lots_budget,
                "ce_candidates": ce_cands,
                "pe_candidates": pe_cands,
            }

        # E-1 advance — only relevant if today is E-1
        if d["is_e1"]:
            tier = "E1"
            cap_alloc = capital_total * tier_capital_split[tier]
            lots_budget = int(cap_alloc / margin_lot)
            target = tier_target_dist(tier, "CE")
            # Filter: enforce ≥4% OTM (Rohan's post-6-May rule) AND cushion ≥ 2.5
            ce_cands = sorted([r for r in all_rows if r["side"] == "CE"
                               and r["cushion"] >= 2.5
                               and r["dist_abs_pct"] >= E1_MIN_DIST_PCT],
                              key=lambda r: abs(r["dist_abs_pct"] - target))[:3]
            pe_cands = sorted([r for r in all_rows if r["side"] == "PE"
                               and r["cushion"] >= 2.5
                               and r["dist_abs_pct"] >= E1_MIN_DIST_PCT],
                              key=lambda r: abs(r["dist_abs_pct"] - target))[:3]
            for r in ce_cands + pe_cands:
                r["lots_in_tier_budget"] = lots_budget
                r["expected_pnl_at_expiry"] = int(r["premium_per_lot"] * lots_budget)
            tiers_out["E1"] = {
                "skipped": False,
                "label": "E-1 Advance — Day-before (5%)",
                "target_dist_ce_pct": round(target, 2),
                "target_dist_pe_pct": round(target, 2),
                "capital_allocated_inr": int(cap_alloc),
                "lots_budget": lots_budget,
                "ce_candidates": ce_cands,
                "pe_candidates": pe_cands,
            }

        # Build "symmetric strangles meeting per-Cr floor" — answers Rohan's
        # question: which strangle clears ₹7.5K/Cr min and ideally ₹10K+/Cr,
        # while still being wall-protected and within an acceptable cushion?
        strangle_options = []
        for ce_strike in strikes:
            ce = prices.get((ce_strike, "CE"), {}); ce_ltp = ce.get("ltp")
            if not ce_ltp or ce_strike <= spot: continue
            ce_dist = (ce_strike - spot) / spot * 100
            # Find approximately symmetric PE strike
            pe_target_dist = -ce_dist
            pe_target = spot * (1 + pe_target_dist/100)
            pe_strike = min((s for s in strikes if s <= pe_target), default=None,
                            key=lambda s: abs(s - pe_target) if s is not None else 1e18) if any(s <= pe_target for s in strikes) else None
            # simpler: pick closest PE to target on the down side
            pe_candidates = [s for s in strikes if s <= pe_target]
            if not pe_candidates: continue
            pe_strike = max(pe_candidates)
            pe = prices.get((pe_strike, "PE"), {}); pe_ltp = pe.get("ltp")
            if not pe_ltp: continue
            combined = round(ce_ltp + pe_ltp, 2)
            per_cr = round(combined * shares_per_cr)
            avg_dist = round((abs((ce_strike - spot) / spot * 100) + abs((pe_strike - spot) / spot * 100)) / 2, 2)
            avg_cushion = round(((ce_strike - spot) + (spot - pe_strike)) / 2 / exp_mv, 2) if exp_mv > 0 else 0
            # Wall protection: is CE strike beyond top CE wall, PE strike beyond top PE wall?
            top_ce_wall = d["ce_walls"][0]["strike"] if d["ce_walls"] else None
            top_pe_wall = d["pe_walls"][0]["strike"] if d["pe_walls"] else None
            ce_wall_protected = (top_ce_wall is not None) and (ce_strike >= top_ce_wall)
            pe_wall_protected = (top_pe_wall is not None) and (pe_strike <= top_pe_wall)
            # Status flag — context-aware floors:
            #   E-1 carry day: ideal ₹10K, min ₹7.5K
            #   E-0 same-day / other: ideal ₹5K, min ₹3K
            if d.get("is_e1"):
                _ideal = PREM_PER_CR_FLOOR_IDEAL; _min = PREM_PER_CR_FLOOR_MIN
            else:
                _ideal = PREM_PER_CR_E0_IDEAL; _min = PREM_PER_CR_E0_FLOOR
            if per_cr >= _ideal: status = "IDEAL"
            elif per_cr >= _min: status = "MIN_MET"
            elif per_cr >= _min * 0.7: status = "CLOSE"
            else: status = "BELOW"
            # Hit-rate proxy from cushion
            if avg_cushion >= 3: hit = 0.96
            elif avg_cushion >= 2: hit = 0.92
            elif avg_cushion >= 1.5: hit = 0.85
            elif avg_cushion >= 1: hit = 0.72
            else: hit = 0.50
            # Wall-bonus to hit rate (heuristic)
            if ce_wall_protected and pe_wall_protected: hit = min(0.97, hit + 0.05)
            strangle_options.append({
                "ce_strike": ce_strike, "pe_strike": pe_strike,
                "ce_ltp": ce_ltp, "pe_ltp": pe_ltp,
                "combined": combined,
                "ce_dist_pct": round((ce_strike - spot)/spot*100, 2),
                "pe_dist_pct": round((pe_strike - spot)/spot*100, 2),
                "avg_dist_pct": avg_dist,
                "avg_cushion": avg_cushion,
                "per_cr_inr": per_cr,
                "status": status,
                "hit_rate_est": hit,
                "ce_wall_protected": ce_wall_protected,
                "pe_wall_protected": pe_wall_protected,
                "ce_limit": round(ce_ltp * regime["limit_mult"], 2),
                "pe_limit": round(pe_ltp * regime["limit_mult"], 2),
            })
        # Sort by status desc (IDEAL → MIN_MET → CLOSE → BELOW), then by avg_cushion desc within status
        status_order = {"IDEAL": 0, "MIN_MET": 1, "CLOSE": 2, "BELOW": 3}
        strangle_options.sort(key=lambda r: (status_order.get(r["status"], 9), -r["avg_cushion"]))

        # Pick "RECOMMENDED" T1 ultra-safe pair per Rohan's rules:
        #   1. Distance ≥ 2.5% (the ultra-safe floor — non-event days)
        #   2. Per-Cr ≥ ₹3,000 (E-0 absolute floor); ideally ≥ ₹5,000
        #   3. Wall-protected both sides preferred
        #   4. Among qualifying, pick the one closest to ₹5K/Cr ideal
        recommended = None
        # Tier 1 priority: distance ≥ 2.5% AND per-Cr ≥ ₹3K (floor)
        ultra_safe_pool = [r for r in strangle_options
                           if r["avg_dist_pct"] >= 2.5
                           and r["per_cr_inr"] >= PREM_PER_CR_E0_FLOOR]
        # Fallback 1: distance ≥ 2.0% (T2 zone) if no ultra-safe meets premium floor
        t2_pool = [r for r in strangle_options
                   if r["avg_dist_pct"] >= 2.0
                   and r["per_cr_inr"] >= PREM_PER_CR_E0_FLOOR]
        # Fallback 2: ANY IDEAL (last resort — late-day, never skip)
        any_ideal = [r for r in strangle_options if r["status"] == "IDEAL"]
        pool = ultra_safe_pool or t2_pool or any_ideal or strangle_options[:5]
        if pool:
            recommended = max(pool, key=lambda r: (
                int(r["ce_wall_protected"] and r["pe_wall_protected"]),  # both walls
                -abs(r["per_cr_inr"] - PREM_PER_CR_E0_IDEAL) / 1000,    # prefer near ₹5K ideal
                r["avg_cushion"],                                         # then cushion
            ))

        return {
            "instrument": instrument,
            "spot": spot,
            "spot_chg_pct": d.get("spot_chg_pct", 0),
            "expiry": d["expiry"],
            "dte": dte,
            "is_e0": d["is_e0"],
            "is_e1": d["is_e1"],
            "vix": vix,
            "vix_chg_pct": d.get("vix_chg_pct", 0),
            "regime": regime,
            "max_pain": max_pain,
            "max_pain_pct_from_spot": d["max_pain_pct_from_spot"],
            "oi_pcr": d["oi_pcr"],
            "ce_walls": d["ce_walls"],
            "pe_walls": d["pe_walls"],
            "expected_move_pts": round(exp_mv, 1),
            "expected_move_pct": round(exp_mv / spot * 100, 2),
            "lot_size": LOT_SIZE[instrument],
            "margin_per_lot": margin_lot,
            "lots_per_cr": LOTS_PER_CR[instrument],
            "shares_per_cr": shares_per_cr,
            "prem_per_cr_floor_min": PREM_PER_CR_FLOOR_MIN,
            "prem_per_cr_floor_ideal": PREM_PER_CR_FLOOR_IDEAL,
            "capital_cr": capital_cr,
            "risk": risk,
            "bias": bias,
            "tiers": tiers_out,
            "strangle_options": strangle_options[:12],   # top 12
            "recommended_strangle": recommended,
            "market": ms,
            "verdict": verdict,
            "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        }

    return cached(f"strategy_{instrument}_{risk}_{capital_cr}_{bias}", 5, _build)


# ── Asymmetric strangle picker (Rohan's bias-aware tool) ───────────────
def _pick_asymmetric_strangle(d: dict, instrument: str, bias: str,
                              capital_cr: float, size_pct: float,
                              regime: dict) -> dict | None:
    """Pick best CE/PE strangle with ASYMMETRIC distances based on bias.

    bias = 'auto' → infer from max-pain direction
         | 'neutral' → CE & PE equidistant
         | 'bullish' → CE further, PE closer (spot drifting up → PE buffer grows)
         | 'bearish' → PE further, CE closer (spot drifting down → CE buffer grows)

    Returns dict with chosen strikes, premiums, per-Cr captured, suggested lots,
    limit prices (with regime multiplier), and bias inference details.
    """
    from lib.deep_otm import expected_move, cushion_ratio
    spot = d["spot"]; vix = d["vix"]; dte = d["dte"]; max_pain = d["max_pain"]
    prices = d["prices"]; strikes = d["strikes"]
    shares_per_cr = SHARES_PER_CR[instrument]
    margin_lot = MARGIN_PER_LOT_E0[instrument]
    lot = LOT_SIZE[instrument]
    exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

    # Bias inference
    bias_in = bias
    auto_signals = []
    if bias == "auto":
        # Use max-pain direction + intraday spot move + PCR
        mp_signal = 0
        if max_pain and max_pain > spot * 1.002:
            mp_signal = 1; auto_signals.append(f"max-pain {max_pain} > spot {spot} (pin↑)")
        elif max_pain and max_pain < spot * 0.998:
            mp_signal = -1; auto_signals.append(f"max-pain {max_pain} < spot {spot} (pin↓)")
        spot_signal = 0
        sc = d.get("spot_chg_pct", 0)
        if sc > 0.5: spot_signal = 1; auto_signals.append(f"intraday +{sc}%")
        elif sc < -0.5: spot_signal = -1; auto_signals.append(f"intraday {sc}%")
        pcr = d.get("oi_pcr") or 1.0
        pcr_signal = 0
        if pcr > 1.2: pcr_signal = 1; auto_signals.append(f"PCR {pcr} (put-heavy → bullish lean)")
        elif pcr < 0.7: pcr_signal = -1; auto_signals.append(f"PCR {pcr} (call-heavy → bearish lean)")
        score = mp_signal + spot_signal + pcr_signal
        if score >= 1: bias = "bullish"
        elif score <= -1: bias = "bearish"
        else: bias = "neutral"

    # Distance targets per bias (asymmetric)
    # Rohan's hard floor: 3.5% MIN on either side, even when biased.
    # Ultra-safe = ≥3.5%; volatile days push wider via regime dist_adj on caller side.
    targets = {
        "neutral":  {"ce": 3.5, "pe": 3.5},
        "bullish":  {"ce": 4.0, "pe": 3.5},   # CE further (risk side), PE at 3.5 floor
        "bearish":  {"ce": 3.5, "pe": 4.0},   # PE further (risk side), CE at 3.5 floor
    }[bias]

    # Volatility regime push (HIGH/EXTREME VIX → wider both)
    vix_v = d["vix"]
    if vix_v >= 22: targets = {k: v + 1.0 for k, v in targets.items()}
    elif vix_v >= 18: targets = {k: v + 0.5 for k, v in targets.items()}
    elif vix_v >= 16: targets = {k: v + 0.25 for k, v in targets.items()}

    # On E-1 day: enforce ≥ 4% on both sides (Rohan's post-6-May rule)
    if d.get("is_e1"):
        targets = {"ce": max(targets["ce"], 4.0), "pe": max(targets["pe"], 4.0)}

    # Generate candidate (CE, PE) pairs around targets (±0.5% search grid)
    cands = []
    for ce_off in [-0.5, -0.25, 0, 0.25, 0.5, 0.75, 1.0]:
        for pe_off in [-0.5, -0.25, 0, 0.25, 0.5, 0.75, 1.0]:
            ce_target_pct = targets["ce"] + ce_off
            pe_target_pct = targets["pe"] + pe_off
            if ce_target_pct < 1.5 or pe_target_pct < 1.5: continue   # safety floor
            ce_target = spot * (1 + ce_target_pct/100)
            pe_target = spot * (1 - pe_target_pct/100)
            ce_options = [s for s in strikes if s >= ce_target]
            pe_options = [s for s in strikes if s <= pe_target]
            if not ce_options or not pe_options: continue
            ce_strike = min(ce_options)
            pe_strike = max(pe_options)
            ce_p = prices.get((ce_strike, "CE"), {})
            pe_p = prices.get((pe_strike, "PE"), {})
            ce_ltp = ce_p.get("ltp")
            pe_ltp = pe_p.get("ltp")
            if not ce_ltp or not pe_ltp or ce_ltp <= 0 or pe_ltp <= 0: continue
            ce_dist = (ce_strike - spot) / spot * 100
            pe_dist = (pe_strike - spot) / spot * 100
            combined = round(ce_ltp + pe_ltp, 2)
            per_cr = round(combined * shares_per_cr)
            ce_cush = round(cushion_ratio(ce_strike - spot, exp_mv), 2)
            pe_cush = round(cushion_ratio(spot - pe_strike, exp_mv), 2)
            avg_cush = round((ce_cush + pe_cush) / 2, 2)
            ce_oi = ce_p.get("oi", 0) or 0
            pe_oi = pe_p.get("oi", 0) or 0
            top_ce_wall = d["ce_walls"][0]["strike"] if d["ce_walls"] else None
            top_pe_wall = d["pe_walls"][0]["strike"] if d["pe_walls"] else None
            ce_wall_ok = top_ce_wall is not None and ce_strike >= top_ce_wall
            pe_wall_ok = top_pe_wall is not None and pe_strike <= top_pe_wall
            if per_cr >= PREM_PER_CR_FLOOR_IDEAL: status = "IDEAL"
            elif per_cr >= PREM_PER_CR_FLOOR_MIN: status = "MIN_MET"
            else: status = "BELOW"
            cands.append({
                "ce_strike": ce_strike, "pe_strike": pe_strike,
                "ce_ltp": ce_ltp, "pe_ltp": pe_ltp,
                "ce_dist_pct": round(ce_dist, 2), "pe_dist_pct": round(pe_dist, 2),
                "combined_premium": combined,
                "per_cr_inr": per_cr,
                "ce_cushion": ce_cush, "pe_cushion": pe_cush, "avg_cushion": avg_cush,
                "ce_oi": int(ce_oi), "pe_oi": int(pe_oi),
                "ce_wall_protected": ce_wall_ok, "pe_wall_protected": pe_wall_ok,
                "status": status,
            })

    if not cands: return None

    # Pick recommendation:
    # 1. Prefer IDEAL, then MIN_MET, then BELOW
    # 2. Within tier: prefer wall-protected both sides
    # 3. Then prefer cushion ≥ 1.5 (T3 floor)
    # 4. Then minimize over-tightening from base targets
    def score(c):
        s = {"IDEAL": 0, "MIN_MET": 1, "BELOW": 2}[c["status"]]
        wp = -2 if (c["ce_wall_protected"] and c["pe_wall_protected"]) else (-1 if (c["ce_wall_protected"] or c["pe_wall_protected"]) else 0)
        cu = -1 if c["avg_cushion"] >= 1.5 else 0
        # Stretch penalty: how far from clean target distances
        stretch = abs(c["ce_dist_pct"] - targets["ce"]) + abs(c["pe_dist_pct"] - targets["pe"])
        # Penalty for over-loaded (too premium-heavy → too close to spot)
        overload = max(0, (c["per_cr_inr"] - 18000) / 5000)
        return (s, wp + cu, stretch + overload)
    cands.sort(key=score)
    chosen = cands[0]

    # Sizing
    cap_used = capital_cr * 1e7 * (size_pct / 100)
    lots = int(cap_used / margin_lot)
    max_profit = int(lots * lot * chosen["combined_premium"])
    # Limit prices with regime multiplier
    ce_limit = round(chosen["ce_ltp"] * regime["limit_mult"], 2)
    pe_limit = round(chosen["pe_ltp"] * regime["limit_mult"], 2)

    # Hit-rate proxy (cushion + wall bonus)
    avg_cush = chosen["avg_cushion"]
    if avg_cush >= 3: hit = 0.96
    elif avg_cush >= 2: hit = 0.92
    elif avg_cush >= 1.5: hit = 0.85
    elif avg_cush >= 1.0: hit = 0.72
    else: hit = 0.50
    if chosen["ce_wall_protected"] and chosen["pe_wall_protected"]: hit = min(0.97, hit + 0.05)

    return {
        "bias_used": bias,
        "bias_requested": bias_in,
        "auto_signals": auto_signals,
        "ce_strike": chosen["ce_strike"], "pe_strike": chosen["pe_strike"],
        "ce_ltp": chosen["ce_ltp"], "pe_ltp": chosen["pe_ltp"],
        "ce_limit": ce_limit, "pe_limit": pe_limit,
        "ce_dist_pct": chosen["ce_dist_pct"], "pe_dist_pct": chosen["pe_dist_pct"],
        "ce_cushion": chosen["ce_cushion"], "pe_cushion": chosen["pe_cushion"],
        "avg_cushion": chosen["avg_cushion"],
        "combined_premium": chosen["combined_premium"],
        "per_cr_inr": chosen["per_cr_inr"],
        "status": chosen["status"],
        "ce_oi": chosen["ce_oi"], "pe_oi": chosen["pe_oi"],
        "ce_wall_protected": chosen["ce_wall_protected"],
        "pe_wall_protected": chosen["pe_wall_protected"],
        "lots": lots,
        "capital_used_inr": int(cap_used),
        "capital_used_cr": round(cap_used / 1e7, 2),
        "max_profit_inr": max_profit,
        "max_profit_per_cr": int(max_profit / max(capital_cr, 0.01)),
        "hit_rate_est": hit,
        "lot_size": lot,
        "alternative_pairs": [{
            "ce_strike": c["ce_strike"], "pe_strike": c["pe_strike"],
            "ce_dist_pct": c["ce_dist_pct"], "pe_dist_pct": c["pe_dist_pct"],
            "combined_premium": c["combined_premium"],
            "per_cr_inr": c["per_cr_inr"],
            "avg_cushion": c["avg_cushion"],
            "status": c["status"],
        } for c in cands[1:6]],   # top 5 alternatives
    }


@app.get("/api/recommend/{instrument}")
def recommend(instrument: str, bias: str = "auto", capital_cr: float = 100.0, size_pct: float = 5.0):
    """Asymmetric strangle recommendation with bias-aware distances + per-Cr floor.

    Args:
      bias: auto | neutral | bullish | bearish
      capital_cr: total capital in ₹Cr (default 100)
      size_pct: % of capital for THIS trade (default 5 = E-1 size)
    """
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")
    bias = bias.lower()
    if bias not in ("auto", "neutral", "bullish", "bearish"):
        raise HTTPException(400, "bias must be auto/neutral/bullish/bearish")

    def _build():
        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        regime = vix_regime(d["vix"])
        rec = _pick_asymmetric_strangle(d, instrument, bias, capital_cr, size_pct, regime)
        if rec is None:
            return {"error": "no_eligible_strangle"}
        verdict = _trade_verdict(instrument)
        return {
            "instrument": instrument,
            "spot": d["spot"],
            "spot_chg_pct": d.get("spot_chg_pct", 0),
            "max_pain": d["max_pain"],
            "max_pain_pct_from_spot": d["max_pain_pct_from_spot"],
            "vix": d["vix"], "vix_chg_pct": d.get("vix_chg_pct", 0),
            "regime": regime,
            "expiry": d["expiry"], "dte": d["dte"],
            "is_e0": d["is_e0"], "is_e1": d["is_e1"],
            "oi_pcr": d["oi_pcr"],
            "ce_walls": d["ce_walls"], "pe_walls": d["pe_walls"],
            "verdict": verdict,
            "recommendation": rec,
            "size_pct": size_pct,
            "shares_per_cr": SHARES_PER_CR[instrument],
            "margin_per_lot_e0": MARGIN_PER_LOT_E0[instrument],
            "lots_per_cr": LOTS_PER_CR[instrument],
            "prem_per_cr_floor_min": PREM_PER_CR_FLOOR_MIN,
            "prem_per_cr_floor_ideal": PREM_PER_CR_FLOOR_IDEAL,
            "ist_time": datetime.now(IST).strftime("%H:%M:%S"),
        }

    return cached(f"recommend_{instrument}_{bias}_{capital_cr}_{size_pct}", 5, _build)


def _harvest_plan(candidates: list, amount: float, n: int = 4, tp_mult: int = 6, lot_default: int = 75):
    """AMOUNT-IN → split + lots + TP + possible result. Splits `amount` across the
    ripest, most-liquid deep-OTM candidates (analysis 014 lottery harvest). SELL-LIMIT
    at 6×, NOT 12×: the median band spike is ~7.3× and fades fast — a 12× limit fills on
    only 7% of days (3/42), a ~6× limit catches the typical spike. Harvest stays a small
    flutter (backtest EV is thin) — not a yield source."""
    import math as _m
    order = {"🔥 HIGH": 3, "MED": 2, "LOW": 1}
    # BACKTESTED sweet spot (analysis 014): ₹0.15-0.40. Ultra-cheap ≤0.10 spikes most but
    # brokerage nets only ~+₹3/winning lot; >0.50 spikes too rarely. 0.15-0.40 nets +₹54-124.
    pool = [c for c in candidates if 0.15 <= (c.get("ltp") or 0) <= 0.50 and (c.get("volume") or 0) > 500_000]
    if not pool:   # fallback to anything liquid if the band is empty
        pool = [c for c in candidates if (c.get("ltp") or 0) > 0 and (c.get("volume") or 0) > 500_000]
    pool.sort(key=lambda c: (-order.get(c.get("ripeness"), 0), -(c.get("volume") or 0)))
    # BALANCE the sides — a harvest covers BOTH directions (a spike can go either way).
    # Take ~half from CE and half from PE among the ripest.
    half = max(1, n // 2)
    ce = [c for c in pool if c.get("side") == "CE"][:half]
    pe = [c for c in pool if c.get("side") == "PE"][:half]
    pick = (ce + pe)[:n]
    if len(pick) < n:   # top up from whatever's left if one side is thin
        for c in pool:
            if c not in pick:
                pick.append(c)
            if len(pick) >= n:
                break
    if not pick:
        return None
    per = amount / len(pick)
    legs, cost, all_tp = [], 0.0, 0.0
    for c in pick:
        prem = c["ltp"]; lot = c.get("lot_size") or lot_default
        lots = _m.floor(per / (prem * lot))
        if lots < 1:
            continue
        leg_cost = lots * prem * lot
        tp = round(prem * tp_mult, 2)
        profit = round(lots * lot * (tp - prem))
        cost += leg_cost; all_tp += profit
        legs.append({"strike": c["strike"], "side": c["side"], "premium": prem,
                     "dist_pct": c["dist_pct"], "lots": lots, "cost": round(leg_cost),
                     "tp_limit": tp, "profit_at_tp": profit,
                     "spike_prob_pct": c.get("premium_rise_prob"), "ripeness": c.get("ripeness")})
    if not legs:
        return None
    return {"amount": round(amount), "deployed": round(cost), "n_legs": len(legs), "tp_mult": tp_mult,
            "legs": legs, "max_if_all_hit": round(all_tp),
            "net_if_one_hits": round(all_tp / len(legs) - cost),
            "worst_case": -round(cost),
            "basis": "analysis 014 (lottery harvest) — sell-limit at 12×; most legs expire worthless, one spike pays the lot."}


@app.get("/api/manipulation/{instrument}/plan")
def manipulation_plan(instrument: str, amount: float = 10000):
    """Amount-driven harvest plan: ₹amount → which strikes, how many lots, TP, possible result."""
    base = manipulation(instrument)
    if isinstance(base, dict) and base.get("error"):
        return {"error": base["error"]}
    plan = _harvest_plan(base.get("candidates", []), amount)
    # TIMING (analysis 014): spikes cluster 14:00-15:00 and premiums drift cheaper into it.
    now = datetime.now(IST).time()
    if now < time(14, 0):
        timing = {"verdict": "WAIT", "tone": "wait",
                  "headline": f"⏳ WAIT — buy at 14:00 (it's {now.strftime('%H:%M')})",
                  "why": "Premiums drift cheaper into the 14:00 window and spikes cluster 14:00-15:00 — "
                         "buying now overpays and sits through dead time. Get the order ready, fire at 14:00."}
    elif now < time(15, 0):
        timing = {"verdict": "BUY NOW", "tone": "go",
                  "headline": "🟢 BUY NOW — in the harvest window",
                  "why": "14:00-15:00 is the spike window and premiums are at their cheapest. "
                         "Buy the band below and place the ~6× sell-limits (the data-backed fill level) immediately."}
    elif now < time(15, 25):
        timing = {"verdict": "LATE", "tone": "hold",
                  "headline": "⚠ Late — spike window closing (15:25 cutoff)",
                  "why": "Only buy if you already see early spike action; otherwise the window's nearly gone."}
    else:
        timing = {"verdict": "OFF", "tone": "wait", "headline": "Harvest window closed for today", "why": ""}
    return {"instrument": instrument, "spot": base.get("spot"),
            "phase": base.get("phase_label"), "current_time": base.get("current_time"),
            "timing": timing,
            "plan": plan, "error": None if plan else "no candidates in the ₹0.15-0.40 band right now"}


@app.get("/api/manipulation/{instrument}")
def manipulation(instrument: str):
    """Manipulation harvest panel — Thursday SENSEX / Tuesday NIFTY E-0 spike opportunity.

    Phases (IST):
      <13:30  WAIT  — pre-window, monitor only
      13:30-14:00 PREP — identify candidates, ready capital
      14:00-14:30 BUY  — execute deep OTM cheap buys (₹10-15K per strike)
      14:30-15:00 LADDER — place sell-limits at 12× LTP
      15:00-15:25 CATCH — spike window, watch for fills
      15:25+ CLEANUP — square remaining
    Off-day → not_e0
    """
    if not kite_alive():
        raise HTTPException(401, "Kite session expired")
    instrument = instrument.upper()
    if instrument not in ("NIFTY", "SENSEX"):
        raise HTTPException(400, "instrument must be NIFTY or SENSEX")

    def _build():
        from lib.deep_otm import expected_move, cushion_ratio
        d = _fetch_chain_full(instrument, distance_pct=8.0)
        if "error" in d: return d
        spot = d["spot"]; vix = d["vix"]; dte = d["dte"]
        prices = d["prices"]; strikes = d["strikes"]
        is_e0 = d["is_e0"]
        now = datetime.now(IST)
        hh_mm = now.hour * 60 + now.minute
        ms = _market_state()
        exp_mv = expected_move(spot, vix, dte if dte > 0 else 1)

        # Phase determination
        if not is_e0:
            phase = "off"
            phase_label = f"Not E-0 — {instrument} expiry on {d['expiry']}"
            phase_action = "Manipulation harvest is E-0-only. Watch market dynamics; come back on expiry day."
        elif hh_mm < 13*60 + 30:
            phase = "wait"
            phase_label = "WAIT — pre-window"
            phase_action = "Too early. Manipulation typically starts 14:00 onwards. Don't enter yet."
        elif hh_mm < 14*60:
            phase = "prep"
            phase_label = "PREP — ready capital"
            phase_action = "Identify candidates, close 20% existing shorts at ₹0.05-0.10. Buy budget ₹50-75K (5 strikes × ₹10-15K)."
        elif hh_mm < 14*60 + 30:
            phase = "buy"
            phase_label = "BUY phase — execute deep OTM purchases"
            phase_action = "Buy 5 deep-OTM strikes at MARKET. Budget ₹10-15K per strike. Pick from candidate list."
        elif hh_mm < 15*60:
            phase = "ladder"
            phase_label = "LADDER — place sell-limit orders"
            phase_action = "Place SELL LIMITs at 12× your buy price across all 5 strikes. Use take-profit GTT."
        elif hh_mm < 15*60 + 25:
            phase = "catch"
            phase_label = "CATCH window — spike monitor active"
            phase_action = "Watch for spikes. 73% of historical SENSEX manipulation hits 15:00-15:25. Hands off the wheel — limits do the work."
        else:
            phase = "cleanup"
            phase_label = "CLEANUP — close remaining"
            phase_action = "Square any unsold positions at market. Log what worked. Cancel unfilled limits."

        # Spike-ripe candidates: 4.5-6.0% OTM, OI < 2L, LTP < ₹3 (the cheap deep ones)
        # Per analysis 014: this is the sweet spot for SENSEX manipulation
        candidates = []
        for s in strikes:
            for side in ("CE", "PE"):
                p = prices.get((s, side), {})
                ltp = p.get("ltp")
                oi = p.get("oi", 0) or 0
                vol = p.get("volume", 0) or 0
                if ltp is None or ltp <= 0: continue
                dist_pct = (s - spot) / spot * 100
                if side == "CE" and dist_pct < 0: continue
                if side == "PE" and dist_pct > 0: continue
                ad = abs(dist_pct)
                # Spike-ripe filter
                if not (4.0 <= ad <= 6.5): continue
                if ltp > 5: continue       # too expensive — already noticed
                # Score: high score = ripe for spike
                score = 0
                if 4.5 <= ad <= 6.0: score += 2     # sweet spot
                if oi < 100000: score += 2
                elif oi < 200000: score += 1
                if vol > 0 and vol < 500: score += 1   # nearly untraded → big move possible
                if ltp < 1.0: score += 2
                elif ltp < 2.0: score += 1
                # Recommended buy qty: ₹12.5K mid budget per strike
                lot = LOT_SIZE[instrument]
                budget = 12500
                qty_lots = max(1, int(budget / (ltp * lot))) if ltp * lot > 0 else 1
                # If lot is bigger than budget allows, still buy 1 lot if cost <₹15K
                cost = qty_lots * ltp * lot
                if cost > 15000 and qty_lots > 1:
                    qty_lots -= 1
                    cost = qty_lots * ltp * lot
                # Targets
                target_sell_limit = round(ltp * 12, 2)   # 12× — Section 9M
                conservative_tp = round(ltp * 8, 2)      # 8× — alternate
                payoff_at_12x = int((target_sell_limit - ltp) * lot * qty_lots)
                payoff_at_8x = int((conservative_tp - ltp) * lot * qty_lots)
                # Premium-rise probability: HIGH = bullish for the harvest BUYER
                cush = cushion_ratio(abs(s - spot), exp_mv)
                prem_rise = _premium_rise_prob(
                    cushion=cush, dist_pct=dist_pct, vix_chg_pct=d.get("vix_chg_pct", 0),
                    hh_mm=hh_mm, oi=oi, ltp=ltp, dte=dte, side=side,
                    spot_chg_pct=d.get("spot_chg_pct", 0), market_open=ms["market_open"],
                )
                candidates.append({
                    "strike": s,
                    "side": side,
                    "dist_pct": round(dist_pct, 2),
                    "ltp": ltp,
                    "oi": oi,
                    "volume": vol,
                    "score": score,
                    "ripeness": "🔥 HIGH" if score >= 5 else ("MED" if score >= 3 else "LOW"),
                    "rec_lots": qty_lots,
                    "rec_cost": int(cost),
                    "target_sell_12x": target_sell_limit,
                    "target_sell_8x": conservative_tp,
                    "payoff_12x": payoff_at_12x,
                    "payoff_8x": payoff_at_8x,
                    "premium_rise_prob": prem_rise,
                    "lot_size": lot,
                })
        # Sort by score desc, then by lower OI
        candidates.sort(key=lambda r: (-r["score"], r["oi"]))
        candidates = candidates[:20]   # top 20

        return {
            "instrument": instrument,
            "spot": spot,
            "expiry": d["expiry"],
            "dte": dte,
            "is_e0": is_e0,
            "vix": vix,
            "max_pain": d["max_pain"],
            "phase": phase,
            "phase_label": phase_label,
            "phase_action": phase_action,
            "current_time": now.strftime("%H:%M:%S"),
            "candidates": candidates,
            "total_candidates": len(candidates),
            "analysis_note": "Per backtest 014: 212 SENSEX manipulation spikes found in 1 yr. 75% of expiry days have ≥1 spike. 73% in 15:00-15:25 window. Sweet spot 4.5-6.0% OTM with OI <2L.",
            "lot_size": LOT_SIZE[instrument],
            "market": ms,
        }

    return cached(f"manip_{instrument}", 5, _build)


@app.get("/api/holidays")
def holidays():
    """Upcoming market holidays."""
    from lib.expiry_calendar import MARKET_HOLIDAYS
    today = datetime.now(IST).date()
    upcoming = sorted([(d, name) for d, name in MARKET_HOLIDAYS.items() if d >= today])[:5]
    return {"upcoming": [{"date": str(d), "name": name, "weekday": pd.Timestamp(d).day_name()} for d, name in upcoming]}


@app.get("/api/expiries")
def expiries():
    """Next weekly expiries."""
    from lib.expiry_calendar import NIFTY_WEEKLY_EXPIRIES, SENSEX_WEEKLY_EXPIRIES
    today = datetime.now(IST).date()
    nifty_next = [d for d in NIFTY_WEEKLY_EXPIRIES if d >= today][:3]
    sensex_next = [d for d in SENSEX_WEEKLY_EXPIRIES if d >= today][:3]
    return {
        "NIFTY": [{"date": str(d), "weekday": pd.Timestamp(d).day_name()} for d in nifty_next],
        "SENSEX": [{"date": str(d), "weekday": pd.Timestamp(d).day_name()} for d in sensex_next],
    }


# ── Pages ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request):
    # Home → the Overview dashboard (the main landing view)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/overview", status_code=307)


@app.get("/chain/{instrument}", response_class=HTMLResponse)
def page_chain(request: Request, instrument: str):
    return templates.TemplateResponse(request, "chain.html", {"instrument": instrument.upper()})


@app.get("/manipulation/{instrument}", response_class=HTMLResponse)
def page_manipulation(request: Request, instrument: str):
    return templates.TemplateResponse(request, "manipulation.html", {"instrument": instrument.upper()})


@app.get("/recommend/{instrument}", response_class=HTMLResponse)
def page_recommend(request: Request, instrument: str):
    """The new redesigned 3-tier strategy command center."""
    return templates.TemplateResponse(request, "recommend.html", {"instrument": instrument.upper()})


@app.get("/recommend", response_class=HTMLResponse)
def page_recommend_default(request: Request):
    return templates.TemplateResponse(request, "recommend.html", {"instrument": "SENSEX"})


# ─── PLAYBOOK + ALERTS (§9W framework) ──────────────────────────────────────────

@app.get("/playbook", response_class=HTMLResponse)
def page_playbook(request: Request):
    """§9W tier 2/3 playbook with live regime check + trigger calculator."""
    return templates.TemplateResponse(request, "playbook.html", {"active_nav": "playbook"})


@app.get("/alerts", response_class=HTMLResponse)
def page_alerts(request: Request):
    """Live alerts dashboard + Telegram config + position triggers."""
    return templates.TemplateResponse(request, "alerts.html", {"active_nav": "alerts"})


# ── Unified strategy desks (one journal, one generic UI) ─────────────────────
# Each new tab is a "desk": positions (from the shared journal, filtered by
# strategy_group) + a new-position form + an optional analyzer. Existing expiry
# tabs are untouched.
STRATEGY_DESKS = {
    "/expiry/desk": {"nav": "expiry_desk", "group": "Expiry", "sub": "Expiry Desk (new)",
        "title": "Expiry — Deep OTM (unified shell)", "icon": "", "kind": "option_sell", "s_code": "S4",
        "blurb": "Weekly deep-OTM index option selling, in the unified Plan / Monitor / Report shell. (The existing Expiry pages stay as-is — this is the new format to compare.)",
        "instruments": ["NIFTY", "SENSEX", "BANKNIFTY"], "default_legs": ["PE", "CE"],
        "default_otm": 2.0, "default_step": 50, "default_lot": 75},
    "/index/monthly": {"nav": "idx_monthly", "group": "Index", "sub": "Monthly OTM",
        "title": "Monthly OTM Index", "icon": "", "kind": "option_sell", "s_code": "S3",
        "blurb": "Sell monthly OTM index options at safe levels (1–2.5% target). Strangles or single-leg.",
        "instruments": ["NIFTY", "SENSEX", "BANKNIFTY"], "default_legs": ["PE", "CE"],
        "default_otm": 2.0, "default_step": 50, "default_lot": 75},
    "/index/long": {"nav": "idx_long", "group": "Index", "sub": "Long NIFTY",
        "title": "Long NIFTY", "icon": "", "kind": "option_sell", "s_code": "S6",
        "blurb": "6-month / 1-year index calls & puts — strangles, or single leg when the opportunity is rich.",
        "instruments": ["NIFTY", "SENSEX"], "default_legs": ["PE", "CE"],
        "default_otm": 5.0, "default_step": 50, "default_lot": 75},
    "/cc/otm": {"nav": "cc_otm", "group": "Covered Calls", "sub": "Regular OTM",
        "title": "Covered Calls — Regular OTM (buy-write)", "icon": "", "kind": "covered_call",
        "cc_mode": "regular_otm", "s_code": "S2A",
        "blurb": "Buy futures, sell OTM call — gain on the call premium AND the future if it rises.",
        "instruments": [], "default_legs": ["FUT", "CE"],
        "default_otm": 6.0, "default_step": 100, "default_lot": 1},
    "/cc/itm": {"nav": "cc_itm", "group": "Covered Calls", "sub": "ITM theta",
        "title": "Covered Calls — ITM (theta harvest)", "icon": "", "kind": "covered_call",
        "cc_mode": "itm_theta", "s_code": "S2B",
        "blurb": "Buy futures, sell ITM call to harvest time value as an income asset.",
        "instruments": [], "default_legs": ["FUT", "CE"],
        "default_otm": -3.0, "default_step": 100, "default_lot": 1},
    "/commodity": {"nav": "commodity", "group": "Commodity", "sub": "Commodity",
        "title": "Commodity (S7)", "icon": "", "kind": "option_sell", "s_code": "S7",
        "blurb": "Commodity F&O — Gold, Silver, Crude etc. Positions, P&L, new entry.",
        "instruments": ["GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"], "default_legs": ["FUT"],
        "default_otm": 3.0, "default_step": 50, "default_lot": 1},
}

def _make_desk(cfg):
    async def _page(request: Request):
        return templates.TemplateResponse(request, "strategy_desk.html", {**cfg, "active_nav": cfg["nav"]})
    return _page

for _path, _cfg in STRATEGY_DESKS.items():
    app.add_api_route(_path, _make_desk(_cfg), response_class=HTMLResponse, methods=["GET"])


@app.get("/dummy", response_class=HTMLResponse)
def page_dummy(request: Request):
    return templates.TemplateResponse(request, "dummy.html", {"active_nav": "dummy"})


@app.get("/overview", response_class=HTMLResponse)
def page_overview(request: Request):
    return templates.TemplateResponse(request, "overview.html", {"active_nav": "overview"})


@app.get("/holdings", response_class=HTMLResponse)
def page_holdings(request: Request):
    return templates.TemplateResponse(request, "holdings_book.html", {"active_nav": "holdings"})


@app.get("/admin/users", response_class=HTMLResponse)
def page_admin_users(request: Request):
    # Middleware already 403s non-admins on /admin/*.
    return templates.TemplateResponse(request, "admin_users.html", {"active_nav": "admin_users"})


@app.get("/admin/demats", response_class=HTMLResponse)
def page_admin_demats(request: Request):
    return templates.TemplateResponse(request, "demat_setup.html", {"active_nav": "admin_demats"})


def _require_admin(request: Request):
    """Credential management is admin-only (these routes aren't under /api/admin, so
    the middleware's admin gate doesn't cover them). Returns a 403 Response or None."""
    from lib import access as _access
    u = getattr(request.state, "user_obj", None)
    if not (u and _access.is_admin(u)):
        return JSONResponse({"error": "admin only"}, status_code=403)
    return None


@app.get("/api/demat-creds")
def api_demat_creds_list(request: Request):
    """Configured demats (passwords MASKED — never sent to the browser). Admin only."""
    if (g := _require_admin(request)): return g
    from lib import demat_creds as DC
    return {"demats": DC.list_demats()}


@app.post("/api/demat-creds")
async def api_demat_creds_set(request: Request):
    """Set up a demat once: contract-note password + broker + client. Stored locally
    on the host (0600), never in git/training (lib.demat_creds). Admin only."""
    if (g := _require_admin(request)): return g
    from lib import demat_creds as DC
    b = await request.json()
    if not (b.get("demat") or "").strip():
        return JSONResponse({"error": "demat code required"}, status_code=400)
    DC.set_demat(b.get("demat"), password=b.get("password"), broker=b.get("broker"), client=b.get("client"))
    return {"saved": True}


@app.delete("/api/demat-creds/{code}")
def api_demat_creds_delete(code: str, request: Request):
    if (g := _require_admin(request)): return g
    from lib import demat_creds as DC
    return {"deleted": DC.delete_demat(code)}


@app.get("/import", response_class=HTMLResponse)
def page_import(request: Request):
    return templates.TemplateResponse(request, "import_hub.html", {"active_nav": "import"})


@app.get("/builder", response_class=HTMLResponse)
def page_builder(request: Request):
    return templates.TemplateResponse(request, "builder.html", {"active_nav": "builder"})


@app.get("/expiry/timing", response_class=HTMLResponse)
def page_expiry_timing(request: Request):
    return templates.TemplateResponse(request, "expiry_timing.html", {"active_nav": "expiry_timing"})


@app.get("/research", response_class=HTMLResponse)
def page_research(request: Request):
    return templates.TemplateResponse(request, "research.html", {"active_nav": "research"})


@app.get("/backtest", response_class=HTMLResponse)
def page_backtest(request: Request):
    return templates.TemplateResponse(request, "backtest.html", {"active_nav": "backtest"})


@app.get("/api/backtest/findings")
def api_backtest_findings():
    """Surface the backtest library — every committed analysis's one-line finding from
    FINDINGS_LOG.md (the canonical research log). Read-only; the data/CSVs stay local."""
    from pathlib import Path as _P
    fp = _P(__file__).resolve().parent.parent / "FINDINGS_LOG.md"
    out = []
    if fp.exists():
        for raw in fp.read_text().splitlines():
            line = raw.strip()
            if not line.startswith("- "):
                continue
            parts = [p.strip() for p in line[2:].split(" · ")]
            if len(parts) < 2:
                continue
            date = parts[0]
            aid = parts[1]
            kind = "analysis"
            if "(LIVE)" in aid or "LIVE" in date.upper():
                kind = "live"
            elif "DECISION" in aid.upper() or "DECISION" in date.upper():
                kind = "decision"
            out.append({
                "date": date, "id": aid, "kind": kind,
                "title": parts[2] if len(parts) > 2 else "",
                "finding": parts[-1] if len(parts) > 2 else (parts[1] if len(parts) > 1 else ""),
                "full": " · ".join(parts[2:]) if len(parts) > 2 else "",
            })
    out.reverse()  # newest first
    return {"findings": out, "count": len(out)}


@app.get("/margin", response_class=HTMLResponse)
def page_margin(request: Request):
    return templates.TemplateResponse(request, "margin_ledger.html", {"active_nav": "margin"})


@app.get("/api/margin-ledger")
def api_margin_ledger(as_of: str = "", broker: str = "", demat: str = ""):
    """Demat-wise margin & ledger monitor (from the M-sheets): margin used vs available
    vs collateral (pledge/GSEC/FD/cash) vs ledger, shortfall/excess, expiry-day usage.
    `as_of` (YYYY-MM-DD) → position as of that date (latest row on/before it) so you can
    see a previous day. broker/demat → filter."""
    from lib import full_report as fr
    from lib.expiry_calendar import is_e0
    from lib import demat_creds as DC
    from datetime import date as _date
    d = fr.load_report()
    bank = d.get("bank_reco", {})
    # demat → broker map (from the per-demat setup), so the broker filter works
    broker_of = {(c.get("demat") or "").upper(): (c.get("broker") or "") for c in DC.list_demats()}
    entities = []
    dates_seen = set()
    totals = {"margin_used": 0.0, "available": 0.0, "ledger": 0.0, "pledge": 0.0,
              "gsec": 0.0, "fd": 0.0, "cash_blocked": 0.0, "margin_fut": 0.0, "margin_opt": 0.0}

    def _mu(r):
        return r.get("margin_total") or ((r.get("margin_fut") or 0) + (r.get("margin_opt") or 0))

    for entity, rows in bank.items():
        ent_broker = broker_of.get(entity.upper(), "")
        if broker and ent_broker.lower() != broker.lower():
            continue
        if demat and entity.upper() != demat.upper():
            continue
        pop = [r for r in rows if (r.get("margin_total") or r.get("available") or r.get("ledger")
                                   or r.get("margin_fut") or r.get("margin_opt"))]
        for r in pop:
            if r.get("date"):
                dates_seen.add(r["date"])
        if not pop:
            continue
        # as-of: latest row on/before the chosen date (else the latest)
        sel = [r for r in pop if (not as_of or (r.get("date") or "") <= as_of)]
        if not sel:
            continue
        latest = sel[-1]
        mu = _mu(latest)
        av = latest.get("available") or 0
        led = latest.get("ledger") or 0
        pledge = latest.get("pledge") or 0
        gsec = latest.get("gsec") or 0
        fd = latest.get("fd") or 0
        cash_blocked = latest.get("cash_blocked") or 0
        has_avail = bool(latest.get("available"))
        headroom = round(av - mu) if has_avail else None
        series = []
        for r in pop[-12:]:
            e0 = False
            try:
                dd = _date.fromisoformat(r.get("date"))
                e0 = is_e0(dd, "NIFTY") or is_e0(dd, "SENSEX")
            except Exception:
                pass
            rmu = _mu(r)
            hr = ((r.get("available") or 0) - rmu) if r.get("available") else None
            series.append({"date": r.get("date"), "margin": rmu or None,
                           "available": r.get("available"), "ledger": r.get("ledger"),
                           "pledge": r.get("pledge"), "headroom": round(hr) if hr is not None else None, "is_expiry": e0})
        entities.append({
            "entity": entity, "broker": ent_broker, "as_of": latest.get("date"),
            "margin_used": round(mu), "available": round(av), "ledger": round(led),
            "margin_fut": round(latest.get("margin_fut") or 0), "margin_opt": round(latest.get("margin_opt") or 0),
            "pledge": round(pledge), "gsec": round(gsec), "fd": round(fd), "cash_blocked": round(cash_blocked),
            "collateral": round(pledge + gsec + fd), "req_fo": round(latest.get("req_fo") or 0),
            "headroom": headroom,
            "status": "shortfall" if (headroom is not None and headroom < 0) else ("excess" if (headroom is not None and headroom > 5e6) else "ok"),
            "util_pct": round(mu / av * 100, 1) if av else None, "series": series,
        })
        totals["margin_used"] += mu; totals["available"] += av; totals["ledger"] += led
        totals["pledge"] += pledge; totals["gsec"] += gsec; totals["fd"] += fd
        totals["cash_blocked"] += cash_blocked
        totals["margin_fut"] += latest.get("margin_fut") or 0; totals["margin_opt"] += latest.get("margin_opt") or 0
    totals = {k: round(v) for k, v in totals.items()}
    totals["collateral"] = totals["pledge"] + totals["gsec"] + totals["fd"]
    totals["headroom"] = round(totals["available"] - totals["margin_used"])
    totals["util_pct"] = round(totals["margin_used"] / totals["available"] * 100, 1) if totals["available"] else None
    return {"entities": entities, "totals": totals, "as_of": as_of or (max(dates_seen) if dates_seen else None),
            "dates": sorted(dates_seen, reverse=True),
            "brokers": sorted({b for b in broker_of.values() if b}),
            "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/other/new", response_class=HTMLResponse)
def page_strategy_new(request: Request):
    return templates.TemplateResponse(request, "strategy_new.html", {"active_nav": "newstrat"})


@app.get("/other/desk/{slug}", response_class=HTMLResponse)
def page_custom_desk(request: Request, slug: str):
    from lib import custom_strategies as cs
    s = cs.get(slug)
    if not s:
        raise HTTPException(404, "strategy not found")
    cfg = {"nav": "newstrat", "group": s["name"], "sub": s["name"],
           "title": s["name"] + " (custom)", "icon": "", "kind": s.get("kind", "generic"),
           "cc_mode": "", "blurb": s.get("note") or "Custom strategy.",
           "instruments": s.get("instruments", []),
           "default_legs": ["PE", "CE"] if s.get("kind") == "option_sell" else (["FUT", "CE"] if s.get("kind") == "buy_write" else ["CE"])}
    return templates.TemplateResponse(request, "strategy_desk.html", {**cfg, "active_nav": "newstrat"})


@app.get("/api/strategy/custom")
def api_custom_list():
    from lib import custom_strategies as cs
    return {"strategies": cs.all_custom(), "kinds": cs.KINDS}


@app.post("/api/strategy/custom")
async def api_custom_create(request: Request):
    from lib import custom_strategies as cs
    b = await request.json()
    if not b.get("name"):
        return JSONResponse({"error": "name required"}, status_code=400)
    rec = cs.create(b["name"], b.get("kind", "generic"), b.get("instruments", ""), b.get("note", ""))
    return {"saved": True, "strategy": rec}


@app.post("/api/strategy/custom/delete")
async def api_custom_delete(request: Request):
    from lib import custom_strategies as cs
    b = await request.json()
    cs.delete(b.get("slug", ""))
    return {"saved": True}


@app.get("/cc/investment", response_class=HTMLResponse)
def page_cc_investment(request: Request):
    return templates.TemplateResponse(request, "cc_investment.html", {"active_nav": "cc_inv"})


@app.get("/api/cc/holdings")
def api_cc_holdings():
    """Covered-call holdings + coverage (qty held vs CE sold) from the Selling Plan
    OVERLAID with manually-entered holdings (futures + equity qty per underlying),
    enriched with premium collected from the live journal (Covered Calls group)."""
    from lib import cc_holdings as CCH
    from lib import holdings as H
    from lib import journal
    hs = CCH.merged_holdings()
    # premium collected per underlying from journal CC trades
    prem_by_sym = {}
    for t in journal.all_trades():
        if (t.get("strategy_group") or "") != "Covered Calls":
            continue
        for l in t.get("legs", []):
            u = (l.get("underlying") or t.get("instrument") or "").upper()
            if (l.get("qty") or 0) < 0:
                prem_by_sym[u] = prem_by_sym.get(u, 0) + abs(l["qty"]) * (l.get("price") or 0)
    for h in hs:
        h["premium_collected"] = round(prem_by_sym.get(h["symbol"].upper(), 0)) or None
    return {"holdings": hs, "summary": H.portfolio_summary(hs),
            "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.post("/api/cc/holdings-manual")
async def api_cc_holdings_manual_save(request: Request):
    """Upsert a manual holding-for-investment entry (equity qty + futures qty +
    lot/price per underlying). Overlays the Excel; never writes the workbook."""
    from lib import cc_holdings as CCH
    b = await request.json()
    sym = (b.get("symbol") or "").strip()
    if not sym:
        return JSONResponse({"error": "symbol required"}, status_code=400)
    try:
        row = CCH.upsert(sym, **{k: b.get(k) for k in CCH.FIELDS})
        return {"saved": True, "row": row}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/cc/holdings-manual/{symbol}")
def api_cc_holdings_manual_delete(symbol: str):
    from lib import cc_holdings as CCH
    return {"deleted": CCH.delete(symbol)}


@app.post("/api/cc/holdings-upload")
async def api_cc_holdings_upload(request: Request):
    """Bulk-upload holdings-for-investment from CSV/XLSX. Columns (case-insensitive,
    fuzzy): symbol, equity_qty, equity_avg, futures_qty, futures_avg, lot, current,
    high52, ce_sold_qty. Upserts into the manual store (Sheet still overrides later)."""
    import io, pandas as pd
    from lib import cc_holdings as CCH
    form = await request.form()
    f = form.get("file")
    if f is None:
        return JSONResponse({"error": "no file"}, status_code=400)
    raw = await f.read()
    name = (getattr(f, "filename", "") or "").lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(raw))
        else:
            df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:
        return JSONResponse({"error": f"parse failed: {e}"}, status_code=400)

    def col(*names):
        for n in names:
            for c in df.columns:
                if str(c).strip().lower().replace(" ", "").replace("_", "") == n:
                    return c
        return None
    m = {"symbol": col("symbol", "stock", "scrip", "script", "underlying"),
         "equity_qty": col("equityqty", "equity", "eqqty"),
         "equity_avg": col("equityavg", "eqavg", "equitybuy", "buyrate"),
         "futures_qty": col("futuresqty", "futqty", "futures", "future"),
         "futures_avg": col("futuresavg", "futavg", "futuresbuy"),
         "lot": col("lot", "lotsize"), "current": col("current", "currentrate", "ltp", "cmp"),
         "high52": col("high52", "52wkhigh", "52whigh", "52weekhigh"),
         "ce_sold_qty": col("cesoldqty", "sold", "soldqty", "actualqtysold")}
    if not m["symbol"]:
        return JSONResponse({"error": "no symbol column found"}, status_code=400)
    n = 0
    for _, r in df.iterrows():
        sym = str(r.get(m["symbol"]) or "").strip()
        if not sym or sym.lower() in ("nan", "none"):
            continue
        fields = {}
        for k in CCH.FIELDS:
            if m.get(k) is not None:
                v = r.get(m[k])
                if pd.notna(v):
                    fields[k] = v
        try:
            CCH.upsert(sym, **fields)
            n += 1
        except Exception:
            pass
    return {"saved": n}


@app.get("/api/holdings/book")
def api_holdings_book(request: Request, client: str = "RHS"):
    """Holdings-with-strategy book for a client: per-symbol total + strategy/
    purpose/type splits. Each bucket = type×purpose×strategy×qty×avg."""
    from lib import holdings_book as HB
    from lib import access as _access
    u = getattr(request.state, "user_obj", None)
    # 'All' = union of clients the user may see; a specific client must be in scope.
    if client and client != "All" and not _access.allows(u, "clients", client):
        return JSONResponse({"error": "Forbidden: client not in your access"}, status_code=403)
    return HB.book(client)


@app.post("/api/holdings/book")
async def api_holdings_book_save(request: Request):
    """Upsert one (client, symbol) with its full bucket list."""
    from lib import holdings_book as HB
    b = await request.json()
    sym = (b.get("symbol") or "").strip()
    if not sym:
        return JSONResponse({"error": "symbol required"}, status_code=400)
    try:
        row = HB.upsert_holding(b.get("client") or "RHS", sym, b.get("buckets") or [],
                                lot=b.get("lot"), current=b.get("current"), high52=b.get("high52"),
                                assignment_price=b.get("assignment_price"), family=b.get("family"))
        return {"saved": True, "row": row}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/holdings/book/{client}/{symbol}")
def api_holdings_book_delete(client: str, symbol: str):
    from lib import holdings_book as HB
    return {"deleted": HB.delete_holding(client, symbol)}


@app.get("/api/clients")
def api_clients(request: Request):
    from lib import holdings_book as HB
    from lib import access as _access
    u = getattr(request.state, "user_obj", None)
    clients = _access.filter_values(u, "clients", HB.list_clients())
    default = clients[0] if clients else HB.DEFAULT_CLIENT
    return {"clients": clients, "default": default}


# ── Identity + access control ────────────────────────────────────────────
@app.get("/api/me")
def api_me(request: Request):
    """Who am I + what can I do — consumed by the UI to hide out-of-scope nav."""
    from lib import access as _access
    u = getattr(request.state, "user_obj", None) or _access.ADMIN
    return {
        "username": u.get("username"), "name": u.get("name"), "role": u.get("role"),
        "is_admin": _access.is_admin(u), "can_write": _access.can_write(u),
        "scopes": u.get("scopes"),
    }


def _list_brokers():
    try:
        return sorted((_broker_costs() or {}).keys())
    except Exception:
        return []


def _list_demats():
    """Distinct demat accounts seen in the holdings book (best-effort)."""
    try:
        from lib import holdings_book as HB
        ds = set()
        for r in HB._load():
            for b in (r.get("buckets") or []):
                d = b.get("demat") or b.get("demat_account")
                if d:
                    ds.add(d)
        return sorted(ds)
    except Exception:
        return []


@app.get("/api/admin/refdata")
def api_admin_refdata():
    """Dimension values to populate the access editor (admin-gated by middleware)."""
    from lib import access as _access
    from lib import holdings_book as HB
    return {
        "roles": list(_access.ROLES),
        "strategies": [{"key": k, "label": v} for k, v in _access.STRATEGIES],
        "clients": HB.list_clients(),
        "brokers": _list_brokers(),
        "demats": _list_demats(),
    }


@app.get("/api/admin/users")
def api_admin_users():
    from lib import access as _access
    return {"users": _access.list_users()}


@app.post("/api/admin/users")
async def api_admin_users_save(request: Request):
    from lib import access as _access
    b = await request.json()
    try:
        u = _access.upsert_user(
            username=b.get("username"), role=b.get("role") or "viewer",
            scopes=b.get("scopes") or {}, name=b.get("name"),
            password=(b.get("password") or None))
        return {"saved": True, "user": u}
    except Exception as e:
        return JSONResponse({"saved": False, "error": str(e)}, status_code=400)


@app.delete("/api/admin/users/{username}")
def api_admin_users_delete(username: str):
    from lib import access as _access
    return {"deleted": _access.delete_user(username)}


# ── Chat assistant (OpenRouter, free model) ──────────────────────────────
_OPENROUTER_CFG = Path.home() / ".config" / "openrouter.json"
_CHAT_SYSTEM = (
    "You are the assistant inside Theta Quant, a trading-operations dashboard for an "
    "Indian options-selling desk (NIFTY/SENSEX weekly deep-OTM option selling, covered "
    "calls, index and commodity strategies). Help the user understand and navigate the "
    "tool and answer general markets/trading questions concisely.\n"
    "Tabs: Overview (dashboard + holdings & coverage), Holdings (equity+futures split by "
    "strategy, covered vs uncovered, invested/current value/unrealised, assignment 'let-go' "
    "price), Expiry desk, Index (Monthly OTM / Long NIFTY), Covered Calls (S1 against "
    "investment, S2A regular OTM, S2B ITM theta), Execution (dummy trades + alerts), "
    "Reporting & Margin, Import (data templates), Users & Access.\n"
    "Be brief and practical. Do NOT give personalized buy/sell/hold advice or specific price "
    "targets — you are not a licensed advisor; if asked, say so and keep to general education. "
    "Never invent the user's live numbers; if asked for live data, point them to the right tab."
)


_ANTHROPIC_CFG = Path.home() / ".config" / "anthropic.json"


def _openrouter_cfg():
    import os as _os
    key = _os.environ.get("OPENROUTER_API_KEY")
    model = _os.environ.get("OPENROUTER_MODEL")
    if (not key) and _OPENROUTER_CFG.exists():
        try:
            c = json.loads(_OPENROUTER_CFG.read_text())
            key = key or c.get("api_key"); model = model or c.get("model")
        except Exception:
            pass
    return key, (model or "meta-llama/llama-3.3-70b-instruct:free")


def _anthropic_cfg():
    import os as _os
    key = _os.environ.get("ANTHROPIC_API_KEY")
    model = _os.environ.get("ANTHROPIC_MODEL")
    if (not key) and _ANTHROPIC_CFG.exists():
        try:
            c = json.loads(_ANTHROPIC_CFG.read_text())
            key = key or c.get("api_key"); model = model or c.get("model")
        except Exception:
            pass
    # Haiku = cheap + fast, ideal for a basic helper bot
    return key, (model or "claude-haiku-4-5-20251001")


def _chat_anthropic(key, model, msgs):
    import urllib.request as _u, urllib.error as _ue
    payload = {"model": model, "max_tokens": 700, "system": _CHAT_SYSTEM, "messages": msgs}
    req = _u.Request("https://api.anthropic.com/v1/messages",
                     data=json.dumps(payload).encode(),
                     headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                              "content-type": "application/json"})
    try:
        with _u.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
        parts = [b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text"]
        return {"reply": ("".join(parts).strip() or "(no response)"), "model": model, "provider": "anthropic"}
    except _ue.HTTPError as e:
        try: detail = e.read().decode()[:300]
        except Exception: detail = ""
        return JSONResponse({"error": f"Claude {e.code}. {detail}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"Chat failed: {e}"}, status_code=502)


def _chat_openrouter(key, model, msgs):
    import urllib.request as _u, urllib.error as _ue
    payload = {"model": model, "max_tokens": 700, "temperature": 0.3,
               "messages": [{"role": "system", "content": _CHAT_SYSTEM}] + msgs}
    req = _u.Request("https://openrouter.ai/api/v1/chat/completions",
                     data=json.dumps(payload).encode(),
                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                              "HTTP-Referer": "https://theta-quant.local", "X-Title": "Theta Quant"})
    try:
        with _u.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
        reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        return {"reply": reply or "(no response)", "model": model, "provider": "openrouter"}
    except _ue.HTTPError as e:
        try: detail = e.read().decode()[:300]
        except Exception: detail = ""
        return JSONResponse({"error": f"OpenRouter {e.code}. {detail}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"Chat failed: {e}"}, status_code=502)


@app.post("/api/chat")
async def api_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    msgs = [{"role": m.get("role"), "content": str(m.get("content", ""))[:2000]}
            for m in (body.get("messages") or []) if m.get("role") in ("user", "assistant")][-10:]
    if not msgs:
        return JSONResponse({"error": "no message"}, status_code=400)
    # Provider priority: Claude (Anthropic) now → OpenRouter later. First configured wins.
    akey, amodel = _anthropic_cfg()
    if akey:
        return _chat_anthropic(akey, amodel, msgs)
    okey, omodel = _openrouter_cfg()
    if okey:
        return _chat_openrouter(okey, omodel, msgs)
    return {"reply": "Chat isn't connected yet. On the host, add a key in ~/.config — either "
                     "anthropic.json {\"api_key\": \"sk-ant-...\"} to use Claude now, or "
                     "openrouter.json {\"api_key\": \"sk-or-...\"} for a free model — then reload.",
            "unconfigured": True}


# ── Import hub: templates + uploads ──────────────────────────────────────
@app.get("/api/import/types")
def api_import_types():
    from lib import import_io as IO
    out = []
    for key, t in IO.TYPES.items():
        out.append({"key": key, "title": t["title"], "group": t["group"],
                    "target": t["target"], "desc": t["desc"],
                    "columns": [c[0] for c in t["columns"]]})
    return {"types": out}


@app.get("/api/import/template/{key}")
def api_import_template(key: str):
    from lib import import_io as IO
    if key not in IO.TYPES:
        return JSONResponse({"error": "unknown type"}, status_code=404)
    csv_text = IO.template_csv(key)
    return _Response(content=csv_text, media_type="text/csv",
                     headers={"Content-Disposition": f'attachment; filename="theta_import_{key}.csv"'})


@app.get("/api/import/reporting-template")
def api_reporting_template():
    """The master reporting workbook as a format REFERENCE — the live workbook with the
    'Info' sheet (Kite secrets) stripped, regenerated fresh each download so secrets can
    never leak. Same structure as what fills the whole tool."""
    from fastapi.responses import FileResponse
    from lib import full_report as fr
    import openpyxl, tempfile, os
    src = fr.WORKBOOK
    if not src.exists():
        return JSONResponse({"error": "master workbook not found"}, status_code=404)
    wb = openpyxl.load_workbook(src, read_only=False, keep_vba=False)
    for sh in list(wb.sheetnames):
        if sh.strip().lower() in ("info",):       # never expose the Kite-secrets sheet
            del wb[sh]
    tmp = os.path.join(tempfile.gettempdir(), "Theta Quant_Reporting_Format_Reference.xlsx")
    wb.save(tmp)
    return FileResponse(tmp, filename="Theta Quant_Reporting_Format_Reference.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/import/master-workbook")
async def api_import_master_workbook(request: Request):
    """Ingest a monthly MASTER workbook (per-strategy S1..S7 sheets) into the journal.
    Form: file, exclude_expiry ('1' default — S4 corrected separately), purge_months
    (comma-sep 'YYYY-MM' to replace live entries for those months)."""
    from lib import master_import as MW
    import tempfile, os as _os
    form = await request.form()
    f = form.get("file")
    if f is None:
        return JSONResponse({"error": "no file"}, status_code=400)
    exclude_expiry = str(form.get("exclude_expiry", "1")).strip() not in ("0", "false", "no", "")
    purge = [m.strip() for m in str(form.get("purge_months", "")).split(",") if m.strip()]
    raw = await f.read()
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    try:
        tmp.write(raw); tmp.close()
        res = MW.ingest_master_workbook(tmp.name, exclude_expiry=exclude_expiry,
                                        purge_months=purge or None)
        _cache.clear()
        return {"ok": "error" not in res, **res}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        try: _os.unlink(tmp.name)
        except Exception: pass


@app.post("/api/import/{key}")
async def api_import_upload(key: str, request: Request):
    from lib import import_io as IO
    if key not in IO.TYPES:
        return JSONResponse({"error": "unknown type"}, status_code=404)
    form = await request.form()
    f = form.get("file")
    if f is None:
        return JSONResponse({"error": "no file"}, status_code=400)
    raw = await f.read()
    try:
        df = IO.read_table(raw, getattr(f, "filename", "") or "")
    except Exception as e:
        return JSONResponse({"error": f"parse failed: {e}"}, status_code=400)
    try:
        res = IO.ingest(key, df)
        _cache.clear()
        return {"ok": True, **res}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/underlying/{symbol}")
def api_underlying(symbol: str):
    """The whole picture for one underlying: equity + futures held, and every option/
    future leg grouped by the strategy it sits under (with strike & qty). Pulls option
    legs from the master workbook (where the per-strike CC data lives) + holdings."""
    from lib import full_report as fr, cc_holdings as CCH
    symu = (symbol or "").upper()
    d = fr.load_report()
    groups = {}
    for r in d.get("trades", []):
        if (r.get("symbol") or "").upper() != symu:
            continue
        key = r.get("strategy") or r.get("s_code") or "?"
        g = groups.setdefault(key, {"strategy": key, "s_code": r.get("s_code"),
                                    "group": r.get("strategy_group"), "legs": []})
        g["legs"].append({
            "type": (r.get("type") or "").upper(), "strike": r.get("strike"),
            "qty": r.get("sell_qty"), "sell_price": r.get("sell_price"),
            "buy_price": r.get("buy_price"), "demat": r.get("demat") or "",
            "expiry": r.get("expiry") or "", "status": r.get("status") or "",
        })
    for g in groups.values():
        g["legs"].sort(key=lambda x: (x["type"], x.get("strike") or 0))
    h = next((x for x in CCH.merged_holdings() if (x.get("symbol") or "").upper() == symu), {})
    return {"symbol": symu, "name": h.get("name") or symu, "current": h.get("current"),
            "equity_qty": h.get("equity_qty"), "equity_avg": h.get("equity_avg"),
            "futures_qty": h.get("futures_qty"), "futures_avg": h.get("futures_avg"),
            "ce_sold_qty": h.get("ce_sold_qty"),
            "strategies": sorted(groups.values(), key=lambda g: (g.get("s_code") or "z"))}


@app.get("/api/cc/consolidated")
def api_cc_consolidated():
    """Per-underlying held-vs-sold (CE + PE separate) + assignment-aware P&L
    (effective strike from rolls; net P&L vs original & month-start notional)."""
    from lib import cc_assignment as CA
    return {**CA.consolidated(), "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/research/news")
def api_research_news(symbol: str = "", n: int = 10):
    """Free news (Kite-independent). symbol → per-stock; else market headlines + risk brief."""
    from lib import news as N
    if symbol:
        return {"symbol": symbol.upper(), "items": N.stock_news(symbol, max_items=n)}
    return {"items": N.google_news("nifty OR sensex OR RBI OR 'india stock market'", max_items=n),
            "brief": N.risk_brief()}


@app.get("/api/research/screen")
def api_research_screen(leg: str = "CE"):
    """Own-compute consensus screener over the stock universe (holdings ∪ watchlist):
    our technical verdict + momentum + use-case tag + per-symbol launchers (lib.research)."""
    from lib import research as R
    return {"rows": R.screen(leg=(leg or "CE").upper()), "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/research/watchlist")
def api_research_watchlist_get():
    from lib import research as R
    return {"watchlist": R._load_watchlist(), "universe": R.universe()}


@app.post("/api/research/watchlist")
async def api_research_watchlist_add(request: Request):
    from lib import research as R
    b = await request.json()
    return {"watchlist": R.add_watch((b.get("symbol") or "").strip())}


@app.delete("/api/research/watchlist/{symbol}")
def api_research_watchlist_del(symbol: str):
    from lib import research as R
    return {"watchlist": R.remove_watch(symbol)}


@app.post("/api/approvals")
async def api_approvals_add(request: Request):
    """Approve a suggested strike with a chosen limit → adds to the daily order list
    (front of the trade lifecycle, lib.approvals)."""
    from lib import approvals as AP
    b = await request.json()
    if not (b.get("symbol") and b.get("strike")):
        return JSONResponse({"error": "symbol and strike required"}, status_code=400)
    return {"saved": True, "order": AP.add(b)}


@app.get("/api/approvals")
def api_approvals_list(date: str = "", status: str = "", month: str = "", whatsapp: bool = False):
    from lib import approvals as AP
    rows = AP.list_orders(date=date or None, status=status or None, month=month or None)
    out = {"orders": rows, "count": len(rows), "today": AP._now().strftime("%Y-%m-%d")}
    if whatsapp:
        out["whatsapp"] = AP.whatsapp_text(rows)
    return out


@app.get("/api/approvals/whatsapp")
def api_approvals_whatsapp(date: str = ""):
    from lib import approvals as AP
    rows = AP.list_orders(date=date or AP._now().strftime("%Y-%m-%d"))
    return {"text": AP.whatsapp_text(rows), "count": len(rows)}


@app.post("/api/approvals/{oid}/claim")
async def api_approvals_claim(oid: str, request: Request):
    """Team/broker reports back: taken@price (or not taken). Layer-A operational data —
    the contract-note reco confirms it later."""
    from lib import approvals as AP
    b = await request.json()
    AP.set_claimed(oid, price=b.get("price"), taken=bool(b.get("taken", True)))
    return {"saved": True}


@app.delete("/api/approvals/{oid}")
def api_approvals_delete(oid: str):
    from lib import approvals as AP
    AP.delete(oid)
    return {"deleted": True}


@app.get("/api/cc/eligibility/{symbol}")
def api_cc_eligibility(symbol: str, leg: str = "CE"):
    """Technical-eligibility verdict for selling a {leg} on this underlying:
    GREEN/YELLOW/RED/REJECT + RSI/MACD/trend/breakout/gap snapshot (lib.cc_signals,
    ported from the Covered Call Analyzer). Needs a live Kite session for candles."""
    from lib import cc_signals as CS
    return CS.verdict(symbol, leg=(leg or "CE").upper())


@app.get("/api/cc/eligibility-scan")
def api_cc_eligibility_scan(leg: str = "CE"):
    """Run the eligibility verdict across all held CC underlyings in one shot —
    parallel (daily candles cached) so a desk-wide scan is a few seconds."""
    from lib import cc_signals as CS, cc_holdings as CCH
    from concurrent.futures import ThreadPoolExecutor, as_completed
    legu = (leg or "CE").upper()
    syms = [h["symbol"] for h in CCH.merged_holdings() if h.get("symbol")]
    out = {}
    if not syms:
        return {"leg": legu, "results": out}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(CS.verdict, s, legu): s for s in syms}
        for f in as_completed(futs):
            s = futs[f]
            try:
                out[s.upper()] = f.result()
            except Exception as e:
                out[s.upper()] = {"symbol": s.upper(), "verdict": "UNKNOWN", "reasons": [str(e)[:80]]}
    return {"leg": legu, "results": out, "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/cc/roll/{symbol}")
def api_cc_roll(symbol: str, strike: float, qty: float, premium: float,
                spot: float = 0, expiry: str = "", lot: int = 1, iv: float = 0):
    """Roll decision card for one threatened short call: cost-to-close + three roll
    candidates (up / out / up-and-out) + hold-and-watch. IV backed out of the live
    premium (lib.cc_roll). If spot omitted, pulls the live snapshot."""
    from lib import cc_roll as RC
    s = spot
    if not s:
        try:
            from lib import cc_signals as CS
            s = (CS.snapshot(symbol) or {}).get("spot") or 0
        except Exception:
            s = 0
    return RC.roll_candidates(symbol, strike=strike, qty=qty, current_premium=premium,
                              spot=s, expiry=expiry, lot=lot, iv=(iv or None))


@app.post("/api/cc/notional/capture")
def api_cc_notional_capture(overwrite: bool = False):
    """Snapshot month-start notional prices from Kite for all holding symbols
    (run on the 1st trading day; idempotent unless overwrite)."""
    from lib import cc_notional as NOT, cc_holdings as CCH
    syms = [h["symbol"] for h in CCH.merged_holdings() if h.get("symbol")]
    return NOT.capture_from_kite(syms, overwrite=overwrite)


@app.post("/api/cc/notional/set")
async def api_cc_notional_set(request: Request):
    """Manual override of one underlying's month-start notional price."""
    from lib import cc_notional as NOT
    b = await request.json()
    sym, price = (b.get("symbol") or "").strip(), b.get("price")
    if not sym or price in (None, ""):
        return JSONResponse({"error": "symbol and price required"}, status_code=400)
    NOT.set_price(sym, float(price))
    return {"saved": True, "symbol": sym.upper(), "price": float(price)}


@app.get("/api/cc/stockwise")
def api_cc_stockwise():
    """Per-underlying covered-call view: held qty (futures) vs CE/PE sold, options
    net P&L, margin, % return — from the Stockwise sheet. The real CC P&L."""
    from lib import holdings as H
    sw = H.load_stockwise()
    tot = {"net_pnl": round(sum(x.get("net_pnl") or 0 for x in sw)),
           "margin": round(sum(x.get("margin") or 0 for x in sw)),
           "ce_sold": round(sum(x.get("ce_sold_qty") or 0 for x in sw)),
           "pe_sold": round(sum(x.get("pe_sold_qty") or 0 for x in sw)), "n": len(sw)}
    return {"stockwise": sw, "totals": tot, "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/futures/m2m")
def api_futures_m2m():
    """Investment futures (NOT assigned monthly) with clean M2M vs original buy."""
    from lib import holdings as H
    fm = H.load_futures_m2m()
    tot = {"m2m_total": round(sum(f.get("m2m_total") or 0 for f in fm)),
           "invested": round(sum(f.get("buy_total") or 0 for f in fm)),
           "daily_m2m": round(sum(f.get("daily_m2m") or 0 for f in fm)),
           "monthly_m2m": round(sum(f.get("monthly_m2m") or 0 for f in fm)),
           "margin": round(sum(f.get("margin") or 0 for f in fm)), "n": len(fm)}
    return {"futures": fm, "totals": tot, "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/cc/monitor")
def api_cc_monitor(s_code: str = "S1"):
    """Per-underlying covered-call monitoring straight from the master Excel sheet
    (S1 = Against Investment, S2A/S2B = regular/ITM, S3 = index …). Each sold option
    with its sold premium + current LTP + spot (all from the sheet) → RGB status:
    premium ≥ sold = orange / ≥2× = red; spot within 5%/3% of strike = orange/red;
    ≥80% captured = take-profit. Grouped by underlying for the dropdown."""
    from lib import full_report as fr, holdings as H, covered_call as cc
    spot_by = {}
    for h in H.load_holdings():
        if h.get("current"):
            spot_by[h["symbol"].upper()] = h["current"]
    d = fr.load_report()
    groups = {}
    for r in d.get("trades", []):
        if s_code and r.get("s_code") != s_code:
            continue
        side = (r.get("type") or "").upper()
        if side not in ("CE", "PE"):
            continue
        qty = r.get("sell_qty") or 0
        if qty == 0:
            continue
        sym = (r.get("symbol") or "").upper()
        strike = r.get("strike")
        if not sym or strike is None:
            continue
        spot = r.get("cur_price") or spot_by.get(sym)
        sold_prem = r.get("sell_price")
        cur_prem = r.get("ltp")
        st = cc.cc_monitor_status(spot, strike, side, sold_prem, cur_prem)
        g = groups.setdefault(sym, {"underlying": sym, "spot": spot, "legs": [], "worst": "GREEN", "expiries": {}})
        if not g["spot"] and spot:
            g["spot"] = spot
        exp = r.get("expiry") or ""
        g["legs"].append({
            "strike": strike, "side": side, "qty": qty,
            "sold_premium": sold_prem, "current_premium": cur_prem,
            "expiry": exp, "expiry_key": r.get("expiry_key") or "",
            "demat": r.get("demat") or "", "strategy": r.get("strategy") or "",
            "pnl": r.get("net_amount"), **st})
        rank = {"GREEN": 0, "ORANGE": 1, "RED": 2}
        if rank[st["level"]] > rank[g["worst"]]:
            g["worst"] = st["level"]
        # per-expiry rollup so 2-month positions are monitored separately
        e = g["expiries"].setdefault(exp or "—", {"expiry": exp or "—",
            "expiry_key": r.get("expiry_key") or "", "n": 0, "worst": "GREEN"})
        e["n"] += 1
        if rank[st["level"]] > rank[e["worst"]]:
            e["worst"] = st["level"]
    for g in groups.values():
        g["legs"].sort(key=lambda l: (l.get("expiry_key") or "zzz", l.get("strike") or 0))
        g["expiries"] = sorted(g["expiries"].values(), key=lambda x: x.get("expiry_key") or "zzz")
        g["multi_expiry"] = len(g["expiries"]) > 1
    out = sorted(groups.values(), key=lambda x: {"RED": 0, "ORANGE": 1, "GREEN": 2}[x["worst"]])
    summary = {"red": sum(1 for g in out if g["worst"] == "RED"),
               "orange": sum(1 for g in out if g["worst"] == "ORANGE"),
               "green": sum(1 for g in out if g["worst"] == "GREEN"), "n": len(out),
               "multi_expiry": sum(1 for g in out if g.get("multi_expiry"))}
    return {"underlyings": out, "summary": summary, "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/cc/suggestions")
def api_cc_suggestions(mode: str = "against_investment"):
    """Selling-plan suggestions for every holding with uncovered qty: DEEP/MID
    strikes sized to cover the uncovered, margin, eligibility, monthly-yield floor.
    Sorted by most uncovered. The analyzer's selling-plan, live on our holdings."""
    from lib import cc_holdings as CCH, covered_call as cc
    out = []
    for h in CCH.merged_holdings():
        unc = h.get("uncovered_qty") or 0
        if unc <= 0 or not h.get("current") or not h.get("lot"):
            continue
        s = cc.suggest_levels(h["current"], h.get("high52"), h["lot"], unc, mode)
        out.append({"symbol": h["symbol"], "name": h.get("name"),
                    "coverage_pct": h.get("coverage_pct"), **s})
    out.sort(key=lambda x: -(x.get("uncovered_qty") or 0))
    return {"suggestions": out, "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/cc/levels")
def api_cc_levels(current: float, high52: float = 0, lot: float = 1):
    """Suggested CE selling levels (DEEP 10% / MID 6%) with margin estimate + an
    eligibility read from distance-off-52w-high — ported from the analyzer."""
    from lib import covered_call as cc
    off_high = round((current - high52) / high52 * 100, 1) if high52 else None
    out = []
    for bucket, dist in (("DEEP", 10.0), ("MID", 6.0)):
        strike = round(current * (1 + dist / 100) / 5) * 5
        margin = strike * lot * 0.18
        # simple eligibility: near 52w-high → risky for CE selling
        snap = {"pct_off_high": off_high, "delta": 0.10 if bucket == "DEEP" else 0.22}
        elig = cc.cc_eligibility(snap, "CE")
        out.append({"bucket": bucket, "distance_pct": dist, "strike": strike,
                    "margin_per_lot": round(margin), "eligibility": elig["verdict"],
                    "score": elig["score"], "reasons": elig["reasons"]})
    return {"current": current, "high52": high52, "pct_off_high": off_high, "levels": out}


def _dummy_ltp(instrument, strike, side):
    """Live option LTP for the dummy monitor (from the shared chain cache)."""
    if not kite_alive():
        return None
    try:
        rows = {r["strike"]: r for r in chain(instrument).get("rows", [])}
        row = rows.get(int(strike))
        return row.get("pe_ltp" if side == "PE" else "ce_ltp") if row else None
    except Exception:
        return None


def _enrich_dummy(s):
    """Attach live premium + computed yield/distance for display."""
    prem = {"ce": None, "pe": None, "combined": None}
    if s.get("ce_strike"):
        prem["ce"] = _dummy_ltp(s["instrument"], s["ce_strike"], "CE")
    if s.get("pe_strike"):
        prem["pe"] = _dummy_ltp(s["instrument"], s["pe_strike"], "PE")
    if prem["ce"] is not None or prem["pe"] is not None:
        prem["combined"] = round((prem["ce"] or 0) + (prem["pe"] or 0), 2)
    s = dict(s)
    s["live"] = prem
    if prem["combined"]:
        from lib import dummy as _d
        s["live_yield_per_cr"] = _d.yield_per_cr(prem["combined"], s["instrument"])
    # entry target as premium for display
    from lib import dummy as _d
    thr = s.get("combined_threshold")
    if thr is None and s.get("yield_per_cr"):
        thr = _d.premium_for_yield(s["yield_per_cr"], s["instrument"])
    s["entry_target_combined"] = thr
    return s


@app.get("/api/dummy/list")
def api_dummy_list():
    from lib import dummy
    return {"strategies": [_enrich_dummy(s) for s in dummy.all_strategies()],
            "market": _market_state(),
            "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/dummy/preview")
def api_dummy_preview(instrument: str, distance_pct: float = 2.0, mode: str = "strangle",
                      ce_distance_pct: float = None, pe_distance_pct: float = None):
    """Live strike + premium + yield preview for the criteria builder. CE and PE
    distances can differ (e.g. CE 3% / PE 2%)."""
    from lib import dummy
    inst = instrument.upper()
    try:
        ch = chain(inst)
        spot = ch.get("spot")
        rows = {r["strike"]: r for r in ch.get("rows", [])}
    except Exception as e:
        return {"error": str(e)}
    st = dummy.grid_strikes(inst, spot, distance_pct, mode,
                            ce_distance_pct=ce_distance_pct, pe_distance_pct=pe_distance_pct)
    ce = (rows.get(st["ce_strike"]) or {}).get("ce_ltp") if st["ce_strike"] else None
    pe = (rows.get(st["pe_strike"]) or {}).get("pe_ltp") if st["pe_strike"] else None
    combined = round((ce or 0) + (pe or 0), 2)
    return {"instrument": inst, "spot": spot, **st, "ce_premium": ce, "pe_premium": pe,
            "combined": combined, "yield_per_cr": dummy.yield_per_cr(combined, inst),
            "lot": dummy.LOT.get(inst), "margin_per_lot": dummy.MARGIN_PER_LOT.get(inst)}


@app.post("/api/dummy/create")
async def api_dummy_create(request: Request):
    from lib import dummy
    b = await request.json()
    if not b.get("instrument"):
        return JSONResponse({"error": "instrument required"}, status_code=400)
    # resolve strikes from distance if not given (CE/PE distances can differ)
    def _f(v):
        try: return float(v)
        except (TypeError, ValueError): return None
    if not (b.get("ce_strike") or b.get("pe_strike")) and (b.get("distance_pct") is not None or b.get("ce_distance_pct") is not None or b.get("pe_distance_pct") is not None):
        try:
            spot = chain(b["instrument"]).get("spot")
            b.update(dummy.grid_strikes(b["instrument"], spot, _f(b.get("distance_pct")), b.get("mode", "strangle"),
                                        ce_distance_pct=_f(b.get("ce_distance_pct")), pe_distance_pct=_f(b.get("pe_distance_pct"))))
        except Exception:
            pass
    rec = dummy.create(b)
    return {"saved": True, "strategy": rec}


@app.get("/api/dummy/presets")
def api_dummy_presets():
    from lib import dummy
    return {"presets": dummy.list_presets()}


@app.post("/api/dummy/preset")
async def api_dummy_preset_save(request: Request):
    from lib import dummy
    b = await request.json()
    return {"saved": True, "preset": dummy.save_preset(b)}


@app.delete("/api/dummy/preset/{pid}")
def api_dummy_preset_delete(pid: str):
    from lib import dummy
    return {"deleted": dummy.delete_preset(pid)}


@app.post("/api/dummy/arm-preset/{pid}")
def api_dummy_arm_preset(pid: str):
    """Arm a live dummy strategy from a saved preset — resolves strikes off the
    current spot (so the same preset re-arms cleanly each expiry, E-1 or E-0)."""
    from lib import dummy
    p = dummy.get_preset(pid)
    if not p:
        return JSONResponse({"error": "preset not found"}, status_code=404)
    cfg = dict(p)
    cfg.pop("id", None)
    try:
        spot = chain(cfg["instrument"]).get("spot")
        st = dummy.grid_strikes(cfg["instrument"], spot, cfg.get("distance_pct"),
                                cfg.get("mode", "strangle"),
                                ce_distance_pct=cfg.get("ce_distance_pct"),
                                pe_distance_pct=cfg.get("pe_distance_pct"))
        cfg.update(st)
    except Exception as e:
        return JSONResponse({"error": f"could not resolve strikes (kite?): {e}"}, status_code=400)
    return {"saved": True, "strategy": dummy.create(cfg)}


# ── Dummy scheduler: auto-arm at start time, auto-stop at stop time ───────
_SCHED_EVENTS = []   # last ~50 events (auto-start/stop/error) for UI popups


def _push_sched_event(ev):
    ev = {**ev, "at": datetime.now(IST).strftime("%H:%M:%S"), "ts": time_mod.time()}
    _SCHED_EVENTS.append(ev)
    del _SCHED_EVENTS[:-50]
    try:                                   # best-effort Telegram (bot owns the token)
        from scripts.telegram_bot import tg_send  # type: ignore
        tg_send(ev.get("msg", ""))
    except Exception:
        pass


@app.post("/api/dummy/schedule/{pid}")
async def api_dummy_schedule(pid: str, request: Request):
    from lib import dummy
    b = await request.json()
    r = dummy.set_schedule(pid, start=b.get("sched_start"), stop=b.get("sched_stop"),
                           enabled=b.get("sched_enabled"))
    if not r:
        return JSONResponse({"error": "preset not found"}, status_code=404)
    return {"saved": True, "preset": r}


@app.get("/api/dummy/events")
def api_dummy_events(since: float = 0):
    """Recent scheduler events (auto-start/stop/error) for browser popups."""
    return {"events": [e for e in _SCHED_EVENTS if e["ts"] > since], "now": time_mod.time()}


@app.get("/api/fills/scored")
def api_fills_scored(limit: int = 200):
    """Learning loop closed — each actual fill scored vs the day's premium path:
    % of peak captured + minutes sold before the peak (lib.fill_learning)."""
    from lib import fill_learning as FL
    return FL.score_fills(limit=limit)


@app.get("/api/dummy/fills")
def api_dummy_fills(limit: int = 200):
    """Fill-timing learning feed — every fill's premium + exact IST time + strikes.
    'this premium got sold at this time' → consumed by analyses/900_learning_loop to
    compare actual sell-timing vs the backtested optimum (027/028 premium-timing leak)."""
    from pathlib import Path as _P
    fp = _P(__file__).resolve().parent.parent / "data" / "fill_timing.jsonl"
    out = []
    if fp.exists():
        for line in fp.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    out.reverse()
    return {"fills": out[:limit], "count": len(out)}


def _dummy_scheduler_tick():
    from lib import dummy
    now = datetime.now(IST); hhmm = now.strftime("%H:%M"); today = now.strftime("%Y-%m-%d")
    # auto-STOP (square off scheduled strategies whose stop time has passed)
    for s in dummy.strategies_due_to_stop(hhmm):
        try:
            dummy.cancel(s["id"])
            _push_sched_event({"type": "auto_stop", "name": s.get("name"),
                               "msg": f"⏹ Scheduled stop — {s.get('name')} squared off at {hhmm}"})
        except Exception:
            pass
    # auto-START only when the market is open
    if not _market_state().get("market_open"):
        return
    for p in dummy.presets_due_to_start(hhmm, today):
        dummy.set_runtime(p["id"], sched_last_armed=today)   # mark first → never double-arm / retry-spam
        try:
            cfg = dict(p); cfg.pop("id", None); cfg["sched_preset"] = p["id"]
            spot = chain(cfg["instrument"]).get("spot")
            st = dummy.grid_strikes(cfg["instrument"], spot, cfg.get("distance_pct"),
                                    cfg.get("mode", "strangle"),
                                    ce_distance_pct=cfg.get("ce_distance_pct"),
                                    pe_distance_pct=cfg.get("pe_distance_pct"))
            cfg.update(st)
            strat = dummy.create(cfg)
            _push_sched_event({"type": "auto_start", "name": p["name"], "id": strat["id"],
                               "msg": f"⏰ {p['name']} auto-started {hhmm} · CE {st.get('ce_strike')} / PE {st.get('pe_strike')}"})
        except Exception as e:
            _push_sched_event({"type": "error", "name": p["name"],
                               "msg": f"⚠️ {p['name']} could not auto-start at {hhmm}: {e}"})


@app.post("/api/dummy/exit-levels")
async def api_dummy_exit_levels(request: Request):
    """Set / change TP & SL on a strategy (before or after entry)."""
    from lib import dummy
    b = await request.json()
    if not b.get("id"):
        return JSONResponse({"error": "id required"}, status_code=400)
    dummy.set_exit_levels(b["id"], tp=b.get("tp"), sl=b.get("sl"))
    return {"saved": True}


@app.post("/api/dummy/mark")
async def api_dummy_mark(request: Request):
    """Manual lifecycle nudge: enter-now / close / cancel / delete."""
    from lib import dummy
    b = await request.json()
    sid = b.get("id"); act = b.get("action")
    if not sid:
        return JSONResponse({"error": "id required"}, status_code=400)
    if act == "cancel": dummy.cancel(sid)
    elif act == "delete": dummy.delete(sid)
    elif act == "close": dummy.update(sid, {"status": "CLOSED", "closed_at": datetime.now(IST).isoformat()})
    elif act == "enter":
        prem = {"ce": _dummy_ltp(b.get("instrument"), b.get("ce_strike"), "CE") if b.get("ce_strike") else 0,
                "pe": _dummy_ltp(b.get("instrument"), b.get("pe_strike"), "PE") if b.get("pe_strike") else 0}
        dummy.mark_entered(sid, prem["ce"] or 0, prem["pe"] or 0, round((prem["ce"] or 0)+(prem["pe"] or 0), 2))
    return {"saved": True}


@app.post("/api/dummy/check")
def api_dummy_check():
    """Monitor: advance ARMED→ENTERED and fire TP/SL on ENTERED strategies — ONLY
    while the market is open. When closed, do nothing (last-traded premiums must not
    auto-fill a trade). Returns events fired this tick."""
    from lib import dummy
    mkt = _market_state()
    if not mkt.get("market_open"):
        return {"fired": [], "market_closed": True, "market": mkt,
                "checked_at": datetime.now(IST).strftime("%H:%M:%S")}
    fired = dummy.check(_dummy_ltp)
    # TODO(telegram): for f in fired: notify(...)
    return {"fired": fired, "market": mkt, "checked_at": datetime.now(IST).strftime("%H:%M:%S")}


_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month_label(key):
    """'2026-05' → 'May-26' (always — never the raw key)."""
    try:
        y, m = key.split("-")
        return f"{_MONTH_ABBR[int(m) - 1]}-{y[2:]}"
    except Exception:
        return key


def _sane_month(key):
    """Keep only plausible expiry months (guards against mis-parsed option symbols
    like a strike fragment reading as a 2021 year)."""
    try:
        y = int(key.split("-")[0])
        return 2024 <= y <= 2028
    except Exception:
        return False


def _trade_expiry(t):
    """(label='May-26', key='2026-05') for a trade's expiry. From an option symbol if
    present; else nearest weekly expiry after entry (index); else entry month. This is
    how options/futures bucket 'expiry-wise' — a May expiry sold in April counts to May."""
    from lib.full_report import _parse_expiry
    for l in t.get("legs", []):
        os = l.get("option_symbol")
        if os:
            _, key = _parse_expiry(os)
            if key and _sane_month(key):
                return _month_label(key), key
    ed = t.get("entry_date") or ""
    inst = (t.get("instrument") or "").upper()
    if inst in ("NIFTY", "SENSEX", "BANKNIFTY") and ed:
        try:
            from lib.expiry_calendar import nearest_weekly_expiry_after
            from datetime import date as _date
            e = nearest_weekly_expiry_after(_date.fromisoformat(ed[:10]), inst)
            if e:
                key = e.strftime("%Y-%m")
                return _month_label(key), key
        except Exception:
            pass
    if ed and len(ed) >= 7 and _sane_month(ed[:7]):
        return _month_label(ed[:7]), ed[:7]
    return "", ""


@app.get("/api/expiry-months")
def api_expiry_months():
    """Distinct, sane, deduped expiry months across the journal (newest first) —
    populates the expiry picker. Labels are uniform ('May-26'), one per month."""
    from lib import journal
    keys = set()
    for t in journal.all_trades():
        _, key = _trade_expiry(t)
        if key and _sane_month(key):
            keys.add(key)
    months = [{"key": k, "label": _month_label(k)} for k in sorted(keys, reverse=True)]
    return {"months": months}


@app.get("/api/desk/positions")
def api_desk_positions(group: str = None, sub: str = None):
    """Positions for a strategy group/sub from the shared journal, with live P&L
    where a chain is available (index) and premium/margin rollups."""
    from lib import journal
    out = []
    chains = {}
    for t in journal.all_trades():
        if group and (t.get("strategy_group") or "Expiry") != group:
            continue
        if sub and t.get("tier") != sub and sub not in (t.get("strategy_name") or ""):
            pass  # sub is advisory; don't hard-filter (keeps it forgiving)
        legs_out = []
        unbooked = 0.0
        inst = t.get("instrument", "")
        for l in t.get("legs", []):
            leg = dict(l)
            ltp = l.get("ltp")
            if ltp is None and inst in ("NIFTY", "SENSEX", "BANKNIFTY") and kite_alive() and (l.get("qty") or 0) != 0:
                if inst not in chains:
                    try: chains[inst] = {r["strike"]: r for r in chain(inst).get("rows", [])}
                    except Exception: chains[inst] = {}
                row = chains[inst].get(l.get("strike"))
                if row:
                    ltp = row.get("pe_ltp" if l.get("side") == "PE" else "ce_ltp")
            leg["ltp"] = ltp
            if ltp is not None and l.get("price") is not None and l.get("qty"):
                q = l["qty"]
                leg["pnl"] = round((l["price"] - ltp) * abs(q) if q < 0 else (ltp - l["price"]) * q, 0)
                unbooked += leg["pnl"]
            legs_out.append(leg)
        booked = journal.booked_pnl(t)
        premium = sum(abs(l.get("qty") or 0) * (l.get("price") or 0)
                      for l in t.get("legs", []) if (l.get("qty") or 0) < 0)
        ex_lbl, ex_key = _trade_expiry(t)
        out.append({**t, "legs": legs_out, "unbooked_pnl": round(unbooked),
                    "booked_pnl": booked, "total_pnl": round(unbooked + booked),
                    "premium_collected": round(premium), "demat": (t.get("legs") or [{}])[0].get("demat") or "",
                    "expiry": ex_lbl, "expiry_month": ex_key})
    out.sort(key=lambda x: x.get("recorded_at", ""), reverse=True)
    totals = {"net": round(sum(t["total_pnl"] for t in out)),
              "premium": round(sum(t["premium_collected"] for t in out)), "n": len(out)}
    return {"group": group, "positions": out, "totals": totals,
            "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


@app.get("/api/covered-call/payoff")
def api_cc_payoff(fut_entry: float, strike: float, premium: float, spot: float,
                  lots: int = 1, lot_size: int = 1, mode: str = "regular_otm"):
    """Buy-write / ITM payoff + curve for the covered-call calculators."""
    from lib import covered_call as cc
    p = cc.payoff_buy_write(fut_entry, strike, premium, spot, lots, lot_size)
    p["curve"] = cc.payoff_curve(fut_entry, strike, premium, lots, lot_size, spot)
    p["config"] = cc.CONFIG.get(mode, {})
    return p


@app.get("/report-full", response_class=HTMLResponse)
def page_report_full(request: Request):
    """Unified Full Reporting — all strategies, bank reco, strategy notes."""
    return templates.TemplateResponse(request, "report_full.html", {"active_nav": "report_full"})


def _journal_strategies():
    """Each live journal trade → one strategy with premium, dual margins, P&L, yields."""
    from lib import journal
    out = []
    chains = {}
    _js_today = datetime.now(IST).strftime("%Y-%m-%d")
    for t in journal.all_trades():
        inst = t.get("instrument", "")
        legs = t.get("legs", [])
        booked = journal.booked_pnl(t)
        unbooked = 0.0
        leg_rows = []
        for l in legs:
            q = l.get("qty") or 0
            ltp = l.get("ltp")
            if ltp is None and q != 0 and inst in ("NIFTY", "SENSEX", "BANKNIFTY"):
                # PEEK the chain cache only — never trigger a (slow) live fetch here, or
                # the report blocks ~10s/instrument. Live pages keep the chain warm; the
                # report uses it when fresh, else shows booked P&L (MTM fills in later).
                if inst not in chains:
                    ck = _cache.get(f"chain_{inst}_5.0")
                    chains[inst] = ({r["strike"]: r for r in ck[1].get("rows", [])}
                                    if (ck and time_mod.time() - ck[0] < 60) else {})
                row = chains[inst].get(l.get("strike"))
                if row: ltp = row.get("pe_ltp" if l.get("side") == "PE" else "ce_ltp")
            # Past-expiry legs with no live quote settle at ₹0 (expired worthless) — the
            # realised outcome, not stale MTM. Mirrors the day-view (_expired_worthless)
            # so report-full/dashboard agree with it. Deep-OTM index only; ITM refinement
            # (intrinsic from parquet close) is a follow-up.
            if (ltp is None or ltp == 0) and q != 0 and _expired_worthless(t, _js_today):
                ltp = 0.0
            pnl = None
            if ltp is not None and l.get("price") is not None and q:
                pnl = round((l["price"] - ltp) * abs(q) if q < 0 else (ltp - l["price"]) * q)
                unbooked += pnl
            leg_rows.append({**l, "ltp": ltp, "pnl": pnl})
        # premium = OPTIONS only (short CE/PE). A short FUTURES leg's |qty|×price is
        # NOTIONAL, not premium — must never inflate premium/yield/max-profit (C1).
        premium = sum(abs(l.get("qty") or 0) * (l.get("price") or 0) for l in legs
                      if (l.get("qty") or 0) < 0 and (l.get("side") or l.get("leg_type") or "").upper() in ("CE", "PE"))
        # live margin: reconstruct entry position (open + booked) → SPAN approx
        recon = {}
        for l in legs:
            recon[(l.get("strike"), l.get("side"))] = recon.get((l.get("strike"), l.get("side")), 0) + (l.get("qty") or 0)
        for b in (t.get("booked_legs") or []):
            k = (b.get("strike"), b.get("side"))
            # H4: add the closed qty back in the position's ORIGINAL direction. Match the
            # still-open leg's sign if present (covered-call longs would be +); else short.
            sign = 1 if recon.get(k, -1) > 0 else -1
            recon[k] = recon.get(k, 0) + sign * abs(b.get("qty") or 0)
        entry_legs = [{"strike": s, "side": sd, "qty": q} for (s, sd), q in recon.items() if q]
        is_open = t.get("status") == "open"
        # live margin only for OPEN positions (closed = capital released)
        margin_live = (_strategy_margin(entry_legs, inst)
                       if (is_open and inst in ("NIFTY", "SENSEX", "BANKNIFTY")) else None)
        m_entry = t.get("margin_at_entry")
        # LIVE Kite margin — the REAL basket SPAN+exposure for this strategy's open legs.
        # Only for open, index, non-expired (skip settled positions → no wasted Kite call);
        # cached 60s in kite_live, so the report doesn't hang.
        margin_kite = None
        _has_short_opt = any((l.get("side") or l.get("leg_type") or "").upper() in ("CE", "PE")
                             and (l.get("qty") or 0) < 0 for l in legs)
        if (is_open and _has_short_opt and inst in ("NIFTY", "SENSEX", "BANKNIFTY")
                and kite_alive() and not _expired_worthless(t, _js_today)):
            try:
                from lib import kite_live as _kl
                margin_kite = _kl.basket_margin(entry_legs, inst)
            except Exception:
                margin_kite = None
        demats = sorted({(l.get("demat") or "") for l in legs} - {""})
        ex_lbl, ex_key = _trade_expiry(t)
        out.append({
            "id": t["id"], "source_kind": "live",
            # authoritative S-code from the sheet (Strategy Group Code) — beats name-matching
            "s_code": next((l.get("strategy_group_code") for l in legs if l.get("strategy_group_code")), ""),
            "sub_code": next((l.get("strategy_code") for l in legs if l.get("strategy_code")), ""),
            "strategy_group": t.get("strategy_group") or "Expiry",
            "strategy": t.get("strategy_name") or t.get("tier") or "?",
            "broker": t.get("broker", ""), "demat": (demats[0] if len(demats) == 1 else ("mixed" if demats else (t.get("demat") or ""))),
            "trader": t.get("trader", ""), "trade_date": t.get("entry_date", ""),
            "expiry": ex_lbl, "expiry_month": ex_key,
            "status": t.get("status", "open"), "instrument": inst, "n_legs": len([l for l in legs if (l.get("qty") or 0) != 0]),
            "premium": round(premium), "pnl": round(unbooked + booked),
            "margin_entry": m_entry, "margin_live": round(margin_live) if margin_live else None,
            "margin_kite": margin_kite,          # real live basket margin (open index only)
            "is_open": is_open,
            "max_profit": round(premium + booked) if t.get("status") == "open" else round(booked),
            "legs": leg_rows, "booked_legs": t.get("booked_legs") or [],
            "note": t.get("note", ""), "editable": True,
            "sl": t.get("sl"), "tp": t.get("tp"),
        })
    return out


def _excel_strategies(excel_legs):
    """Group the master-Excel legs into strategies (by sheet/strategy/broker/demat/date)."""
    groups = {}
    for r in excel_legs:
        # Investment-futures sheet's "Net Current Gain/Loss" is polluted (it nets the
        # deep-ITM calls written against holdings). Its real P&L = futures M2M, shown
        # separately. So don't let it pollute strategy P&L.
        if (r.get("strategy_group") or "") == "Investment":
            r = {**r, "net_amount": None, "notional": None}
        key = (r.get("sheet"), r.get("strategy"), r.get("broker"), r.get("demat"), r.get("trade_date"))
        g = groups.setdefault(key, {"legs": [], "premium": 0.0, "pnl": 0.0, "max_profit": 0.0,
                                    "margin_entry": None, "margin_live": None})
        # P&L = the book's own "Net Total Amount" (realised for closed legs; premium
        # collected for open legs). This matches how Rohan tracks it (and the journal
        # import). We do NOT use "Net Current Gain/Loss" (live MTM) as the headline —
        # for open deep-OTM shorts mid-cycle that shows a scary paper loss that decays
        # to profit by expiry. MTM is kept separately as `mtm`.
        typ = (r.get("type") or "").upper()
        sp, ltp, q = r.get("sell_price"), r.get("ltp"), r.get("sell_qty")
        bp = r.get("buy_price")
        if typ in ("CE", "PE"):
            # OPTIONS: "Net Total Amount" = realised P&L (closed) / premium collected (open).
            nt = r.get("notional")
            if nt is not None:
                leg_pnl = nt
            elif sp is not None and q:
                # bp present (incl. 0 = expired worthless) → realised; bp None → still open
                leg_pnl = (sp - bp) * abs(q) if bp is not None else abs(q) * sp
            else:
                leg_pnl = 0
        else:
            # FUTURES / EQUITY: "Net Total Amount" is the NOTIONAL value, not P&L. The
            # P&L is the M2M ("Net Current Gain/Loss"). Using notional here is what made
            # Covered Calls show a phantom −20Cr (it summed futures position values).
            leg_pnl = r.get("net_amount") or 0
        mtm = ((sp - ltp) * abs(q)) if (typ in ("CE", "PE") and sp is not None and ltp is not None and q) else None
        r = {**r, "computed_pnl": round(leg_pnl)}
        g["legs"].append(r)
        # PREMIUM is an OPTIONS concept — futures/equity "sell_total" is notional turnover,
        # NOT premium. Only CE/PE legs contribute (C1/H3).
        if typ in ("CE", "PE"):
            if r.get("sell_total"): g["premium"] += r["sell_total"]
            elif r.get("sell_price") and r.get("sell_qty"): g["premium"] += abs(r["sell_qty"]) * r["sell_price"]
        g["pnl"] += leg_pnl
        # max profit (OPTIONS only) = booked P&L of CLOSED legs + full premium of OPEN legs.
        # Closed = bp is not None (incl. 0 worthless); open = bp None. Never fold a futures
        # leg's M2M into max profit (its P&L already lives in g["pnl"]).
        if typ in ("CE", "PE") and sp is not None and q:
            g["max_profit"] += (leg_pnl if bp is not None else abs(q) * sp)
        for mk in ("margin_entry", "margin_live"):
            if r.get(mk) and not g[mk]: g[mk] = r[mk]
    out = []
    for (sheet, strat, broker, demat, date), g in groups.items():
        first = g["legs"][0]
        out.append({
            "id": "xl:" + str(abs(hash((sheet, strat, broker, demat, date))) % 10**10),
            "source_kind": "excel", "strategy_group": first.get("strategy_group") or "?",
            "strategy": strat or "?", "broker": broker or "", "demat": demat or "",
            "trader": first.get("trader", ""), "trade_date": date or "",
            "expiry": first.get("expiry") or _month_label(first.get("expiry_key") or ""),
            "expiry_month": (first.get("expiry_key") if _sane_month(first.get("expiry_key") or "") else ""),
            "status": first.get("status") or "", "instrument": first.get("symbol") or "",
            "n_legs": len(g["legs"]), "premium": round(g["premium"]) if g["premium"] else None,
            "pnl": round(g["pnl"]) if g["pnl"] else None,
            "margin_entry": round(g["margin_entry"]) if g["margin_entry"] else None,
            "margin_live": round(g["margin_live"]) if g["margin_live"] else None,
            "max_profit": round(g["max_profit"]) if g["max_profit"] else None,
            "legs": g["legs"], "note": first.get("note", ""), "editable": False,
        })
    return out


@app.get("/api/report-full")
def api_report_full():
    """Strategy-level P&L + dual-margin dashboard: live journal + master Excel."""
    from lib import full_report as fr
    d = fr.load_report()
    if d.get("error"):
        d = {"trades": [], "bank_reco": {}, "notes": [], "dropdowns": {}, "source": d.get("error")}
    # Double-count fence: once a month is uploaded as a consolidated sheet (journal
    # source=import), the master Excel is NO LONGER the trade source for that month —
    # exclude its legs so May isn't counted twice. Excel stays the source for any
    # month WITHOUT a consolidated upload, plus margin + holdings (separate endpoints).
    from lib import journal as _J
    import collections as _coll
    # Only fence a month that was GENUINELY uploaded as a consolidated sheet — i.e. has
    # a SUBSTANTIAL count of import trades (≥ _FENCE_MIN). A few stray import rows with
    # garbage/typo dates (e.g. 2024-04, 2026-12 from a messy sheet) must NOT fence the
    # Excel data for that whole month — that wiped many months from the report before.
    _FENCE_MIN = 20
    _imp_by_month = _coll.Counter((t.get("entry_date") or "")[:7] for t in _J.all_trades()
                                  if t.get("source") == "import")
    uploaded_months = {m for m, c in _imp_by_month.items() if m and c >= _FENCE_MIN}
    # Fence by trade_date AND expiry month: some master-Excel rows are aggregate/summary
    # rows with a blank trade_date (they'd otherwise slip past a trade_date-only fence and
    # double-count against the journal import for that month).
    excel_strats = [s for s in _excel_strategies(d["trades"])
                    if (s.get("trade_date") or "")[:7] not in uploaded_months
                    and (s.get("expiry_month") or "") not in uploaded_months]
    strategies = _journal_strategies() + excel_strats
    # yields per strategy
    for s in strategies:
        prem, me, ml = s.get("premium"), s.get("margin_entry"), s.get("margin_live")
        s["yield_entry_pct"] = round(prem / me * 100, 2) if (prem and me) else None    # original
        s["yield_live_pct"] = round(prem / ml * 100, 2) if (prem and ml) else None      # current
        s["per_cr"] = round(prem / (ml / 1e7)) if (prem and ml) else None

    def grp(field):
        out = {}
        for s in strategies:
            k = s.get(field) or "?"
            g = out.setdefault(k, {"n": 0, "pnl": 0.0, "premium": 0.0, "margin": 0.0})
            g["n"] += 1
            g["pnl"] += s.get("pnl") or 0
            g["premium"] += s.get("premium") or 0
            g["margin"] += s.get("margin_live") or 0
        for v in out.values():
            v["pnl"] = round(v["pnl"]); v["premium"] = round(v["premium"]); v["margin"] = round(v["margin"])
            v["per_cr"] = round(v["premium"] / (v["margin"] / 1e7)) if v["margin"] else None
        return out

    kpis = {
        "pnl": round(sum(s.get("pnl") or 0 for s in strategies)),
        "premium": round(sum(s.get("premium") or 0 for s in strategies)),
        "margin_live": round(sum(s.get("margin_live") or 0 for s in strategies)),
        "margin_entry": round(sum(s.get("margin_entry") or 0 for s in strategies)),
        "max_profit": round(sum(s.get("max_profit") or 0 for s in strategies)),
        "n_strategies": len(strategies), "n_open": len([s for s in strategies if (s.get("status") or "").lower().startswith("open")]),
    }
    kpis["per_cr"] = round(kpis["premium"] / (kpis["margin_live"] / 1e7)) if kpis["margin_live"] else None
    kpis["yield_live_pct"] = round(kpis["premium"] / kpis["margin_live"] * 100, 2) if kpis["margin_live"] else None

    # ── REAL deployed margin from the M-sheet ledger (authoritative) ──────────
    # _strategy_margin is a flat SPAN-per-lot estimate that overstates deep-OTM
    # margin ~2x. The broker ledger (bank_reco) has the real figure, split into
    # option vs futures margin. We (a) surface the real totals, and (b) scale each
    # OPEN strategy's option-margin estimate so per-strategy margin ties to reality.
    led_opt = led_fut = led_total = 0.0
    for _ent, _rows in (d.get("bank_reco") or {}).items():
        _pop = [r for r in _rows if (r.get("margin_total") or r.get("margin_opt")
                or r.get("margin_fut") or r.get("ledger") or r.get("available"))]
        if not _pop:
            continue
        _lr = _pop[-1]
        led_opt += _lr.get("margin_opt") or 0
        led_fut += _lr.get("margin_fut") or 0
        led_total += _lr.get("margin_total") or ((_lr.get("margin_opt") or 0) + (_lr.get("margin_fut") or 0))
    kpis["margin_ledger"] = round(led_total)
    kpis["margin_ledger_opt"] = round(led_opt)
    kpis["margin_ledger_fut"] = round(led_fut)
    # Scale open strategies' flat option-margin estimate to the ledger's real opt margin.
    _open = [s for s in strategies if (s.get("status") or "").lower().startswith("open")]
    _flat_open = sum(s.get("margin_live") or 0 for s in _open)
    _scale = (led_opt / _flat_open) if (led_opt and _flat_open) else None
    for s in strategies:
        if _scale is not None and s in _open and s.get("margin_live"):
            s["margin_real"] = round(s["margin_live"] * _scale)
        else:
            s["margin_real"] = s.get("margin_live")   # closed / sheet-real / no ledger → as-is
    kpis["margin_real"] = round(led_opt) if _scale is not None else kpis["margin_live"]
    kpis["margin_real_is_ledger"] = _scale is not None
    # yield/Cr = premium on the CURRENTLY-OPEN book ÷ real margin (apples-to-apples;
    # all-time premium ÷ current margin would be meaningless).
    kpis["premium_open"] = round(sum(s.get("premium") or 0 for s in _open))
    kpis["per_cr_real"] = round(kpis["premium_open"] / (kpis["margin_real"] / 1e7)) if kpis.get("margin_real") else None

    # investment futures M2M — captured SEPARATELY (clean, vs original buy)
    try:
        from lib import holdings as _H
        fm = _H.load_futures_m2m()
        kpis["futures_m2m"] = round(sum(f.get("m2m_total") or 0 for f in fm))
        kpis["futures_invested"] = round(sum(f.get("buy_total") or 0 for f in fm))
    except Exception:
        kpis["futures_m2m"] = None
    # Covered-call OFFSET book: the CC/S1 option legs in the trade sheet show losses
    # that are offset by the stock held (Holdings/Stockwise). Stockwise "net p/l" is
    # the options-NETTED per-underlying CC result → the offset-aware figure. Surfaced
    # so Full Reporting can split headline = trading vs CC offset book.
    try:
        sw = _H.load_stockwise()
        kpis["cc_net_offset"] = round(sum(x.get("net_pnl") or 0 for x in sw))
        kpis["cc_n_underlyings"] = len([x for x in sw if x.get("net_pnl") is not None])
    except Exception:
        kpis["cc_net_offset"] = None

    # bank reco: latest row per entity + period sums of funding/revenue/expense/net
    bank_summary = {}
    for entity, rows in d["bank_reco"].items():
        if not rows:
            continue
        # last row that actually has figures (sheets have trailing blank future rows)
        populated = [r for r in rows if r.get("margin_total") or r.get("ledger") or r.get("available")]
        latest = populated[-1] if populated else rows[-1]
        bank_summary[entity] = {
            "latest_date": latest["date"],
            "margin_total": latest.get("margin_total"),
            "available": latest.get("available"),
            "ledger": latest.get("ledger"),
            "funding_sum": round(sum(r.get("funding") or 0 for r in rows)),
            "revenue_sum": round(sum(r.get("revenue") or 0 for r in rows)),
            "expense_sum": round(sum(r.get("expense") or 0 for r in rows)),
            "net_sum": round(sum(r.get("net") or 0 for r in rows)),
            "rows": rows,
        }

    return {
        "strategies": strategies, "kpis": kpis,
        "by_group": grp("strategy_group"), "by_broker": grp("broker"), "by_demat": grp("demat"),
        "bank": bank_summary, "notes": d["notes"], "dropdowns": d["dropdowns"],
        "source": d.get("source"),
        "computed_at": datetime.now(IST).strftime("%H:%M:%S"),
    }


# ── Stock groups (Adani etc.) + per-strategy margin targets — dashboard config ──
_STRAT_TARGETS_FILE = ROOT / "data" / "strategy_targets.json"


@app.get("/api/stock-groups")
def api_stock_groups():
    from lib import stock_groups as SG
    return {"groups": SG.all_groups()}


@app.post("/api/stock-groups")
async def api_stock_groups_save(request: Request):
    from lib import stock_groups as SG
    b = await request.json()
    return {"saved": True, "groups": SG.save_groups(b.get("groups") or {})}


@app.get("/api/strategy-targets")
def api_strategy_targets():
    """Per-strategy margin targets (S-code → ₹). Blank until the user sets them."""
    try:
        return {"targets": json.loads(_STRAT_TARGETS_FILE.read_text())}
    except Exception:
        return {"targets": {}}


@app.post("/api/strategy-targets")
async def api_strategy_targets_save(request: Request):
    b = await request.json()
    cur = {}
    try:
        cur = json.loads(_STRAT_TARGETS_FILE.read_text())
    except Exception:
        pass
    # merge: {code: target_or_null}; null/blank removes the target
    for k, v in (b.get("targets") or {}).items():
        if v in (None, "", 0):
            cur.pop(k, None)
        else:
            try:
                cur[k] = float(v)
            except Exception:
                pass
    _STRAT_TARGETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STRAT_TARGETS_FILE.write_text(json.dumps(cur, indent=2))
    return {"saved": True, "targets": cur}


@app.get("/api/playbook/regime")
def api_playbook_regime():
    """Live regime check: gap, pre-range, VIX, tier qualification per instrument.

    Uses shared lib.playbook for all rule logic (same source as Telegram bot).
    """
    from lib import playbook as pb
    out: dict = {"computed_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")}
    if not kite_alive():
        return {**out, "error": "Kite session expired"}

    try:
        from lib.kite_live import _kite
        k = _kite()
        sn = k.quote(["NSE:NIFTY 50", "BSE:SENSEX", "NSE:INDIA VIX"])

        nifty_q = sn["NSE:NIFTY 50"]
        sensex_q = sn["BSE:SENSEX"]
        vix = sn["NSE:INDIA VIX"]["last_price"]

        # Compute pre-entry range (9:15 to NOW or 10:30, whichever earlier)
        from datetime import time as _t
        ist_now = datetime.now(IST)
        end_t = min(ist_now.time(), _t(10, 30))

        def pre_range_for(idx_name: str, ohlc: dict) -> float | None:
            """Best-effort: use ohlc.day_range as proxy if we can't pull intraday bars."""
            if not ohlc:
                return None
            h = ohlc.get("high"); l = ohlc.get("low"); o = ohlc.get("open")
            if not (h and l and o):
                return None
            return round((h - l) / o * 100, 2)

        nifty_oh = nifty_q.get("ohlc", {})
        sensex_oh = sensex_q.get("ohlc", {})
        nifty_range = pre_range_for("NIFTY", nifty_oh)
        sensex_range = pre_range_for("SENSEX", sensex_oh)

        # Gap %: open vs prev close
        def gap_pct(ohlc):
            o = ohlc.get("open"); c = ohlc.get("close")
            if not (o and c): return None
            return round((o - c) / c * 100, 2)
        nifty_gap = gap_pct(nifty_oh)
        sensex_gap = gap_pct(sensex_oh)

        # VIX context
        vix_rising = False  # would need prev-close VIX to compute; placeholder
        vix_high = vix > 19

        out["snapshot"] = {
            "ist_time": ist_now.strftime("%H:%M:%S"),
            "vix": round(vix, 2),
            "vix_status": "HIGH (>19)" if vix_high else ("OK" if vix < 17 else "Elevated 17-19"),
            "NIFTY": {
                "spot": nifty_q.get("last_price"),
                "gap_pct": nifty_gap,
                "pre_range_pct": nifty_range,
                "high": nifty_oh.get("high"),
                "low": nifty_oh.get("low"),
                "open": nifty_oh.get("open"),
            },
            "SENSEX": {
                "spot": sensex_q.get("last_price"),
                "gap_pct": sensex_gap,
                "pre_range_pct": sensex_range,
                "high": sensex_oh.get("high"),
                "low": sensex_oh.get("low"),
                "open": sensex_oh.get("open"),
            },
        }

        # Hand off to shared playbook module (single source of truth)
        snapshot_for_rules = {
            "vix": vix,
            "NIFTY": {"gap_pct": nifty_gap, "pre_range_pct": nifty_range},
            "SENSEX": {"gap_pct": sensex_gap, "pre_range_pct": sensex_range},
        }
        out["hard_exclusions"] = pb.hard_exclusions(snapshot_for_rules)
        out["only_tier_1"] = len(out["hard_exclusions"]) > 0
        # Tier 1 distance recommendation (per analysis 025)
        regime = pb.classify_regime(snapshot_for_rules)
        out["regime"] = regime
        out["tier1_recommendation"] = {
            "NIFTY": {"distance_pct": pb.tier1_distance(regime, "NIFTY"),
                      "expected_premium_per_cr": pb.tier1_expected_premium(regime, "NIFTY")},
            "SENSEX": {"distance_pct": pb.tier1_distance(regime, "SENSEX"),
                       "expected_premium_per_cr": pb.tier1_expected_premium(regime, "SENSEX")},
        }
        # Tier qualification (5 setups from STRATEGY_LIVE §9W)
        out["tiers"] = [
            {"tier": t["label"],
             "instrument": t["instrument"],
             "otm_pct": t["otm_pct"],
             "entry_time": t["entry_time"],
             "qualifies": t["qualifies"],
             "reason_passed": t["reason_passed"],
             "premium_floor_per_cr": t["premium_floor_per_cr"],
             "mean_pcr": t["mean_pcr"],
             "win_pct": t["win_pct"],
             "worst_pcr": t["worst_pcr"],
             "is_star": t.get("star", False)}
            for t in pb.qualifying_tiers(snapshot_for_rules)
            if t["tier"] == 3  # web only shows Tier 3 + STAR by default
        ] or [
            # If exclusions or no tier 3 — fall back to all
            {"tier": t["label"], "instrument": t["instrument"], "otm_pct": t["otm_pct"],
             "entry_time": t["entry_time"], "qualifies": t["qualifies"],
             "reason_passed": t["reason_passed"], "premium_floor_per_cr": t["premium_floor_per_cr"],
             "mean_pcr": t["mean_pcr"], "win_pct": t["win_pct"],
             "worst_pcr": t["worst_pcr"], "is_star": t.get("star", False)}
            for t in pb.qualifying_tiers(snapshot_for_rules)
        ]
    except Exception as e:
        out["error"] = str(e)
    return out


@app.get("/api/kicker/{instrument}")
def api_kicker(instrument: str):
    """Live Tier-4 KICKER card (analysis 034): at 12:00 on NIFTY E-0, strikes = 1.0× the
    range-so-far (clamped 0.3–1.5%), TP = buy back at 60% of entry (40% capture),
    SL = 2× entry at market, else carry to 15:20. SENSEX: skipped (negative in the
    latest quarter). Sizing per the book-level zero-loss standard: ₹1.5–2 Cr."""
    from lib import playbook as pb
    from lib.expiry_calendar import is_e0
    inst = instrument.upper()
    now = datetime.now(IST)
    out = {"instrument": inst, "now": now.strftime("%H:%M"), "recipe":
           "12:00 · strikes = 1.0× range-so-far (0.3–1.5%) · TP 40% capture · SL 2× (market) · else carry to 15:20 · size ₹1.5–2 Cr"}
    if inst == "SENSEX":
        return {**out, "verdict": "SKIP", "why": "All SENSEX kicker variants decayed to negative "
                "in the latest quarter (analysis 034). NIFTY only for now."}
    if not is_e0(now.date(), inst):
        return {**out, "is_e0": False, "verdict": "NOT TODAY",
                "why": f"Not a {inst} expiry day — the kicker is an E-0 trade."}
    out["is_e0"] = True
    if not kite_alive():
        return {**out, "verdict": "NO DATA", "why": "Kite session expired — log in for live strikes."}
    sn = snapshot()
    d = sn.get(inst, {})
    spot, rng = d.get("spot"), d.get("day_range_pct") or 0
    if not spot:
        return {**out, "verdict": "NO DATA", "why": "No live spot."}
    cfg = pb.INSTRUMENT_CFG[inst]
    dist = max(0.3, min(1.5, 1.0 * rng))
    pe_k = pb.nearest_strike(spot, dist, "PE", cfg["grid"])
    ce_k = pb.nearest_strike(spot, dist, "CE", cfg["grid"])
    rows = {r["strike"]: r for r in chain(inst).get("rows", [])}
    pe = (rows.get(pe_k) or {}).get("pe_ltp")
    ce = (rows.get(ce_k) or {}).get("ce_ltp")
    comb = round(pe + ce, 2) if (pe is not None and ce is not None) else None
    t = now.time()
    if t < time(12, 0):
        phase, verdict = "pre", f"WAIT — enter at 12:00 (range still forming: {rng}%)"
    elif t <= time(12, 20):
        phase, verdict = "window", "🟢 ENTER NOW — sell both strikes, place TP + SL immediately"
    elif t <= time(14, 30):
        phase, verdict = "late", "Late — backtest edge is the 12:00 entry; premium already decayed"
    else:
        phase, verdict = "over", "Window over for today"
    lot, lpc = cfg["lot"], cfg["lots_per_cr"]
    return {**out, "verdict": verdict, "phase": phase, "spot": spot, "range_pct": rng,
            "dist_pct": round(dist, 2), "pe_strike": pe_k, "ce_strike": ce_k,
            "pe_ltp": pe, "ce_ltp": ce, "entry_comb": comb,
            "tp_level": round(comb * 0.6, 2) if comb else None,
            "sl_level": round(comb * 2.0, 2) if comb else None,
            "capture_per_cr": round(comb * 0.4 * lot * lpc) if comb else None,
            "premium_per_cr": round(comb * lot * lpc) if comb else None,
            "backtest": {"mean_pcr": 24138, "win_pct": 84, "tp_hit_pct": 82,
                         "p5_pcr": -87169, "worst_pcr": -204196, "n": 56},
            "discipline": "TP hit → square off, no exceptions. SL 2× → out at market. "
                          "Worst days = calm noon then violent afternoon; the SL is the survival rule.",
            "sizing": "₹1.5–2 Cr — one bad kicker day must stay inside the deep-OTM book's same-expiry profit (book-level ~zero-loss standard)."}


@app.post("/api/playbook/triggers")
async def api_playbook_triggers(request: Request):
    """Given a position (instrument, entry spot, PE strike, CE strike, entry premiums),
    return exact Yellow/Red trigger spot levels.

    Body:
      {"instrument":"SENSEX", "entry_spot":75000, "pe_strike":74250, "ce_strike":75750,
       "pe_entry":14, "ce_entry":12, "pre_entry_high":75150, "pre_entry_low":74850}
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    inst = (body.get("instrument") or "").upper()
    entry_spot = float(body.get("entry_spot") or 0)
    pe_strike = float(body.get("pe_strike") or 0)
    ce_strike = float(body.get("ce_strike") or 0)
    pre_high = float(body.get("pre_entry_high") or 0)
    pre_low = float(body.get("pre_entry_low") or 0)
    pe_entry = float(body.get("pe_entry") or 0)
    ce_entry = float(body.get("ce_entry") or 0)

    if not (inst and entry_spot and pe_strike and ce_strike):
        return JSONResponse({"error": "Missing required fields"}, status_code=400)

    # Delegate to shared playbook
    from lib import playbook as pb
    core = pb.compute_triggers(inst, entry_spot, pe_strike, ce_strike,
                               pe_entry=pe_entry or None, ce_entry=ce_entry or None,
                               pre_entry_high=pre_high or None)
    # Wrap into web-UI shape with action strings
    triggers = {
        **core,
        "yellow_pe": {
            "spot_alert": core["yellow_pe_spot"],
            "secondary_check": f"30-min net move ≤ −{core['big_move_pts']} pts (= −0.4%)",
            "action": f"BUY BACK {pe_strike:.0f} PE only. Keep {ce_strike:.0f} CE running.",
        },
        "yellow_ce": {
            "spot_alert": core["yellow_ce_spot"],
            "secondary_check": f"30-min net move ≥ +{core['big_move_pts']} pts",
            "action": f"BUY BACK {ce_strike:.0f} CE only. Keep {pe_strike:.0f} PE running.",
        },
        "red_pe": {
            "spot_alert": core["red_pe_spot"],
            "action": "BUY BACK BOTH legs at market. No re-entry today.",
        },
        "red_ce": {
            "spot_alert": core["red_ce_spot"],
            "action": "BUY BACK BOTH legs at market. No re-entry today.",
        },
        "red_catastrophic": {
            "spot_alert_pe": pe_strike, "spot_alert_ce": ce_strike,
            "action": "Spot touched strike. Close all immediately.",
        },
    }
    if "profit_take_combined" in core:
        triggers["profit_take"] = {
            "combined_target": core["profit_take_combined"],
            "explanation": (f"At 30% of entry = ₹{core['profit_take_combined']} combined → "
                            f"take profit (locks {core['profit_take_decay_pct']}% decay)"),
        }
    return triggers


# ─── Telegram alerts ────────────────────────────────────────────────────────────

@app.get("/api/playbook/recommendations")
def api_playbook_recommendations(instrument: str = None):
    """Two strike options per tier with full desk metrics, E-0 playbook primary.

    ?instrument=NIFTY|SENSEX overrides; default = today's expiring index
    (or SENSEX preview on non-expiry days).
    """
    from lib import playbook as pb
    out = {"computed_at": datetime.now(IST).strftime("%H:%M:%S")}
    if not kite_alive():
        return {**out, "error": "Kite session expired"}

    expiring = pb.expiring_today()
    if instrument and instrument.upper() in ("NIFTY", "SENSEX"):
        inst = instrument.upper()
    else:
        inst = expiring[0] if expiring else "SENSEX"
    out["instrument"] = inst
    out["is_expiry_today"] = inst in expiring
    out["expiring_today"] = expiring
    # Day type label
    nx = pb.next_expiries().get(inst)
    out["days_to_expiry"] = nx[1] if nx else None
    out["next_expiry"] = nx[0].isoformat() if nx else None
    out["day_type"] = "E-0" if (nx and nx[1] == 0) else (f"E-{nx[1]}" if nx else "?")

    # Live snapshot for regime
    snap = snapshot()
    if "error" in snap:
        return {**out, "error": snap["error"]}
    regime = pb.classify_regime(snap)
    out["regime"] = regime
    d_inst = snap.get(inst, {})
    spot = d_inst.get("spot")
    rng = d_inst.get("day_range_pct") or 0
    out["spot"] = spot
    out["pre_range_pct"] = rng

    cfg = pb.INSTRUMENT_CFG[inst]
    grid, lot, lpc = cfg["grid"], cfg["lot"], cfg["lots_per_cr"]

    # Live chain
    ch = chain(inst)
    rows = {r["strike"]: r for r in ch.get("rows", [])}

    out["lot_size"] = lot
    out["lots_per_cr"] = lpc

    def option(dist, floor, reason, entry_time, exit_rule, pop, max_loss_note, label="",
               wait_advice="", max_premium_note=""):
        pe_k = pb.nearest_strike(spot, dist, "PE", grid)
        ce_k = pb.nearest_strike(spot, dist, "CE", grid)
        pe = rows.get(pe_k, {}).get("pe_ltp")
        ce = rows.get(ce_k, {}).get("ce_ltp")
        comb = (pe + ce) if (pe is not None and ce is not None) else None
        per_cr = comb * lot * lpc if comb else None
        # PoP — empirical, conditioned on the LIVE regime (analysis 031), not hardcoded.
        # Falls back to the passed estimate only if the backtest table has no data.
        pop_val, pop_meta = pop, None
        try:
            from lib import pop as _POP
            _r = _POP.conditional_pop(inst, dist, regime, basis="strangle")
            if _r:
                pop_val = _r["pop"]
                pop_meta = {"n": _r["n"], "low_sample": _r["low_sample"],
                            "regime": _r["regime_used"], "source": "backtest"}
        except Exception:
            pass
        return {"label": label, "dist_pct": dist, "pe_strike": pe_k, "ce_strike": ce_k,
                "pe_ltp": pe, "ce_ltp": ce,
                "combined": round(comb, 2) if comb else None,
                "per_cr": round(per_cr, 0) if per_cr else None,
                "floor_per_cr": floor,
                "floor_ok": (per_cr or 0) >= floor,
                "entry_time": entry_time, "exit_rule": exit_rule,
                "pop_pct": pop_val, "pop_meta": pop_meta, "max_loss_note": max_loss_note,
                "wait_advice": wait_advice, "max_premium_note": max_premium_note,
                "reason": reason}

    t1_d = pb.tier1_distance(regime, inst)
    tiers = [
        {"tier": "TIER 1 — Deep OTM", "book_pct": 75,
         "options": [
            option(t1_d, 4000,
                   f"Analysis 025: regime '{regime}' → {t1_d}% is the closest distance with 100% win + 0% ITM "
                   f"across 119 E-0 days. Maximum safe premium.",
                   pb.tier1_entry_time(regime, inst), "HOLD to 15:25", 100,
                   "Backtest worst day still POSITIVE at this distance", "Recommended",
                   wait_advice=pb.SPIKE_LIMIT_RULE,
                   max_premium_note=f"Backtest median at entry for '{regime}' regime: ₹{pb.tier1_expected_premium(regime, inst)/1000:.1f}K/Cr — premium peaks at open."),
            option(t1_d + 0.25, 4000,
                   f"+0.25% contingency. Same 100% win, ~20% less premium. Use when regime read is uncertain "
                   f"or news pending within 24h.",
                   "09:25-09:35", "HOLD to 15:25", 100,
                   "Backtest worst day POSITIVE", "Conservative",
                   wait_advice="Same window as Recommended — don't wait past 09:40.",
                   max_premium_note="~20% below the Recommended option's premium at any given time."),
         ]},
        {"tier": "TIER 2 — Mid OTM", "book_pct": 15,
         "options": [
            option(1.25, 15000,
                   f"§9W: {inst} 1.25% @ 10:00 needs pre-range ≤0.8% (now {rng}%) + ≥₹15K/Cr premium. "
                   f"Backtest mean +₹32K/Cr.",
                   "10:00", "HOLD to 15:25", 100,
                   "Backtest worst +₹400/Cr (still positive)", "Recommended",
                   wait_advice="CAN wait till ~10:30 with minor decay — but range must stay ≤0.8%. If range worsens, skip rather than chase.",
                   max_premium_note="Backtest median at 10:00 entry: ₹32K/Cr (SENSEX) / ₹21K (NIFTY 11:30)."),
            option(1.5, 12500,
                   f"Wider Tier 2, lower floor ₹12.5K/Cr — qualifies more often on low-VIX days. "
                   f"Backtest mean +₹30K/Cr.",
                   "10:00", "HOLD to 15:25", 100,
                   "Backtest worst +₹1K/Cr", "Conservative",
                   wait_advice="Most forgiving setup — waiting till 11:00 costs ~15% premium.",
                   max_premium_note="Backtest median: ₹30K/Cr (SENSEX 10:00)."),
         ]},
        {"tier": "TIER 3 — Near OTM", "book_pct": 8,
         "options": [
            option(1.0, 20000,
                   f"★ STAR. §9W: {inst} 1.0% @ 10:00 needs range ≤0.7% (now {rng}%) + ≥₹20K/Cr. "
                   f"Best risk/reward in the entire backtest.",
                   "10:00", "HOLD (PT if combined ≤30% of entry)", 100,
                   "Backtest worst day +₹20K/Cr (positive)", "★ Star",
                   wait_advice="Do NOT wait — window is 10:00-10:20 sharp after the range check. Late entry loses the edge.",
                   max_premium_note="Backtest median at 10:00: ₹47K/Cr. Premium decays ~₹3-5K/Cr per 30min after."),
            option(0.7, 30000,
                   f"Closer, later entry. §9W: 0.7% @ 11:30 needs range ≤0.8% + ≥₹30K/Cr. Needs midday "
                   f"calm confirmation; active management advised.",
                   "11:30", "HOLD or PT_60", 100,
                   "Backtest worst +₹4K/Cr; Yellow/Red discipline mandatory", "Aggressive",
                   wait_advice="MUST wait till 11:30 — morning entries at this distance are the danger zone (71-83% win pre-11:30).",
                   max_premium_note="Backtest median at 11:30: ₹42K/Cr."),
         ]},
    ]
    out["tiers"] = tiers
    out["notes"] = [
        "Premiums shown are LIVE and decay through the day — figures at your entry moment are what's capturable.",
        "PoP = backtest win rate at the stated filter conditions (analyses 018-025).",
        f"Sizing: {lot} units/lot, {lpc} lots/Cr margin. ₹/Cr = combined × {lot} × {lpc}.",
        "Yellow: spot eats 50% of buffer + 0.4% 30-min move → close that leg. Red: 85% buffer → close both.",
    ]
    return out


# ─── Trade Journal (learning substrate) ─────────────────────────────────────────

@app.post("/api/journal/trade")
async def api_journal_add(request: Request):
    """Register a trade with full entry context. Body:
    {instrument, tier, legs:[{strike,side,qty,price}], entry_time:'HH:MM',
     entry_date?, broker?, source?, note?}
    Regime snapshot is auto-captured live for the learning loop."""
    from lib import journal, playbook as pb
    try:
        b = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not (b.get("instrument") and b.get("legs")):
        return JSONResponse({"error": "instrument + legs required"}, status_code=400)
    regime_snap = {}
    try:
        if kite_alive():
            sn = snapshot()
            regime_snap = {
                "regime": pb.classify_regime(sn), "vix": sn.get("vix"),
                "spot": sn.get(b["instrument"].upper(), {}).get("spot"),
                "gap_pct": sn.get(b["instrument"].upper(), {}).get("gap_pct"),
                "range_pct": sn.get(b["instrument"].upper(), {}).get("day_range_pct"),
            }
    except Exception:
        pass
    rec = journal.add_trade(
        instrument=b["instrument"], tier=b.get("tier", "?"), legs=b["legs"],
        entry_time=b.get("entry_time"), entry_date=b.get("entry_date"),
        broker=b.get("broker", ""), source=b.get("source", "manual"),
        regime_snapshot=regime_snap, note=b.get("note", ""),
        strategy_name=b.get("strategy_name", ""), portfolio=b.get("portfolio", ""),
        strategy_group=b.get("strategy_group", "Expiry"),
        margin_at_entry=b.get("margin_at_entry"), demat=b.get("demat", ""),
        sl=b.get("sl"), tp=b.get("tp"))
    return {"saved": True, "trade": rec}


@app.post("/api/journal/close")
async def api_journal_close(request: Request):
    """Close a trade: {id, exit_time?, exit_date?, pnl?, exit_legs?, note?}"""
    from lib import journal
    try:
        b = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not b.get("id"):
        return JSONResponse({"error": "id required"}, status_code=400)
    rec = journal.close_trade(b["id"], exit_time=b.get("exit_time"),
                              exit_date=b.get("exit_date"), exit_legs=b.get("exit_legs"),
                              pnl=b.get("pnl"), note=b.get("note", ""))
    return {"saved": True, "exit": rec}


@app.get("/api/journal")
def api_journal_list(status: str = None):
    from lib import journal
    trades = journal.all_trades()
    if status:
        trades = [t for t in trades if t.get("status") == status]
    return {"trades": trades, "summary": journal.summary()}


@app.post("/api/journal/csv")
async def api_journal_csv(request: Request):
    """Bulk ingest day's trades from CSV text. Body: {csv_text, default_date?}
    Columns (header, case-insensitive): instrument,tier,strike,side,qty,price,
    entry_time,broker,note. One row per leg; rows with same (tier,broker,entry_time)
    group into one trade."""
    from lib import journal
    import csv as _csv
    from io import StringIO
    try:
        b = await request.json()
        text = b.get("csv_text") or ""
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    rows = list(_csv.DictReader(StringIO(text)))
    def g(r, k, d=""):
        for key in r:
            if key and key.strip().lower() == k: return (r[key] or "").strip()
        return d
    groups = {}
    errors = []
    for i, r in enumerate(rows, 2):
        try:
            key = (g(r,"instrument").upper(), g(r,"tier"), g(r,"broker"), g(r,"entry_time"))
            groups.setdefault(key, []).append({
                "strike": int(float(g(r,"strike"))), "side": g(r,"side").upper(),
                "qty": int(float(g(r,"qty"))), "price": float(g(r,"price"))})
        except Exception as e:
            errors.append({"row": i, "error": str(e)})
    saved = []
    for (inst, tier, broker, etime), legs in groups.items():
        if not inst: continue
        rec = journal.add_trade(instrument=inst, tier=tier or "?", legs=legs,
                                entry_time=etime or None,
                                entry_date=b.get("default_date"),
                                broker=broker, source="csv")
        saved.append(rec["id"])
    return {"saved": len(saved), "trade_ids": saved, "errors": errors}


def _pop_from_buffer(buffer_pct: float) -> int:
    """Live PoP estimate from worst-leg buffer % (analysis 018 ITM rates):
    0.5%→78, 0.7%→86, 1.0%→95, 1.5%→98, ≥2.0%→99+."""
    pts = [(0.0, 50), (0.3, 65), (0.5, 78), (0.7, 86), (1.0, 95), (1.5, 98), (2.0, 99), (3.0, 100)]
    if buffer_pct < 0: return 35           # ITM (negative buffer) — genuinely bad
    if buffer_pct == 0: return 50          # M2: ATM = the 0.0 knot, not a 15-pt cliff
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if buffer_pct <= x2:
            return int(y1 + (y2 - y1) * (buffer_pct - x1) / (x2 - x1))
    return 100


@app.post("/api/journal/screenshot")
async def api_journal_screenshot(request: Request):
    """Upload a Sensibull screenshot (multipart 'file') → OCR (macOS Vision) →
    parsed strategies returned for user confirmation. Save via /api/journal/trade."""
    from lib import ocr as _ocr
    form = await request.form()
    f = form.get("file")
    if f is None:
        return JSONResponse({"error": "no file"}, status_code=400)
    img = await f.read()
    text = _ocr.ocr_image_bytes(img)
    if not text.strip():
        return JSONResponse({"error": "OCR produced no text — try a sharper screenshot"}, status_code=422)
    parsed = _ocr.parse_sensibull(text)
    return {"parsed": parsed, "ocr_chars": len(text)}


@app.get("/api/playbook/next_action")
def api_playbook_next_action():
    """THE single most-important directive right now, from time-of-day × regime ×
    locked tables (025/§9W). Drives the hero card + first line of bot messages."""
    from lib import playbook as pb
    now = datetime.now(IST)
    t = now.time()
    out = {"computed_at": now.strftime("%H:%M:%S")}

    expiring = pb.expiring_today()
    if now.weekday() >= 5:
        return {**out, "headline": "Market closed (weekend)", "detail": "", "urgency": "idle"}
    if not expiring:
        nx = pb.next_expiries()
        soon = min(nx.values(), key=lambda v: v[1]) if nx else None
        inst_next = [k for k, v in nx.items() if v == soon][0] if soon else "?"
        if soon and soon[1] == 1:
            return {**out, "headline": f"E-1 day for {inst_next} — entry windows per regime",
                    "detail": "calm/normal: 09:20 @ 2.0% · moderate: NIFTY 10-11:00 / SENSEX 12:00 @ 2.0% · high-risk: 14:45 @ 3.5%",
                    "urgency": "active", "instrument": inst_next}
        return {**out, "headline": f"No expiry today — next: {inst_next} in {soon[1]}d" if soon else "No expiry data",
                "detail": "Observation day. Data auto-saves at 16:30.", "urgency": "idle"}

    inst = expiring[0]
    out["instrument"] = inst

    if not kite_alive():
        return {**out, "headline": f"{inst} E-0 today — Kite session expired",
                "detail": "Run ./morning.sh or paste login URL to Claude/bot.", "urgency": "warning"}

    sn = snapshot()
    regime = pb.classify_regime(sn)
    excl = pb.hard_exclusions(sn)
    out["regime"] = regime
    d1 = pb.tier1_distance(regime, inst)

    from datetime import time as _t
    if excl:
        return {**out, "headline": f"⛔ Hard exclusions ({len(excl)}) — TIER 1 ONLY today",
                "detail": "; ".join(excl) + f" · Tier 1 at {d1+0.25 if regime=='high_risk' else d1}% when calm window appears",
                "urgency": "danger"}
    if t < _t(9, 15):
        return {**out, "headline": f"{inst} E-0 — market opens 09:15",
                "detail": f"Plan: Tier 1 window 09:25-09:35 at {d1}% OTM. Star trade check at 10:00.", "urgency": "active"}
    if t < _t(9, 25):
        return {**out, "headline": "Observe the open — Tier 1 window starts 09:25",
                "detail": f"Regime so far: {regime}. Target {d1}% OTM both sides.", "urgency": "active"}
    if t < _t(9, 40):
        return {**out, "headline": f"🟢 TIER 1 ENTRY WINDOW NOW — {d1}% OTM {inst}",
                "detail": f"Regime {regime} · expected ₹{pb.tier1_expected_premium(regime, inst)/1000:.1f}K/Cr · HOLD to 15:25",
                "urgency": "act_now"}
    if t < _t(10, 0):
        return {**out, "headline": "Tier 1 window closing — Star trade check at 10:00",
                "detail": f"At 10:00: if pre-range ≤0.7% and premium ≥₹20K/Cr → SELL 1.0% OTM strangle (Tier 3 star).",
                "urgency": "active"}
    if t < _t(10, 20):
        rng = sn.get(inst, {}).get("day_range_pct") or 0
        ok = rng <= 0.7
        return {**out, "headline": ("⭐ STAR TRADE WINDOW — 1.0% OTM" if ok else "Star trade SKIPPED (range too high)"),
                "detail": f"Pre-range {rng}% {'≤' if ok else '>'} 0.7%" + (" · check premium ≥₹20K/Cr on desk below" if ok else " · fall back to Tier 2 at 10:00-10:30 if ≤0.8%"),
                "urgency": "act_now" if ok else "active"}
    if t < _t(11, 30):
        return {**out, "headline": "Mid-morning — Tier 2 window (1.25-1.5%) if range allows",
                "detail": "SENSEX 0.7% option at 11:30 needs range ≤0.8% + ₹30K/Cr.", "urgency": "active"}
    if t < _t(14, 0):
        return {**out, "headline": "Hold phase — premium chop is normal till ~12:30",
                "detail": "Don't manage on MTM. Yellow/Red triggers only. Volatile-recovery 0.5% window at 12:00 if morning was choppy (T_1400 exit).",
                "urgency": "hold"}
    if t < _t(15, 25):
        return {**out, "headline": "Final decay phase — let winners run to 15:25",
                "detail": "No new entries after 14:00 except planned exits. Day-end report at 15:30.", "urgency": "hold"}
    return {**out, "headline": "Market closed — log today's trades in Report",
            "detail": "Evening data save at 16:30. Learning loop runs with evening.sh.", "urgency": "idle"}


# ─── Reporting module (Sensibull-style day/strategy view) ──────────────────────

MARGIN_PER_LOT = {"NIFTY": 235_000, "SENSEX": 250_000, "BANKNIFTY": 250_000}

def _expired_worthless(t: dict, view_date: str) -> bool:
    """True once this strategy's options have expired — so a leg with no live LTP
    should read ₹0 (worthless), not '—'. An E-0 strategy expires the same day after
    close; any earlier expiry day is also done. Conservative: only fires on E-0
    trades whose date is past, or today after 15:30 IST."""
    from lib.expiry_calendar import is_e0
    try:
        ed = date.fromisoformat(t.get("entry_date"))
    except Exception:
        return False
    inst = t.get("instrument", "")
    try:
        e0 = is_e0(ed, inst)
    except ValueError:
        # non-index instrument (e.g. NIFTY50 mistag, covered-call equity, commodity)
        # has no weekly-expiry calendar → never auto-mark worthless.
        return False
    if not e0:                    # not an expiry-day strategy → don't assume worthless
        return False
    today = datetime.now(IST).date()
    if ed < today:
        return True
    if ed == today and datetime.now(IST).time() >= time(15, 30):
        return True
    return False


def _strategy_margin(legs: list, instrument: str, provided: float = None) -> float:
    """SPAN strangle-offset margin: max(CE lots, PE lots) x margin/lot.
    Uses sheet-provided figure when given."""
    if provided and provided > 0:
        return provided
    from lib import playbook as pb
    cfg = pb.INSTRUMENT_CFG.get(instrument)
    if not cfg or instrument not in MARGIN_PER_LOT:
        return None   # M4: don't fabricate NIFTY's lot/margin for an unknown instrument
    lot = cfg.get("lot", 75)
    ce_lots = sum(abs(l.get("qty") or 0) for l in legs if l.get("side") == "CE" and (l.get("qty") or 0) < 0) / lot
    pe_lots = sum(abs(l.get("qty") or 0) for l in legs if l.get("side") == "PE" and (l.get("qty") or 0) < 0) / lot
    return round(max(ce_lots, pe_lots) * MARGIN_PER_LOT.get(instrument, 235_000), 0)


@app.post("/api/journal/excel")
async def api_journal_excel(request: Request):
    """Upload broker-wise expiry report .xlsx (team format). Multipart 'file',
    optional 'dry_run'=1 to preview without saving.
    Groups rows by (Broker, Demat, Strategy, trade date) -> one journal strategy.
    Closed rows get exits recorded. Supports optional 'Entry Time' column."""
    from lib import journal
    import pandas as pd
    from io import BytesIO
    form = await request.form()
    f = form.get("file")
    dry = str(form.get("dry_run") or "") in ("1", "true")
    if f is None:
        return JSONResponse({"error": "no file"}, status_code=400)
    try:
        xl = pd.ExcelFile(BytesIO(await f.read()))
        frames = [pd.read_excel(xl, sheet_name=s) for s in xl.sheet_names]
        df = pd.concat(frames, ignore_index=True)
    except Exception as e:
        return JSONResponse({"error": f"excel parse failed: {e}"}, status_code=422)

    def col(*starts):
        for c_ in df.columns:
            if any(str(c_).strip().lower().startswith(s) for s in starts):
                return c_
        return None

    C = {k: col(*v) for k, v in {
        "broker": ("broker",), "demat": ("demat",), "status": ("status",),
        "strategy": ("strategy",), "dt": ("trade date",), "symbol": ("stock symbol",),
        "type": ("type",), "strike": ("strike",), "sell_price": ("sell price",),
        "sell_qty": ("sell qty",), "buy_price": ("buy price",),
        "margin": ("margin consume", "total margin"), "entry_time": ("entry time",),
        "notes": ("notes",)}.items()}
    if not (C["strike"] and C["type"] and C["sell_qty"]):
        return JSONResponse({"error": "unrecognized format — need Strike/Type/Sell Qty columns"}, status_code=422)

    groups = {}
    for _, r in df.iterrows():
        try:
            strike = r[C["strike"]]
            side = str(r[C["type"]]).strip().upper()
            if pd.isna(strike) or side not in ("CE", "PE"):
                continue
            qty = float(r[C["sell_qty"]] or 0)
            if qty <= 0:
                continue
            sym = str(r[C["symbol"]] or "").upper()
            inst = "NIFTY" if "NIFTY" in sym else ("SENSEX" if "SENSEX" in sym else None)
            if not inst:
                continue
            dt = pd.to_datetime(r[C["dt"]], dayfirst=True) if C["dt"] is not None and not pd.isna(r[C["dt"]]) else None
            edate = dt.strftime("%Y-%m-%d") if dt is not None else None
            etime = None
            if C["entry_time"] and not pd.isna(r.get(C["entry_time"])):
                etime = str(r[C["entry_time"]])[:5]
            elif dt is not None and (dt.hour or dt.minute):
                etime = dt.strftime("%H:%M")
            broker = str(r[C["broker"]] or "").strip() if C["broker"] else ""
            demat = str(r[C["demat"]] or "").strip() if C["demat"] else ""
            strat = str(r[C["strategy"]] or "").strip() if C["strategy"] else ""
            status = str(r[C["status"]] or "").strip().lower() if C["status"] else ""
            margin = float(r[C["margin"]] or 0) if C["margin"] and not pd.isna(r.get(C["margin"])) else 0
            buy_price = float(r[C["buy_price"]] or 0) if C["buy_price"] and not pd.isna(r.get(C["buy_price"])) else 0

            # Group by STRATEGY (not demat) so one tier's strikes across multiple
            # demats live in ONE strategy, each leg tagged with its demat.
            key = (broker, strat, edate, inst)
            g = groups.setdefault(key, {"legs": [], "closed_rows": [], "etime": etime,
                                        "demats": set(), "all_closed": True, "margin": 0.0,
                                        "note": str(r[C["notes"]]) if C["notes"] and not pd.isna(r.get(C["notes"])) else ""})
            sell_p = round(float(r[C["sell_price"]] or 0), 4)
            g["legs"].append({"strike": int(float(strike)), "side": side,
                              "qty": -int(qty), "price": sell_p, "demat": demat})
            if demat: g["demats"].add(demat)
            g["margin"] += margin
            if status == "closed":
                g["closed_rows"].append({"strike": int(float(strike)), "side": side,
                                         "qty": int(qty), "buy": round(buy_price, 4),
                                         "sell": sell_p, "demat": demat})
            else:
                g["all_closed"] = False
            if etime and not g["etime"]:
                g["etime"] = etime
        except Exception:
            continue

    preview, saved = [], []
    for (broker, strat, edate, inst), g in groups.items():
        tier = ("Tier 1" if "deep" in strat.lower() else
                "Tier 2" if "mid" in strat.lower() else
                "Tier 3" if "high" in strat.lower() else strat or "?")
        dlist = sorted(g["demats"])
        dlabel = dlist[0] if len(dlist) == 1 else ("mixed" if dlist else "")
        item = {"broker": broker, "demat": dlabel, "demats": dlist, "strategy": strat, "tier": tier,
                "instrument": inst, "entry_date": edate, "entry_time": g["etime"],
                "n_legs": len(g["legs"]), "closed": g["all_closed"],
                "margin_from_sheet": g["margin"],
                "max_premium": round(sum(abs(l["qty"]) * l["price"] for l in g["legs"]), 0)}
        preview.append(item)
        if not dry:
            rec = journal.add_trade(instrument=inst, tier=tier, legs=g["legs"],
                                    entry_time=g["etime"], entry_date=edate,
                                    broker=broker, source="excel",
                                    note=(g["note"] or "") + (f" margin_sheet={g['margin']:.0f}" if g["margin"] else ""),
                                    strategy_name=f"{strat} ({broker})", portfolio=broker)
            # book each closed row at the leg level (carries demat for reco)
            for cr in g["closed_rows"]:
                journal.close_leg(rec["id"], cr["strike"], cr["side"], cr["qty"], cr["buy"],
                                  entry_price=cr["sell"], demat=cr["demat"], note="excel-close")
            saved.append(rec["id"])
    return {"groups": len(groups), "preview": preview,
            "saved": (None if dry else len(saved)), "dry_run": dry}


@app.get("/api/reporting/days")
def api_reporting_days():
    """All days that have journal trades, with aggregate P&L per day."""
    from lib import journal
    days = {}
    for t in journal.all_trades():
        d = days.setdefault(t.get("entry_date", "?"),
                            {"date": t.get("entry_date"), "n": 0, "booked": 0.0, "open": 0})
        d["n"] += 1
        d["booked"] += journal.booked_pnl(t)
        if t.get("status") != "closed":
            d["open"] += 1
    return {"days": sorted(days.values(), key=lambda x: x["date"], reverse=True)}


@app.get("/api/reporting/day")
def api_reporting_day(date: str = None):
    """Full day view: strategies with legs enriched with live LTP + unbooked P&L,
    plus dashboard aggregates (total / by broker / by tier)."""
    from lib import journal
    d = date or datetime.now(IST).strftime("%Y-%m-%d")
    trades = [t for t in journal.all_trades() if t.get("entry_date") == d]

    # Live chains for open-trade enrichment (one fetch per instrument present)
    chains = {}
    if kite_alive():
        for inst in {t["instrument"] for t in trades if t.get("status") == "open"}:
            try:
                ch = chain(inst)
                chains[inst] = {r["strike"]: r for r in ch.get("rows", [])}
            except Exception:
                pass

    out_trades = []
    agg = {"total": 0.0, "booked": 0.0, "unbooked": 0.0,
           "by_broker": {}, "by_tier": {}}
    ccfg = _broker_costs()
    for t in trades:
        enriched = dict(t)
        unbooked = 0.0
        legs_out = []
        expired = _expired_worthless(t, d)
        for l in t.get("legs", []):
            leg = dict(l)
            if (l.get("qty") or 0) != 0:        # only legs still open (not booked out)
                # priority: manual ltp override → live chain → 0 if expired & no quote.
                # (ITM legs keep their live/manual value; only truly worthless legs,
                #  which have no quote after expiry, fall through to ₹0 instead of '—'.)
                if l.get("ltp") is not None:
                    ltp = l.get("ltp")
                else:
                    row = chains.get(t["instrument"], {}).get(l.get("strike"))
                    ltp = (row or {}).get("pe_ltp" if l.get("side") == "PE" else "ce_ltp")
                    if (ltp is None or ltp == 0) and expired:
                        ltp = 0.0          # expired worthless → ₹0, not '—'
                leg["ltp"] = ltp
                if ltp is not None and l.get("price") is not None and l.get("qty"):
                    qty = l["qty"]
                    leg["pnl"] = round((l["price"] - ltp) * abs(qty) if qty < 0
                                       else (ltp - l["price"]) * qty, 0)
                    unbooked += leg["pnl"]
            legs_out.append(leg)
        enriched["legs"] = legs_out
        booked = journal.booked_pnl(t)
        enriched["booked_legs"] = t.get("booked_legs") or []
        enriched["unbooked_pnl"] = round(unbooked, 0)
        enriched["booked_pnl"] = booked
        enriched["total_pnl"] = round(unbooked + booked, 0)
        # Max profit = open OPTION premium (if all expire worthless) + booked P&L.
        # Short futures legs contribute notional, not premium → exclude (C1).
        if t.get("status") == "open":
            mp = sum(abs(l.get("qty") or 0) * (l.get("price") or 0)
                     for l in t.get("legs", [])
                     if (l.get("qty") or 0) < 0 and (l.get("side") or l.get("leg_type") or "").upper() in ("CE", "PE"))
            enriched["max_profit"] = round(mp + booked, 0)
        else:
            enriched["max_profit"] = round(booked, 0)
        # Margin = capital that WAS deployed — computed from the ORIGINAL entry
        # position (open legs + booked-back legs reconstructed) so per-Cr yield
        # is meaningful even after the day is fully closed.
        recon = {}
        for l in t.get("legs", []):
            k = (l.get("strike"), l.get("side"))
            recon[k] = recon.get(k, 0) + (l.get("qty") or 0)
        for bk in (t.get("booked_legs") or []):
            sgn = -1 if (bk.get("entry_price") or 0) >= 0 else -1   # entries here are shorts
            k = (bk.get("strike"), bk.get("side"))
            recon[k] = recon.get(k, 0) - abs(bk.get("qty") or 0)
        entry_legs = [{"strike": s, "side": sd, "qty": q} for (s, sd), q in recon.items() if q]
        sheet_m = None
        import re as _re
        mm = _re.search(r"margin_sheet=(\d+)", t.get("note") or "")
        if mm: sheet_m = float(mm.group(1))
        enriched["margin_used"] = _strategy_margin(entry_legs, t["instrument"], sheet_m)
        # demat: a strategy can span demats (same strikes sold across accounts) →
        # show the single demat, or 'mixed', and split P&L/costs at the leg level.
        leg_demats = {(l.get("demat") or "") for l in t.get("legs", [])} | \
                     {(b.get("demat") or "") for b in (t.get("booked_legs") or [])}
        leg_demats.discard("")
        enriched["demat"] = (list(leg_demats)[0] if len(leg_demats) == 1
                             else ("mixed" if len(leg_demats) > 1 else ""))
        lot = pb.INSTRUMENT_CFG.get(t["instrument"], {}).get("lot", 50)
        cb = _strategy_costs(t, ccfg, lot)
        enriched["costs"] = cb["total"]
        enriched["cost_breakup"] = cb
        out_trades.append(enriched)

        agg["costs"] = agg.get("costs", 0) + (enriched["costs"] or 0)
        agg["max_profit"] = agg.get("max_profit", 0) + (enriched["max_profit"] or 0)
        agg["margin_used"] = agg.get("margin_used", 0) + (enriched["margin_used"] or 0)
        agg["booked"] += (booked or 0)
        agg["unbooked"] += (unbooked or 0)
        for key, field in (("by_broker", "broker"), ("by_tier", "tier")):
            k = t.get(field) or "?"
            b = agg[key].setdefault(k, {"n": 0, "pnl": 0.0})
            b["n"] += 1
            b["pnl"] += booked + unbooked
        # by_demat — LEG LEVEL: open-leg unbooked + booked P&L bucketed by leg demat,
        # plus per-demat costs. This is what month-end bank reconciliation uses.
        dpnl = {}
        for lg in legs_out:
            if lg.get("pnl") is not None:
                dpnl.setdefault(lg.get("demat") or "?", {"pnl": 0.0, "costs": 0.0})["pnl"] += lg["pnl"]
        for bk in (t.get("booked_legs") or []):
            dpnl.setdefault(bk.get("demat") or "?", {"pnl": 0.0, "costs": 0.0})["pnl"] += (bk.get("pnl") or 0)
        for dm, c in _costs_by_demat(t, ccfg, lot).items():
            dpnl.setdefault(dm or "?", {"pnl": 0.0, "costs": 0.0})["costs"] += c
        for dm, v in dpnl.items():
            b = agg.setdefault("by_demat", {}).setdefault(dm, {"n": 0, "pnl": 0.0, "costs": 0.0})
            b["pnl"] += v["pnl"]; b["costs"] += v["costs"]; b["n"] += 1
    agg.setdefault("by_demat", {})
    for k, v in agg["by_demat"].items():
        v["pnl"] = round(v["pnl"], 0); v["costs"] = round(v["costs"], 0)
        v["net"] = round(v["pnl"] - v["costs"], 0)
    agg["max_profit"] = round(agg.get("max_profit", 0), 0)
    agg["margin_used"] = round(agg.get("margin_used", 0), 0)
    agg["yield_on_margin_pct"] = round(agg["max_profit"] / agg["margin_used"] * 100, 3) if agg.get("margin_used") else None
    agg["total"] = round(agg["booked"] + agg["unbooked"], 0)
    agg["costs"] = round(agg.get("costs", 0), 0)
    agg["net_total"] = round(agg["total"] - agg["costs"], 0)
    agg["booked"] = round(agg["booked"], 0)
    agg["unbooked"] = round(agg["unbooked"], 0)
    for key in ("by_broker", "by_tier"):
        for k in agg[key]:
            agg[key][k]["pnl"] = round(agg[key][k]["pnl"], 0)

    return {"date": d, "strategies": out_trades, "dashboard": agg,
            "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


def _trade_expiry_date(t) -> str:
    """The trade's EXPIRY date (YYYY-MM-DD) — what months are bucketed by, NOT
    entry date. An option sold in June for a July expiry counts toward July.
    NIFTY/SENSEX/BANKNIFTY: the weekly expiry on/after entry (deep-OTM trades are
    entered E-1/E-0). Others: fall back to entry date until per-leg expiry exists."""
    inst = (t.get("instrument") or "").upper()
    ed = t.get("entry_date") or ""
    if not ed:
        return ed
    if inst in ("NIFTY", "SENSEX", "BANKNIFTY"):
        try:
            from lib.expiry_calendar import nearest_weekly_expiry_after, is_e0
            from datetime import date as _d
            y, m, d = map(int, ed.split("-")); ddt = _d(y, m, d)
            exp = ddt if is_e0(ddt, inst) else nearest_weekly_expiry_after(ddt, inst)
            return str(exp) if exp else ed
        except Exception:
            return ed
    return ed


@app.get("/api/reporting/range")
def api_reporting_range(start: str = None, end: str = None, by: str = "expiry"):
    """Realized (booked) P&L + full costs aggregated across a date range —
    powers month / custom-range / FY / quarter reports. Per-day breakdown plus
    grouped totals by broker / tier / demat. Uses booked P&L (history), not live."""
    from lib import journal
    if not (start and end):
        return JSONResponse({"error": "start and end required (YYYY-MM-DD)"}, status_code=400)
    ccfg = _broker_costs()
    # bucket by EXPIRY month (default) — an option counts toward its expiry's month,
    # regardless of when it was sold. by='entry' reverts to trade-date grouping.
    _dkey = (lambda t: (t.get("entry_date") or "")) if by == "entry" else _trade_expiry_date
    trades = [t for t in journal.all_trades()
              if start <= _dkey(t) <= end]
    days, by_broker, by_tier, by_demat = {}, {}, {}, {}
    strategies = []
    tot = {"booked": 0.0, "costs": 0.0, "max_profit": 0.0, "margin_used": 0.0, "n": 0}

    def _bucket(m, key, realized, cost, mp):
        g = m.setdefault(key or "?", {"n": 0, "booked": 0.0, "costs": 0.0, "net": 0.0, "max_profit": 0.0})
        g["n"] += 1; g["booked"] += realized; g["costs"] += cost
        g["net"] += realized - cost; g["max_profit"] += mp

    def _bucket1(m, key, realized, cost, mp):
        g = m.setdefault(key or "?", {"n": 0, "booked": 0.0, "costs": 0.0, "net": 0.0, "max_profit": 0.0})
        g["n"] += 1; g["booked"] += realized; g["costs"] += cost
        g["net"] += realized - cost; g["max_profit"] += mp

    for t in trades:
        realized = journal.booked_pnl(t)
        leg_demats = {(l.get("demat") or "") for l in t.get("legs", [])} | \
                     {(b.get("demat") or "") for b in (t.get("booked_legs") or [])}
        leg_demats.discard("")
        demat = (list(leg_demats)[0] if len(leg_demats) == 1
                 else ("mixed" if len(leg_demats) > 1 else ""))
        lot = pb.INSTRUMENT_CFG.get(t["instrument"], {}).get("lot", 50)
        cost = _strategy_costs(t, ccfg, lot)["total"]
        mp = (sum(abs(l.get("qty") or 0) * (l.get("price") or 0)
                  for l in t.get("legs", []) if (l.get("qty") or 0) < 0) + realized)
        margin = (_strategy_margin(t.get("legs", []), t["instrument"]) or 0) if t.get("status") == "open" else 0
        dd = _dkey(t)                      # expiry date (or entry, per `by`)
        _bucket(days, dd, realized, cost, mp); days[dd]["date"] = dd
        _bucket(by_broker, t.get("broker"), realized, cost, mp)
        _bucket(by_tier, t.get("tier"), realized, cost, mp)
        # by_demat LEG LEVEL: realized booked P&L + costs split per leg's demat
        dcosts = _costs_by_demat(t, ccfg, lot)
        drealized = {}
        for bk in (t.get("booked_legs") or []):
            drealized[bk.get("demat") or "?"] = drealized.get(bk.get("demat") or "?", 0) + (bk.get("pnl") or 0)
        for dm in set(list(dcosts.keys()) + list(drealized.keys())):
            _bucket1(by_demat, dm, drealized.get(dm, 0), dcosts.get(dm, 0), 0)
        tot["booked"] += realized; tot["costs"] += cost
        tot["max_profit"] += mp; tot["margin_used"] += margin; tot["n"] += 1
        strategies.append({"id": t["id"], "entry_date": t.get("entry_date"), "expiry_date": _trade_expiry_date(t),
                           "strategy_name": t.get("strategy_name"),
                           "instrument": t.get("instrument"), "tier": t.get("tier"),
                           "broker": t.get("broker"), "demat": demat, "status": t.get("status"),
                           "booked": round(realized), "costs": cost, "net": round(realized - cost)})

    def _round(m):
        for g in m.values():
            for k in ("booked", "costs", "net", "max_profit"):
                g[k] = round(g[k])
        return m
    tot = {k: round(v) for k, v in tot.items()}
    tot["net"] = round(tot["booked"] - tot["costs"])
    return {"start": start, "end": end,
            "days": sorted(_round(days).values(), key=lambda x: x["date"]),
            "by_broker": _round(by_broker), "by_tier": _round(by_tier), "by_demat": _round(by_demat),
            "totals": tot, "strategies": sorted(strategies, key=lambda x: x["entry_date"]),
            "computed_at": datetime.now(IST).strftime("%H:%M:%S")}


BROKER_COSTS_FILE = ROOT / "data" / "broker_costs.json"

# Full Indian F&O cost profile. Percentages are on value (premium turnover);
# brokerage is flat per lot, both sides executed. Standard 2026 option rates.
COST_FIELDS = ["brokerage_per_lot", "stt_pct", "txn_pct", "gst_pct", "stamp_pct", "sebi_per_cr"]
_COST_DEFAULT = {"brokerage_per_lot": 10, "stt_pct": 0.0625, "txn_pct": 0.035,
                 "gst_pct": 18, "stamp_pct": 0.003, "sebi_per_cr": 10}

def _broker_costs() -> dict:
    try:
        c = json.loads(BROKER_COSTS_FILE.read_text())
        if isinstance(c, dict) and c:
            return c
    except Exception:
        pass
    return {"Monarch": {**_COST_DEFAULT, "brokerage_per_lot": 10},
            "Axis": {**_COST_DEFAULT, "brokerage_per_lot": 6},
            "default": dict(_COST_DEFAULT), "_demats": {}}


def _cost_profile(ccfg: dict, broker: str, demat: str) -> dict:
    """Resolve effective cost rates: default ← broker ← demat override."""
    prof = dict(_COST_DEFAULT)
    prof.update({k: v for k, v in (ccfg.get("default") or {}).items() if k in COST_FIELDS})
    prof.update({k: v for k, v in (ccfg.get(broker or "") or {}).items() if k in COST_FIELDS})
    if demat:
        prof.update({k: v for k, v in ((ccfg.get("_demats") or {}).get(demat) or {}).items() if k in COST_FIELDS})
    return prof


def _cost_calc(legs: list, bl: list, xl: list, broker: str, demat: str, ccfg: dict, lot: int) -> dict:
    """Full cost breakup from a set of legs (entry/booked/exit executions).
    Brokerage on every executed lot; STT on sell premium; txn/SEBI on turnover;
    GST on (brokerage+txn); stamp on buy value. Worthless (price 0) exits are
    not executed → no brokerage/charges on them."""
    p = _cost_profile(ccfg, broker, demat)
    entry_units = sum(abs(l.get("qty") or 0) for l in legs) + sum((b.get("qty") or 0) for b in bl)
    exit_units = (sum((b.get("qty") or 0) for b in bl if (b.get("exit_price") or 0) > 0)
                  + sum(abs(x.get("qty") or 0) for x in xl if (x.get("price") or 0) > 0))
    lots = (entry_units + exit_units) / (lot or 1)
    sell_value = (sum((l.get("price") or 0) * abs(l.get("qty") or 0) for l in legs if (l.get("qty") or 0) < 0)
                  + sum((b.get("entry_price") or 0) * (b.get("qty") or 0) for b in bl))
    buy_value = (sum((b.get("exit_price") or 0) * (b.get("qty") or 0) for b in bl if (b.get("exit_price") or 0) > 0)
                 + sum((x.get("price") or 0) * abs(x.get("qty") or 0) for x in xl if (x.get("price") or 0) > 0))
    turnover = sell_value + buy_value
    brokerage = lots * p["brokerage_per_lot"]
    stt = sell_value * p["stt_pct"] / 100
    txn = turnover * p["txn_pct"] / 100
    gst = (brokerage + txn) * p["gst_pct"] / 100
    stamp = buy_value * p["stamp_pct"] / 100
    sebi = turnover / 1e7 * p["sebi_per_cr"]
    return {"brokerage": round(brokerage), "stt": round(stt), "txn": round(txn),
            "gst": round(gst), "stamp": round(stamp), "sebi": round(sebi),
            "total": round(brokerage + stt + txn + gst + stamp + sebi)}


def _partition_by_demat(t: dict) -> dict:
    """Split a strategy's legs / booked / exit executions by each leg's demat."""
    parts = {}
    def slot(dm):
        return parts.setdefault(dm or "", {"legs": [], "bl": [], "xl": []})
    for l in t.get("legs") or []:
        slot(l.get("demat")).setdefault("legs", []).append(l)
    for b in t.get("booked_legs") or []:
        slot(b.get("demat")).setdefault("bl", []).append(b)
    for x in t.get("exit_legs") or []:
        slot(x.get("demat")).setdefault("xl", []).append(x)
    return parts


def _strategy_costs(t: dict, ccfg: dict, lot: int, demat: str = None) -> dict:
    """Strategy cost breakup = sum of per-demat cost breakups (each demat is a
    separate execution set → correct brokerage/STT/GST per demat for bank reco)."""
    parts = _partition_by_demat(t)
    if not parts:
        return _cost_calc([], [], [], t.get("broker"), demat or "", ccfg, lot)
    keys = ["brokerage", "stt", "txn", "gst", "stamp", "sebi", "total"]
    agg = {k: 0 for k in keys}
    for dm, p in parts.items():
        cb = _cost_calc(p.get("legs", []), p.get("bl", []), p.get("xl", []), t.get("broker"), dm, ccfg, lot)
        for k in keys:
            agg[k] += cb[k]
    return agg


def _costs_by_demat(t: dict, ccfg: dict, lot: int) -> dict:
    """{demat: total_cost} for one strategy."""
    out = {}
    for dm, p in _partition_by_demat(t).items():
        out[dm or "?"] = _cost_calc(p.get("legs", []), p.get("bl", []), p.get("xl", []),
                                    t.get("broker"), dm, ccfg, lot)["total"]
    return out


@app.get("/api/settings/broker_costs")
def api_broker_costs_get():
    return {"costs": _broker_costs()}


@app.post("/api/settings/broker_costs")
async def api_broker_costs_set(request: Request):
    """Body: {costs: {"Monarch": {"brokerage_per_lot": 10}, ...}}"""
    b = await request.json()
    costs = b.get("costs")
    if not isinstance(costs, dict):
        return JSONResponse({"error": "costs dict required"}, status_code=400)
    BROKER_COSTS_FILE.write_text(json.dumps(costs, indent=2))
    return {"saved": True}


@app.post("/api/reporting/unbook")
async def api_reporting_unbook(request: Request):
    """Remove a wrong booked exit. Body: {id, strike, side, qty, exit_price, exit_time?}"""
    from lib import journal
    b = await request.json()
    for k in ("id", "strike", "side", "qty", "exit_price"):
        if b.get(k) in (None, ""):
            return JSONResponse({"error": f"{k} required"}, status_code=400)
    journal.unbook_leg(b["id"], int(b["strike"]), b["side"].upper(), int(b["qty"]),
                       float(b["exit_price"]), exit_time=b.get("exit_time"))
    return {"saved": True}


@app.post("/api/reporting/edit_booking")
async def api_reporting_edit_booking(request: Request):
    """Correct a booked exit in place: removes the old booking, books the new
    one (entry_price included so P&L is exact).
    Body: {id, old:{strike,side,qty,exit_price,exit_time}, new:{qty,entry_price,exit_price,exit_time}}"""
    from lib import journal
    b = await request.json()
    o, n = b.get("old") or {}, b.get("new") or {}
    if not (b.get("id") and o.get("strike") and n.get("qty")):
        return JSONResponse({"error": "id + old + new required"}, status_code=400)
    journal.unbook_leg(b["id"], int(o["strike"]), o["side"].upper(), int(o["qty"]),
                       float(o.get("exit_price") or 0), exit_time=o.get("exit_time"))
    journal.close_leg(b["id"], int(o["strike"]), o["side"].upper(), int(n["qty"]),
                      float(n.get("exit_price") or 0), exit_time=n.get("exit_time") or o.get("exit_time"),
                      entry_price=float(n["entry_price"]) if n.get("entry_price") not in (None, "") else None,
                      note="corrected", reduce_qty=False)
    return {"saved": True}


@app.post("/api/reporting/settle_expired")
async def api_reporting_settle_expired():
    """End of market on expiry day: book every open leg of TODAY-expiring
    instruments at settlement — ₹0 if OTM at close, intrinsic if ITM.
    Idempotent: legs already booked (qty 0) are skipped."""
    from lib import journal
    from lib import playbook as pb
    expiring = pb.expiring_today()
    if not expiring:
        return {"settled": 0, "note": "no index expires today"}
    settled, errors, itm_pending = [], [], []
    spots = {}
    for t in journal.open_trades():
        inst = t["instrument"]
        if inst not in expiring:
            continue
        if inst not in spots:
            try:
                spots[inst] = chain(inst).get("spot")
            except Exception as e:
                errors.append(f"{inst} spot: {e}")
                spots[inst] = None
        spot = spots[inst]
        if not spot:
            continue
        for l in t.get("legs", []):
            qty = l.get("qty") or 0
            if qty == 0:
                continue
            strike = l.get("strike")
            intrinsic = max(0.0, (spot - strike) if l.get("side") == "CE" else (strike - spot))
            if intrinsic > 0:
                # ITM: leave OPEN — actual square-off price must be entered manually
                itm_pending.append({"id": t["id"], "name": t.get("strategy_name") or inst,
                                    "strike": strike, "side": l.get("side"), "qty": abs(qty),
                                    "intrinsic": round(intrinsic, 2)})
                continue
            journal.close_leg(t["id"], strike, l.get("side"), abs(qty),
                              0.0, exit_time="15:30",
                              note="auto-settle worthless", entry_price=l.get("price"))
            settled.append({"id": t["id"], "name": t.get("strategy_name") or inst,
                            "strike": strike, "side": l.get("side"), "qty": abs(qty),
                            "settle_price": 0.0})
    return {"settled": len(settled), "legs": settled, "itm_pending": itm_pending,
            "spots": spots, "errors": errors}


@app.post("/api/reporting/close_strategy")
async def api_reporting_close(request: Request):
    """Close a strategy with per-leg exit prices; P&L computed server-side.
    Body: {id, exit_legs:[{strike,side,qty,price}], exit_time?, note?}"""
    from lib import journal
    try:
        b = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    t = next((x for x in journal.all_trades() if x["id"] == b.get("id")), None)
    if not t:
        return JSONResponse({"error": "trade not found"}, status_code=404)
    # book each leg individually (same editable history as Sq off); the strategy
    # auto-closes when every leg reaches qty 0
    pnl = 0.0
    for ex in b.get("exit_legs") or []:
        leg = next((l for l in t.get("legs", [])
                    if l.get("strike") == ex.get("strike") and l.get("side") == ex.get("side")
                    and (l.get("qty") or 0) != 0), None)
        if leg is None:
            continue
        q = abs(leg["qty"])
        rec = journal.close_leg(b["id"], int(ex["strike"]), ex["side"].upper(), q,
                                float(ex.get("price") or 0), exit_time=b.get("exit_time"),
                                entry_price=leg.get("price"), note=b.get("note", ""))
        e_p = leg.get("price") or 0
        x_p = float(ex.get("price") or 0)
        pnl += (e_p - x_p) * q if leg["qty"] < 0 else (x_p - e_p) * q
    return {"saved": True, "pnl": round(pnl, 2)}


@app.post("/api/reporting/margin-entry")
async def api_reporting_margin_entry(request: Request):
    """Set the manual margin-at-entry (and optional demat/trader) on a journal trade."""
    from lib import journal
    b = await request.json()
    if not b.get("id"):
        return JSONResponse({"error": "id required"}, status_code=400)
    meta = {}
    if b.get("margin_at_entry") not in (None, ""):
        meta["margin_at_entry"] = float(b["margin_at_entry"])
    for k in ("demat", "trader", "strategy_group"):
        if b.get(k) not in (None, ""):
            meta[k] = b[k]
    journal.set_meta(b["id"], meta)
    return {"saved": True}


@app.post("/api/reporting/amend")
async def api_reporting_amend(request: Request):
    """Replace a strategy's legs (edit strikes/qty/price, add legs).
    Body: {id, legs:[{strike,side,qty,price,demat?}]}"""
    from lib import journal
    b = await request.json()
    if not (b.get("id") and isinstance(b.get("legs"), list)):
        return JSONResponse({"error": "id + legs required"}, status_code=400)
    journal.amend_trade(b["id"], b["legs"], note=b.get("note", ""), meta=b.get("meta"))
    return {"saved": True}


@app.post("/api/reporting/close_leg")
async def api_reporting_close_leg(request: Request):
    """Partial/full square-off of ONE leg.
    Body: {id, strike, side, qty, price, exit_time?}"""
    from lib import journal
    b = await request.json()
    for k in ("id", "strike", "side", "qty", "price"):
        if b.get(k) in (None, ""):
            return JSONResponse({"error": f"{k} required"}, status_code=400)
    ep = b.get("entry_price")
    journal.close_leg(b["id"], int(b["strike"]), b["side"].upper(),
                      int(b["qty"]), float(b["price"]), exit_time=b.get("exit_time"),
                      entry_price=float(ep) if ep not in (None, "") else None)
    return {"saved": True}


@app.delete("/api/reporting/strategy/{trade_id}")
def api_reporting_delete(trade_id: str):
    from lib import journal
    journal.delete_trade(trade_id)
    return {"deleted": trade_id}


@app.get("/api/monitor/status")
def api_monitor_status():
    """Live Yellow/Orange/Red status for all OPEN journal trades + today's
    dashboard positions. Used by the desk Live Triggers card."""
    from lib import journal, playbook as pb
    out = {"computed_at": datetime.now(IST).strftime("%H:%M:%S"), "items": []}
    if not kite_alive():
        return {**out, "error": "Kite session expired"}
    sn = snapshot()
    spots = {k: sn.get(k, {}).get("spot") for k in ("NIFTY", "SENSEX")}

    def leg_status(inst, strike, side):
        spot = spots.get(inst)
        if not spot: return None
        buf = (spot - strike) if side == "PE" else (strike - spot)
        pct = buf / spot * 100
        if buf <= 0: lvl = "RED_ITM"
        elif pct < 0.15: lvl = "RED"
        elif pct < 0.40: lvl = "ORANGE"
        elif pct < 0.80: lvl = "YELLOW_WATCH"
        else: lvl = "SAFE"
        # Explicit spot levels: where each alert fires (CE: spot rising; PE: falling)
        if side == "CE":
            yellow_at = round(strike / 1.008); orange_at = round(strike / 1.004)
            red_at = round(strike / 1.0015); direction = "≥"
        else:
            yellow_at = round(strike / 0.992); orange_at = round(strike / 0.996)
            red_at = round(strike / 0.9985); direction = "≤"
        return {"strike": strike, "side": side, "buffer_pts": int(buf),
                "buffer_pct": round(pct, 2), "level": lvl,
                "direction": direction,
                "yellow_at": yellow_at, "orange_at": orange_at,
                "red_at": red_at, "itm_at": strike}

    # Live Triggers only monitors genuinely-live INDEX option strangles — not the
    # carried/investment book (Long NIFTY, commodity, master-workbook imports) and
    # not lapsed positions whose expiry already passed. Those are junk here.
    from lib import expiry_calendar as _ec
    _today = datetime.now(IST).date()

    def _monitor_relevant(t):
        inst = (t.get("instrument") or "").upper()
        if not any(inst.startswith(x) for x in ("NIFTY", "SENSEX", "BANKNIFTY")):
            return False                      # drop GOLD / commodity
        if (t.get("source") or "") == "import":
            return False                      # drop carried/investment/bulk-imported book
        if (t.get("tier") or "") in ("Long NIFTY", "Gold"):
            return False
        # lapsed? any weekly expiry for this index fell on/after entry and before today
        try:
            ed = datetime.strptime((t.get("entry_date") or "")[:10], "%Y-%m-%d").date()
            key = "NIFTY" if inst.startswith("NIFTY") else ("SENSEX" if inst.startswith("SENSEX") else inst)
            if any(ed <= e < _today for e in _ec.weekly_expiries(key)):
                return False
        except Exception:
            pass
        return True

    chains_pt = {}
    for t in journal.open_trades():
        if not _monitor_relevant(t):
            continue
        inst = t["instrument"]
        if inst not in chains_pt:
            try:
                ch = chain(inst)
                chains_pt[inst] = {r["strike"]: r for r in ch.get("rows", [])}
            except Exception:
                chains_pt[inst] = {}
        live_legs = [l for l in t.get("legs", []) if (l.get("qty") or 0) != 0]
        legs = [leg_status(inst, l["strike"], l["side"]) for l in live_legs]
        legs = [l for l in legs if l]
        worst = max((["SAFE","YELLOW_WATCH","ORANGE","RED","RED_ITM"].index(l["level"]) for l in legs), default=0)
        overall = ["SAFE","YELLOW_WATCH","ORANGE","RED","RED_ITM"][worst]

        # Profit-take: combined live <= 30% of combined entry (70% decay captured)
        comb_entry = sum((l.get("price") or 0) for l in live_legs)
        comb_now = 0.0; have_all = True
        for l in live_legs:
            row = chains_pt[inst].get(l.get("strike"))
            ltp = (row or {}).get("pe_ltp" if l.get("side") == "PE" else "ce_ltp")
            if ltp is None: have_all = False; break
            comb_now += ltp
        decay_pct = None; profit_take = False
        if have_all and comb_entry > 0:
            decay_pct = round((comb_entry - comb_now) / comb_entry * 100, 0)
            if comb_now <= comb_entry * 0.30 and overall in ("SAFE", "YELLOW_WATCH"):
                profit_take = True
                overall = "PROFIT_TAKE"

        worst_buf = min((l["buffer_pct"] for l in legs), default=99)
        out["items"].append({"id": t["id"], "source": "journal", "instrument": inst,
                             "pop_pct": _pop_from_buffer(worst_buf),
                             "tier": t.get("tier"), "entry_time": t.get("entry_time"),
                             "strategy_name": t.get("strategy_name"),
                             "legs": legs,
                             "combined_entry": round(comb_entry, 2),
                             "combined_now": round(comb_now, 2) if have_all else None,
                             "decay_pct": decay_pct,
                             "profit_take": profit_take,
                             "overall": overall})
    return out


@app.get("/api/news")
def api_news():
    """Risk brief: scheduled events + crude + headlines (lib/news.py)."""
    from lib.news import risk_brief
    return risk_brief()


@app.get("/api/telegram/status")
def api_telegram_status():
    """Check if Telegram bot is configured."""
    import json as _json
    cfg_path = Path.home() / ".config" / "telegram_bot.json"
    if not cfg_path.exists():
        return {"configured": False, "instructions": "Send token via /api/telegram/config"}
    try:
        cfg = _json.loads(cfg_path.read_text())
        return {"configured": True, "bot_username": cfg.get("bot_username"),
                "chat_id": cfg.get("chat_id"), "configured_at": cfg.get("configured_at")}
    except Exception:
        return {"configured": False, "error": "Invalid config file"}


@app.post("/api/telegram/config")
async def api_telegram_config(request: Request):
    """Save Telegram bot token + auto-discover chat_id.

    Body: {"bot_token": "1234:ABC..."}
    Returns chat_id if a message has been sent to the bot, else asks user to message it.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    token = (body.get("bot_token") or "").strip()
    chat_id_override = body.get("chat_id")
    if not token or ":" not in token:
        return JSONResponse({"error": "Invalid bot_token format"}, status_code=400)

    # Fetch bot info. TLS verification stays ON (the bot token is in the URL — a
    # MITM must not see it). Only disable it behind an explicit opt-in env flag for
    # a broken corporate-proxy host.
    import urllib.request as _u, ssl, json as _json, os as _os
    _SSL_CTX = ssl.create_default_context()
    if _os.environ.get("TG_INSECURE_TLS") == "1":
        _SSL_CTX.check_hostname = False
        _SSL_CTX.verify_mode = ssl.CERT_NONE
    try:
        req = _u.Request(f"https://api.telegram.org/bot{token}/getMe")
        with _u.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            bot_info = _json.load(resp).get("result", {})
        bot_username = bot_info.get("username")
    except Exception as e:
        return JSONResponse({"error": f"Token rejected by Telegram: {e}"}, status_code=400)

    # Auto-discover chat_id from latest messages
    chat_id = chat_id_override
    if not chat_id:
        try:
            req = _u.Request(f"https://api.telegram.org/bot{token}/getUpdates")
            with _u.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                updates = _json.load(resp).get("result", [])
            for upd in updates[::-1]:
                msg = upd.get("message") or upd.get("channel_post")
                if msg and msg.get("chat", {}).get("id"):
                    chat_id = msg["chat"]["id"]
                    break
        except Exception:
            pass

    if not chat_id:
        return {"bot_username": bot_username,
                "chat_id": None,
                "next_step": f"Open Telegram, message @{bot_username} (say 'hi'), then POST again."}

    # Save config
    cfg_path = Path.home() / ".config" / "telegram_bot.json"
    cfg = {
        "bot_token": token,
        "bot_username": bot_username,
        "chat_id": chat_id,
        "configured_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
    }
    cfg_path.write_text(_json.dumps(cfg, indent=2))
    cfg_path.chmod(0o600)

    # Send confirmation message
    try:
        import urllib.parse as _up
        msg = "✅ Theta Quant connected — you'll get alerts here for Yellow/Red triggers and profit-take fires."
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = _up.urlencode({"chat_id": chat_id, "text": msg}).encode()
        _u.urlopen(url, data=data, timeout=10, context=_SSL_CTX)
    except Exception:
        pass

    return {"configured": True, "bot_username": bot_username, "chat_id": chat_id}


@app.post("/api/telegram/send")
async def api_telegram_send(request: Request):
    """Send a Telegram message.

    Body: {"text": "alert text", "urgent": true/false}
    """
    cfg_path = Path.home() / ".config" / "telegram_bot.json"
    if not cfg_path.exists():
        return JSONResponse({"error": "Telegram not configured"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    text = body.get("text") or ""
    if not text:
        return JSONResponse({"error": "Missing text"}, status_code=400)

    import json as _json
    cfg = _json.loads(cfg_path.read_text())
    try:
        import urllib.request as _u, urllib.parse as _up, ssl as _ssl, os as _os
        ctx = _ssl.create_default_context()
        if _os.environ.get("TG_INSECURE_TLS") == "1":
            ctx.check_hostname = False; ctx.verify_mode = _ssl.CERT_NONE
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        data = _up.urlencode({"chat_id": cfg['chat_id'], "text": text,
                              "parse_mode": "Markdown"}).encode()
        with _u.urlopen(url, data=data, timeout=10, context=ctx) as resp:
            res = _json.load(resp)
        return {"sent": res.get("ok", False)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/live", response_class=HTMLResponse)
def page_live(request: Request):
    """Legacy alias — redirects to /report."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/report", status_code=307)


@app.get("/report", response_class=HTMLResponse)
def page_report(request: Request):
    """Reporting — Sensibull-style day/strategy module (primary)."""
    return templates.TemplateResponse(request, "reporting.html", {"active_nav": "report"})


# ── Auto-open in browser (opt-in via THETADESK_AUTOOPEN=1) ──────────────
# Disabled by default so server restarts during development don't spam tabs.
# Set the env var to re-enable for the very first launch of the day.
import os as _os
if _os.environ.get("THETADESK_AUTOOPEN") == "1":
    @app.on_event("startup")
    def open_browser_on_start():
        import webbrowser, threading
        def _open():
            time_mod.sleep(1.5)
            webbrowser.open("http://localhost:8000")
        threading.Thread(target=_open, daemon=True).start()


# ── Background scheduler loop (always on): ticks the dummy scheduler every 60s ──
@app.on_event("startup")
async def _start_dummy_scheduler():
    import asyncio

    async def _loop():
        while True:
            try:
                _dummy_scheduler_tick()
            except Exception:
                pass
            await asyncio.sleep(60)

    asyncio.create_task(_loop())


@app.on_event("startup")
async def _warm_caches():
    # The master Excel workbook takes ~24s to parse cold; warm it in a background
    # thread so the first Full Reporting / Overview request is instant, not a 24s hang.
    import threading

    def _warm():
        try:
            from lib import full_report
            full_report.load_report()
        except Exception:
            pass

    threading.Thread(target=_warm, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.server:app", host="127.0.0.1", port=8000, reload=False)
