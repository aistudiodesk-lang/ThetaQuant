# Daily Data Saving — Routine

**Purpose:** Keep the unified NIFTY + SENSEX minute-data store growing every trading day.
**Why:** The strategy improves with more data. Every saved day = better backtest. Every missed day = blind spot.

---

## ⏰ The ONE thing you (or anyone on the team) must do daily

Every trading morning, ideally before 9:30 AM IST:

```bash
cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"
python3 scripts/kite_login.py
```

Steps:
1. Browser opens → log in to Zerodha (PIN + TOTP)
2. Browser shows "site can't be reached" at `127.0.0.1:5000` — **expected**
3. **Copy the FULL URL from address bar** (contains `request_token=...`)
4. Paste into the terminal where the script is waiting
5. Script prints `✓ Session saved`. Done.

**That's it.** Login itself now does everything automatically — see "Auto-sync on every login" below.

---

## ⚡ Auto-sync on every login (added 2026-05-11)

**The moment `kite_session.json` is written (by `kite_login.py` OR by the dashboard's `/api/kite-exchange` web flow), this fires automatically in the background:**

`scripts/post_login_sync.py` →
1. **Parquet backfill** — `run_kite_ingest.py --days 14` (catches any missed days; idempotent)
2. **Dashboard snapshot save** — POSTs to `http://127.0.0.1:8000/api/snapshot/save` preserving any existing positions, refreshing market context + MTM analysis
3. **Verification** — logs the latest 7 trading days in the parquet store

**Log:** `results/post_login_sync.log` (timestamped, every run)

**Why this exists:** before 2026-05-11, daily login required users to remember to manually run ingest. On the 11-May expiry-week, 6 days of data were missed because the post-close cron failed silently on expired tokens. Now login = sync. No manual steps after login.

**Verify it ran:**
```bash
tail -30 "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)/results/post_login_sync.log"
```

You should see "Step 1: parquet ingest" → "Step 2: snapshot save" → "Step 3: parquet verification" with the last 7 trading days printed.

---

## 🤖 Two scheduled launchd jobs (also still running)

### 1. Morning health check — 09:30 IST weekdays
**File:** `scripts/com.rohanshah.morning-check.plist` (loaded via launchd)
**What it does:** Looks at yesterday's data. If missing OR if today's session isn't logged in, **fires a macOS notification** + writes to `results/morning_check.log`.
**You see it:** as a desktop notification within seconds — visible even if Mac is locked.

### 2. Data ingest — 16:30 IST weekdays (fallback)
**File:** `scripts/com.rohanshah.kite-ingest.plist` (loaded via launchd)
**What it does:** After market close, automatically pulls last 2 trading days × NIFTY + SENSEX → minute candles → appends to parquet.
**You see it:** silent on success. Logs in `results/kite_ingest_stdout.log`.
**Note:** This depends on a valid session at 16:30. Since tokens expire ~6 AM next day, the session must have been refreshed earlier in the day for this to work. **The auto-sync-on-login (above) is the primary safety net; this 16:30 job is a fallback.**

---

## ✅ How to verify everything's working

```bash
cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"

# Check yesterday is in the log
python3 -c "import pandas as pd; print(pd.read_parquet('data/kite_ingest_log.parquet').tail(5).to_string(index=False))"
```

Expected output: yesterday's date appears for both NIFTY and SENSEX.

---

## 🔧 If something is missed — catch-up

If you skipped login one day (Mac was off, etc.), run this the next morning:

```bash
cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"

# 1. Login first
python3 scripts/kite_login.py

# 2. Catch-up (auto-skips already-saved days)
python3 scripts/run_kite_ingest.py --days 7
```

Takes ~2-3 minutes. Auto-deduplicates — same day twice is harmless.

---

## 🚨 Common issues

| Problem | Fix |
|---|---|
| `Missing kite_session.json` | Re-run `python3 scripts/kite_login.py` |
| Token expired error | Same — re-login |
| `request_token expired` | Login again, paste URL within 60 sec of clicking |
| Cron not running automatically | `launchctl load -w "scripts/com.rohanshah.kite-ingest.plist"` |
| Morning-check cron not firing | `launchctl load -w "scripts/com.rohanshah.morning-check.plist"` |
| Need full health check status | `python3 scripts/morning_data_check.py` (run manually anytime) |

---

## 📦 What's saved

- **NIFTY + SENSEX minute data** — both instruments, same parquet store
- ~1 MB per instrument per day → ~2 MB/trading day total
- Annual: ~500 MB combined
- Auto-deduplicates: re-running a day is safe

Storage location: `data/parquet/instrument={NIFTY,SENSEX}/year=YYYY/month=MM/`

---

## 🆘 If completely stuck — full reset

```bash
cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"
rm ~/.config/kite_session.json
python3 scripts/kite_login.py
python3 scripts/run_kite_ingest.py --days 14
```

Or text Rohan / open a fresh chat with the assistant — paste the error message.

---

## 📞 Notify Rohan if any of these happen

- Morning check fires for 2+ consecutive days (cron may have unloaded)
- Kite re-subscription required (₹2K/mo auto-debits from Zerodha funds)
- Major Kite API change (rare, but possible)

---

*Strategy + backtest details: see `OPERATIONS_MANUAL.md` (separate doc). This file is ONLY about daily data saving.*
