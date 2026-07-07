# Theta Quant — Deployment & Dev Setup

## Phase 1 — portability foundation ✅ DONE (2026-07-07)
The container/config keystone is in place; local run is unchanged, and the app
is now container- and AWS-ready. Shipped:
- **`lib/config.py`** — every path env-overridable (`TQ_DATA_DIR`, `TQ_CONFIG_DIR`,
  `TQ_PARQUET_DIR`, `TQ_JOURNAL`…); defaults reproduce the native Mac-mini layout,
  so setting nothing changes nothing. `journal.py` now sources its path from it.
- **`Dockerfile` + `docker-compose.yml` + `.dockerignore`** — one image runs on
  Docker / Fly / Render / AWS ECS-EC2. Data + secrets stay on the host, mounted in
  (Parquet **read-only**); nothing sensitive is baked into the image.
- **`.env.example`** (template only; real `.env` gitignored) + **`.github/workflows/ci.yml`**
  (byte-compile + import smoke + `docker build`, with **no data/secrets** in CI).

Next: Phase 2 (Supabase adapter, §4) — gated on your Supabase project + the
data-location decision. Phases below unchanged.

## 0. Security — status
The old committed PAT was **revoked** and git history purged (see the security
posture memo). The remote is set to the private repo `tGainR/Theta-gainers`.
Secrets live only as runtime files under `~/.config` (mode 600) — never in code,
git, or the Docker image. `.env` is gitignored (only `.env.example` is committed).

---

## 1. Push to GitHub
No remote is set (the previous remote belonged to a **separate, unrelated project** and has been removed). Create a fresh **private** repo for Theta Quant and add it:
```bash
git remote add origin <your-private-theta-quant-repo-url>
```

**Recommended auth — GitHub CLI (one-time):**
```bash
brew install gh         # if not installed
gh auth login           # choose GitHub.com → HTTPS → login via browser
```
Then push:
```bash
cd "…/05 - Backtest Engine (separate)"
git add -A && git commit -m "…"      # commit any pending work
git push -u origin main
```
(Alternative: create a **fine-grained PAT**, repo-scoped, and let macOS Keychain store it on first push — never put it back in the URL.)

**Note on data:** runtime data files (`data/trade_journal.jsonl`, `dummy_trades.jsonl`, `broker_costs.json`, snapshots, parquet) are small/ignored today, but for production they move to **Supabase** (section 4). Don't rely on git for live data.

---

## 2. Develop in VS Code
```bash
git clone <your-private-theta-quant-repo-url> theta-quant
cd theta-quant
# Python 3.11 is the law for this project (deps live only in 3.11)
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
- Install the **Python** + **Claude Code** extensions in VS Code. Select interpreter `.venv`.
- Run locally: `uvicorn dashboard.server:app --reload --port 8000` → http://localhost:8000
- Run the test harness anytime: `python scripts/smoke_test.py` (or ask the **qa-tester** agent).

---

## 2b. Portability rule — NEVER get locked in (enforced by the portability-guardian agent)
There is always **ONE standard build** that runs anywhere, and cloud providers are **optional, deletable adapters**.

- **Standard build = FastAPI + local-file storage** (JSONL / parquet / JSON). Runs with no cloud env vars or SDKs on: this laptop (same-WiFi users), a Mac mini, AWS, Hostinger/any VPS, or self-host. This is the forever-fallback.
- **Cloud code is a config-selected adapter.** When Supabase/Vercel is added: a storage interface `lib/store.py` with adapters `store_local.py` (default) and `store_supabase.py`, chosen by `STORAGE_BACKEND=local|supabase`. Vercel-only bits (`vercel.json`, `api/` shims) stay isolated.
- **Core never imports a cloud SDK directly** — only through the interface. Deleting the Supabase/Vercel files must leave the standard build 100% working.
- Cloud SDKs go in a separate `requirements-cloud.txt`, never in the base `requirements.txt`.

**If we ever leave Supabase+Vercel** → delete the adapter + cloud config, set `STORAGE_BACKEND=local`, and run the standard build on AWS / Hostinger / Mac mini / this laptop. Zero redevelopment. The **portability-guardian** agent checks this on every change (`grep` for stray cloud imports + local-build smoke test).

## 3. Architecture reality (read before choosing a host)
The app today is **stateful**: FastAPI server-rendered UI + local files (JSONL journals, parquet) + **Kite login** + **background monitor loops** (dummy/alerts, Telegram). Vercel is **serverless & stateless** — it cannot keep a Kite session, run a polling monitor, or read local parquet. So a naive "put it on Vercel" does NOT work.

**The right split:**
| Concern | Where it runs |
|---|---|
| Live engine: Kite login, ingest, monitor/alert loops, Telegram, parquet backtests | **Always-on box** — your Mac mini, or a small VPS (Render / Railway / Fly.io / a ₹500-1000/mo cloud VM) |
| Data: journals, dummy trades, holdings, costs, reports | **Supabase** (Postgres) |
| Web UI + reporting | Either keep **FastAPI on the always-on box** (simplest) OR rebuild as **Next.js on Vercel** reading Supabase |

### Path A — fastest, recommended to start
Keep the FastAPI app, run it on the **always-on Mac mini (or a VPS) in Docker**, point its data layer at **Supabase Postgres**, and expose it securely:
- HTTPS + public URL via **Cloudflare Tunnel** (free) or **Caddy** (auto-TLS). No port-forwarding, real cert → fixes the "connection not private" warning.
- Login/permissions (dealers/reporting/traders) added at the app layer (already scaffolded — Basic Auth now; role-based next).
- Mobile/desktop/laptop all just hit the one HTTPS URL.

### Path B — full Vercel + Supabase (bigger, later)
- Rebuild the front-end as **Next.js on Vercel** (the ui-ux + mobile agents help here).
- ALL data in **Supabase** (Postgres + Auth + Storage). Supabase Auth gives you the dealer/reporting/trader roles + row-level security for free.
- The **live engine stays on the always-on box** as a worker that writes to Supabase; Vercel reads Supabase. Kite session + monitor never run on Vercel.

---

## 4. Supabase migration (data → Postgres)
Move these local stores to Supabase tables (keeps the app multi-user & cloud-ready):
- `trades` (from `lib/journal.py` JSONL) — append-only events or a normalized table
- `dummy_strategies` (from `lib/dummy.py`)
- `holdings`, `broker_costs`, `bank_reco`
- `users` + `roles` (dealers enter-only, reporting edit+reco, traders, admin) → **Supabase Auth + RLS**

Steps: create a Supabase project → define tables (I can generate the SQL + a `lib/db.py` that swaps the JSONL stores for Supabase, behind the same function signatures so nothing else changes) → set `SUPABASE_URL` / `SUPABASE_KEY` env vars → migrate existing JSONL/Excel data in once.

---

## 5. Suggested sequence
1. ✅ Revoke the old token; push clean repo to GitHub.
2. Stand up **Supabase** project; I generate the schema + `lib/db.py` (drop-in for the JSONL stores).
3. **Path A**: Dockerize + run on the always-on Mac mini behind Cloudflare Tunnel (HTTPS, real login). You're live for the team, multi-device, no warnings.
4. Add **role-based login/permissions** (off locally, on in cloud).
5. UI/UX polish + mobile (the two agents) in parallel.
6. (Optional, later) Path B: Next.js front-end on Vercel reading Supabase.

> Use the **qa-tester** agent after every change, the **ui-ux-researcher** for polish, and **mobile-responsive** for phone layout.
