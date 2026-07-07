# Trading System — Operations Manual

**Owner:** Rohan Shah
**System:** Local NIFTY/SENSEX deep-OTM strangle backtest engine + live trading assistant
**Last updated:** 28-Apr-2026 (post first live execution day)
**Status:** v2.0 strategy live, daily auto-ingest active, cross-validation pending more SENSEX data

---

## 1. What this system does

A self-improving short-strangle trading machine for Indian index options:

1. **Backtest engine** — 1+ year of NIFTY minute-bar data in parquet, queryable via DuckDB. Generates rule-validated trade configurations (analyses 001-009).
2. **Live data feed** — Kite Connect API (paid, ₹2K/mo) pulls real-time spot, VIX, option chains.
3. **Auto-ingest pipeline** — saves every weekday's minute candles to parquet (relevant strikes only, not GBs of bloat).
4. **Trading assistant interface** — you ask "give me expiry levels for [date]", I read live data + apply the locked v2.0 strategy + check fundamentals + return a trade card.
5. **Continuous learning** — every live trade day adds data to the parquet store. Rules update with new evidence.

**First live result (28-Apr-2026):** ~₹3.88 lakh net P&L on ~₹100 cr margin (0.39% in one day, ~98% annualized at sustained rate). 100% of strikes expired worthless. Strategy validated end-to-end.

---

## 2. The locked strategy (v2.0)

### 2A. Trade days
- ✅ **Mon (E-1 to NIFTY Tue expiry)** + **Tue (NIFTY E-0)**
- ✅ **Wed (E-1 to SENSEX Thu expiry)** + **Thu (SENSEX E-0)**
- ⏸ Fri / mid-week gaps — no trade (DTE 5+ premium too thin)

### 2B. Two-shot pattern per expiry cycle

| Slot | Capital | Distance | Entry time | Hold |
|---|---|---|---|---|
| **E-1 advance** | 5-7% | 3.5% OTM both sides | 10:00 AM previous day | Hold to expiry close |
| **E-0 T1** (workhorse) | ~85% | 3.0% OTM both sides | **9:25-9:35 AM expiry day** | Hold to 15:25 expiry |
| **E-0 T2** (medium) | ~8% | 2.5% OTM both sides | 9:30-9:45 | Hold to 15:25 |
| **E-0 T3** (premium grab) | ~2% | 2.0% OTM both sides | 9:30 | Hold to 15:25 |

### 2C. Condition overlays (read at 9:15-9:30 each morning)

**Gap direction (vs prev close):**
- ±0.5%: defaults
- Gap UP > 0.5%: CE +0.5% farther, PE −0.5% closer
- Gap UP > 1%: CE +1% farther, halve T3
- Gap DOWN: mirror (PE farther, CE closer)

**INDIA VIX:**
- < 13: tighten 0.25% (low vol = safe to be closer)
- 13-16: defaults
- 16-18: +0.25% to T1, T2
- 18-22: +0.5% + skip T3 + delay T1 to 10:30
- > 22: +1.0% + halve T2 + skip T3 + delay T1 to 11:00

**Premium fatness (combined CE+PE @ 2.5% OTM at 9:30):**
- < ₹2: thin → tighten 0.5%
- ₹2-6: default
- ₹6-15: elevated → +0.25%
- > ₹15: spike → +0.5% + halve T3

**Major events (FOMC, RBI, big earnings, geopolitical):**
- Today or tomorrow → +1.0% to all + skip T3 + delay T1 to 10:30+

### 2D. Limit-pricing rule (refined live 28-Apr-2026)

| 9:15-9:19 spot drift | CE leg limit | PE leg limit |
|---|---|---|
| Rally (UP > +0.1%) | **LTP exact** (no hike) | **LTP + ₹0.05-0.10** |
| Fall (DOWN > -0.1%) | **LTP + ₹0.05-0.10** | **LTP exact** |
| Flat (±0.1%) | LTP + ₹0.05 | LTP + ₹0.05 |

**Time budget:** unfilled +5 min → drop ₹0.05. Unfilled +10 min → market order or abort. Don't wait past 9:35 AM.

### 2E. Hold rule (the strategy's actual edge)
- **Don't square off on intraday wobbles.** Backtest shows 100% expire-worthless rate at 2.5%+ OTM E-0.
- Even when spot moves AGAINST your CE short, theta + IV crush eat premium faster than delta builds intrinsic.
- "Wait it out" beats "panic exit" 100% of sample days.

### 2F. Kill-switch thresholds (only fire on real emergency)

| Tier | Combined adverse move from entry | Action |
|---|---|---|
| E-1 advance | ≥ ₹6/share | Exit |
| E-0 T1 | ≥ ₹4/share | Exit |
| E-0 T2 | ≥ ₹4/share | Exit |
| E-0 T3 | ≥ ₹3/share | Exit |
| **Portfolio hard kill** | NIFTY ±1.5% intraday OR ₹35K/Cr loss | **Exit ALL** |

**Time-decayed risk thresholds:** the same distance % is more or less risky depending on minutes-to-expiry. By 15:00 PM with LTP ₹0.05, even 0.5% OTM is essentially safe. Use judgment.

### 2G. Asymmetric premium (put-skew dominance)

On rally days: PE is sticky (oscillates, sometimes UP), CE decays fast (theta+IV crush). Mirror on fall days. **Use this**: on rally days, PE side limit can be patient (LTP+0.10), CE must be fast (LTP).

---

## 3. Daily routine

| Time IST | Action | What/How |
|---|---|---|
| **~9:00 AM** | Login to Kite | `python3 scripts/kite_login.py` → click URL → paste back redirect URL |
| **9:15-9:30** | Detect conditions | Tell assistant: **"give me expiry levels for [date]"** |
| **9:25-9:35** | Place E-0 trades | Manual order placement on Axis/Monarch terminal per trade card |
| **10:00-10:15** | E-1 advance trade (Mon/Wed only) | Manual placement |
| **15:25** | Trades expire — no action needed | — |
| **16:30 (auto)** | Cron ingests today's minute data | **No action** — runs automatically |

**That's the entire daily routine.** Login + ask assistant + place orders. Ingest happens by itself.

---

## 4. Asking for trade levels — how to interface with the assistant

When you say **"give me expiry levels for [date]"**, the assistant will:

1. Fetch live conditions via Kite (spot, gap, VIX, premium @ 2.5%, nearest expiry, max-pain)
2. Web-search major events for that day + next day (FOMC, RBI, earnings, geopolitical)
3. Apply v2.0 strategy + all condition overlays
4. Return a trade card with:
   - Detected conditions block
   - All 4 tier strikes (E-1 advance + T1 + T2 + T3) with limit-price guidance
   - Expected per-Cr P&L (gross + net)
   - Kill-switch thresholds
   - Hold plan
   - **Major event flags + recommendations** (e.g., "FOMC tonight → reduce overnight size 50%, deepen distances +1%")

Example commands the assistant accepts:
- `give me expiry levels` → today's nearest expiry, full plan
- `give me expiry levels for 5-may` → for 5-May expiry (Mon 4-May would be E-1 advance day; Tue 5-May the E-0)
- `give me sensex levels` → SENSEX-specific (lot=20, grid=100)
- `give me only the e-0 plan` → skip the E-1 advance
- `update for live conditions` → re-pull and re-apply current overlays

---

## 5. Data infrastructure

### 5A. What's saved (per trading day, per instrument)

| Data | Volume | Reason |
|---|---|---|
| Underlying SPOT (NIFTY 50 / SENSEX) minute bars | ~375 rows | Spot path, gap, vol bucket |
| Futures (current month + next month) minute bars | ~750 rows | Spot proxy + basis |
| Option chain ±5% from open × 2 nearest weeklies | ~73,500 rows | Tradeable strikes only |
| **Per day per instrument** | **~75,000 rows · ~1 MB** | |
| Strikes >5% OTM, monthly/quarterly contracts | NOT saved | Irrelevant — never traded |

**Annual storage:** ~500 MB for NIFTY + SENSEX combined. **5-year storage:** ~2.5 GB.

### 5B. Where it lives

```
data/
├── parquet/
│   ├── instrument=NIFTY/year=YYYY/month=MM/<hash>.parquet
│   └── instrument=SENSEX/year=YYYY/month=MM/<hash>.parquet
├── kite_ingest_log.parquet   ← tracks (instrument, date, n_rows, ingested_at)
└── manifest.parquet          ← (legacy, for the bulk historical load)
```

**Dedup**: each (instrument, date) writes to a deterministic hash filename. Re-running same date reads existing file → concats → drops duplicates → writes back. **Safe to re-run.**

### 5C. How to access data yourself (CSV/Excel)

**Option A — Pre-existing CSVs from analyses:**
```
results/
├── 001_non_expiry_intraday_deep_otm/per_day.csv
├── 008_e_zero_time_distance_grid/full_grid.csv
├── 009_e_zero_minute_level_entry/minute_grid.csv
├── 007_real_broker_cost_winner/realistic_winners.csv
└── ...etc — every analysis produces a CSV
```

**Option B — Self-service exporter (`scripts/export_csv.py`):**
```bash
# Today's intraday for a few strikes (your traded ones, for example)
python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \
    --strikes 23400,23500,24700 --out my_strikes.csv

# Full intraday minute bars for one strike
python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \
    --strike 24700 --opt CE --out 24700_CE.csv

# NIFTY spot path for a date
python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \
    --spot --out spot_28apr.csv

# Full option chain snapshot at a specific time
python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \
    --chain-at 09:30 --out chain_at_930.csv

# P&L reconstruction for your live trades
python3 scripts/export_csv.py --pnl-summary --instrument NIFTY \
    --date 2026-04-28 \
    --positions "24700:CE:42900:0.81,23400:PE:50700:0.85" \
    --out my_pnl.csv

# Output goes to: results/exports/<filename>.csv
# Open in Excel, Numbers, Google Sheets — anything.
```

**Option C — Ad-hoc — ask the assistant:**
"Export 2.5% OTM premium history every minute for last 47 E-0 days as CSV" — I'll write the query and put a CSV in `results/exports/`.

---

## 6. Cron — auto-ingest infrastructure

**File:** `scripts/com.rohanshah.kite-ingest.plist`
**What it does:** runs `scripts/run_kite_ingest.py --days 2` Mon-Fri at 16:30 IST
**Pulls:** last 2 trading days × NIFTY + SENSEX → all spot/FUT/option chain minute bars (filtered to ±5% strikes × 2 nearest weeklies) → appends + dedupes into parquet
**Logs:** `results/kite_ingest_stdout.log` and `kite_ingest_stderr.log`

### Management commands

```bash
cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"

# Status
launchctl print gui/$(id -u)/com.rohanshah.kite-ingest

# Disable (long break)
launchctl unload "scripts/com.rohanshah.kite-ingest.plist"

# Re-enable
launchctl load -w "scripts/com.rohanshah.kite-ingest.plist"

# Manual run — last 7 trading days (catch up after holiday)
python3 scripts/run_kite_ingest.py --days 7

# Manual run — specific date
python3 scripts/run_kite_ingest.py --date 2026-05-04

# Force re-ingest (override log; for fixing corrupted data)
python3 scripts/run_kite_ingest.py --date 2026-05-04 --force

# Check today's cron output
tail -50 results/kite_ingest_stdout.log

# Check ingest log (what's been saved)
python3 -c "import pandas as pd; print(pd.read_parquet('data/kite_ingest_log.parquet').to_string(index=False))"
```

### Recovery / troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `python3 lib/kite_live.py` says "Missing kite_session.json" | Token expired | `python3 scripts/kite_login.py` |
| Cron log says "Kite session invalid" | Same | Same |
| Cron not running at 16:30 | Job unloaded | `launchctl load -w scripts/com.rohanshah.kite-ingest.plist` |
| Want to inspect today's ingest output | — | `tail -100 results/kite_ingest_stdout.log` |
| Need to wipe corrupted day | — | Delete the date's parquet file + run with `--force` |

---

## 7. Strategy file reference

| File | What it is |
|---|---|
| `STRATEGY_LIVE.md` | **Canonical strategy doc — v2.0 with all rules + sections 9F-9J of live-trading lessons** |
| `FINDINGS_LOG.md` | Append-only log of every analysis + live result |
| `analyses/001-009_*.py` | All backtest scripts (re-runnable) |
| `results/NNN_*/summary.md` | Markdown report per analysis with charts + tables |
| `results/backtest_report.pdf` | Combined PDF of all analyses (for sharing) |
| `lib/kite_live.py` | Live data adapter (used by assistant for trade cards) |
| `lib/kite_historical.py` | Rate-limited wrapper for Kite historical_data |
| `ingest/kite_daily.py` | Daily ingest engine (called by cron) |
| `scripts/kite_login.py` | Daily Kite login flow |
| `scripts/run_kite_ingest.py` | CLI wrapper for cron + manual runs |
| `scripts/export_csv.py` | Self-service CSV exporter |

---

## 8. Live trading lessons (28-Apr-2026 — the first live day)

1. **Strategy validated end-to-end.** 100% expire-worthless rate matched the 47-day backtest exactly.
2. **Theta + IV crush > delta on E-0 morning.** Even when spot rallies +49 pts in 30 min, CE premium drops 4-30%.
3. **Asymmetric premium ("put-skew dominance"):** 23400 PE went UP +12% during a +30 pt rally while 24700 CE dropped −8%. Put side is sticky on rally days.
4. **Limit fills**: 9:17-9:22 has only ~50% fill rate. **9:25-9:35 is the practical sweet spot** (~95% fill, ~85% premium captured vs 9:15 baseline).
5. **The drift-against side limit must be at LTP** (no premium hike) — it never returns. The drift-favorable side can be at LTP+0.05-0.10.
6. **Don't break the 2.0% T3 floor.** Tempting premium at 0.98% OTM worked once but sample shows 1% OTM = only 57% worthless rate. Single win ≠ rule validation.
7. **Real friction = ~11.4% of gross.** Much smaller than the placeholder model. Embed in projections.
8. **Late-day entry is profitable on calm-drift days but exposes to gap-and-trend tail.** Better to wait for next cycle than late-deploy on volatile days.

---

## 9. Risks, caveats, known gaps

1. **Backtest sample = ~1 year (47 NIFTY E-0 days, 46 E-1 days).** Cross-validation on 2024 data still pending.
2. **SENSEX support is unproven.** Only 8 historical days in parquet (the cron is now adding daily, but 30+ days needed before SENSEX-specific rules can be validated).
3. **Asymmetric distance overlays** (gap-up days CE further) are principled inferences, not individually backtested per condition cell.
4. **Vol-bucket conditioning** is currently impotent — every E-0 day in our sample had high_vol. Useless filter.
5. **Funding cost ₹600/Cr** conservatively applied to every event; real returns slightly higher if funding only occasional.
6. **Major-event filter is subjective** — assistant must do web-search per ask. Not perfectly automatable yet.
7. **Kill-switches are heuristic** — set 3× the per-trade stop derived from ₹7K/Cr cap. Permissive deliberately.

---

## 10. What's queued for future improvement

1. **Cross-validation backtest** on 2024 NIFTY data when ingested
2. **SENSEX backtest** once 30+ days of minute data accumulated
3. **Laddered strangle design** — combine 2.5%+3.5% to diversify intra-day correlation
4. **VIX/IV regime filter** — bucket days, check if filtering improves Sharpe
5. **Max-pain OI tracking** as an entry overlay (skip days where max-pain is far from spot)
6. **Automated fundamentals calendar feed** — replace web-search-on-demand with daily-pulled calendar JSON
7. **E-2 / E-3 clean survey** (a 004-style survey at DTE 2 and 3) to round out the picture

---

## 11. Quick-reference cheat sheet

```
Daily login (~9 AM):    python3 scripts/kite_login.py
Trade card request:     ask assistant: "give me expiry levels"
Manual data export:     python3 scripts/export_csv.py [options]
Ingest catch-up:        python3 scripts/run_kite_ingest.py --days 7
Cron status:            launchctl print gui/$(id -u)/com.rohanshah.kite-ingest
Strategy doc:           STRATEGY_LIVE.md
Findings log:           FINDINGS_LOG.md
PDF report:             results/backtest_report.pdf
CSVs from analyses:     results/NNN_*/{per_day,full_grid,...}.csv
Self-service exports:   results/exports/
```

---

*End of Operations Manual.*
*Strategy version: v2.0 (locked 2026-04-28)*
*Live infrastructure: Kite Connect API (App #2) + launchd cron (16:30 IST weekdays)*
*First validated live result: ₹3.88 lakh on ₹100 cr margin in 1 trading day*
