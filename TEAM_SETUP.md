# Theta Quant — Team Setup & Hosting Guide

How to run the tool 24×7 on the **Mac mini**, use it seamlessly from Rohan's Mac + phones at the
office, and access it from outside via Cloudflare — with the right security separation.

---

## 0. The model (read this first)

- **Theta Quant is a Python web app (FastAPI).** It runs with `python`/`uvicorn`. It does **NOT** need Claude or any AI tool to run. Claude Code is only used by Rohan on his Mac to *develop* the code — never install it on the host.
- **Mac mini = the one always-on host.** It runs the web server + the Telegram alert bot 24×7 and holds the data. It is the single source of truth.
- **Rohan's Mac, phones, team = clients.** They open the mini's URL in a browser. Nobody runs a second copy of the server (two copies = two Kite sessions + data conflicts — never do this).
- **Code flows one way:** Rohan edits on his Mac → pushes to the private GitHub repo → the mini does `git pull`. The mini is read-only for code; no editing happens there.

| Machine | Role | Runs server? | Can edit code? | Has data? |
|---|---|---|---|---|
| Rohan's Mac | Development + daily use | no (uses the mini) | yes | no (uses the mini) |
| **Mac mini** | 24×7 host (restricted user) | **yes** | no (read-only `git pull`) | yes (FileVault-encrypted) |

---

## 1. One-time: GitHub (do on Rohan's Mac)

1. Create a **PRIVATE** GitHub repo for Theta Quant (e.g. `theta-quant`). **Never public** — this is the live ₹100Cr book.
2. Revoke any old Personal Access Tokens (see `DEPLOYMENT.md`).
3. From the project folder:
   ```bash
   git remote add origin <your-private-repo-url>
   git push -u origin feat/thetadesk-dashboard-v0.2   # or merge to main first
   ```

---

## 2. Mac mini — one-time setup

### 2a. Lock the machine down
- Enable **FileVault** (System Settings → Privacy & Security → FileVault). Disk encrypted — a stolen/offline mini reveals nothing.
- Create a **dedicated, non-admin user** named e.g. `thetahost`. The server runs as this user. It has no admin rights, no editor, no Claude.
- Set the mini to **never sleep** when plugged in (System Settings → Battery/Energy → Prevent automatic sleeping; "Wake for network access" on).
- Give the mini a **reserved IP** in your router (so its address never changes), or use its `<name>.local` hostname.

### 2b. Install Python 3.11 + the code (as `thetahost`)
```bash
# Python 3.11 (the project runs ONLY on 3.11)
# install from python.org (3.11.x) — gives /Library/Frameworks/Python.framework/Versions/3.11
PY=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3

# clone the private repo (read-only checkout — this user has NO push rights)
git clone <your-private-repo-url> ~/theta-quant
cd ~/theta-quant
$PY -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2c. Secrets (live in ~/.config, mode 600 — never in the repo)
- `~/.config/kite_credentials.json` → `{"api_key": "...", "api_secret": "..."}` (copy from Rohan).
- `~/.config/thetadesk_web.json` → `{"username": "team", "password": "<CHANGE THIS>"}` — **change the default password.** This is the login team members use remotely.
- `chmod 600 ~/.config/kite_credentials.json ~/.config/thetadesk_web.json`

### 2d. Seamless office access (no login on the home/office network)
Set an env var so any device on your network is auto-admin (no password) while the internet still requires login. Put your office subnet (find it in router settings, usually `192.168.1.0/24`):
```bash
# add to thetahost's ~/.zprofile
export TG_TRUSTED_HOSTS="192.168.1.0/24"
```
Now Rohan's Mac + office phones on WiFi open the tool with no prompt. Anything via Cloudflare/internet must log in.

### 2e. Auto-start 24×7 (launchd — server + Telegram bot)
Create `~/Library/LaunchAgents/com.thetagainers.server.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.thetagainers.server</string>
  <key>WorkingDirectory</key><string>/Users/thetahost/theta-quant</string>
  <key>EnvironmentVariables</key><dict><key>TG_TRUSTED_HOSTS</key><string>192.168.1.0/24</string></dict>
  <key>ProgramArguments</key><array>
    <string>/Users/thetahost/theta-quant/.venv/bin/python</string>
    <string>-m</string><string>uvicorn</string>
    <string>dashboard.server:app</string>
    <string>--host</string><string>0.0.0.0</string><string>--port</string><string>8000</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/Users/thetahost/theta-quant/results/server.log</string>
</dict></plist>
```
Then the bot, `~/Library/LaunchAgents/com.thetagainers.bot.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.thetagainers.bot</string>
  <key>WorkingDirectory</key><string>/Users/thetahost/theta-quant</string>
  <key>ProgramArguments</key><array>
    <string>/Users/thetahost/theta-quant/.venv/bin/python</string>
    <string>scripts/telegram_bot.py</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/Users/thetahost/theta-quant/results/bot.log</string>
</dict></plist>
```
Load them (run as `thetahost`):
```bash
launchctl load -w ~/Library/LaunchAgents/com.thetagainers.server.plist
launchctl load -w ~/Library/LaunchAgents/com.thetagainers.bot.plist
```
Both now start on boot and auto-restart on crash. Check: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/overview` → `200`.

---

## 3. Daily routine (every trading morning)

Kite tokens expire ~6 AM daily and **cannot be made fully headless** (security). So once each morning:
1. Open the login link (from any device):
   `https://kite.zerodha.com/connect/login?api_key=<api_key>&v=3`
2. Log in to Kite. The browser redirects to a page that may look blank — **copy the full URL from the address bar** (it contains `request_token=...`).
3. On the **mini**, run: `python scripts/kite_login.py --token "<the request_token>"`
   (or paste the full callback URL to the Telegram bot if that flow is enabled).
4. Done — live data flows for the day. (We can add a "paste login URL" box on the page to make this one click.)

---

## 4. How everyone uses it

- **At the office / home WiFi:** open `http://<mini-name>.local:8000` (or `http://<mini-ip>:8000`). Trusted-network devices are auto-admin (no login). Bookmark it.
- **Team members at the office:** same URL; if their device isn't on the trusted subnet they enter `team` / `<password>`.
- **Away from the network (phone on cellular, remote):** via Cloudflare (section 5) — always requires the login.

---

## 5. Cloudflare Tunnel (access from anywhere — free, no Vercel/Supabase)

On the **mini** (as `thetahost`):
```bash
brew install cloudflared
cloudflared tunnel login          # one-time, free Cloudflare account
cloudflared tunnel --url http://localhost:8000     # quick test → gives an https URL
```
For a permanent named tunnel + your own domain, follow Cloudflare's "named tunnel" docs, then run it under launchd too. Add **Cloudflare Access** (email login) for per-person access + an audit trail. The tool already forces the `team` login for all tunnel traffic, so it's never exposed unauthenticated.

---

## 6. Updating the code (only Rohan, from his Mac)
```bash
# Rohan's Mac: edit, then
git add -A && git commit -m "..." && git push
# Mac mini (thetahost):
cd ~/theta-quant && git pull && launchctl kickstart -k gui/$(id -u)/com.thetagainers.server
```
The mini never edits — it only pulls. Restrict its repo to read-only (no write token configured).

---

## 7. Security summary
- Code: editable only on Rohan's Mac; the mini pulls read-only. No dev tools/Claude on the mini.
- Data: lives on the mini (it must, to serve it) but the disk is **FileVault-encrypted**, files are `chmod 600/700` under the `thetahost` user, and the web app requires login for anyone off the trusted network.
- Secrets (Kite, web password) live in `~/.config` (600), never in git.
- Backup: Time Machine on the mini covers the `data/` folder; optionally rsync `~/theta-quant/data` to Rohan's Mac nightly.

> Questions / changes go to Rohan — the mini is run-only.
