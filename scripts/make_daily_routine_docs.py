"""Build PDF + DOCX of DAILY_DATA_ROUTINE.md — single page, command-highlighted."""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "DAILY_DATA_ROUTINE.pdf"

styles = getSampleStyleSheet()
styles.add(ParagraphStyle("Title2", parent=styles["Heading1"], fontSize=22,
                           textColor=colors.HexColor("#0F172A"), alignment=1, spaceAfter=4))
styles.add(ParagraphStyle("Sub", parent=styles["Heading2"], fontSize=11,
                           textColor=colors.HexColor("#64748B"), alignment=1, spaceAfter=12))
styles.add(ParagraphStyle("H1x", parent=styles["Heading1"], fontSize=14,
                           textColor=colors.HexColor("#1E40AF"), spaceAfter=6, spaceBefore=12))
styles.add(ParagraphStyle("H2x", parent=styles["Heading2"], fontSize=11,
                           textColor=colors.HexColor("#334155"), spaceAfter=4, spaceBefore=8))
styles.add(ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10, leading=13))


def code_block(lines, accent="#1E40AF"):
    rows = []
    for line in lines:
        if not line:
            line = "&nbsp;"
        else:
            esc = (line.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
                       .replace(" ","&nbsp;"))
            color = "#64748B" if line.lstrip().startswith("#") else "#0F172A"
            rows.append([Paragraph(f"<font face='Courier' size='9' color='{color}'>{esc}</font>", styles["Body"])])
    t = Table(rows, colWidths=[16.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#F1F5F9")),
        ("LINEBEFORE", (0,0), (0,-1), 4, colors.HexColor(accent)),
        ("BOX", (0,0), (-1,-1), 0.4, colors.HexColor("#94A3B8")),
        ("LEFTPADDING", (0,0), (-1,-1), 10),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
    ]))
    return t


def callout(label, body, bg="#FEF08A", border="#CA8A04"):
    rows = [
        [Paragraph(f"<b>{label}</b>", styles["Body"])],
        [Paragraph(body, styles["Body"])],
    ]
    t = Table(rows, colWidths=[16.5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor(bg)),
        ("LINEBEFORE", (0,0), (0,-1), 5, colors.HexColor(border)),
        ("BOX", (0,0), (-1,-1), 0.4, colors.HexColor("#64748B")),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    return t


def kv_table(rows, col_widths):
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1E40AF")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#CBD5E1")),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    return t


flows = []
flows.append(Paragraph("Daily Data Saving — Routine", styles["Title2"]))
flows.append(Paragraph("Save 1-minute NIFTY + SENSEX option data every trading day", styles["Sub"]))

# Step 1
flows.append(Paragraph('⏰ &nbsp;What needs to be done — ONCE every trading day before 4 PM IST', styles["H1x"]))
flows.append(Paragraph('<b>Just one command. ~30 seconds.</b>', styles["Body"]))
flows.append(Spacer(1, 0.15*cm))
flows.append(code_block([
    'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
    'python3 scripts/kite_login.py',
], accent="#16A34A"))

flows.append(Spacer(1, 0.2*cm))
flows.append(Paragraph('<b>Steps when the script runs:</b>', styles["Body"]))
for i, step in enumerate([
    "Browser opens → log in to Zerodha (PIN + TOTP)",
    'Browser shows <i>"site can&apos;t be reached"</i> at <font face="Courier" size="9" backColor="#F1F5F9">127.0.0.1:5000</font> — <b>that\'s expected</b>',
    "<b>Copy the FULL URL from address bar</b> (contains <font face='Courier' size='9'>request_token=...</font>)",
    "Paste it back into the terminal where the script is waiting",
    "Script prints <b><font color='#16A34A'>✓ Session saved</font></b>. Done.",
], 1):
    flows.append(Paragraph(f"<b>{i}.</b>&nbsp; {step}", styles["Body"]))
flows.append(Spacer(1, 0.2*cm))
flows.append(callout("THAT'S IT",
    "A scheduled cron job runs automatically at <b>16:30 IST</b> after market close and saves the day's data. No further action needed.",
    bg="#BBF7D0", border="#16A34A"))

# Verify
flows.append(Paragraph('✅ &nbsp;How to verify it worked (after 4:30 PM IST)', styles["H1x"]))
flows.append(code_block([
    'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
    "",
    "# Check today is in the log:",
    'python3 -c "import pandas as pd; print(pd.read_parquet(\'data/kite_ingest_log.parquet\').tail(5).to_string(index=False))"',
]))
flows.append(Paragraph("Today's date should appear with both NIFTY and SENSEX in the last few rows. If missing → run the catch-up below.", styles["Body"]))

# Catch-up
flows.append(Paragraph('🔧 &nbsp;If something is missed — catch-up', styles["H1x"]))
flows.append(Paragraph("If login was skipped one day OR cron failed, run this the next morning:", styles["Body"]))
flows.append(Spacer(1, 0.15*cm))
flows.append(code_block([
    'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
    "",
    "# 1. Login first (always)",
    "python3 scripts/kite_login.py",
    "",
    "# 2. Catch up missing days (safe — auto-skips days already saved)",
    "python3 scripts/run_kite_ingest.py --days 7",
], accent="#CA8A04"))
flows.append(Paragraph("Takes ~2-3 minutes. Auto-deduplicated — same day twice is harmless.", styles["Body"]))

# Issues
flows.append(Paragraph('🚨 &nbsp;Common issues', styles["H1x"]))
flows.append(kv_table([
    ["Problem", "Fix"],
    ["Missing kite_session.json error",
     Paragraph("Re-run <font face='Courier' size='9' backColor='#F1F5F9'>python3 scripts/kite_login.py</font>", styles["Body"])],
    ["Token expired error", "Same — re-login"],
    ["request_token expired", "Login again, paste URL within 60 sec of clicking"],
    ["Cron not running automatically",
     Paragraph("<font face='Courier' size='9' backColor='#F1F5F9'>launchctl load -w \"scripts/com.rohanshah.kite-ingest.plist\"</font>", styles["Body"])],
], col_widths=[6*cm, 10.5*cm]))

# Last resort
flows.append(Paragraph('🆘 &nbsp;If completely stuck — full reset', styles["H1x"]))
flows.append(code_block([
    'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
    "rm ~/.config/kite_session.json",
    "python3 scripts/kite_login.py",
    "python3 scripts/run_kite_ingest.py --days 14",
], accent="#B91C1C"))
flows.append(Paragraph("Or text Rohan / open a fresh chat with the assistant — paste the error message.", styles["Body"]))

# Footer
flows.append(Spacer(1, 0.3*cm))
flows.append(callout("STORAGE NOTE",
    "~1 MB per instrument per day. ~500 MB/year total. Auto-dedupes. Don't delete anything from <font face='Courier' size='9'>data/parquet/</font>.",
    bg="#F1F5F9", border="#1E40AF"))

flows.append(Spacer(1, 0.2*cm))
flows.append(Paragraph('<i>Strategy + backtest details: see OPERATIONS_MANUAL.md (separate doc). This file is ONLY about daily data saving.</i>', styles["Body"]))

# Build PDF
doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                         leftMargin=2*cm, rightMargin=2*cm,
                         topMargin=1.5*cm, bottomMargin=1.5*cm,
                         title="Daily Data Saving Routine")
doc.build(flows)
print(f"✓ {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")
