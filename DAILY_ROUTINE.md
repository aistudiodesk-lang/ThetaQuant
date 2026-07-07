# Daily Data Routine (zero-token, runs on your Mac)

Three shell commands handle everything. Run them from the project folder.

```bash
cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"
```

## 🌅 ONCE per morning (before 9 AM IST)

```bash
./morning.sh
```

What it does:
1. Checks if Kite session is fresh (<18h)
2. If stale → opens browser to login, you log in, paste back callback URL
3. Auto-fires backfill of last 14 days + saves dashboard snapshot
4. Shows you the latest 5 trading days in your store

**That's it.** Done in 1 minute.

## 🌙 Auto-runs at 16:30 IST (no action needed)

Launchd cron `com.rohanshah.kite-ingest` runs daily at 16:30 IST Mon-Fri.

It uses the session you logged into in the morning to fetch the full day's data after market close.

If for any reason the auto-cron fails, fall back to:

```bash
./evening.sh
```

## 🔍 ANYTIME to check status

```bash
./check.sh
```

Shows: session age, last 5 days of data, cron status. **No API calls, no Kite login needed.** Run this whenever you want to know if today's data is saved.

---

## What if you forget the morning login?

The 16:30 cron will fail silently (token expired ~6 AM). Just run `./morning.sh` whenever you remember — backfill picks up everything missed.

## What if your Mac is off all day?

When you wake it up next, run `./morning.sh`. The backfill scans the last 14 days; any missing day gets pulled.

## What if Kite Connect token expires mid-day?

Tokens expire ~6 AM IST. You won't notice unless you run something that hits Kite API directly. Just re-run `./morning.sh` to refresh.

---

## File paths

| File | Purpose |
|---|---|
| `./morning.sh` | Daily login + sync (run once per morning) |
| `./evening.sh` | Manual fallback if 16:30 cron fails |
| `./check.sh` | Read-only health check |
| `scripts/kite_login.py` | Underlying Python login (called by morning.sh) |
| `scripts/post_login_sync.py` | Backfill logic (auto-fires after login) |
| `scripts/com.rohanshah.kite-ingest.plist` | Launchd 16:30 cron definition |
| `data/parquet/instrument={NIFTY,SENSEX}/` | Where the data lives |
| `results/post_login_sync.log` | History of all sync runs |

## How to verify the 16:30 cron is loaded

```bash
launchctl list | grep rohanshah
```

Should show `com.rohanshah.kite-ingest`. If not:

```bash
launchctl load ~/Library/LaunchAgents/com.rohanshah.kite-ingest.plist
```

---

**Token usage:** zero. None of these scripts need Claude/AI to run.

**You only need Claude when:** you want analysis on the data, want to write a new backtest, or want to interpret results.
