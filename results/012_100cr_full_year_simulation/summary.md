# 012 — ₹100 Cr Full Year Simulation (4-Tier Strategy)

## Capital allocation

| Tier | % | NIFTY dist | SENSEX dist | Notes |
|---|---|---|---|---|
| ULTRA_SAFE | **80%** | 3.0% OTM | 3.5% OTM | Workhorse — bulletproof |
| E-1 ADVANCE | 5% | 3.5% OTM | 4.0% OTM | Carry from previous day |
| MID_RISK | 12% | 2.0% OTM | 2.5% OTM | Higher premium, modest tail |
| MID_HIGH | 3% | 1.0% OTM | 1.5% OTM | Signal-gated, tight SL |

## Per-tier annual P&L (on ₹100 Cr)

| Tier | Events traded | Median per event | Annual P&L | Best | Worst |
|---|---|---|---|---|---|
| ULTRA_SAFE (80%) | 107 | ₹3.82L | **₹5.93 Cr** | ₹70.93L | ₹0.00L |
| E-1 ADVANCE (5%) | 108 | ₹0.11L | **₹0.16 Cr** | ₹1.60L | ₹0.03L |
| MID_RISK (12%) | 107 | ₹1.01L | **₹2.14 Cr** | ₹30.89L | ₹-23.56L |
| MID_HIGH (3%) | 108 | ₹-0.06L | **₹0.15 Cr** | ₹5.64L | ₹-4.74L |

## TOTAL on ₹100 Cr

- **Annual P&L: ₹8.38 Cr** (8.38%)
- Median per event: ₹4.69L (0.047% of capital per event)
- Win rate: 96.3% of events positive
- Best event: ₹109.06L · Worst event: ₹-23.33L

## Caveats

- ULTRA_SAFE + MID_RISK use REAL backtest data (NIFTY 54 + SENSEX 53 events × distance)
- E_1_ADVANCE approximated at 60% of E-0 P&L (no direct overnight-hold backtest yet — to be added)
- MID_HIGH (1.0% OTM) approximated: 50% signal-take rate × 85% win × 1.8× mid-risk premium - 15%×₹2K/lot loss. **Needs proper backtest** at 1.0% OTM with signal filter.
- Real friction included (Axis ₹6/lot + STT + GST + exchange + funding).
- Sample = 1 year (April 2025 → April 2026). Cross-validate when 2024 data lands.
