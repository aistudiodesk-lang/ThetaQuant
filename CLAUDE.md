# Backtest Engine + Live Trading System — Claude Protocol

**READ `SESSION_STATE.md` FIRST.** It has current services, locked rules, analyses index,
and open items. Do NOT re-explore the repo or re-read large files to orient yourself.

## What this is
Live trading support system for Rohan Shah (Navin Group, ~₹100Cr book): Parquet backtest
engine + FastAPI dashboard (:8000) + Telegram bot (@ExpiryTrading_Bot) for NIFTY/SENSEX
weekly-expiry deep-OTM option selling.

## Token-efficiency protocol (user-mandated)
1. `SESSION_STATE.md` first; grep specific functions instead of reading whole files.
2. Terse answers. No recaps/summaries unless asked. No live-data pulls unless needed.
3. Long jobs → background + one tight Monitor. Analysis scripts print top-10 rows max.
4. Update `SESSION_STATE.md` at end of session (cheap insurance for the next one).

## Hard rules
1. Never write to data source folders. Never upload data anywhere.
2. Every analysis = committed script in `analyses/NNN_slug.py` → `results/NNN_slug/`.
3. Append one-line finding to `FINDINGS_LOG.md` after each analysis.
4. Use `lib/expiry_calendar.py` (hardcoded dates) — never weekday inference.
5. Use `lib/playbook.py` for ALL strategy rules — web + bot share it. Never duplicate rule logic.
6. New CSV with unseen schema → FLAG, don't guess. IST timezone throughout.
7. Backtests: bulk DuckDB queries (one query, resolve in pandas) — never per-row loops.

## Strategy guardrails (locked, backtested — see SESSION_STATE.md for numbers)
- Tier 1 floor: 2.0% OTM, never closer. Straddles: abandoned, never suggest.
- 100%-win requirement for Tier 1 recommendations.
- Risk standard (corrected 2026-07-02): the WHOLE BOOK should ideally lose ~ZERO even on extremely
  bad days — "₹3.5L/Cr worst-day tolerance" was a misconception, don't use it as an acceptance cap.
  Bad positions get ACTIVELY MANAGED (e.g. bring the other side closer) — only when essential,
  never as a trade style. Backtests report the UNMANAGED tail; judge it by whether the rest of
  the book's same-day profit + active defense can plausibly absorb it.
- Canonical rulebook: `STRATEGY_LIVE.md` §9O-§9W (grep the section, don't read the file).

## Key paths
- Data: `data/parquet/instrument={NIFTY,SENSEX}/` · snapshots: `data/dashboard_snapshots/`
- Rules: `lib/playbook.py` · Calendar: `lib/expiry_calendar.py`
- Bot: `scripts/telegram_bot.py` (+ `bot_start.sh`/`bot_stop.sh`/`bot_status.sh`)
- Daily ops: `morning.sh` / `evening.sh` / `check.sh` · Kite: `scripts/kite_login.py`
- Docs: `TIER_PLAYBOOK_PRINTABLE.md` (user-facing rules) · `THETA_GAINERS_BRAIN_DUMP.md` (external-LLM handoff)

## Findings log
See `FINDINGS_LOG.md` — append-only, read before proposing new analyses.
