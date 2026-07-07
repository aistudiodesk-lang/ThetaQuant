"""
lib/cc_assignment.py — consolidated holding-vs-sold + assignment-aware P&L for
Covered Calls Against Investment (S1).

Per underlying it nets:
  - Held qty (equity + futures) vs CE sold qty → uncovered/coverage. PE sold is
    counted but kept SEPARATE (different instrument, same strategy book).
  - Effective assignment strike = nominal CE strike + (net CE premium ÷ CE qty),
    where net premium = Σ(CE sold) − Σ(CE bought back to roll). Eating time value
    via rolls pushes the real exit price above the printed strike.
  - Assignment P&L = (effective_strike − basis) × ce_qty against TWO bases:
    original buy price (lifetime) and month-start notional (this-month). This
    replaces the phantom "CE leg deep-ITM loss" with the true net outcome.

Pure aggregation over full_report S1 legs + merged holdings + month-start notional.
"""
from __future__ import annotations


def _basis(h: dict):
    """Blended original cost basis from equity & futures avgs (qty-weighted)."""
    eq, fut = h.get("equity_qty") or 0, h.get("futures_qty") or 0
    ea, fa = h.get("equity_avg"), h.get("futures_avg")
    num, den = 0.0, 0.0
    if eq and ea: num += eq * ea; den += eq
    if fut and fa: num += fut * fa; den += fut
    return (num / den) if den else (ea or fa)


def consolidated(s_code: str = "S1") -> dict:
    from lib import full_report as fr, cc_holdings as CCH, cc_notional as NOT
    holdings = {(h.get("symbol") or "").upper(): h for h in CCH.merged_holdings()}
    notional = NOT.all_for_month()
    d = fr.load_report()

    agg = {}   # symbol -> CE/PE aggregates
    for r in d.get("trades", []):
        if r.get("s_code") != s_code:
            continue
        side = (r.get("type") or "").upper()
        if side not in ("CE", "PE"):
            continue
        sym = (r.get("symbol") or "").upper()
        if not sym:
            continue
        a = agg.setdefault(sym, {"ce_sold_qty": 0.0, "ce_buy_qty": 0.0,
            "ce_sold_val": 0.0, "ce_buy_val": 0.0, "ce_strike_x_qty": 0.0,
            "pe_sold_qty": 0.0, "spot": None})
        if a["spot"] is None and r.get("cur_price"):
            a["spot"] = r["cur_price"]
        sq = r.get("sell_qty") or 0
        sp = r.get("sell_price") or 0
        bq = r.get("buy_qty") or 0
        bp = r.get("buy_price") or 0
        strike = r.get("strike") or 0
        if side == "CE":
            a["ce_sold_qty"] += sq
            a["ce_buy_qty"] += bq
            a["ce_sold_val"] += sp * sq
            a["ce_buy_val"] += bp * bq
            a["ce_strike_x_qty"] += strike * sq
        else:
            a["pe_sold_qty"] += sq

    rows = []
    tot = {"held": 0.0, "ce_sold": 0.0, "pe_sold": 0.0, "uncovered": 0.0,
           "assign_pnl_orig": 0.0, "assign_pnl_notional": 0.0, "itm_count": 0,
           "underlying_pnl": 0.0, "ce_leg_pnl": 0.0, "net_pnl": 0.0}
    syms = set(agg) | set(holdings)
    for sym in syms:
        a = agg.get(sym, {})
        h = holdings.get(sym, {})
        held = h.get("total_qty") or 0
        ce_sold = a.get("ce_sold_qty", 0)
        ce_open = ce_sold - a.get("ce_buy_qty", 0)            # net open short CE
        pe_sold = a.get("pe_sold_qty", 0)
        spot = a.get("spot") or h.get("current")
        nominal = (a.get("ce_strike_x_qty", 0) / ce_sold) if ce_sold else None
        net_prem = a.get("ce_sold_val", 0) - a.get("ce_buy_val", 0)
        eff_strike = (nominal + net_prem / ce_open) if (nominal and ce_open) else nominal
        itm = bool(nominal and spot and spot > nominal)
        basis_orig = _basis(h)
        basis_not = notional.get(sym)
        qty_for_assign = ce_open if ce_open > 0 else 0
        ap_orig = ((eff_strike - basis_orig) * qty_for_assign) if (eff_strike and basis_orig and qty_for_assign) else None
        ap_not = ((eff_strike - basis_not) * qty_for_assign) if (eff_strike and basis_not and qty_for_assign) else None
        uncovered = (held - ce_sold) if held else None
        # ── CONSOLIDATED CE + underlying P&L ───────────────────────────────
        # The point: a CE that has gone ITM shows a loss, but the underlying
        # future/equity it's written against has GAINED. Net them.
        underlying_pnl = ((spot - basis_orig) * held) if (spot and basis_orig and held) else None
        # short-CE MTM ≈ premium kept − intrinsic owed (intrinsic only if ITM)
        ce_intrinsic = max(0.0, (spot - nominal)) * ce_open if (spot and nominal and ce_open > 0) else 0.0
        ce_leg_pnl = (net_prem - ce_intrinsic) if (net_prem or ce_intrinsic) else None
        net_pnl = None
        if underlying_pnl is not None or ce_leg_pnl is not None:
            net_pnl = (underlying_pnl or 0) + (ce_leg_pnl or 0)
        row = {
            "symbol": sym, "name": h.get("name") or sym,
            "held_qty": held, "equity_qty": h.get("equity_qty") or 0,
            "futures_qty": h.get("futures_qty") or 0,
            "ce_sold_qty": ce_sold, "ce_open_qty": ce_open, "pe_sold_qty": pe_sold,
            "uncovered_qty": uncovered,
            "coverage_pct": round(ce_sold / held * 100, 1) if held else None,
            "spot": spot, "nominal_strike": round(nominal) if nominal else None,
            "net_premium": round(net_prem) if net_prem else None,
            "effective_strike": round(eff_strike, 1) if eff_strike else None,
            "itm": itm,
            "basis_original": round(basis_orig, 1) if basis_orig else None,
            "basis_notional": round(basis_not, 1) if basis_not else None,
            "assign_pnl_original": round(ap_orig) if ap_orig is not None else None,
            "assign_pnl_notional": round(ap_not) if ap_not is not None else None,
            "underlying_pnl": round(underlying_pnl) if underlying_pnl is not None else None,
            "ce_leg_pnl": round(ce_leg_pnl) if ce_leg_pnl is not None else None,
            "net_pnl": round(net_pnl) if net_pnl is not None else None,
        }
        rows.append(row)
        tot["held"] += held or 0
        tot["ce_sold"] += ce_sold or 0
        tot["pe_sold"] += pe_sold or 0
        tot["uncovered"] += (uncovered or 0) if (uncovered and uncovered > 0) else 0
        if ap_orig: tot["assign_pnl_orig"] += ap_orig
        if ap_not: tot["assign_pnl_notional"] += ap_not
        if underlying_pnl: tot["underlying_pnl"] += underlying_pnl
        if ce_leg_pnl: tot["ce_leg_pnl"] += ce_leg_pnl
        if net_pnl: tot["net_pnl"] += net_pnl
        if itm: tot["itm_count"] += 1

    rows.sort(key=lambda x: (not x["itm"], -(x.get("ce_sold_qty") or 0)))
    for k in ("held", "ce_sold", "pe_sold", "uncovered", "assign_pnl_orig",
              "assign_pnl_notional", "underlying_pnl", "ce_leg_pnl", "net_pnl"):
        tot[k] = round(tot[k])
    return {"rows": rows, "totals": tot, "month": NOT.month_key()}
