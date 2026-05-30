"""JPMorgan Chase business-checking PDF statement parser.

parse_chase(pdf_path) -> list[Txn]

Layout (all pages joined):
  - Header: <Month DD, YYYY>through<Month DD, YYYY> — year from the "through" side
  - *start*deposits and additions ... *end*deposits and additions
  - *start*electronic withdrawal ... *end*electronic withdrawal
  - Each section has a column-header row (DATE DESCRIPTION AMOUNT) then transaction rows.
  - Transaction rows: first line begins with MM/DD (optionally prefixed by another MM/DD);
    amount is always on that first line, trailing, with optional $ prefix.
  - Continuation lines (Trn:, wrapped memo text) do not start with MM/DD — skipped.
  - January file has no transaction sections → returns empty list.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from robotx_qbo.models import Txn

# ── regexes ───────────────────────────────────────────────────────────────────

# Extract year from the "through" date in the header
_HDR_YEAR = re.compile(r"through[A-Za-z]+ \d{1,2}, (\d{4})")

# Transaction row: optional leading MM/DD prefix, then the real MM/DD, description, amount.
# Amount may have a leading $ and uses commas.
# Examples:
#   "02/20 Fedwire Credit Via: ... $170,000.00"
#   "03/30 03/30 Domestic Wire Transfer ... 497,000.00"
#   "04/01 03/31 Online Transfer ... $15,000.00"
_TXN_ROW = re.compile(
    r"^(?:\d{2}/\d{2}\s+)?(\d{2})/(\d{2})\s+(.*?)\s+\$?([\d,]+\.\d{2})\s*$"
)

# Section boundary markers (lowercased for matching)
_SECT_DEP_START = "*start*deposits and additions"
_SECT_DEP_END = "*end*deposits and additions"
_SECT_WD_START = "*start*electronic withdrawal"
_SECT_WD_END = "*end*electronic withdrawal"

# Column header line to skip
_COL_HDR = re.compile(r"^DATE\s+DESCRIPTION\s+AMOUNT", re.IGNORECASE)

# Total lines to stop at (safety stop within section)
_TOTAL_LINE = re.compile(r"^Total\s+", re.IGNORECASE)


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _kind_dep(desc: str) -> str:
    u = desc.upper()
    if "FEDWIRE" in u or "WIRE" in u:
        return "transfer"
    if "DEPOSIT" in u:
        return "deposit"
    return "deposit"


def _kind_wd(desc: str) -> str:
    u = desc.upper()
    if "ONLINE TRANSFER" in u or "ONLINE DOMESTIC WIRE" in u or "DOMESTIC WIRE" in u or "WIRE" in u:
        return "transfer"
    if "IRS" in u or "USATAXPYMT" in u:
        return "tax"
    if "EMPLOYMENT DEVEL" in u or "EDD" in u:
        return "tax"
    if "CA DEPT TAX" in u or "CDTFA" in u:
        return "tax"
    if "CHASE CREDIT CRD" in u or "CREDIT CRD" in u or "AUTOPAYBUSSEC" in u:
        return "cc"
    return "withdrawal"


def parse_chase(pdf_path: str | Path) -> list[Txn]:
    src = Path(pdf_path).name

    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    # Extract year from "throughMonth DD, YYYY" pattern
    m = _HDR_YEAR.search(text)
    if not m:
        raise ValueError(f"Cannot find 'through<date>' header in {pdf_path}")
    year = int(m.group(1))

    txns: list[Txn] = []
    lines = text.splitlines()

    # We scan through lines, activating sections when we hit *start* markers.
    section: str | None = None  # "dep" | "wd" | None

    for line in lines:
        u = line.strip()
        lo = u.lower()

        # ── section boundary detection ────────────────────────────────────────
        if lo == _SECT_DEP_START:
            section = "dep"
            continue
        if lo == _SECT_DEP_END:
            section = None
            continue
        if lo == _SECT_WD_START:
            section = "wd"
            continue
        if lo == _SECT_WD_END:
            section = None
            continue

        if section is None:
            continue

        # ── within an active section ──────────────────────────────────────────
        if not u:
            continue
        if _COL_HDR.match(u):
            continue
        if _TOTAL_LINE.match(u):
            section = None
            continue

        # Match transaction row: must start with MM/DD (possibly preceded by another MM/DD)
        m2 = _TXN_ROW.match(u)
        if not m2:
            # Continuation line (Trn:, wrapped memo, etc.) — skip
            continue

        mm_s, dd_s, desc, amt_s = m2.groups()
        amt = _money(amt_s)
        txn_date = date(year, int(mm_s), int(dd_s))

        if section == "dep":
            signed_amt = amt
            kind = _kind_dep(desc)
        else:
            signed_amt = -amt
            kind = _kind_wd(desc)

        txns.append(
            Txn(
                account="chase",
                date=txn_date,
                amount=signed_amt,
                kind=kind,
                description=desc.strip(),
                source=src,
            )
        )

    return txns
