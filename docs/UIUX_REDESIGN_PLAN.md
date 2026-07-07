# Theta Gainers — UI/UX Redesign Plan

Sources reviewed: old "Covered Call App Development Old" (Project_Full_Internal, Google-Sheet
architecture, Internal Reporting doc, AI wireframe JSX, References: Sensibull/BornToSell/mProfit,
UI/UX drafts 1–3) + the Sensibull screenshots given this session + the current live app.

## 1. Diagnosis — why it feels complicated today
- **Two overlapping nav models** (left sidebar + global top nav) that say similar things → cognitive overhead.
- **Inconsistent per-page layouts**: CC Against Investment has 5 tabs, desks have 2, reporting has its own. Nothing is learnable because each page is different.
- **CC Against Investment is the worst offender**: holdings input is buried, equity vs futures aren't cleanly separated ("weird capture"), and selling-plan + monitoring + holdings are scattered across tabs.
- **No single "this is my whole book" consolidated view** — the one thing the software should do better than the Google Sheet.
- **No progressive disclosure** — every page dumps everything at once instead of summary→drill.

## 2. Design principles (best-of Sensibull / Screener / mProfit / BornToSell)
1. **One nav model.** Left sidebar = sections. Within a section, a slim **segmented control** for views. Drop the duplicate top-nav menus (keep only a global search + account).
2. **Every section uses the SAME skeleton:** sticky header (title + KPI strip) → view switcher → content. Learn it once, know the whole app.
3. **Progressive disclosure:** summary first; click a row → expand/drawer (Sensibull-style) instead of tab-hopping.
4. **Consistent components:** cards, Indian number format (Cr/L), RGB signals, one table style, drill-down drawer, inline edit.
5. **Mobile-first responsive.**

## 3. Information architecture (sidebar sections, each with the standard skeleton)
1. **Overview** — whole-book consolidated dashboard
2. **Expiry** — Trade Desk / Harvest / Chain / Report / **Execution (paper algo)**
3. **Index** — Monthly OTM / Long NIFTY
4. **Covered Calls** — Against Investment / Regular OTM / ITM
5. **Other Strategies** — Commodity / custom
6. **Reporting** — Full / period reports / consolidated dashboard
7. **Margin & Ledger**

Within each strategy section the standard is **Strategy | Monitoring** (already built for the desks).

## 4. ⭐ Covered Calls Against Investment — the redesign (your priority)
One section, three views in workflow order. Fixes the "weird" equity/futures capture with a clean data model.

### View 1 — Holdings (INPUT)
"What I own", entered cleanly per underlying:
- **Equity** block: qty + avg buy + current value
- **Futures** block: qty + avg buy + current value  *(kept visually separate, not blended)*
- Lot size, current price, 52-week high
- Inline add/edit row; or auto-pulled from the Google Sheet (ingest in flight)
- **Total held = equity + futures** shown explicitly

### View 2 — Selling Plan (the ladder, BornToSell-style)
Per underlying row: **Held (eq+fut) | Sold (CE qty) | Uncovered | Suggested strike | premium | yield/mo | eligibility (RGB)**.
- Sized to cover the uncovered qty.
- **Dropdown per row → exactly which strikes are sold, at what premium, + live RGB status** (the thing you asked for).

### View 3 — Consolidated (the software's edge over the Sheet)
One screen the Google Sheet can't do live:
- KPI bar: Holding value · Futures value · Premium collected (MTD) · Coverage % · Uncovered value · Margin used
- Interactive breakdown (by stock / by moneyness)
- Full book table, each row drills into holdings + sold strikes + RGB + P&L

**Data-model fix:** holdings store keyed by underlying with explicit `equity{qty,avg}` + `futures{qty,avg}` blocks; "sold against" pulled from journal + S1 sheet; uncovered computed. (Re-shapes the existing cc_holdings store so capture is clean, not weird.)

## 5. Standard strategy desks (Index / CC-Regular / ITM / Commodity)
Keep **Strategy | Monitoring**. Polish: Strategy = selling-plan calculator (+ CC holdings plan); Monitoring = live positions + RGB + payoff. Same component as CC for consistency.

## 6. Overview + Reporting
- **Overview**: the consolidated book dashboard (all strategies) — KPI bar + pie (by strategy/stock/moneyness) + drill-down, mirroring the old Internal Reporting doc's Dashboard tab.
- **Reporting**: keep Full Reporting + period reports; add the dashboard view; filters fixed (done).

## 7. Ease-of-use (cross-cutting)
- One reusable view-switcher + sticky KPI header everywhere.
- Drill-down drawers instead of tab-hopping.
- Inline edit (avoid modals where possible).
- Empty/loading states, toasts on save.
- Global search; quick-add.
- Mobile responsive pass.

## 8. Build phases
- **Phase 1 (priority): Design-system + nav unification + CC Against Investment 3-view redesign.**
- Phase 2: Overview consolidated dashboard + Reporting dashboard.
- Phase 3: Desk polish + payoff/Greeks + mobile pass.
- Phase 4 (later): AI assistant; Google-Sheet ingest (in flight).

## 8b. Google Sheet sync — LOCKED RULES (user-mandated)
- **Zero AI tokens.** Entire pipeline = Drive API fetch + pandas parse + JS confirm dialog. No LLM call anywhere.
- **Expiry excluded.** Sheet never overrides the Expiry section — it stays sourced from screenshots/journal. Sheet feeds all OTHER sections only.
- **Section-wise confirm, never silent, never per-trade.** On change, detect which sections changed (Index / Covered Calls / Commodity / Margin…) via a per-section content hash; show one "Apply / Review / Skip" banner per changed section. User approves a whole section; individual trades are never auto-applied alone.
- **Change detection is cheap/tokenless:** compare Drive `modifiedTime` then per-section hash; only prompt on real diffs.
- **Frequency:** check on page load + a manual "Check for updates" button (default). Optional light background poll. All tokenless.
- Manual entries are the base; an *approved* Sheet section overrides them. Until approved, manual stands.

## 9. What stays / is reused (don't rebuild)
Two-section desks, RGB monitor engine, full_report parser, journal, holdings store (re-shaped), reporting filters (fixed), algo engine. This is a re-skin + restructure, not a rewrite. Portability rule preserved (standard build stays host-agnostic).
