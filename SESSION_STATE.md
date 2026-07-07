# SESSION STATE (read this FIRST — avoids re-exploring the repo)
_Last updated: 2026-06-11 midday. Update this file at end of each working session._

## NEW since last update (11-Jun)
- **Nav reorganized**: 🎯 Trade Desk (/playbook, PRIMARY) · 🧪 Strike Lab (/recommend) · 🌾 Harvest · Chain · 📋 Report · 🔔 Alerts.
- **Expiry Desk on /playbook**: instrument dropdown, E-0 primary view, 2 options/tier with editable strikes (live repricing from chain), PoP, ₹/Cr, entry windows, logic text. Endpoint: GET /api/playbook/recommendations?instrument=.
- **Trade journal** (lib/journal.py → data/trade_journal.jsonl): POST /api/journal/trade|/close|/csv, GET /api/journal. Auto-captures regime snapshot at entry. Entry TIME is mandatory context.
- **Day Report card on /report**: manual entry (entry_time+tier), CSV/Excel upload, screenshot path via bot.
- **Live Triggers card on /playbook**: Y/O/R per open journal trade (GET /api/monitor/status, 20s poll).
- **Learning loop** (analyses/900_learning_loop.py, hooked into evening.sh): closed trades vs backtest expectation per (tier, regime) → FINDINGS_LOG; auto-recalibration of distance tables when ≥10 new E-0 days.
- **E-1 table (analysis 026, ready to lock into lib/playbook)**: calm→09:20@2.0%, normal→09:20@2.0% (NIFTY ₹29K!), moderate→NIFTY 10:00-11:00@2.0% / SENSEX 12:00@2.0%, high_risk→14:45@3.5% both. NOT yet wired into playbook.py — pending.

## What's running (Mac, all started via shell)
| Service | Start | Check | Port/ID |
|---|---|---|---|
| Dashboard (FastAPI) | `nohup python3 -m uvicorn dashboard.server:app --port 8000 &` | `curl -s localhost:8000/api/health` | :8000 |
| Telegram bot | `./bot_start.sh` (force-kills dupes) | `./bot_status.sh` | @ExpiryTrading_Bot (admin chat id in ~/.config, runtime-only) |
| 16:30 ingest cron | launchd `com.rohanshah.kite-ingest` | `launchctl list \| grep rohan` | — |

## Daily ops (zero-token)
- Morning: `./morning.sh` (Kite login + 14-day backfill + snapshot). Kite token expires ~6 AM daily.
- Login URL: `https://kite.zerodha.com/connect/login?api_key=<KITE_API_KEY>&v=3` (key in ~/.config, runtime-only)
- Token exchange: `echo TOKEN | python3 scripts/kite_login.py` (auto-fires post_login_sync.py)
- Health: `./check.sh` · Evening fallback: `./evening.sh`

## Core rule modules (single source of truth)
- `lib/playbook.py` — ALL §9W rules: regime classifier, TIER1_DISTANCE lookup, tier setups, triggers, expiry calendar helpers. Web + bot both import this.
- `lib/expiry_calendar.py` — hardcoded NIFTY/SENSEX weekly expiries. NIFTY=Tue, SENSEX=Thu. Never use weekday inference.

## Locked strategy rules (backtested)
- **Tier 1 (75% book): 2.0% OTM floor** (2.25% on high_risk regime). 100% win across 119 E-0 days (analysis 025). NEVER closer than 2.0%.
- **Tier 2 (15%): 1.25-1.5% OTM**, range filters per §9W.4.
- **Tier 3 (8%): 0.5-1.0% OTM**, strict filters per §9W.3. Star trade: SENSEX 1.0% @ 10:00.
- Regimes: calm_green / normal / moderate / high_risk (gap, pre-range, VIX thresholds in lib/playbook.classify_regime).
- Yellow/Red triggers: Yellow = 50% buffer + 0.4% 30-min move → close losing leg only. Red = 85% buffer or strike touch → close both.
- Straddles: ABANDONED (§9U). Never suggest.
- E-1: §9O says 14:45+/3.5% (from 6-May incident) BUT analyses 006/007 say 10:00/2.5% works. Analysis 026 (reconciliation) results in `results/026_e1_regime_distance/`.

## Analyses index (all in analyses/, results in results/)
001-009 deep OTM foundations · 014 lottery harvest · 015/016 straddle (abandoned) ·
017 CE inflation · 018-024 Tier 2/3 framework (§9W) · 025 Tier 1 distance (2.0% floor) ·
026 E-1 regime×distance×entry-time

## Bot scheduled sends (IST, weekdays)
08:30 pre-market · 09:20 regime · 09:30+11:00 strike recs (expiry-day index only) ·
12-15:00 hourly premium · 15:30 day-end. Commands: /recommend /trade /mytrades /status /positions /sample /pause.
Position monitoring: every 60s, market hours, dashboard positions + /trade trades.

## Screenshot ingestion
OCR (tesseract) NOT installed yet. Until then: user pastes screenshots into chat, I save manually
via POST /api/snapshot/save. Rule: FULL REPLACE per portfolio (parse header → wipe matching broker+tier → insert).

## Open items
1. OCR install (brew + tesseract + pytesseract) — user runs commands
2. Mac mini migration + Tailscale phone access — discussed, not started
3. E-1 rule reconciliation → update §9O after 026 results
4. BANKNIFTY data ingest (placeholder in bot)
5. Tier 2 premium floors recheck at new Tier 1 distances

## Token-efficiency rules for Claude (self-instructions)
- READ THIS FILE FIRST in any new session. Don't re-read server.py/telegram_bot.py/STRATEGY_LIVE.md unless editing them — grep for the specific function instead.
- Analysis scripts: print compact summaries (top-10 rows max). Full data → CSV only.
- Answers: short. No recaps, no "what's committed" sections unless asked.
- Don't pull live data unless the question needs live numbers.
- Background long jobs; don't poll. One Monitor with tight filter.
