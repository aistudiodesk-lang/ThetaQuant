"""
Render OPERATIONS_MANUAL with proper code-block highlighting & callouts → PDF.
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 PageBreak, Table, TableStyle, KeepTogether)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "OPERATIONS_MANUAL.pdf"

styles = getSampleStyleSheet()
styles.add(ParagraphStyle("Title2", parent=styles["Heading1"], fontSize=24,
                           textColor=colors.HexColor("#0F172A"), alignment=1, spaceAfter=8))
styles.add(ParagraphStyle("Subtitle", parent=styles["Heading2"], fontSize=18,
                           textColor=colors.HexColor("#1E40AF"), alignment=1, spaceAfter=4))
styles.add(ParagraphStyle("H1x", parent=styles["Heading1"], fontSize=18,
                           textColor=colors.HexColor("#0F172A"), spaceAfter=10, spaceBefore=14))
styles.add(ParagraphStyle("H2x", parent=styles["Heading2"], fontSize=13,
                           textColor=colors.HexColor("#1E40AF"), spaceAfter=6, spaceBefore=10))
styles.add(ParagraphStyle("H3x", parent=styles["Heading3"], fontSize=10.5,
                           textColor=colors.HexColor("#334155"), spaceAfter=4))
styles.add(ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9.5, leading=13))
styles.add(ParagraphStyle("Mono", parent=styles["BodyText"], fontName="Courier",
                           fontSize=8.5, leading=11, textColor=colors.HexColor("#0F172A")))
styles.add(ParagraphStyle("MonoComment", parent=styles["BodyText"], fontName="Courier",
                           fontSize=8.5, leading=11, textColor=colors.HexColor("#64748B"),
                           fontStyle="italic"))
styles.add(ParagraphStyle("CalloutTitle", parent=styles["BodyText"], fontSize=10.5,
                           textColor=colors.HexColor("#0F172A"), spaceAfter=4))
styles.add(ParagraphStyle("CalloutBody", parent=styles["BodyText"], fontSize=9.5, leading=13))


def _para(text, style="Body"):
    return Paragraph(text, styles[style])


def code_block(lines, accent="#1E40AF"):
    """A shaded box with code lines, accent border on left."""
    rows = []
    for line in lines:
        if not line:
            line = "&nbsp;"
        else:
            esc = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                       .replace(" ", "&nbsp;"))
            # comment lines styled muted
            if line.lstrip().startswith("#"):
                rows.append([Paragraph(f"<font face='Courier' size='8.5' color='#64748B'>{esc}</font>", styles["Body"])])
            else:
                rows.append([Paragraph(f"<font face='Courier' size='8.5' color='#0F172A'>{esc}</font>", styles["Body"])])
    t = Table(rows, colWidths=[16.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#F1F5F9")),
        ("LINEBEFORE", (0,0), (0,-1), 3, colors.HexColor(accent)),
        ("BOX", (0,0), (-1,-1), 0.4, colors.HexColor("#94A3B8")),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))
    return t


def callout(label, body, bg="#FEF08A", border_left="#1E40AF"):
    """A shaded callout box with bold label + body."""
    rows = [[Paragraph(f"<b>{label}</b>", styles["CalloutTitle"])]]
    rows.append([Paragraph(body, styles["CalloutBody"])])
    t = Table(rows, colWidths=[16.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor(bg)),
        ("LINEBEFORE", (0,0), (0,-1), 5, colors.HexColor(border_left)),
        ("BOX", (0,0), (-1,-1), 0.5, colors.HexColor("#64748B")),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    return t


def kv_table(rows, col_widths=None):
    """Generic data table with header + alternating rows."""
    n = len(rows[0]) if rows else 0
    cw = col_widths or [16.5/n*cm] * n
    cells = []
    for r in rows:
        cells.append([
            Paragraph(str(c), styles["Body"]) if not isinstance(c, Paragraph) else c
            for c in r
        ])
    t = Table(cells, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1E40AF")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 8.5),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#CBD5E1")),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    return t


def hl(text):
    """Highlighted yellow inline."""
    return f'<font backColor="#FEF08A">{text}</font>'


def code_inline(text):
    """Inline code - monospace with light bg."""
    return f'<font face="Courier" size="9" backColor="#F1F5F9">{text}</font>'


# ─────────────────────────────────────────────────────────────────────
# Build content
# ─────────────────────────────────────────────────────────────────────
flows = []

# Title page
flows.append(Spacer(1, 1.5*cm))
flows.append(_para("TRADING SYSTEM", "Title2"))
flows.append(_para("Operations Manual", "Subtitle"))
flows.append(Spacer(1, 0.3*cm))
flows.append(_para('<i>NIFTY / SENSEX Deep-OTM Strangle Engine + Live Trading Assistant</i>', "Body"))
flows.append(Spacer(1, 0.5*cm))
flows.append(callout("STATUS: v2.0 LIVE",
    "Strategy locked 28-Apr-2026. First validated live result: <b>₹3.88 lakh net P&amp;L on ~₹100 cr margin in one trading day.</b> 100% expire-worthless rate matched 47-day backtest exactly.",
    bg="#BBF7D0", border_left="#16A34A"))
flows.append(Spacer(1, 0.4*cm))
flows.append(_para("<b>Owner:</b> Rohan Shah  &nbsp;&nbsp;&nbsp;  <b>Last updated:</b> 28-Apr-2026"))
flows.append(PageBreak())

# 1. What this system does
flows.append(_para("1. What this system does", "H1x"))
flows.append(_para("A self-improving short-strangle trading machine for Indian index options:"))
for i, line in enumerate([
    "<b>Backtest engine</b> — 1+ year of NIFTY minute-bar data in parquet, queryable via DuckDB.",
    "<b>Live data feed</b> — Kite Connect API (₹2K/mo) pulls real-time spot, VIX, option chains.",
    "<b>Auto-ingest pipeline</b> — saves every weekday's minute candles to parquet (relevant strikes only, ~1 MB/day).",
    "<b>Trading assistant interface</b> — you ask <i>'give me expiry levels'</i>, I return a trade card.",
    "<b>Continuous learning</b> — every live trade day adds data to parquet. Rules update with new evidence.",
], 1):
    flows.append(_para(f"<b>{i}.</b> &nbsp; {line}"))
flows.append(Spacer(1, 0.3*cm))
flows.append(callout("FIRST LIVE RESULT (28-Apr-2026)",
    "₹3.88 lakh net P&amp;L on ~₹100 cr margin = 0.39% in one day = ~98% annualised. <b>100% of strikes expired worthless.</b>",
    bg="#BBF7D0", border_left="#16A34A"))
flows.append(PageBreak())

# 2. Strategy
flows.append(_para("2. The locked strategy (v2.0)", "H1x"))

flows.append(_para("2A. Trade days", "H2x"))
flows.append(_para('<font color="#16A34A">✓</font>  <b>Mon</b> (E-1 to NIFTY Tue expiry) + <b>Tue</b> (NIFTY E-0)'))
flows.append(_para('<font color="#16A34A">✓</font>  <b>Wed</b> (E-1 to SENSEX Thu expiry) + <b>Thu</b> (SENSEX E-0)'))
flows.append(_para('<font color="#B91C1C">⏸</font>  Fri / mid-week gaps — no trade (DTE 5+ premium too thin)'))
flows.append(Spacer(1, 0.3*cm))

flows.append(_para("2B. Two-shot pattern per expiry cycle", "H2x"))
flows.append(kv_table([
    ["Slot", "Capital", "Distance", "Entry time", "Hold to"],
    ["E-1 advance", "5-7%", "3.5% OTM both sides", "10:00 AM prev day", "expiry close"],
    [Paragraph("<b>E-0 T1 (workhorse)</b>", styles["Body"]), Paragraph(hl("~85%"), styles["Body"]), "3.0% OTM both sides", Paragraph(hl("9:25-9:35 AM"), styles["Body"]), "15:25 expiry"],
    ["E-0 T2 (medium)", "~8%", "2.5% OTM both sides", "9:30-9:45", "15:25 expiry"],
    ["E-0 T3 (premium grab)", "~2%", "2.0% OTM both sides", "9:30", "15:25 expiry"],
], col_widths=[3.5*cm, 1.7*cm, 4*cm, 4*cm, 3.3*cm]))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para("2C. Condition overlays (read at 9:15-9:30)", "H2x"))
flows.append(_para("<b>Gap direction (vs prev close):</b>"))
flows.append(_para("• ±0.5%: defaults"))
flows.append(_para("• Gap UP > 0.5%: <b>CE +0.5% farther, PE −0.5% closer</b>"))
flows.append(_para("• Gap UP > 1%: <b>CE +1% farther, halve T3</b>"))
flows.append(_para("• Gap DOWN: mirror (PE farther, CE closer)"))
flows.append(Spacer(1, 0.2*cm))
flows.append(_para("<b>INDIA VIX:</b>"))
flows.append(_para("• &lt; 13: tighten 0.25%   • 13-16: defaults   • 16-18: +0.25% to T1, T2"))
flows.append(_para('• 18-22: <font color="#B91C1C"><b>+0.5% + skip T3 + delay T1 to 10:30</b></font>'))
flows.append(_para('• &gt; 22: <font color="#B91C1C"><b>+1.0% + halve T2 + skip T3 + delay T1 to 11:00</b></font>'))
flows.append(Spacer(1, 0.2*cm))
flows.append(_para("<b>Premium fatness (combined CE+PE @ 2.5% OTM at 9:30):</b>"))
flows.append(_para("• &lt; ₹2: thin → tighten 0.5%   • ₹2-6: default   • ₹6-15: elevated → +0.25%   • &gt; ₹15: +0.5% + halve T3"))
flows.append(Spacer(1, 0.3*cm))
flows.append(callout("MAJOR EVENT FILTER",
    "If FOMC, RBI, big earnings, or major geopolitical event today/tomorrow → <b>+1.0% to all + skip T3 + delay T1 to 10:30+</b>. Always check macro calendar before placing the day's trade.",
    bg="#FECACA", border_left="#B91C1C"))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para("2D. Limit-pricing rule (refined live 28-Apr-2026)", "H2x"))
flows.append(kv_table([
    ["9:15-9:19 spot drift", "CE leg limit", "PE leg limit"],
    [Paragraph("<b>Rally</b> (UP > +0.1%)", styles["Body"]),
     Paragraph(hl("LTP exact (no hike)"), styles["Body"]),
     "LTP + ₹0.05-0.10"],
    [Paragraph("<b>Fall</b> (DOWN > -0.1%)", styles["Body"]),
     "LTP + ₹0.05-0.10",
     Paragraph(hl("LTP exact"), styles["Body"])],
    [Paragraph("<b>Flat</b> (±0.1%)", styles["Body"]),
     "LTP + ₹0.05",
     "LTP + ₹0.05"],
], col_widths=[5.5*cm, 5.5*cm, 5.5*cm]))
flows.append(Spacer(1, 0.2*cm))
flows.append(_para(f"<b>Time budget:</b> unfilled +5 min → drop ₹0.05. Unfilled +10 min → market order or abort. {hl('Don&apos;t wait past 9:35 AM.')}"))

flows.append(Spacer(1, 0.4*cm))
flows.append(callout("THE EDGE — Hold rule",
    "Don't square off on intraday wobbles. Backtest shows <b>100% expire-worthless rate at 2.5%+ OTM E-0</b>. Even when spot moves AGAINST your CE short, theta + IV crush eat premium faster than delta builds intrinsic. <b>'Wait it out' beats 'panic exit' 100% of sample days.</b>",
    bg="#BBF7D0", border_left="#16A34A"))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para("2F. Kill-switch thresholds (only fire on real emergency)", "H2x"))
flows.append(kv_table([
    ["Tier", "Combined adverse from entry", "Action"],
    ["E-1 advance", "≥ ₹6/share", "Exit"],
    ["E-0 T1", "≥ ₹4/share", "Exit"],
    ["E-0 T2", "≥ ₹4/share", "Exit"],
    ["E-0 T3", "≥ ₹3/share", "Exit"],
    [Paragraph("<b>Portfolio hard kill</b>", styles["Body"]),
     Paragraph("<b>NIFTY ±1.5% OR ₹35K/Cr loss</b>", styles["Body"]),
     Paragraph("<b><font color='#B91C1C'>EXIT ALL</font></b>", styles["Body"])],
], col_widths=[5.5*cm, 6.5*cm, 4.5*cm]))
flows.append(PageBreak())

# 3. Daily routine
flows.append(_para("3. Daily routine", "H1x"))
flows.append(kv_table([
    ["Time IST", "Action", "How"],
    ["~9:00 AM", "Login to Kite", Paragraph(code_inline("python3 scripts/kite_login.py"), styles["Body"])],
    ["9:15-9:30", "Get trade card", Paragraph(f"Tell assistant: {hl('&quot;give me expiry levels&quot;')}", styles["Body"])],
    [Paragraph(hl("9:25-9:35"), styles["Body"]), "Place E-0 trades per card", "Manual on Axis/Monarch terminal"],
    ["10:00-10:15", "E-1 advance trade (Mon/Wed)", "Manual placement"],
    ["15:25", "Trades expire", "No action"],
    [Paragraph("<b>16:30 (auto)</b>", styles["Body"]), Paragraph("<b>Cron ingests minute data</b>", styles["Body"]), Paragraph("<i>Nothing — runs automatically</i>", styles["Body"])],
], col_widths=[3*cm, 6*cm, 7.5*cm]))

flows.append(Spacer(1, 0.4*cm))
flows.append(callout("THAT'S IT",
    "Login + ask assistant + place orders. Ingest happens by itself.",
    bg="#BBF7D0", border_left="#16A34A"))
flows.append(PageBreak())

# 4. Asking for trade levels
flows.append(_para("4. Asking for trade levels — assistant interface", "H1x"))
flows.append(_para("When you say <b>'give me expiry levels for [date]'</b>, the assistant will:"))
for i, item in enumerate([
    "Fetch live conditions via Kite (spot, gap, VIX, premium @ 2.5%, nearest expiry)",
    "Web-search major events (FOMC, RBI, earnings, geopolitical)",
    "Apply v2.0 strategy + all condition overlays",
    "Return a trade card with all 4 tier strikes + limit prices + expected P&amp;L + kill-switches",
], 1):
    flows.append(_para(f"<b>{i}.</b> {item}"))

flows.append(Spacer(1, 0.3*cm))
flows.append(_para("<b>Commands the assistant accepts:</b>"))
for cmd, desc in [
    ('"give me expiry levels"', "today's nearest expiry, full plan"),
    ('"give me expiry levels for 5-may"', "for 5-May expiry"),
    ('"give me sensex levels"', "SENSEX-specific (lot=20, grid=100)"),
    ('"give me only the e-0 plan"', "skip E-1 advance"),
    ('"update for live conditions"', "re-pull and re-apply current overlays"),
]:
    flows.append(_para(f"• {code_inline(cmd)} — {desc}"))
flows.append(PageBreak())

# 5. Data infrastructure
flows.append(_para("5. Data infrastructure", "H1x"))

flows.append(_para("5A. What's saved per trading day", "H2x"))
flows.append(kv_table([
    ["Data", "Volume", "Why"],
    ["Underlying SPOT minute bars", "~375 rows", "Spot path, gap, vol bucket"],
    ["Futures (current + next month)", "~750 rows", "Spot proxy + basis"],
    ["Option chain ±5% × 2 weeklies", "~73,500 rows", "Tradeable strikes only"],
    [Paragraph("<b>Per day per instrument</b>", styles["Body"]),
     Paragraph(hl("<b>~75,000 rows · ~1 MB</b>"), styles["Body"]),
     "Total"],
    [Paragraph("<i>Strikes >5% OTM, monthlies</i>", styles["Body"]),
     Paragraph("<i>NOT saved</i>", styles["Body"]),
     Paragraph("<i>Irrelevant — never traded</i>", styles["Body"])],
], col_widths=[6*cm, 4.5*cm, 6*cm]))
flows.append(Spacer(1, 0.2*cm))
flows.append(_para(f"<b>Annual storage:</b> ~500 MB combined. <b>5-year:</b> ~2.5 GB."))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para("5B. Where it lives", "H2x"))
flows.append(code_block([
    "data/",
    "├── parquet/",
    "│   ├── instrument=NIFTY/year=YYYY/month=MM/<hash>.parquet",
    "│   └── instrument=SENSEX/year=YYYY/month=MM/<hash>.parquet",
    "├── kite_ingest_log.parquet   # tracks (instrument, date, n_rows, ingested_at)",
    "└── manifest.parquet          # legacy bulk historical load",
]))
flows.append(Spacer(1, 0.3*cm))
flows.append(callout("DEDUP CONFIRMED",
    "Each (instrument, date) writes to a deterministic hash filename. Re-running the same date reads the existing file → concats new rows → drops duplicates → writes back. <b>Safe to re-run any time.</b>",
    bg="#BBF7D0", border_left="#16A34A"))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para("5C. CSV / Excel access — three ways", "H2x"))
flows.append(_para("<b>A. Pre-existing CSVs from analyses:</b>"))
flows.append(code_block([
    "results/001_non_expiry_intraday_deep_otm/per_day.csv",
    "results/008_e_zero_time_distance_grid/full_grid.csv",
    "results/009_e_zero_minute_level_entry/minute_grid.csv",
    "results/007_real_broker_cost_winner/realistic_winners.csv",
]))
flows.append(_para("<b>B. Self-service exporter (recommended for ad-hoc):</b>"))
flows.append(code_block([
    "# Today's intraday for chosen strikes",
    "python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\",
    "    --strikes 23400,24700 --opt CE,PE --out my_strikes.csv",
    "",
    "# Full intraday minute bars for one strike",
    "python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\",
    "    --strike 24700 --opt CE --out 24700_CE.csv",
    "",
    "# NIFTY spot path for a date",
    "python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\",
    "    --spot --out spot_28apr.csv",
    "",
    "# Full chain snapshot at a specific time",
    "python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\",
    "    --chain-at 09:30 --out chain_at_930.csv",
    "",
    "# P&L reconstruction for live trades",
    "python3 scripts/export_csv.py --pnl-summary --instrument NIFTY \\",
    "    --date 2026-04-28 \\",
    "    --positions \"24700:CE:42900:0.81,23400:PE:50700:0.85\" \\",
    "    --out my_pnl.csv",
    "",
    "# All exports → results/exports/<filename>.csv",
]))
flows.append(_para('<b>C. Ad-hoc — ask the assistant: </b><i>"export 2.5% OTM history every minute as CSV"</i> → I write the query and put a CSV in results/exports/.'))
flows.append(PageBreak())

# 6. Cron management
flows.append(_para("6. Cron — auto-ingest infrastructure", "H1x"))
flows.append(_para(f"<b>File:</b> {code_inline('scripts/com.rohanshah.kite-ingest.plist')}"))
flows.append(_para(f"<b>What it does:</b> runs {code_inline('scripts/run_kite_ingest.py --days 2')} Mon-Fri at <b>16:30 IST</b>"))
flows.append(_para("<b>Pulls:</b> last 2 trading days × NIFTY + SENSEX → all spot/FUT/option chain minute bars (filtered to ±5% strikes × 2 nearest weeklies) → appends + dedupes into parquet."))
flows.append(_para(f"<b>Logs:</b> {code_inline('results/kite_ingest_stdout.log')} and {code_inline('kite_ingest_stderr.log')}"))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para("Management commands", "H2x"))

flows.append(_para('<font color="#16A34A"><b>🟢 DAILY (every trading morning ~9 AM):</b></font>'))
flows.append(code_block([
    'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
    'python3 scripts/kite_login.py',
    '# Browser opens. Log in to Zerodha. Copy redirect URL. Paste back.',
], accent="#16A34A"))

flows.append(_para('<font color="#1E40AF"><b>🔵 MANUAL ON-DEMAND:</b></font>'))
flows.append(code_block([
    "# Verify live data feed working",
    "python3 lib/kite_live.py",
    "",
    "# Last 7 trading days (catch up after holiday/break)",
    "python3 scripts/run_kite_ingest.py --days 7",
    "",
    "# Specific date — NIFTY only",
    "python3 scripts/run_kite_ingest.py --instruments NIFTY --date 2026-05-04",
    "",
    "# Specific date — SENSEX only",
    "python3 scripts/run_kite_ingest.py --instruments SENSEX --date 2026-05-04",
    "",
    "# Force re-ingest (override log; useful if data corrupted)",
    "python3 scripts/run_kite_ingest.py --date 2026-05-04 --force",
    "",
    "# Check what's been ingested",
    "python3 -c \"import pandas as pd; print(pd.read_parquet('data/kite_ingest_log.parquet').to_string(index=False))\"",
], accent="#1E40AF"))

flows.append(_para('<font color="#CA8A04"><b>🟡 CRON MANAGEMENT:</b></font>'))
flows.append(code_block([
    "# Status — is cron loaded? when did it last run?",
    "launchctl print gui/$(id -u)/com.rohanshah.kite-ingest 2>&1 | head -20",
    "",
    "# Disable cron (long break)",
    'launchctl unload "scripts/com.rohanshah.kite-ingest.plist"',
    "",
    "# Re-enable cron",
    'launchctl load -w "scripts/com.rohanshah.kite-ingest.plist"',
    "",
    "# View today's cron output",
    "tail -50 results/kite_ingest_stdout.log",
    "",
    "# View cron errors (if any)",
    "tail -50 results/kite_ingest_stderr.log",
], accent="#CA8A04"))

flows.append(_para('<font color="#B91C1C"><b>🔴 EMERGENCY / RECOVERY:</b></font>'))
flows.append(code_block([
    "# If cron silently stopped, force-run for missing days",
    "python3 scripts/run_kite_ingest.py --days 14 --force",
    "",
    "# If session keeps failing despite re-login, regenerate fresh",
    "rm ~/.config/kite_session.json",
    "python3 scripts/kite_login.py",
    "",
    "# If parquet corrupted for a date, recompute",
    'rm "data/parquet/instrument=NIFTY/year=2026/month=04/<hash>.parquet"',
    "python3 scripts/run_kite_ingest.py --date 2026-04-28 --force",
], accent="#B91C1C"))
flows.append(PageBreak())

# 7. File reference
flows.append(_para("7. Strategy file reference", "H1x"))
flows.append(kv_table([
    ["File", "Purpose"],
    [Paragraph(code_inline("STRATEGY_LIVE.md"), styles["Body"]), "Canonical strategy doc — v2.0 + sections 9F-9J live lessons"],
    [Paragraph(code_inline("FINDINGS_LOG.md"), styles["Body"]), "Append-only log of every analysis + live result"],
    [Paragraph(code_inline("analyses/001-009_*.py"), styles["Body"]), "All backtest scripts (re-runnable)"],
    [Paragraph(code_inline("results/NNN_*/summary.md"), styles["Body"]), "Markdown report per analysis"],
    [Paragraph(code_inline("results/backtest_report.pdf"), styles["Body"]), "Combined PDF of all analyses"],
    [Paragraph(code_inline("lib/kite_live.py"), styles["Body"]), "Live data adapter (assistant uses)"],
    [Paragraph(code_inline("lib/kite_historical.py"), styles["Body"]), "Rate-limited Kite historical wrapper"],
    [Paragraph(code_inline("ingest/kite_daily.py"), styles["Body"]), "Daily ingest engine (called by cron)"],
    [Paragraph(code_inline("scripts/kite_login.py"), styles["Body"]), "Daily Kite login flow"],
    [Paragraph(code_inline("scripts/run_kite_ingest.py"), styles["Body"]), "CLI wrapper for cron + manual"],
    [Paragraph(code_inline("scripts/export_csv.py"), styles["Body"]), "Self-service CSV exporter"],
    [Paragraph(code_inline("OPERATIONS_MANUAL.md/docx/pdf"), styles["Body"]), "This document (3 formats)"],
], col_widths=[5.5*cm, 11*cm]))

flows.append(PageBreak())

# 8. Lessons
flows.append(_para("8. Live trading lessons (28-Apr-2026)", "H1x"))
for i, lesson in enumerate([
    "Strategy validated end-to-end. <b>100% expire-worthless rate</b> matched 47-day backtest exactly.",
    "<b>Theta + IV crush > delta on E-0 morning.</b> Even when spot rallies +49 pts in 30 min, CE premium drops 4-30%.",
    f"<b>Asymmetric premium ('put-skew dominance'):</b> 23400 PE went UP +12% during a +30 pt rally while 24700 CE dropped -8%. {hl('Put side is sticky on rally days.')}",
    f"<b>Limit fills:</b> 9:17-9:22 has only ~50% fill rate. {hl('9:25-9:35 is the practical sweet spot')} (~95% fill, ~85% premium captured vs 9:15 baseline).",
    "<b>Drift-against side limit must be at LTP exact</b> (no premium hike) — it never returns. Drift-favorable side can be at LTP+0.05-0.10.",
    "<b>Don't break the 2.0% T3 floor.</b> Tempting premium at 0.98% OTM worked once but sample shows 1% OTM = only 57% worthless rate. Single win ≠ rule validation.",
    "<b>Real friction = ~11.4% of gross.</b> Much smaller than the placeholder model. Embed in projections.",
    "<b>Late-day entry is profitable on calm-drift days but exposes to gap-and-trend tail.</b> Better to wait for next cycle than late-deploy on volatile days.",
], 1):
    flows.append(_para(f"<b>{i}.</b> {lesson}"))
    flows.append(Spacer(1, 0.1*cm))

flows.append(PageBreak())

# 9. Caveats
flows.append(_para("9. Risks, caveats, known gaps", "H1x"))
for i, caveat in enumerate([
    "Backtest sample = ~1 year (47 NIFTY E-0 days, 46 E-1 days). Cross-validation on 2024 data still pending.",
    "<b>SENSEX support is unproven.</b> Only 8 historical days in parquet (cron now adds daily; 30+ days needed before SENSEX-specific rules can be validated).",
    "Asymmetric distance overlays (gap-up days CE further) are principled inferences, not individually backtested per condition cell.",
    "Vol-bucket conditioning is currently impotent — every E-0 day in our sample had high_vol. Useless filter.",
    "Funding cost ₹600/Cr conservatively applied to every event; real returns slightly higher if funding occasional.",
    "Major-event filter is subjective — assistant must do web-search per ask. Not perfectly automatable yet.",
    "Kill-switches are heuristic — set 3× the per-trade stop derived from ₹7K/Cr cap. Permissive deliberately.",
], 1):
    flows.append(_para(f"<b>{i}.</b> {caveat}"))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para("10. Quick-reference cheat sheet", "H1x"))
flows.append(callout("THE ESSENTIALS",
    "<b>Daily login (~9 AM):</b> python3 scripts/kite_login.py<br/>"
    "<b>Trade card request:</b> ask assistant — &quot;give me expiry levels&quot;<br/>"
    "<b>Manual data export:</b> python3 scripts/export_csv.py [options]<br/>"
    "<b>Ingest catch-up:</b> python3 scripts/run_kite_ingest.py --days 7<br/>"
    "<b>Cron status:</b> launchctl print gui/$(id -u)/com.rohanshah.kite-ingest<br/>"
    "<b>Strategy doc:</b> STRATEGY_LIVE.md<br/>"
    "<b>Findings log:</b> FINDINGS_LOG.md<br/>"
    "<b>PDF report:</b> results/backtest_report.pdf<br/>"
    "<b>CSVs from analyses:</b> results/NNN_*/{per_day,full_grid,...}.csv<br/>"
    "<b>Self-service exports:</b> results/exports/",
    bg="#FEF08A", border_left="#CA8A04"))

flows.append(Spacer(1, 0.4*cm))
flows.append(_para('<i>End of Operations Manual · Strategy v2.0 · Locked 28-Apr-2026</i>', "Body"))
flows.append(_para('<i>Live infra: Kite Connect API + launchd cron · First validated: ₹3.88L on ₹100cr</i>', "Body"))

# Build
doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                         leftMargin=2*cm, rightMargin=2*cm,
                         topMargin=1.8*cm, bottomMargin=1.8*cm,
                         title="Trading System Operations Manual")
doc.build(flows)
print(f"✓ {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")
