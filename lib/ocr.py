"""
lib/ocr.py — screenshot OCR via macOS Vision framework (no tesseract needed).

Used by both the Telegram bot and the web /api/journal/screenshot endpoint.
Vision's VNRecognizeTextRequest is highly accurate on app screenshots
(phone + web), far better than default tesseract on UI text.
"""
from __future__ import annotations
import tempfile
from pathlib import Path


def ocr_image_bytes(img_bytes: bytes) -> str:
    """OCR image bytes → plain text, line per recognized string (top-to-bottom)."""
    import Vision
    import Quartz
    from Foundation import NSURL

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(img_bytes)
        tmp = f.name
    try:
        url = NSURL.fileURLWithPath_(tmp)
        src = Quartz.CGImageSourceCreateWithURL(url, None)
        if src is None:
            return ""
        cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
        if cg is None:
            return ""
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            return ""
        # Collect observations with bounding boxes; sort top-to-bottom, left-to-right
        items = []
        for obs in req.results() or []:
            cand = obs.topCandidates_(1)
            if not cand or not len(cand):
                continue
            text = str(cand[0].string())
            bb = obs.boundingBox()           # normalized, origin bottom-left
            y = 1.0 - bb.origin.y - bb.size.height   # top-down
            x = bb.origin.x
            items.append((round(y, 3), x, text))
        items.sort(key=lambda t: (t[0], t[1]))
        # Group into lines: same y bucket (±0.012) joined with spaces
        lines, cur_y, cur = [], None, []
        for y, x, text in items:
            if cur_y is None or abs(y - cur_y) <= 0.012:
                cur.append(text); cur_y = y if cur_y is None else cur_y
            else:
                lines.append(" ".join(cur)); cur = [text]; cur_y = y
        if cur:
            lines.append(" ".join(cur))
        return "\n".join(lines)
    finally:
        Path(tmp).unlink(missing_ok=True)


def parse_sensibull(text: str) -> dict:
    """Parse OCR text from Sensibull (phone app or web) into structured trades.

    Handles:
      Header: '11th June Sensex Deep OTM (Monarch)' / '...(10th June Trade) M.'
      Spot:   'SENSEX 73867.08 -0.16%'
      Rows (phone+web): '11th Jun 72100 PE -13980 2.25 2.25 0 0 0'
                        'S 09th Jun 22500 PE -61100 0.75 0.05 +43,667 ...'
      Totals: 'Total P&L +2,796', '+1.88L' lakh notation
    """
    import re
    out = {"portfolio_name": None, "instrument": None, "broker": None, "tier": None,
           "spot": None, "positions": [], "totals": {}, "raw_preview": text[:400]}

    def amount(s):
        if not s: return 0.0
        s = s.replace(",", "").replace("+", "").strip()
        mult = 1.0
        if s.endswith(("L", "l")): mult, s = 100_000, s[:-1]
        elif s.endswith(("K", "k")): mult, s = 1_000, s[:-1]
        try: return float(s) * mult
        except ValueError: return 0.0

    header_re = re.compile(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+"
        r"(Sensex|Nifty|Banknifty)\s+(.*?)\s*(?:\((Axis|Monarch|Zerodha|HDFC|ICICI|Kotak)\)|\b(M|A|Z)\s*\.|$)",
        re.IGNORECASE)
    for line in text.splitlines():
        m = header_re.search(line)
        if m and any(k in line.lower() for k in ("otm", "risk", "trade", "carry", "atm", "straddle", "deep", "mid", "high")):
            # Start at the strategy name itself (drops leading OCR'd UI labels like
            # "Total P&L Unbooked P&L") and trim trailing labels after the broker paren.
            name = line[m.start():].strip()
            cut = name.find(")")
            if cut != -1:
                name = name[:cut + 1]
            else:                          # no broker paren — cut at first trailing UI word
                for w in ("Total", "Unbooked", "Booked", "  "):
                    i = name.find(w)
                    if i > 8:
                        name = name[:i].strip(); break
            out["portfolio_name"] = name
            out["instrument"] = m.group(3).upper()
            if m.group(5): out["broker"] = m.group(5).title()
            elif m.group(6): out["broker"] = {"M": "Monarch", "A": "Axis", "Z": "Zerodha"}.get(m.group(6).upper())
            tl = m.group(4).lower()
            out["tier"] = ("Tier 1" if "deep" in tl else
                           "Tier 2" if "mid" in tl else
                           "Tier 3" if "high" in tl else
                           "Carry" if "trade" in tl else
                           "ATM" if ("atm" in tl or "straddle" in tl) else m.group(4).strip())
            break

    m = re.search(r"(SENSEX|NIFTY|BANKNIFTY)\s+([\d,]+\.\d+)", text)
    if m:
        out["spot"] = float(m.group(2).replace(",", ""))
        out["instrument"] = out["instrument"] or m.group(1)

    m = re.search(r"Total\s*P&?L\s*[+\-]?\s*([\d,\.]+[LKlk]?)", text)
    if m: out["totals"]["total_pnl"] = amount(m.group(1))

    # Position rows. OCR (Vision) reorders columns per row by bounding box, so a
    # fixed column-order regex fails on the Sensibull WEB layout. Parse each line
    # ORDER-INDEPENDENTLY: locate the instrument (expiry strike side), then pull
    # the avg/ltp decimals and the qty integer from whatever's left.
    # Strike may OCR comma-grouped on 5-digit SENSEX (e.g. "75,600") — accept the
    # comma form too, else the P&L comma-stripper below would delete the whole row.
    inst_re = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})\s+(\d{2,3},\d{3}|\d{4,6})\s+(PE|CE)", re.I)
    for line in text.splitlines():
        m = inst_re.search(line)
        if not m:
            continue
        strike = int(m.group(3).replace(",", ""))
        side = m.group(4).upper()
        rest = line[:m.start()] + " " + line[m.end():]
        # capture booked P&L BEFORE stripping (rightmost L/K or comma amount on the row)
        # so a closed leg (qty 0 in Sensibull) isn't lost — it still contributed P&L.
        pnl_toks = re.findall(r"[+\-]?\d[\d,]*\.?\d*\s*[LKlk]\b|[+\-]?\d{1,3}(?:,\d{3})+", rest)
        booked_amt = amount(pnl_toks[-1]) if pnl_toks else None
        # 1) strip P&L tokens so their digits don't masquerade as qty/price:
        rest = re.sub(r"[+\-]?\d[\d,]*\.?\d*\s*[LKlk]\b", " ", rest)
        rest = re.sub(r"[+\-]?\d{1,3}(?:,\d{3})+", " ", rest)
        # 2) decimals left are avg (entry) then ltp — order is OCR-reading-order dependent.
        decs = re.findall(r"-?\d{1,4}\.\d{1,2}", rest)
        avg = float(decs[0]) if decs else None
        ltp = float(decs[1]) if len(decs) > 1 else None
        # 3) remove decimals; the largest-magnitude remaining integer is qty
        rest = re.sub(r"-?\d{1,4}\.\d{1,2}", " ", rest)
        ints = [int(x.replace(",", "")) for x in re.findall(r"-?\d{2,8}", rest)]
        ints = [q for q in ints if abs(q) >= 1]
        qty = max(ints, key=abs) if ints else None
        # a CLOSED/booked leg shows qty 0 but carries a Booked P&L — keep it (don't drop).
        if (qty is None or abs(qty) < 1):
            if booked_amt:
                out["positions"].append({
                    "expiry_day": int(m.group(1)), "expiry_month": m.group(2),
                    "strike": strike, "side": side, "qty": 0, "price": avg, "ltp": ltp,
                    "booked_pnl": booked_amt, "status": "closed"})
            continue
        # Sensibull marks sells with a trailing 'S'; force short sign if seen.
        if re.search(r"\bS\b", line) and qty > 0:
            qty = -qty
        # FLAG low-confidence rows (entry price missing, or only one decimal so avg/ltp
        # can't be told apart) instead of importing a silently-wrong premium.
        needs_review = (avg is None) or (len(decs) < 2)
        pos = {"expiry_day": int(m.group(1)), "expiry_month": m.group(2),
               "strike": strike, "side": side, "qty": qty, "price": avg, "ltp": ltp}
        if needs_review:
            pos["needs_review"] = True
            out.setdefault("warnings", []).append(
                f"{m.group(1)} {m.group(2)} {strike}{side}: entry price uncertain (OCR) — verify Avg")
        out["positions"].append(pos)
    return out
