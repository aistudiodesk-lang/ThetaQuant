# Theta Quant Android app — deploy & distribute

The app is a **WebView wrapper** around the Mac-mini server. Three things make it work:

1. The Mac runs the server (`uvicorn dashboard.server:app`).
2. A **Cloudflare tunnel** gives the Mac a stable public HTTPS URL.
3. The **APK** (built from `app-android/`) opens that URL and remembers the login.

> The app is only as available as the Mac + tunnel. Keep both running (launchd handles it).

---

## 1. Stable public URL — Cloudflare named tunnel (one-time, on the Mac)

A *quick* tunnel (`cloudflared tunnel --url ...`) gives a **random URL that changes on
every restart** — fine for a quick test, useless for an installed app. Use a **named**
tunnel with your own domain so the URL never changes.

```bash
brew install cloudflared
cloudflared tunnel login                          # opens browser, pick your domain
cloudflared tunnel create thetadesk               # creates a tunnel + credentials file
cloudflared tunnel route dns thetadesk theta.yourdomain.com
```

Create `~/.cloudflared/config.yml`:
```yaml
tunnel: thetadesk
credentials-file: /Users/<you>/.cloudflared/<TUNNEL-ID>.json
ingress:
  - hostname: theta.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Run it 24/7 as a service:
```bash
sudo cloudflared service install     # or a launchd plist (see TEAM_SETUP.md)
```

Test: open `https://theta.yourdomain.com` in a browser → you should get the Theta Quant
**login page** (not auto-admin — tunnel traffic always logs in).

### (Recommended) email-gate in front
In the Cloudflare Zero Trust dashboard, add an **Access** application for
`theta.yourdomain.com` with an email-OTP policy limited to your team's emails. Now only
whitelisted emails even reach the login screen — defense in depth for the public URL.

---

## 2. Point the app at your URL & build the APK

```bash
cd app-android
# edit capacitor.config.ts → server.allowNavigation: ['theta.yourdomain.com']
# (optional) edit www/index.html → DEFAULT_URL = 'https://theta.yourdomain.com'

export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=$HOME/Library/Android/sdk
npm install            # first time
npm run build:debug    # → android/app/build/outputs/apk/debug/app-debug.apk
```

`app-debug.apk` is installable immediately (users enable "install unknown apps"). Good
for the team.

### Signed release APK (for wider sharing)
```bash
# create a keystore ONCE (keep it + passwords safe, NEVER commit):
keytool -genkey -v -keystore thetadesk.keystore -alias thetadesk \
        -keyalg RSA -keysize 2048 -validity 10000

# android/keystore.properties:
#   storeFile=/absolute/path/thetadesk.keystore
#   storePassword=...
#   keyAlias=thetadesk
#   keyPassword=...

npm run build:release   # → android/app/build/outputs/apk/release/app-release.apk
```
(Signing config wiring in `android/app/build.gradle` — add a `signingConfigs` block that
reads `keystore.properties`; standard Capacitor/Android setup.)

---

## 3. Distribute via Drive + install guide (send this to users)

> **Install Theta Quant**
> 1. Open the Drive link and download **Theta Quant.apk**.
> 2. Tap it. Android will warn "install unknown apps" → allow it for your browser/Files app.
> 3. Open **Theta Quant**. If asked, confirm the server address (pre-filled) and tap **Connect**.
> 4. Log in with your username + password.
> 5. That's it — it stays logged in. The Mac must be on for data to load.

---

## Notes
- The APK contains **no trading data and no secrets** — only the server URL. Safe to share.
- Changing server-side code/UI needs **no rebuild** — the app loads the live server.
- Rebuild the APK only when `www/`, `capacitor.config.ts`, icons, or plugins change.
- iOS is **not** covered here — it can't be sideloaded via Drive (needs an Apple Developer
  account + TestFlight). Separate track if ever needed.
- **Before exposing the public URL widely:** add login rate-limiting on the server (the
  auth path has none today) and keep the GitHub repo private.
