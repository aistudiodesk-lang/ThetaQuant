"""
Daily Kite Connect login flow.

Run once a day (typically before 9:00 AM IST). It:
  1. Loads API key + secret from ~/.config/kite_credentials.json
  2. Opens the Kite login URL in your default browser
  3. Waits for you to paste back the redirected URL (or just the request_token)
  4. Exchanges that for an access_token via API
  5. Saves access_token to ~/.config/kite_session.json (mode 600)

After this runs successfully, lib/kite_live.py functions work for the rest of
the trading day (token expires 6 AM next day).

⚠ Note about your existing Google-Sheets app: Kite Connect typically allows
only ONE active access_token per app at a time. Generating a new session here
may invalidate the Google Sheets app's token; you'd then need to re-login that
one too. If that's a problem, create a SECOND Kite Connect app for this Python
adapter (separate API key + secret + ₹2K/mo each — Zerodha charges per app).
"""
import json
import sys
import webbrowser
from pathlib import Path

try:
    from kiteconnect import KiteConnect
except ImportError:
    print("ERROR: pip install kiteconnect"); sys.exit(1)

CRED = Path.home() / ".config" / "kite_credentials.json"
SESS = Path.home() / ".config" / "kite_session.json"

if not CRED.exists():
    print(f"Missing {CRED}"); sys.exit(1)

creds = json.loads(CRED.read_text())
kite = KiteConnect(api_key=creds["api_key"])

# ─── Token can come from: (1) CLI arg, (2) piped stdin, (3) interactive ───
# Avoid opening browser when token is already supplied (Claude piped flow).
request_token = None

# CLI flag: --token <value> or --url <callback URL>
import argparse
_p = argparse.ArgumentParser(add_help=False)
_p.add_argument("--token", default=None)
_p.add_argument("--url", default=None)
_args, _ = _p.parse_known_args()
if _args.token:
    request_token = _args.token.strip()
elif _args.url:
    if "request_token=" in _args.url:
        request_token = _args.url.split("request_token=", 1)[1].split("&")[0]

# Stdin (piped) — read non-blocking if not a TTY
if request_token is None and not sys.stdin.isatty():
    piped = sys.stdin.read().strip()
    if piped:
        if "request_token=" in piped:
            request_token = piped.split("request_token=", 1)[1].split("&")[0]
        elif "&" not in piped and len(piped) >= 16:
            request_token = piped

# Interactive — only fall back to this if no token piped/passed
if request_token is None:
    login_url = kite.login_url()
    print(f"\n→ Opening login URL in browser:\n  {login_url}\n")
    print("After Zerodha login, your browser will redirect to a URL like:")
    print("  http://127.0.0.1:5000/callback?action=login&type=login&status=success&request_token=XXXXXXXXX&...")
    print("\nCopy & paste the FULL redirect URL (or just the request_token value):")
    try:
        webbrowser.open(login_url)
    except Exception:
        pass
    inp = input("\nPaste here: ").strip()
    if "request_token=" in inp:
        request_token = inp.split("request_token=", 1)[1].split("&")[0]
    elif "&" not in inp and len(inp) >= 16:
        request_token = inp
    else:
        print(f"Couldn't parse request_token from: {inp[:80]}..."); sys.exit(1)

if not request_token or len(request_token) < 16:
    print(f"✗ No valid request_token. Got: {request_token!r}"); sys.exit(1)
print(f"\n→ Using request_token: {request_token[:8]}...{request_token[-4:]}")

print(f"\n→ Exchanging request_token for access_token...")
try:
    sess = kite.generate_session(request_token, api_secret=creds["api_secret"])
except Exception as e:
    print(f"FAILED: {e}"); sys.exit(1)

out = {
    "access_token": sess["access_token"],
    "user_id": sess["user_id"],
    "user_name": sess.get("user_name", ""),
    "login_at": sess.get("login_time", "").isoformat() if hasattr(sess.get("login_time", ""), "isoformat") else str(sess.get("login_time", "")),
}
SESS.write_text(json.dumps(out, indent=2))
SESS.chmod(0o600)
print(f"\n✓ Session saved: user_id={out['user_id']} ({out['user_name']})")
print(f"  Token valid until ~6 AM tomorrow.")
print(f"  File: {SESS}")

# ─── AUTO-FIRE post-login sync (parquet backfill + dashboard snapshot) ───
# Runs in BACKGROUND so the login script returns immediately. Logs to
# results/post_login_sync.log. Safe to repeat — idempotent.
import subprocess
SYNC = Path(__file__).resolve().parent / "post_login_sync.py"
if SYNC.exists():
    print(f"\n→ Firing post_login_sync.py in background (parquet backfill + snapshot)…")
    print(f"  Log: results/post_login_sync.log")
    try:
        subprocess.Popen(
            [sys.executable, str(SYNC)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(SYNC.parent.parent),
            start_new_session=True,
        )
        print(f"  Started. Will auto-ingest last 14 days + save today's snapshot.")
    except Exception as e:
        print(f"  WARN: couldn't fire post_login_sync.py: {e}")
else:
    print(f"\n⚠ scripts/post_login_sync.py not found — run ingest manually:")
    print(f"  python3 scripts/run_kite_ingest.py --days 14")
