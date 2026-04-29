"""Parse Bank of America business checking PDF statements.

Extracts TikTok ACH deposit lines: posting date, amount, payout ID,
storefront code. The payout ID, with the leading 6-digit prefix joined
to the trailing 13 digits (no space), equals the TikTok Payment ID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from tiktok_qbo.money import to_money

DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{2})$")
AMOUNT_RE = re.compile(r"^[\d,]+\.\d{2}$")
PAYOUT_RE = re.compile(r"payout\s+ID\s+(\d+)\s+(\d+)", re.IGNORECASE)
STOREFRONT_RE = re.compile(r"TikTok\s+Shop-(\w+)", re.IGNORECASE)
# 'TikTok Shop DES:... ID:USLCPLELNU' (anomalous TikTok format, no payout ID)
ALT_STOREFRONT_RE = re.compile(r"ID:(USLC\w+)", re.IGNORECASE)
PAYMENT_ID_RE = re.compile(r"PAYMENT\s+ID:0*([1-9]\d*)", re.IGNORECASE)
# HYPERWALLET — TikTok's pre-2024-01-16 ACH origination ('HYPERWALLET SYST DES:MISC CRED')
HYPERWALLET_RE = re.compile(r"HYPERWALLET", re.IGNORECASE)
# 'TikTok Shop DES:' (anomalous TikTok format that doesn't include payout ID)
TIKTOK_ALT_RE = re.compile(r"TikTok\s+Shop\s+DES:", re.IGNORECASE)
# Default storefront for HYPERWALLET / unidentified-storefront lines: assume LELNU
DEFAULT_STOREFRONT = "USLCPLELNU"


@dataclass(frozen=True)
class BankLine:
    posted_date: date
    amount: Decimal
    payout_id: str          # full payment ID (prefix + suffix joined)
    storefront: str         # USLCPLELNU, USLCPYEWHX, USLCCUEL4Y, etc.
    bank_payment_id: str    # PMT ID from the bank line (different from TikTok Payment ID)
    raw_description: str
    source_pdf: str


def _group_by_line(words, y_tol: float = 3.0):
    """Group page words by y-coordinate (rows). Returns list of (y, sorted_words)."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows = []
    current_y = words[0]["top"]
    current = [words[0]]
    for w in words[1:]:
        if abs(w["top"] - current_y) <= y_tol:
            current.append(w)
        else:
            rows.append((current_y, sorted(current, key=lambda x: x["x0"])))
            current_y = w["top"]
            current = [w]
    rows.append((current_y, sorted(current, key=lambda x: x["x0"])))
    return rows


def _row_text(row_words) -> str:
    return " ".join(w["text"] for w in row_words)


def _row_starts_with_date(row_words) -> tuple | None:
    if not row_words:
        return None
    m = DATE_RE.match(row_words[0]["text"])
    if not m:
        return None
    mm, dd, yy = m.groups()
    yyyy = 2000 + int(yy)
    return date(yyyy, int(mm), int(dd))


def _amount_in_row(row_words):
    """Find the rightmost amount-like token (the deposit amount column)."""
    for w in reversed(row_words):
        if AMOUNT_RE.match(w["text"]):
            return to_money(w["text"].replace(",", ""))
    return None


def parse_bank_pdf(pdf_path: Path) -> list[BankLine]:
    """Parse a BofA PDF, returning all TikTok deposit lines."""
    pdf_path = Path(pdf_path)
    out: list[BankLine] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            rows = _group_by_line(words)
            text_rows = [(_row_text(r), r) for _, r in rows]

            # Only process pages within the "Deposits and other credits" section.
            in_deposits = False
            for i, (txt, row_words) in enumerate(text_rows):
                if "Deposits and other credits" in txt:
                    in_deposits = True
                    continue
                if in_deposits and (
                    "Withdrawals and other debits" in txt
                    or "Service fees" in txt
                    or "Total deposits and other credits" in txt
                ):
                    in_deposits = False
                    continue
                if not in_deposits:
                    continue

                # Detect a deposit-start row: begins with MM/DD/YY and originates
                # from TikTok or HYPERWALLET (TikTok's pre-2024-01-16 ACH provider).
                d = _row_starts_with_date(row_words)
                if d is None:
                    continue
                is_tiktok = "TikTok" in txt
                is_hyperwallet = HYPERWALLET_RE.search(txt) is not None
                if not (is_tiktok or is_hyperwallet):
                    continue

                amount = _amount_in_row(row_words)

                # Bank PMT ID is on the same row (only present for old TikTok format).
                pmt_match = PAYMENT_ID_RE.search(txt)
                bank_pmt = pmt_match.group(1) if pmt_match else ""

                # Look at next 1-3 rows for the storefront + payout ID continuation.
                # Stop only at the next deposit row (one starting with MM/DD/YY).
                continuation = ""
                for j in range(1, 4):
                    if i + j >= len(text_rows):
                        break
                    nxt_words = text_rows[i + j][1]
                    if nxt_words and DATE_RE.match(nxt_words[0]["text"]):
                        break
                    continuation += " " + text_rows[i + j][0]

                full = txt + continuation

                sf_m = STOREFRONT_RE.search(full) or ALT_STOREFRONT_RE.search(full)
                po_m = PAYOUT_RE.search(full)

                # Three possible formats:
                #   (a) TikTok with payout ID  -> use payout_id as join key
                #   (b) TikTok-alt (no payout) -> empty payout_id, match by date+amt
                #   (c) HYPERWALLET (no payout)-> empty payout_id, match by date+amt
                if po_m:
                    payout_id = po_m.group(1) + po_m.group(2)
                else:
                    payout_id = ""

                # Storefront resolution:
                #   - If matched, use it
                #   - HYPERWALLET defaults to LELNU (TikTok used HW for that storefront)
                #   - Else empty (will appear in unmatched bank report)
                if sf_m:
                    storefront = sf_m.group(1)
                elif is_hyperwallet:
                    storefront = DEFAULT_STOREFRONT
                else:
                    storefront = ""

                if amount is None:
                    # Amount may be on a continuation row (rare layout glitch).
                    for j in range(1, 4):
                        if i + j >= len(text_rows):
                            break
                        amt = _amount_in_row(text_rows[i + j][1])
                        if amt is not None:
                            amount = amt
                            break
                if amount is None:
                    continue

                out.append(BankLine(
                    posted_date=d,
                    amount=amount,
                    payout_id=payout_id,
                    storefront=storefront,
                    bank_payment_id=bank_pmt,
                    raw_description=full.strip(),
                    source_pdf=pdf_path.name,
                ))

    return out


def parse_bank_pdfs(paths) -> list[BankLine]:
    """Parse multiple PDFs and return a combined sorted list."""
    out: list[BankLine] = []
    for p in paths:
        out.extend(parse_bank_pdf(Path(p)))
    out.sort(key=lambda b: (b.posted_date, b.payout_id))
    return out
