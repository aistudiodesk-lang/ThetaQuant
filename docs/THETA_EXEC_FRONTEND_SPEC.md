# Theta Gainers Execution Frontend — port spec (→ Jinja/Alpine, current look)

Source: `strategies/Theta Gainers Algo Development/01 - Main Code (for Developer)/frontend/src/`.
Backend logic already ported to `lib/algo_engine.py` (state machine, strike selection, triggers, risk loop).

## Lifecycle states (canonical)
DRAFT · MONITORING · ENTERING · LIVE · EXITING · CLOSED · EMERGENCY_HALT.
Chips: LIVE green · MONITORING blue · ENTERING/EXITING yellow · CLOSED gray · HALT red · DRAFT gray.

## Builder (NewStrategy) sections, in order
1. Header: name + Save-as-Template + Load-Template select.
2. Broker & Demat picker (single / multi-broker SOR / multi-demat) + margin allocation row.
3. Margin status strip (free/total/used/blocked + %free bar).
4. Default Strategy CTA (deep-OTM strangle: CE ceil(spot×1.025/grid), PE floor(spot×0.975/grid), target ₹5K/Cr) → Load / Load+Execute.
5. Basics: name, portfolio, underlying (NIFTY lot65/grid50, SENSEX lot20/grid100).
6. Strike selection: Manual / Automatic rule builder.
7. Legs table: ∑(in-trigger) · B/S · expiry · strike(±grid) · type CE/PE · lots · order(LIMIT/+buf/MARKET-off) · LTP · trade price · expand(quote/range/snapshots) · dup/remove. COMBINED/PER_CR lock to 2 legs.
8. Entry time window: restrict toggle + from/to + presets (Open/Morn/Mid/Aft/All).
9. Premium trigger: COMBINED ≥₹ · PER_CR ≥₹/Cr · SEPARATE (per-leg price, linked/independent) · NONE (enter now). Live sum + MET/Waiting chip.
10. Exit rules: SL ₹ · Target ₹ · Square-off IST · MTM-DD kill % · Trailing(trigger/step) · Lock-in(amount→breakeven) · Spot-proximity(points/%, leg/both) · Dead-man(s).
11. Pre-trade preview + 2-person approval (≥5 lots) + iceberg(freeze 1800/1000).
12. Live margin gauge (disable submit if exceeds free).
13. Sticky action bar: Cancel · Save Draft · [Start Monitor | Execute Now] (confirm modal; Execute/Kill = type-to-confirm).

## Execute flow
DRAFT —Save Draft→ DRAFT · —Start (trigger≠NONE)→ MONITORING —trigger met→ ENTERING —fills→ LIVE.
DRAFT —Execute Now (trigger NONE)→ ENTERING→LIVE (type "EXECUTE"). LIVE —Exit→ EXITING→CLOSED · —Kill→ HALT→CLOSED (type "KILL").

## Monitor / lists
- Dashboard "Active strategies" table by state (poll 5s): ID·underlying·strikes·lots·state chip·P&L·Monitor→.
- Strategy detail: unrealized P&L (peak/DD), positions (leg/strike/qty/entry/ltp/slip/pnl + modify/add/close), premium-history chart, order log, exec metrics. Header: Clone/Rollover/Pause/Exit/Kill.

## Templates
Inline quick-presets (Short Strangle/Iron Condor/Bull Put/Calendar) + Templates page (★/name/kind/legs/selection/lastUsed/winRate/avgPnl + apply/dup/edit/delete). kinds: SHORT_STRANGLE/STRADDLE/IRON_CONDOR/BULL_PUT/BEAR_CALL/CUSTOM.

## Notifications
Toasts top-right (success/error/info/warn, 4s). Confirm modals (type-to-confirm for EXECUTE/KILL). WS /strategy/{id}/stream → pnl_tick/state_change/order_update/log.

## Endpoints (map to algo_engine + dummy)
GET/POST /strategy · /strategy/{id}/start|execute-now|exit|kill · POST /strategy/preview-margin · GET /broker/list,/broker/{id}/demats,/broker/margin/summary · POST /broker/margin/allocate · GET /admin/me/permissions · GET/POST/PUT/DELETE /templates · WS /strategy/{id}/stream.

## Build status in this app
- Backend lifecycle: lib/algo_engine.py (ported). Live monitor loop: lib/dummy.py (ARMED→ENTERED→TP/SL via /api/dummy/check).
- Phase 1 (this pass): rebuild Execution page to the lifecycle structure in current look — builder (legs/trigger/exit/entry), Save template, Start-monitor + Execute-now (confirm), Waiting/Live/Closed sections. Backed by dummy engine + presets.
- Phase 2 (next): broker/demat SOR + margin allocation + margin strip; per-leg monitor detail page + WS; Templates analytics page; wire algo_engine's full RMS loop server-side.
