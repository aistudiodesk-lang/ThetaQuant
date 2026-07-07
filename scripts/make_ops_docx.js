// Generate a Word version of OPERATIONS_MANUAL with command-block highlighting.
// Run: node scripts/make_ops_docx.js

const fs = require('fs');
const path = require('path');
const {
    Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
    Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
    BorderStyle, WidthType, ShadingType, VerticalAlign, PageNumber, PageBreak,
} = require('docx');

const OUT = path.resolve(__dirname, '..', 'results', 'OPERATIONS_MANUAL.docx');

// ── Style constants ───────────────────────────────────────────────────
const COLOR_TITLE  = "0F172A";
const COLOR_H1     = "0F172A";
const COLOR_H2     = "1E40AF";
const COLOR_H3     = "334155";
const BG_CMD       = "F1F5F9";   // command box background (light gray-blue)
const BG_HEADER    = "1E40AF";   // table header bg
const BG_HIGHLIGHT = "FEF08A";   // yellow highlight for important values
const BG_RED       = "FECACA";   // red for warnings
const BG_GREEN     = "BBF7D0";   // green for confirmations
const FG_HEADER    = "FFFFFF";
const RULE         = "94A3B8";

// ── Helpers ───────────────────────────────────────────────────────────
const p = (text, opts = {}) => new Paragraph({
    spacing: { before: 60, after: 60 },
    children: Array.isArray(text)
        ? text
        : [new TextRun({ text, ...opts })],
});

const heading = (text, level) => {
    const sizes = { 1: 32, 2: 24, 3: 18 };
    const colors = { 1: COLOR_H1, 2: COLOR_H2, 3: COLOR_H3 };
    return new Paragraph({
        heading: { 1: HeadingLevel.HEADING_1, 2: HeadingLevel.HEADING_2, 3: HeadingLevel.HEADING_3 }[level],
        spacing: { before: 240, after: 120 },
        children: [new TextRun({ text, bold: true, size: sizes[level], color: colors[level] })],
    });
};

const bullet = (text, opts = {}) => new Paragraph({
    numbering: { reference: 'bullets', level: 0 },
    spacing: { before: 30, after: 30 },
    children: Array.isArray(text) ? text : [new TextRun({ text, ...opts })],
});

const numbered = (text) => new Paragraph({
    numbering: { reference: 'numbers', level: 0 },
    spacing: { before: 30, after: 30 },
    children: [new TextRun({ text })],
});

// Highlighted inline (yellow background)
const hl = (text) => new TextRun({ text, highlight: 'yellow' });

// Code-style inline (Courier, light gray bg)
const code = (text) => new TextRun({
    text, font: 'Courier New', size: 18, shading: { type: ShadingType.CLEAR, fill: BG_CMD }
});

// A command block (multi-line code with shaded background) — using a 1-cell table
const codeBlock = (lines) => {
    const cellChildren = lines.map(line =>
        new Paragraph({
            spacing: { before: 0, after: 0 },
            children: [new TextRun({
                text: line || ' ',
                font: 'Courier New',
                size: 18,
                color: '0F172A',
            })],
        })
    );
    return new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [9360],
        rows: [new TableRow({
            children: [new TableCell({
                width: { size: 9360, type: WidthType.DXA },
                shading: { type: ShadingType.CLEAR, fill: BG_CMD, color: 'auto' },
                margins: { top: 100, bottom: 100, left: 200, right: 200 },
                borders: {
                    top: { style: BorderStyle.SINGLE, size: 4, color: '94A3B8' },
                    bottom: { style: BorderStyle.SINGLE, size: 4, color: '94A3B8' },
                    left: { style: BorderStyle.SINGLE, size: 8, color: '1E40AF' },
                    right: { style: BorderStyle.SINGLE, size: 4, color: '94A3B8' },
                },
                children: cellChildren,
            })],
        })],
    });
};

// Callout box (single cell with colored background)
const callout = (label, body, bgColor = BG_HIGHLIGHT) => new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [new TableRow({
        children: [new TableCell({
            width: { size: 9360, type: WidthType.DXA },
            shading: { type: ShadingType.CLEAR, fill: bgColor, color: 'auto' },
            margins: { top: 120, bottom: 120, left: 200, right: 200 },
            borders: {
                top: { style: BorderStyle.SINGLE, size: 4, color: '64748B' },
                bottom: { style: BorderStyle.SINGLE, size: 4, color: '64748B' },
                left: { style: BorderStyle.SINGLE, size: 16, color: '1E40AF' },
                right: { style: BorderStyle.SINGLE, size: 4, color: '64748B' },
            },
            children: [
                new Paragraph({
                    spacing: { before: 0, after: 60 },
                    children: [new TextRun({ text: label, bold: true, size: 22, color: '0F172A' })],
                }),
                new Paragraph({
                    spacing: { before: 0, after: 0 },
                    children: [new TextRun({ text: body, size: 20 })],
                }),
            ],
        })],
    })],
});

// Build a structured table from rows array
const buildTable = (header, rows, colWidths) => {
    const totalWidth = 9360;
    const widths = colWidths || header.map(() => Math.floor(totalWidth / header.length));
    const border = { style: BorderStyle.SINGLE, size: 4, color: 'CBD5E1' };
    const borders = { top: border, bottom: border, left: border, right: border };
    return new Table({
        width: { size: totalWidth, type: WidthType.DXA },
        columnWidths: widths,
        rows: [
            new TableRow({
                tableHeader: true,
                children: header.map((h, i) => new TableCell({
                    width: { size: widths[i], type: WidthType.DXA },
                    shading: { type: ShadingType.CLEAR, fill: BG_HEADER, color: 'auto' },
                    margins: { top: 80, bottom: 80, left: 100, right: 100 },
                    borders,
                    children: [new Paragraph({
                        spacing: { before: 0, after: 0 },
                        children: [new TextRun({ text: h, bold: true, color: FG_HEADER, size: 18 })],
                    })],
                })),
            }),
            ...rows.map((row, idx) => new TableRow({
                children: row.map((cell, i) => new TableCell({
                    width: { size: widths[i], type: WidthType.DXA },
                    shading: { type: ShadingType.CLEAR, fill: idx % 2 === 0 ? 'FFFFFF' : 'F8FAFC' },
                    margins: { top: 80, bottom: 80, left: 100, right: 100 },
                    borders,
                    children: typeof cell === 'string'
                        ? [new Paragraph({
                            spacing: { before: 0, after: 0 },
                            children: [new TextRun({ text: cell, size: 18 })]
                        })]
                        : [cell], // assume a Paragraph
                })),
            })),
        ],
    });
};

// ── Document content ──────────────────────────────────────────────────
const content = [
    // Title page
    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 240 },
        children: [new TextRun({
            text: 'TRADING SYSTEM',
            bold: true, size: 48, color: COLOR_TITLE,
        })],
    }),
    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 120 },
        children: [new TextRun({
            text: 'Operations Manual',
            bold: true, size: 36, color: COLOR_H2,
        })],
    }),
    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 360 },
        children: [new TextRun({
            text: 'NIFTY / SENSEX Deep-OTM Strangle Engine + Live Trading Assistant',
            italics: true, size: 22, color: COLOR_H3,
        })],
    }),
    callout('STATUS: v2.0 LIVE',
        'Strategy locked 28-Apr-2026. First validated live result: ₹3.88 lakh net P&L on ~₹100 cr margin in one trading day. 100% expire-worthless rate matched 47-day backtest exactly.',
        BG_GREEN),
    p(' '),
    p([
        new TextRun({ text: 'Owner: ', bold: true }),
        new TextRun('Rohan Shah    '),
        new TextRun({ text: 'Last updated: ', bold: true }),
        new TextRun('28-Apr-2026'),
    ]),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 1. What this system does ──
    heading('1. What this system does', 1),
    p('A self-improving short-strangle trading machine for Indian index options:'),
    numbered('Backtest engine — 1+ year of NIFTY minute-bar data in parquet, queryable via DuckDB. Generates rule-validated trade configurations (analyses 001-009).'),
    numbered('Live data feed — Kite Connect API (paid, ₹2K/mo) pulls real-time spot, VIX, option chains.'),
    numbered('Auto-ingest pipeline — saves every weekday\'s minute candles to parquet (relevant strikes only, not GBs of bloat).'),
    numbered('Trading assistant interface — you ask "give me expiry levels for [date]", I read live data + apply locked v2.0 strategy + check fundamentals + return a trade card.'),
    numbered('Continuous learning — every live trade day adds data to the parquet store. Rules update with new evidence.'),
    p(' '),
    callout('FIRST LIVE RESULT (28-Apr-2026)',
        '₹3.88 lakh net P&L on ~₹100 cr margin = 0.39% in one day = ~98% annualised at sustained rate. 100% of strikes expired worthless.',
        BG_GREEN),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 2. The locked strategy ──
    heading('2. The locked strategy (v2.0)', 1),

    heading('2A. Trade days', 2),
    bullet([
        new TextRun({ text: '✓ ', color: '16A34A', bold: true }),
        new TextRun({ text: 'Mon (E-1 to NIFTY Tue expiry)', bold: true }),
        new TextRun(' + '),
        new TextRun({ text: 'Tue (NIFTY E-0)', bold: true }),
    ]),
    bullet([
        new TextRun({ text: '✓ ', color: '16A34A', bold: true }),
        new TextRun({ text: 'Wed (E-1 to SENSEX Thu expiry)', bold: true }),
        new TextRun(' + '),
        new TextRun({ text: 'Thu (SENSEX E-0)', bold: true }),
    ]),
    bullet([
        new TextRun({ text: '⏸ ', color: 'B91C1C', bold: true }),
        new TextRun('Fri / mid-week gaps — no trade (DTE 5+ premium too thin)'),
    ]),

    heading('2B. Two-shot pattern per expiry cycle', 2),
    buildTable(
        ['Slot', 'Capital', 'Distance', 'Entry time', 'Hold to'],
        [
            ['E-1 advance', '5-7%', '3.5% OTM both sides', '10:00 AM prev day', 'expiry close'],
            ['E-0 T1 (workhorse)', '~85%', '3.0% OTM both sides', '9:25-9:35 AM expiry day', '15:25 expiry'],
            ['E-0 T2 (medium)', '~8%', '2.5% OTM both sides', '9:30-9:45', '15:25 expiry'],
            ['E-0 T3 (premium grab)', '~2%', '2.0% OTM both sides', '9:30', '15:25 expiry'],
        ],
        [1700, 1100, 2300, 2300, 1960]
    ),

    heading('2C. Condition overlays (read at 9:15-9:30 each morning)', 2),
    p([new TextRun({ text: 'Gap direction (vs prev close):', bold: true })]),
    bullet([
        new TextRun('±0.5%: defaults'),
    ]),
    bullet([
        new TextRun('Gap UP > 0.5%: '),
        new TextRun({ text: 'CE +0.5% farther, PE −0.5% closer', bold: true }),
    ]),
    bullet([
        new TextRun('Gap UP > 1%: '),
        new TextRun({ text: 'CE +1% farther, halve T3', bold: true }),
    ]),
    bullet('Gap DOWN: mirror (PE farther, CE closer)'),
    p(' '),
    p([new TextRun({ text: 'INDIA VIX:', bold: true })]),
    bullet('< 13: tighten 0.25% (low vol = safe to be closer)'),
    bullet('13-16: defaults'),
    bullet('16-18: +0.25% to T1, T2'),
    bullet([
        new TextRun('18-22: '),
        new TextRun({ text: '+0.5% + skip T3 + delay T1 to 10:30', bold: true, color: 'B91C1C' }),
    ]),
    bullet([
        new TextRun('> 22: '),
        new TextRun({ text: '+1.0% + halve T2 + skip T3 + delay T1 to 11:00', bold: true, color: 'B91C1C' }),
    ]),
    p(' '),
    p([new TextRun({ text: 'Premium fatness (combined CE+PE @ 2.5% OTM at 9:30):', bold: true })]),
    bullet('< ₹2: thin → tighten 0.5%'),
    bullet('₹2-6: default'),
    bullet('₹6-15: elevated → +0.25%'),
    bullet('> ₹15: spike → +0.5% + halve T3'),
    p(' '),
    callout('MAJOR EVENT FILTER',
        'If FOMC, RBI, big earnings, or major geopolitical event today/tomorrow → +1.0% to all + skip T3 + delay T1 to 10:30+. Always check the macro calendar before placing the day\'s trade.',
        BG_RED),

    heading('2D. Limit-pricing rule (refined live 28-Apr-2026)', 2),
    buildTable(
        ['9:15-9:19 spot drift', 'CE leg limit', 'PE leg limit'],
        [
            ['Rally (UP > +0.1%)', 'LTP exact (no hike)', 'LTP + ₹0.05-0.10'],
            ['Fall (DOWN > -0.1%)', 'LTP + ₹0.05-0.10', 'LTP exact'],
            ['Flat (±0.1%)', 'LTP + ₹0.05', 'LTP + ₹0.05'],
        ],
        [3120, 3120, 3120]
    ),
    p(' '),
    p([
        new TextRun({ text: 'Time budget: ', bold: true }),
        new TextRun('unfilled +5 min → drop ₹0.05. Unfilled +10 min → market order or abort. '),
        hl('Don\'t wait past 9:35 AM — premium decay accelerates.'),
    ]),

    heading('2E. Hold rule (the strategy\'s actual edge)', 2),
    callout('THE EDGE',
        'Don\'t square off on intraday wobbles. Backtest shows 100% expire-worthless rate at 2.5%+ OTM E-0. Even when spot moves AGAINST your CE short, theta + IV crush eat premium faster than delta builds intrinsic. "Wait it out" beats "panic exit" 100% of sample days.',
        BG_GREEN),

    heading('2F. Kill-switch thresholds (only fire on real emergency)', 2),
    buildTable(
        ['Tier', 'Combined adverse from entry', 'Action'],
        [
            ['E-1 advance', '≥ ₹6/share', 'Exit'],
            ['E-0 T1', '≥ ₹4/share', 'Exit'],
            ['E-0 T2', '≥ ₹4/share', 'Exit'],
            ['E-0 T3', '≥ ₹3/share', 'Exit'],
            ['Portfolio hard kill', 'NIFTY ±1.5% OR ₹35K/Cr loss', 'EXIT ALL'],
        ],
        [3120, 3120, 3120]
    ),

    heading('2G. Asymmetric premium (put-skew dominance)', 2),
    p([
        new TextRun('On '),
        new TextRun({ text: 'rally days', bold: true }),
        new TextRun(': PE is sticky (oscillates, sometimes UP), CE decays fast. On '),
        new TextRun({ text: 'fall days', bold: true }),
        new TextRun(': mirror. '),
    ]),
    p([
        new TextRun({ text: 'Live evidence (28-Apr-2026, 9:55 → 10:30): ', bold: true }),
        new TextRun('spot rallied +30 pts; 24700 CE dropped -8% while '),
        hl('23400 PE went UP +12%'),
        new TextRun('. Use the asymmetric distance overlay.'),
    ]),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 3. Daily routine ──
    heading('3. Daily routine', 1),
    buildTable(
        ['Time IST', 'Action', 'How'],
        [
            ['~9:00 AM', 'Login to Kite (tokens expire daily)', code('python3 scripts/kite_login.py')],
            ['9:15-9:30', 'Get trade card', new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: 'Tell assistant: ', size: 18 }), hl('"give me expiry levels"')] })],
            ['9:25-9:35', 'Place E-0 trades per card', 'Manual on Axis/Monarch terminal'],
            ['10:00-10:15', 'E-1 advance trade (Mon/Wed)', 'Manual placement'],
            ['15:25', 'Trades expire', 'No action'],
            ['16:30 (auto)', 'Cron ingests today\'s minute data', 'Nothing — runs automatically'],
        ],
        [1700, 3500, 4160]
    ),
    p(' '),
    callout('THAT\'S IT',
        'Login + ask assistant + place orders. Ingest happens by itself.', BG_GREEN),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 4. Asking for trade levels ──
    heading('4. Asking for trade levels — assistant interface', 1),
    p('When you say "give me expiry levels for [date]", the assistant will:'),
    numbered('Fetch live conditions via Kite (spot, gap, VIX, premium @ 2.5%, nearest expiry)'),
    numbered('Web-search major events for that day + next day (FOMC, RBI, earnings, geopolitical)'),
    numbered('Apply v2.0 strategy + all condition overlays'),
    numbered('Return a trade card with: detected conditions block, all 4 tier strikes with limit-price guidance, expected per-Cr P&L, kill-switch thresholds, hold plan, major event flags.'),
    p(' '),
    p([new TextRun({ text: 'Commands the assistant accepts:', bold: true })]),
    bullet([code('"give me expiry levels"'), new TextRun(' — today\'s nearest expiry, full plan')]),
    bullet([code('"give me expiry levels for 5-may"'), new TextRun(' — for 5-May expiry')]),
    bullet([code('"give me sensex levels"'), new TextRun(' — SENSEX-specific (lot=20, grid=100)')]),
    bullet([code('"give me only the e-0 plan"'), new TextRun(' — skip E-1 advance')]),
    bullet([code('"update for live conditions"'), new TextRun(' — re-pull and re-apply current overlays')]),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 5. Data infrastructure ──
    heading('5. Data infrastructure', 1),

    heading('5A. What\'s saved (per trading day, per instrument)', 2),
    buildTable(
        ['Data', 'Volume', 'Why'],
        [
            ['Underlying SPOT minute bars', '~375 rows', 'Spot path, gap, vol bucket'],
            ['Futures (current + next month)', '~750 rows', 'Spot proxy + basis'],
            ['Option chain ±5% × 2 weeklies', '~73,500 rows', 'Tradeable strikes only'],
            ['Per day per instrument', new Paragraph({ spacing: { before: 0, after: 0 }, children: [new TextRun({ text: '~75,000 rows · ~1 MB', bold: true })] }), 'Total'],
            ['Strikes >5% OTM, monthlies', 'NOT saved', 'Irrelevant — never traded'],
        ],
        [3500, 2700, 3160]
    ),
    p(' '),
    p([
        new TextRun({ text: 'Annual storage: ', bold: true }),
        new TextRun('~500 MB for NIFTY + SENSEX combined. '),
        new TextRun({ text: '5-year storage: ', bold: true }),
        new TextRun('~2.5 GB.'),
    ]),

    heading('5B. Where it lives', 2),
    codeBlock([
        'data/',
        '├── parquet/',
        '│   ├── instrument=NIFTY/year=YYYY/month=MM/<hash>.parquet',
        '│   └── instrument=SENSEX/year=YYYY/month=MM/<hash>.parquet',
        '├── kite_ingest_log.parquet   ← tracks (instrument, date, n_rows, ingested_at)',
        '└── manifest.parquet          ← (legacy bulk historical load)',
    ]),
    p(' '),
    callout('DEDUP CONFIRMED',
        'Each (instrument, date) writes to a deterministic hash filename. Re-running the same date reads the existing file → concats new rows → drops duplicates → writes back. Safe to re-run any time.',
        BG_GREEN),

    heading('5C. CSV / Excel access', 2),
    p('Three ways to get data into Excel:'),
    p(' '),
    p([new TextRun({ text: 'A. Pre-existing CSVs from analyses:', bold: true })]),
    codeBlock([
        'results/001_non_expiry_intraday_deep_otm/per_day.csv',
        'results/008_e_zero_time_distance_grid/full_grid.csv',
        'results/009_e_zero_minute_level_entry/minute_grid.csv',
        'results/007_real_broker_cost_winner/realistic_winners.csv',
    ]),
    p([new TextRun({ text: 'B. Self-service exporter:', bold: true })]),
    codeBlock([
        '# Today\'s intraday for chosen strikes',
        'python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\',
        '    --strikes 23400,24700 --opt CE,PE --out my_strikes.csv',
        '',
        '# Full intraday minute bars for one strike',
        'python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\',
        '    --strike 24700 --opt CE --out 24700_CE.csv',
        '',
        '# NIFTY spot path for a date',
        'python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\',
        '    --spot --out spot_28apr.csv',
        '',
        '# Full chain snapshot at a specific time',
        'python3 scripts/export_csv.py --instrument NIFTY --date 2026-04-28 \\',
        '    --chain-at 09:30 --out chain_at_930.csv',
        '',
        '# P&L reconstruction for live trades',
        'python3 scripts/export_csv.py --pnl-summary --instrument NIFTY \\',
        '    --date 2026-04-28 \\',
        '    --positions "24700:CE:42900:0.81,23400:PE:50700:0.85" \\',
        '    --out my_pnl.csv',
        '',
        '# All exports go to: results/exports/<filename>.csv',
    ]),
    p([new TextRun({ text: 'C. Ad-hoc — ask the assistant: ', bold: true }), new TextRun('"export 2.5% OTM history every minute as CSV" → I write the query and put a CSV in results/exports/.')]),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 6. Cron ──
    heading('6. Cron — auto-ingest infrastructure', 1),
    p([
        new TextRun({ text: 'File: ', bold: true }),
        code('scripts/com.rohanshah.kite-ingest.plist'),
    ]),
    p([
        new TextRun({ text: 'What it does: ', bold: true }),
        new TextRun('runs '),
        code('scripts/run_kite_ingest.py --days 2'),
        new TextRun(' Mon-Fri at '),
        new TextRun({ text: '16:30 IST', bold: true }),
    ]),
    p([
        new TextRun({ text: 'Pulls: ', bold: true }),
        new TextRun('last 2 trading days × NIFTY + SENSEX → all spot/FUT/option chain minute bars (filtered to ±5% strikes × 2 nearest weeklies) → appends + dedupes into parquet.'),
    ]),
    p([
        new TextRun({ text: 'Logs: ', bold: true }),
        code('results/kite_ingest_stdout.log'),
        new TextRun(' and '),
        code('kite_ingest_stderr.log'),
    ]),
    p(' '),

    heading('Management commands', 2),

    p([new TextRun({ text: '🟢 DAILY (only this — every trading morning ~9 AM):', bold: true, color: '16A34A' })]),
    codeBlock([
        'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
        'python3 scripts/kite_login.py',
        '# → Browser opens. Log in to Zerodha. Copy redirect URL. Paste back.',
    ]),

    p([new TextRun({ text: '🔵 MANUAL ON-DEMAND (only when needed):', bold: true, color: '1E40AF' })]),
    codeBlock([
        '# Verify live data feed working',
        'python3 lib/kite_live.py',
        '',
        '# Last 7 trading days (catch up after holiday/break)',
        'python3 scripts/run_kite_ingest.py --days 7',
        '',
        '# Specific date — NIFTY only',
        'python3 scripts/run_kite_ingest.py --instruments NIFTY --date 2026-05-04',
        '',
        '# Specific date — SENSEX only',
        'python3 scripts/run_kite_ingest.py --instruments SENSEX --date 2026-05-04',
        '',
        '# Force re-ingest (override log; useful if data corrupted)',
        'python3 scripts/run_kite_ingest.py --date 2026-05-04 --force',
        '',
        '# Check what\'s been ingested',
        'python3 -c "import pandas as pd; print(pd.read_parquet(\'data/kite_ingest_log.parquet\').to_string(index=False))"',
    ]),

    p([new TextRun({ text: '🟡 CRON MANAGEMENT:', bold: true, color: 'CA8A04' })]),
    codeBlock([
        '# Status — is cron loaded? when did it last run?',
        'launchctl print gui/$(id -u)/com.rohanshah.kite-ingest 2>&1 | head -20',
        '',
        '# Disable cron (long break)',
        'launchctl unload "scripts/com.rohanshah.kite-ingest.plist"',
        '',
        '# Re-enable cron',
        'launchctl load -w "scripts/com.rohanshah.kite-ingest.plist"',
        '',
        '# View today\'s cron output',
        'tail -50 results/kite_ingest_stdout.log',
        '',
        '# View cron errors (if any)',
        'tail -50 results/kite_ingest_stderr.log',
    ]),

    p([new TextRun({ text: '🔴 EMERGENCY / RECOVERY:', bold: true, color: 'B91C1C' })]),
    codeBlock([
        '# If cron silently stopped, force-run for missing days',
        'python3 scripts/run_kite_ingest.py --days 14 --force',
        '',
        '# If session keeps failing despite re-login, regenerate fresh',
        'rm ~/.config/kite_session.json',
        'python3 scripts/kite_login.py',
        '',
        '# If parquet corrupted for a date, recompute',
        'rm "data/parquet/instrument=NIFTY/year=2026/month=04/<hash>.parquet"',
        'python3 scripts/run_kite_ingest.py --date 2026-04-28 --force',
    ]),
    new Paragraph({ children: [new PageBreak()] }),

    heading('Troubleshooting', 2),
    buildTable(
        ['Symptom', 'Cause', 'Fix'],
        [
            ['"Missing kite_session.json"', 'Token expired', code('python3 scripts/kite_login.py')],
            ['Cron log: "Kite session invalid"', 'Same as above', 'Same'],
            ['Cron not running at 16:30', 'Job unloaded', code('launchctl load -w scripts/com.rohanshah.kite-ingest.plist')],
            ['Want to inspect today\'s ingest', '—', code('tail -100 results/kite_ingest_stdout.log')],
            ['Need to wipe corrupted day', '—', 'Delete file + run with --force'],
        ],
        [2700, 2700, 3960]
    ),

    new Paragraph({ children: [new PageBreak()] }),

    // ── 7. File reference ──
    heading('7. Strategy file reference', 1),
    buildTable(
        ['File', 'Purpose'],
        [
            ['STRATEGY_LIVE.md', 'Canonical strategy doc — v2.0 + live-trading lessons (sections 9F-9J)'],
            ['FINDINGS_LOG.md', 'Append-only log of every analysis + live result'],
            ['analyses/001-009_*.py', 'All backtest scripts (re-runnable)'],
            ['results/NNN_*/summary.md', 'Markdown report per analysis'],
            ['results/backtest_report.pdf', 'Combined PDF of all analyses'],
            ['lib/kite_live.py', 'Live data adapter (assistant uses)'],
            ['lib/kite_historical.py', 'Rate-limited Kite historical wrapper'],
            ['ingest/kite_daily.py', 'Daily ingest engine (called by cron)'],
            ['scripts/kite_login.py', 'Daily Kite login flow'],
            ['scripts/run_kite_ingest.py', 'CLI wrapper for cron + manual'],
            ['scripts/export_csv.py', 'Self-service CSV exporter'],
            ['OPERATIONS_MANUAL.md', 'This document (also as docx + pdf)'],
        ],
        [3000, 6360]
    ),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 8. Live trading lessons ──
    heading('8. Live trading lessons (28-Apr-2026)', 1),
    numbered('Strategy validated end-to-end. 100% expire-worthless rate matched 47-day backtest exactly.'),
    numbered('Theta + IV crush > delta on E-0 morning. Even when spot rallies +49 pts in 30 min, CE premium drops 4-30%.'),
    numbered('Asymmetric premium ("put-skew dominance"): 23400 PE went UP +12% during a +30 pt rally while 24700 CE dropped -8%. Put side is sticky on rally days.'),
    numbered('Limit fills: 9:17-9:22 has only ~50% fill rate. 9:25-9:35 is the practical sweet spot (~95% fill, ~85% premium captured vs 9:15 baseline).'),
    numbered('The drift-against side limit must be at LTP exact (no premium hike) — it never returns. The drift-favorable side can be at LTP+0.05-0.10.'),
    numbered('Don\'t break the 2.0% T3 floor. Tempting premium at 0.98% OTM worked once but sample shows 1% OTM = only 57% worthless rate. Single win ≠ rule validation.'),
    numbered('Real friction = ~11.4% of gross. Much smaller than the placeholder model. Embed in projections.'),
    numbered('Late-day entry is profitable on calm-drift days but exposes to gap-and-trend tail. Better to wait for next cycle than late-deploy on volatile days.'),
    new Paragraph({ children: [new PageBreak()] }),

    // ── 9. Caveats ──
    heading('9. Risks, caveats, known gaps', 1),
    numbered('Backtest sample = ~1 year (47 NIFTY E-0 days, 46 E-1 days). Cross-validation on 2024 data still pending.'),
    numbered('SENSEX support is unproven. Only 8 historical days in parquet (cron now adds daily; 30+ days needed before SENSEX-specific rules can be validated).'),
    numbered('Asymmetric distance overlays (gap-up days CE further) are principled inferences, not individually backtested per condition cell.'),
    numbered('Vol-bucket conditioning is currently impotent — every E-0 day in our sample had high_vol. Useless filter.'),
    numbered('Funding cost ₹600/Cr conservatively applied to every event; real returns slightly higher if funding occasional.'),
    numbered('Major-event filter is subjective — assistant must do web-search per ask. Not perfectly automatable yet.'),
    numbered('Kill-switches are heuristic — set 3× the per-trade stop derived from ₹7K/Cr cap. Permissive deliberately.'),

    heading('10. Quick-reference cheat sheet', 1),
    callout('THE ESSENTIALS',
        'Daily login (~9 AM): python3 scripts/kite_login.py\n' +
        'Trade card request: ask assistant — "give me expiry levels"\n' +
        'Manual data export: python3 scripts/export_csv.py [options]\n' +
        'Ingest catch-up: python3 scripts/run_kite_ingest.py --days 7\n' +
        'Cron status: launchctl print gui/$(id -u)/com.rohanshah.kite-ingest\n' +
        'Strategy doc: STRATEGY_LIVE.md\n' +
        'Findings log: FINDINGS_LOG.md\n' +
        'PDF report: results/backtest_report.pdf\n' +
        'CSVs from analyses: results/NNN_*/{per_day,full_grid,...}.csv\n' +
        'Self-service exports: results/exports/',
        BG_HIGHLIGHT),
    p(' '),
    p({}, ),
    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 240, after: 0 },
        children: [new TextRun({
            text: 'End of Operations Manual · Strategy v2.0 · Locked 28-Apr-2026',
            italics: true, size: 18, color: '64748B',
        })],
    }),
    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 60, after: 0 },
        children: [new TextRun({
            text: 'Live infra: Kite Connect API + launchd cron · First validated: ₹3.88L on ₹100cr',
            italics: true, size: 16, color: '64748B',
        })],
    }),
];

// ── Build document ────────────────────────────────────────────────────
const doc = new Document({
    styles: {
        default: { document: { run: { font: 'Calibri', size: 22 } } },
    },
    numbering: {
        config: [
            {
                reference: 'bullets',
                levels: [{
                    level: 0, format: LevelFormat.BULLET, text: '•',
                    alignment: AlignmentType.LEFT,
                    style: { paragraph: { indent: { left: 720, hanging: 360 } } },
                }],
            },
            {
                reference: 'numbers',
                levels: [{
                    level: 0, format: LevelFormat.DECIMAL, text: '%1.',
                    alignment: AlignmentType.LEFT,
                    style: { paragraph: { indent: { left: 720, hanging: 360 } } },
                }],
            },
        ],
    },
    sections: [{
        properties: {
            page: {
                size: { width: 12240, height: 15840 },
                margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
            },
        },
        headers: {
            default: new Header({
                children: [new Paragraph({
                    alignment: AlignmentType.RIGHT,
                    children: [new TextRun({
                        text: 'Trading System Operations Manual · v2.0',
                        size: 16, color: '64748B', italics: true,
                    })],
                })],
            }),
        },
        footers: {
            default: new Footer({
                children: [new Paragraph({
                    alignment: AlignmentType.CENTER,
                    children: [
                        new TextRun({ text: 'Page ', size: 16, color: '64748B' }),
                        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: '64748B' }),
                        new TextRun({ text: ' of ', size: 16, color: '64748B' }),
                        new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: '64748B' }),
                    ],
                })],
            }),
        },
        children: content,
    }],
});

Packer.toBuffer(doc).then(buf => {
    fs.writeFileSync(OUT, buf);
    const kb = (buf.length / 1024).toFixed(1);
    console.log(`✓ ${OUT} (${kb} KB)`);
});
