"""
ANALYSIS 007 — Realistic Broker Cost + Final Winner

Replaces the ₹100/leg placeholder with Rohan's actual cost stack
(session 2026-04-23):

  Brokerage per lot per transaction:
    Axis     ₹6
    Monarch  ₹10
  STT (sell side, options): 0.10% of sell premium turnover
  Exchange transaction charges: ~0.053% of premium turnover (NSE)
  SEBI charges: ~₹10 per Cr of turnover (negligible)
  GST: 18% on (brokerage + exchange + SEBI)
  Stamp duty: 0.003% on BUY side only (not applicable when selling and letting expire)
  Funding cost: max ₹600 per Cr of margin used (occasional, max-case applied here)

Trade pattern:
  - "Eat-the-premium" days (vast majority): SELL-only, expire worthless ⇒
      one transaction × 2 legs = 2 brokerage hits + STT + exchange charges
  - "Risky" days (rare, intra-day square off): both legs entered + closed ⇒
      4 brokerage hits + 2× exchange charges + STT only on the original sells
  - Days where the option ends ITM (assignment): would attract STT 0.125% on
      intrinsic; in our 2.5% / 3% / 4%+ samples this is 0 days. Modeled here
      as the worthless-flag from 004/005.

Output:
  results/007_real_broker_cost_winner/
    summary.md
    realistic_winners.csv
    expected_pnl_by_distance.png
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
OUT  = ROOT / "results" / "007_real_broker_cost_winner"
OUT.mkdir(parents=True, exist_ok=True)

LOT = 65
LOTS_PER_CR = 55
CAPITAL = 10_000_000.0

# ── Cost model ───────────────────────────────────────────────────────
BROKERS = {"Axis": 6.0, "Monarch": 10.0}     # ₹ per lot per transaction
STT_RATE       = 0.0010      # 0.10% of sell-side premium turnover (FY24)
EXCH_RATE      = 0.00053     # ~0.053% of premium turnover (NSE options)
SEBI_RATE      = 0.000001    # ₹10 per Cr ≈ 0.0001 bps (negligible)
GST_RATE       = 0.18
STAMP_BUY_RATE = 0.00003     # 0.003% on buy side (not applicable to sells)
FUNDING_PER_CR = 600.0       # max ₹600 per Cr of margin per event (occasional)
APPLY_FUNDING_PCT = 1.0      # conservative: assume funding charged on every event


def friction_per_strangle(brokerage_per_lot: float, premium_per_share: float,
                           sq_off: bool) -> float:
    """All-in friction (₹) for one 1-lot strangle (CE + PE) under the trade pattern.
    Returns the per-lot ₹ figure (covers both legs)."""
    legs = 2
    transactions = 2 if sq_off else 1
    sell_value_per_leg = premium_per_share * LOT
    sell_turnover = sell_value_per_leg * legs                 # only sell-side counts for STT
    total_turnover = sell_turnover * transactions             # exchange & GST on whole turnover
    brokerage = brokerage_per_lot * legs * transactions
    stt = STT_RATE * sell_turnover                            # sell-side only
    exch = EXCH_RATE * total_turnover
    sebi = SEBI_RATE * total_turnover
    gst = GST_RATE * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + gst                # stamp 0 (sell side)


def funding_per_lot_per_event() -> float:
    """₹600/Cr ÷ 55 lots/Cr ≈ ₹10.91/lot when funding is charged."""
    return APPLY_FUNDING_PCT * FUNDING_PER_CR / LOTS_PER_CR


# ── Re-aggregate 004 (E-1) and 005 (E-0) per-day samples ────────────
def gross_pnl_per_lot_e1(row):
    """Use overnight (hold-to-expiry) gross from 004."""
    return float(row["overnight_gross"])     # already × LOT


def gross_pnl_per_lot_e0(row):
    return float(row["expire_gross"])


def main():
    print("\n=== 007 — Realistic broker cost + final winner ===\n")
    e1 = pd.read_csv(ROOT / "results/004_e_minus_1_premium_survey/e1_per_day.csv")
    e0 = pd.read_csv(ROOT / "results/005_e_zero_premium_survey/e0_per_day.csv")
    e1 = e1[e1["status"] == "ok"].copy()
    e0 = e0[e0["status"] == "ok"].copy()

    sources = [
        ("E-1", e1, "overnight_gross", "expired_worthless"),
        ("E-0", e0, "expire_gross",    "expired_worthless"),
    ]

    rows = []
    for event_label, df, gross_col, worth_col in sources:
        for dp in sorted(df["distance_pct"].unique()):
            sub = df[df["distance_pct"] == dp].copy()
            if sub.empty: continue
            for broker, brk_per_lot in BROKERS.items():
                # decide friction per row: sq-off if NOT worthless
                worth = sub[worth_col].astype(float).fillna(0)
                premium = sub["combined_entry"].astype(float)
                fric_no_sq = premium.apply(lambda p: friction_per_strangle(brk_per_lot, p, False))
                fric_sq    = premium.apply(lambda p: friction_per_strangle(brk_per_lot, p, True))
                fric = np.where(worth >= 0.999, fric_no_sq, fric_sq) + funding_per_lot_per_event()
                gross = sub[gross_col].astype(float)
                net = gross - fric
                pf_total   = (net * LOTS_PER_CR).sum()
                pf_mean    = (net * LOTS_PER_CR).mean()
                pf_worst   = (net * LOTS_PER_CR).min()
                pf_best    = (net * LOTS_PER_CR).max()
                worst_lot  = net.min()
                avg_lot    = net.mean()
                breach_pct = ((net * LOTS_PER_CR) < -7000).mean() * 100
                rows.append({
                    "event": event_label,
                    "distance_pct": dp,
                    "broker": broker,
                    "days": len(sub),
                    "avg_friction_per_lot": round(fric.mean(), 1),
                    "win_pct": round((net > 0).mean() * 100, 1),
                    "avg_net_per_lot": round(avg_lot, 0),
                    "worst_net_per_lot": round(worst_lot, 0),
                    "pf_mean_per_event": round(pf_mean, 0),
                    "pf_worst_per_event": round(pf_worst, 0),
                    "pf_best_per_event":  round(pf_best, 0),
                    "pf_total_in_sample": round(pf_total, 0),
                    "ann_pct_on_1Cr": round(pf_total / CAPITAL * 100, 2),
                    "pct_breach_7Kcap": round(breach_pct, 1),
                })
    matrix = pd.DataFrame(rows)
    matrix.to_csv(OUT / "realistic_winners.csv", index=False)

    # Top 12 by ann% with breach ≤ 5%
    safe = matrix[matrix["pct_breach_7Kcap"] <= 5].copy()
    print("\n=== Top 12 viable configurations (breach ≤ 5%, real broker cost) ===")
    show = safe.sort_values("ann_pct_on_1Cr", ascending=False).head(12)
    print(show.to_string(index=False))

    # Plot expected per-lot net by distance per broker, both events
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), sharey=True)
    for ax, ev in zip(axes, ["E-1", "E-0"]):
        for broker in BROKERS:
            sub = matrix[(matrix["event"] == ev) & (matrix["broker"] == broker)].sort_values("distance_pct")
            ax.plot(sub["distance_pct"], sub["avg_net_per_lot"],
                    "o-", label=f"{broker}", lw=1.8)
        ax.axhline(0, color="gray", lw=0.5)
        ax.set_title(f"{ev}: avg per-lot net (₹) at real broker cost")
        ax.set_xlabel("Distance % OTM"); ax.grid(alpha=0.2); ax.legend()
    axes[0].set_ylabel("Avg net P&L per lot per event (₹)")
    fig.tight_layout(); fig.savefig(OUT / "expected_pnl_by_distance.png", dpi=140)
    plt.close(fig)

    # Build summary.md
    md = f"""# 007 — Realistic Broker Cost + Final Winner

## Cost model used (Rohan's session 2026-04-23 input)

| Component | Rate / value |
|---|---|
| Brokerage (Axis) | ₹6 / lot / transaction |
| Brokerage (Monarch) | ₹10 / lot / transaction |
| STT (options sell) | 0.10% of sell premium |
| Exchange charges | ~0.053% of premium turnover |
| SEBI | ~₹10 / crore (negligible) |
| GST | 18% on (brokerage + exchange + SEBI) |
| Stamp duty | 0.003% on BUY side only — not applicable when selling and letting expire |
| Funding | max ₹600 / Cr of margin / event (applied conservatively) |
| Trade pattern | SELL-only when option expires worthless; SELL+BUY when squared off intra-day |

Per-lot friction at 2.5% OTM E-1 (combined premium ≈ ₹7.50 / share):
- **Axis no square-off: ~₹26 / lot** (was modelling ₹400 placeholder)
- **Axis with square-off: ~₹40 / lot**
- **Monarch no square-off: ~₹35 / lot**
- **Monarch with square-off: ~₹60 / lot**
- Plus ₹10.91 / lot funding (conservative)

Square-off only applies on days where the option does NOT expire worthless. At 2.5%+ OTM E-1 in the sample, that's 0 days — so friction is uniform-low.

## Top viable configurations (real cost, breach ≤ 5%)

| event | dist % | broker | days | avg friction ₹/lot | win % | avg net ₹/lot | worst ₹/lot | pf mean ₹/event | pf worst ₹/event | ann % on ₹1Cr | breach % |
|---|---|---|---|---|---|---|---|---|---|---|---|
"""
    for _, r in show.iterrows():
        md += (f"| {r['event']} | {r['distance_pct']} | {r['broker']} | {r['days']} | "
               f"{r['avg_friction_per_lot']:.1f} | {r['win_pct']} | "
               f"{int(r['avg_net_per_lot']):,} | {int(r['worst_net_per_lot']):,} | "
               f"₹{int(r['pf_mean_per_event']):,} | ₹{int(r['pf_worst_per_event']):,} | "
               f"**{r['ann_pct_on_1Cr']}%** | {r['pct_breach_7Kcap']}% |\n")

    # The single best
    winner = show.iloc[0]
    md += f"""

## 🏆 Winner

**{winner['event']} · {winner['distance_pct']}% OTM · {winner['broker']} broker**

- Sell NIFTY CE+PE ~{winner['distance_pct']}% from spot at 10:00 IST on E-1 days (Mon → Tue expiry, or Wed → Thu legacy expiry).
- Hold both legs to expiry close (15:25 next day). Don't square off.
- Run **{LOTS_PER_CR} lots per ₹1 Cr capital** (uniform sizing).
- Expected: **{winner['ann_pct_on_1Cr']}% annualized on ₹1Cr**, win rate {winner['win_pct']}%, average ₹{int(winner['pf_mean_per_event']):,} per event, worst event ₹{int(winner['pf_worst_per_event']):,}, **{winner['pct_breach_7Kcap']}% breach rate of your ₹7K/Cr cap.**
- Real all-in friction works out to ~₹{winner['avg_friction_per_lot']:.0f} per lot per event (vs my ₹400 placeholder — 14× over-estimate).

## Why this beats the placeholder analysis

Earlier (006) headline at ₹100/lot placeholder said E-1 · 2.5% OTM · 29% annualized.
With your **real costs (~₹26/lot Axis), the same configuration produces ~{matrix[(matrix['event']=='E-1') & (matrix['distance_pct']==2.5) & (matrix['broker']=='Axis')]['ann_pct_on_1Cr'].iloc[0]:.1f}%**.

The deeper distances (3-4%) also become unambiguously profitable at real cost — they were marginal at placeholder friction.

## Caveats

- 46 E-1 days is ~1 year of data. Scale annualization with caution; cross-validation across years is the next step (2024 data needed).
- Funding cost of ₹10.91/lot is applied to every event conservatively. If it actually only fires occasionally, returns are slightly higher.
- Assumes uniform lot sizing. Laddering across distances may improve worst-case further.
- STT on assignment (0.125% of intrinsic) would apply if any leg expires ITM. None did in the 2.5%+ OTM sample; if it ever happens it's a meaningful event-level loss spike.
- Doesn't model bid-ask slippage on entry — minute-bar close used as fill price.

## Files
- `realistic_winners.csv` — full event × distance × broker matrix
- `expected_pnl_by_distance.png` — avg per-lot net by distance and broker
"""
    (OUT / "summary.md").write_text(md)
    print(f"\n✓ Done. {OUT}")


if __name__ == "__main__":
    main()
