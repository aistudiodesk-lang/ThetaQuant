# ThetaDesk UI/UX Plan

Professional polish pass. Reference apps adapted into ThetaDesk design tokens (CSS vars
`--bg --surface --border --text-1/2/3`, classes `.card .card-flat .data-table .kpi*`,
colour scales gain/loss/warn/accent). No pixel copying — patterns only. Dark/light theme,
Indian money format (₹Cr/L, `1,80,050`) and all Alpine.js bindings preserved.

## Shared building blocks added to `base.html <style>`
- `.kpi-tile` — Screener-style bordered stat card (uppercase grey label + bold mono value + sub).
- `.kpi-grid` — responsive bordered KPI strip (divided cells, collapses 2→3→6 cols).
- `.seg` / `.seg-btn` — segmented control / time-range pills (Screener range pills, Sensibull tabs).
- `.tag` — neutral metadata chip (broker/demat/group labels).
- `.row-builder` — Sensibull per-leg builder row grid.
- `.stat-strip` — Sensibull strategy stat strip (Max P / Max L / R:R / POP / Margin).
- `.btn / .btn-primary / .btn-ghost` — consistent button styling so each page stops re-rolling its own.

These are additive; existing classes/bindings untouched.

## Page-by-page mapping

### overview.html  — Screener.in
| Before | After | Reference |
|---|---|---|
| 4 hero tiles, mixed gradient + plain cards | One restrained gradient hero (net P&L) + 3 clean bordered `.kpi-tile`s | Screener overview stat grid |
| "By strategy" plain cards | Same data, tighter `.kpi`-styled group cards w/ right-aligned mono values, hover ring | Screener |
| Bank mini + jump grid | Kept, restyled with `.tag` chips + section headers | Screener spacing |
| — | Footer disclaimer demoted to muted caption | — |

### report_full.html  — mProfit
| Before | After | Reference |
|---|---|---|
| Tab buttons re-rolled per element | `.seg` segmented control | mProfit tabbed sections |
| KPI strip (already 6-cell) | Promoted to `.kpi-grid`, mono values, gain/loss colour | Screener stat strip |
| Strategy table | mProfit holdings table: bold **net-worth `tr.total` footer** summing Premium / Margin / P&L; sticky header (already in `.data-table`); zebra hover | mProfit holdings |
| Filter row | Grouped into a `.card-flat` toolbar, left "portfolio/demat selector" feel | mProfit left selector |
| Group breakdown chips | active-state highlight kept, restyled as `.kpi-tile` mini | Screener |

### strategy_desk.html  — Sensibull
| Before | After | Reference |
|---|---|---|
| 3-cell summary card | Full **stat strip**: Net P&L · Premium · Max Profit · Reward/Risk · POP · Open — `.stat-strip` | Sensibull strategy builder header |
| Positions table | Cleaner mProfit-style table w/ `tr.total` premium/P&L footer | mProfit |
| New-position modal legs | Sensibull leg-builder rows (B/S · Type · Strike · Lots · Price) via `.row-builder`, labelled header | Sensibull leg rows |
| — | Optional inline SVG payoff sketch for covered-call calc | Sensibull payoff (bonus) |

## Guardrails honoured
- No edits to `server.py` or base.html `<aside>` / global-nav markup (only `<style>` additions).
- Tables stay in `overflow-x-auto`; grids collapse to 1–2 cols on phones.
- Verification: restart uvicorn + `python3.11 scripts/smoke_test.py` stays 30/30.
</content>
