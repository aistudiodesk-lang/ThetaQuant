// Build single-page DOCX of daily data routine.
// Run: node scripts/make_daily_routine_docx.js
const fs = require('fs');
const path = require('path');
const {
    Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
    AlignmentType, LevelFormat, HeadingLevel,
    BorderStyle, WidthType, ShadingType,
} = require('docx');

const OUT = path.resolve(__dirname, '..', 'results', 'DAILY_DATA_ROUTINE.docx');

const BG_CMD = "F1F5F9";
const BG_GREEN = "BBF7D0";
const BG_YELLOW = "FEF08A";
const BG_BLUE = "E0E7FF";

const p = (text, opts = {}) => new Paragraph({
    spacing: { before: 60, after: 60 },
    children: Array.isArray(text) ? text : [new TextRun({ text, ...opts })],
});

const heading = (text) => new Paragraph({
    spacing: { before: 200, after: 100 },
    children: [new TextRun({ text, bold: true, size: 26, color: "1E40AF" })],
});

const code = (text) => new TextRun({
    text, font: "Courier New", size: 18,
    shading: { type: ShadingType.CLEAR, fill: BG_CMD },
});

const codeBlock = (lines, accent = "1E40AF") => {
    const cellChildren = lines.map(line => new Paragraph({
        spacing: { before: 0, after: 0 },
        children: [new TextRun({
            text: line || ' ',
            font: 'Courier New', size: 18,
            color: line.trimStart().startsWith('#') ? '64748B' : '0F172A',
        })],
    }));
    return new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [9360],
        rows: [new TableRow({
            children: [new TableCell({
                width: { size: 9360, type: WidthType.DXA },
                shading: { type: ShadingType.CLEAR, fill: BG_CMD },
                margins: { top: 100, bottom: 100, left: 200, right: 200 },
                borders: {
                    top: { style: BorderStyle.SINGLE, size: 4, color: '94A3B8' },
                    bottom: { style: BorderStyle.SINGLE, size: 4, color: '94A3B8' },
                    left: { style: BorderStyle.SINGLE, size: 16, color: accent },
                    right: { style: BorderStyle.SINGLE, size: 4, color: '94A3B8' },
                },
                children: cellChildren,
            })],
        })],
    });
};

const callout = (label, body, bg = BG_YELLOW, border = "CA8A04") => new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [9360],
    rows: [new TableRow({
        children: [new TableCell({
            width: { size: 9360, type: WidthType.DXA },
            shading: { type: ShadingType.CLEAR, fill: bg },
            margins: { top: 120, bottom: 120, left: 200, right: 200 },
            borders: {
                top: { style: BorderStyle.SINGLE, size: 4, color: '64748B' },
                bottom: { style: BorderStyle.SINGLE, size: 4, color: '64748B' },
                left: { style: BorderStyle.SINGLE, size: 16, color: border },
                right: { style: BorderStyle.SINGLE, size: 4, color: '64748B' },
            },
            children: [
                new Paragraph({
                    spacing: { before: 0, after: 60 },
                    children: [new TextRun({ text: label, bold: true, size: 22 })],
                }),
                new Paragraph({
                    spacing: { before: 0, after: 0 },
                    children: [new TextRun({ text: body, size: 20 })],
                }),
            ],
        })],
    })],
});

const issuesTable = () => {
    const border = { style: BorderStyle.SINGLE, size: 4, color: 'CBD5E1' };
    const borders = { top: border, bottom: border, left: border, right: border };
    return new Table({
        width: { size: 9360, type: WidthType.DXA },
        columnWidths: [3500, 5860],
        rows: [
            new TableRow({
                tableHeader: true,
                children: [
                    new TableCell({
                        width: { size: 3500, type: WidthType.DXA },
                        shading: { type: ShadingType.CLEAR, fill: '1E40AF' },
                        margins: { top: 80, bottom: 80, left: 100, right: 100 },
                        borders,
                        children: [new Paragraph({
                            spacing: { before: 0, after: 0 },
                            children: [new TextRun({ text: 'Problem', bold: true, color: 'FFFFFF', size: 18 })],
                        })],
                    }),
                    new TableCell({
                        width: { size: 5860, type: WidthType.DXA },
                        shading: { type: ShadingType.CLEAR, fill: '1E40AF' },
                        margins: { top: 80, bottom: 80, left: 100, right: 100 },
                        borders,
                        children: [new Paragraph({
                            spacing: { before: 0, after: 0 },
                            children: [new TextRun({ text: 'Fix', bold: true, color: 'FFFFFF', size: 18 })],
                        })],
                    }),
                ],
            }),
            ...[
                ["Missing kite_session.json error", null, "Re-run ", "python3 scripts/kite_login.py"],
                ["Token expired error", null, "Same — re-login", null],
                ["request_token expired", null, "Login again, paste URL within 60 sec of clicking", null],
                ["Cron not running automatically", null,
                 "Re-enable: ", 'launchctl load -w "scripts/com.rohanshah.kite-ingest.plist"'],
            ].map((row, i) => new TableRow({
                children: [
                    new TableCell({
                        width: { size: 3500, type: WidthType.DXA },
                        shading: { type: ShadingType.CLEAR, fill: i % 2 === 0 ? 'FFFFFF' : 'F8FAFC' },
                        margins: { top: 80, bottom: 80, left: 100, right: 100 },
                        borders,
                        children: [new Paragraph({
                            spacing: { before: 0, after: 0 },
                            children: [new TextRun({ text: row[0], size: 18 })],
                        })],
                    }),
                    new TableCell({
                        width: { size: 5860, type: WidthType.DXA },
                        shading: { type: ShadingType.CLEAR, fill: i % 2 === 0 ? 'FFFFFF' : 'F8FAFC' },
                        margins: { top: 80, bottom: 80, left: 100, right: 100 },
                        borders,
                        children: [new Paragraph({
                            spacing: { before: 0, after: 0 },
                            children: [
                                new TextRun({ text: row[2], size: 18 }),
                                ...(row[3] ? [new TextRun({ text: row[3], font: 'Courier New', size: 16, color: '0F172A' })] : []),
                            ],
                        })],
                    }),
                ],
            })),
        ],
    });
};

const content = [
    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 100 },
        children: [new TextRun({ text: 'Daily Data Saving — Routine', bold: true, size: 36, color: '0F172A' })],
    }),
    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 240 },
        children: [new TextRun({
            text: 'Save 1-minute NIFTY + SENSEX option data every trading day',
            italics: true, size: 20, color: '64748B',
        })],
    }),

    heading('⏰ What needs to be done — ONCE every trading day before 4 PM IST'),
    p([new TextRun({ text: 'Just one command. ~30 seconds.', bold: true })]),
    codeBlock([
        'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
        'python3 scripts/kite_login.py',
    ], '16A34A'),

    p([new TextRun({ text: 'Steps when the script runs:', bold: true })]),
    new Paragraph({
        numbering: { reference: 'numbers', level: 0 },
        spacing: { before: 30, after: 30 },
        children: [new TextRun('Browser opens → log in to Zerodha (PIN + TOTP)')],
    }),
    new Paragraph({
        numbering: { reference: 'numbers', level: 0 },
        spacing: { before: 30, after: 30 },
        children: [
            new TextRun('Browser shows '),
            new TextRun({ text: '"site can\'t be reached"', italics: true }),
            new TextRun(' at '),
            code('127.0.0.1:5000'),
            new TextRun(' — '),
            new TextRun({ text: "that's expected", bold: true }),
        ],
    }),
    new Paragraph({
        numbering: { reference: 'numbers', level: 0 },
        spacing: { before: 30, after: 30 },
        children: [
            new TextRun({ text: 'Copy the FULL URL from address bar', bold: true }),
            new TextRun(' (contains '),
            code('request_token=...'),
            new TextRun(')'),
        ],
    }),
    new Paragraph({
        numbering: { reference: 'numbers', level: 0 },
        spacing: { before: 30, after: 30 },
        children: [new TextRun('Paste it back into the terminal where the script is waiting')],
    }),
    new Paragraph({
        numbering: { reference: 'numbers', level: 0 },
        spacing: { before: 30, after: 30 },
        children: [
            new TextRun('Script prints '),
            new TextRun({ text: '✓ Session saved', bold: true, color: '16A34A' }),
            new TextRun('. Done.'),
        ],
    }),

    callout("THAT'S IT",
        "A scheduled cron job runs automatically at 16:30 IST after market close and saves the day's data. No further action needed.",
        BG_GREEN, "16A34A"),

    heading('✅ How to verify it worked (after 4:30 PM IST)'),
    codeBlock([
        'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
        '',
        '# Check today is in the log:',
        "python3 -c \"import pandas as pd; print(pd.read_parquet('data/kite_ingest_log.parquet').tail(5).to_string(index=False))\"",
    ]),
    p("Today's date should appear with both NIFTY and SENSEX in the last few rows. If missing → run the catch-up below."),

    heading('🔧 If something is missed — catch-up'),
    p('If login was skipped one day OR cron failed, run this the next morning:'),
    codeBlock([
        'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
        '',
        '# 1. Login first (always)',
        'python3 scripts/kite_login.py',
        '',
        '# 2. Catch up missing days (safe — auto-skips days already saved)',
        'python3 scripts/run_kite_ingest.py --days 7',
    ], 'CA8A04'),
    p('Takes ~2-3 minutes. Auto-deduplicated — same day twice is harmless.'),

    heading('🚨 Common issues'),
    issuesTable(),

    heading('🆘 If completely stuck — full reset'),
    codeBlock([
        'cd "/Users/rohanshah/Desktop/AI Instructions/Trading Developments/05 - Backtest Engine (separate)"',
        'rm ~/.config/kite_session.json',
        'python3 scripts/kite_login.py',
        'python3 scripts/run_kite_ingest.py --days 14',
    ], 'B91C1C'),
    p('Or text Rohan / open a fresh chat with the assistant — paste the error message.'),

    new Paragraph({ spacing: { before: 240, after: 0 }, children: [] }),
    callout('STORAGE NOTE',
        '~1 MB per instrument per day. ~500 MB/year total. Auto-dedupes. Don\'t delete anything from data/parquet/.',
        BG_BLUE, "1E40AF"),

    new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 200, after: 0 },
        children: [new TextRun({
            text: 'Strategy + backtest details: see OPERATIONS_MANUAL.docx (separate). This file is ONLY about daily data saving.',
            italics: true, size: 16, color: '64748B',
        })],
    }),
];

const doc = new Document({
    styles: { default: { document: { run: { font: 'Calibri', size: 22 } } } },
    numbering: {
        config: [{
            reference: 'numbers',
            levels: [{
                level: 0, format: LevelFormat.DECIMAL, text: '%1.',
                alignment: AlignmentType.LEFT,
                style: { paragraph: { indent: { left: 720, hanging: 360 } } },
            }],
        }],
    },
    sections: [{
        properties: {
            page: {
                size: { width: 12240, height: 15840 },
                margin: { top: 1080, right: 1440, bottom: 1080, left: 1440 },
            },
        },
        children: content,
    }],
});

Packer.toBuffer(doc).then(buf => {
    fs.writeFileSync(OUT, buf);
    console.log(`✓ ${OUT} (${(buf.length/1024).toFixed(1)} KB)`);
});
