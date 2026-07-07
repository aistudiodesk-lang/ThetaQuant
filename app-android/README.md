# Theta Quant — Android app (Capacitor wrapper)

This is a **thin wrapper**, not a standalone app. It's an Android WebView that loads
the Theta Quant server running on the Mac mini, over a public URL (Cloudflare tunnel).
It holds **no data and no secrets** — only the address of your server. Access is gated
by the login on the server, so the `.apk` is safe to share on Drive.

```
Phone (this app)  ──https──▶  Cloudflare tunnel  ──▶  Mac mini : FastAPI :8000
```

The Mac **must be on and the tunnel running** for the app to work.

---

## One-time: point the app at your server

1. Set up a stable public URL for the Mac (see `../DEPLOY_APP.md`, "Cloudflare tunnel").
   You'll get something like `https://theta.yourdomain.com`.
2. Add that host to `capacitor.config.ts` → `server.allowNavigation`:
   ```ts
   allowNavigation: ['theta.yourdomain.com'],
   ```
   (Optionally set `DEFAULT_URL` in `www/index.html` so it's pre-filled on first launch.)

## Build the APK

```bash
# from app-android/
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export ANDROID_HOME=$HOME/Library/Android/sdk

npm install                 # first time only
npm run build:debug         # → android/app/build/outputs/apk/debug/app-debug.apk
```

For a signed release build to share widely, see `../DEPLOY_APP.md` ("Signing").

## What each piece is

| File | Purpose |
|---|---|
| `www/index.html` | Bundled launcher — asks for / remembers the server URL, health-checks it, shows an offline screen. The only screen that ships inside the app. |
| `capacitor.config.ts` | App id/name + `allowNavigation` (the hosts the WebView may load). |
| `resources/icon.svg` / `icon.png` | App icon source (θ on the brand gradient). Regenerate native icons with `npm run icons`. |
| `android/` | Generated native project (Gradle). Regenerate with `npx cap sync`. |

## Update after server/UI changes

Nothing to rebuild for **server-side** changes — the app just loads the live server.
Rebuild the APK only when you change `www/`, `capacitor.config.ts`, icons, or plugins.
