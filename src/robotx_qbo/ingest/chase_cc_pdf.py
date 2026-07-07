"""Chase Ultimate Rewards business credit-card PDF statement parser.

parse_chase_cc(pdf_path) -> list[Txn]

Layout (all pages joined):
  - ACCOUNT SUMMARY block: Previous/Payment,Credits/Purchases/Fees/Interest/New,
    plus "Opening/Closing Date MM/DD/YY - MM/DD/YY" (gives the period + years).
  - ACCOUNT ACTIVITY section: one row per transaction —
      "MM/DD  <merchant / description>  <amount>"
    Amounts: purchases positive; payments and merchant credits negative.
    Fees ("LATE FEE", "FOREIGN TRANSACTION FEE") are positive charge rows.
    Amounts may omit the leading zero (".15") and may carry a leading "-".
  - Airline itinerary sub-lines ("1 K ONT DFW") have no trailing amount → skipped.

Each Txn carries the raw statement sign (positive = charge, negative =
payment/credit) and a `kind`:
    payment  — "AUTOMATIC PAYMENT" / "PAYMENT THANK YOU" (settled on the bank
               side; do NOT re-book as a card charge)
    refund   — any other negative line (merchant credit, fee reversal)
    fee      — positive LATE FEE / FOREIGN TRANSACTION FEE / interest
    charge   — everything else (a normal purchase)
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from robotx_qbo.models import Txn

# "MM/DD  <desc>  <amount>"  — amount may be ".15", "1,027.39", "-40.00".
_ROW = re.compile(r"^(\d{2})/(\d{2})\s+(.+?)\s+(-?\$?[\d,]*\.\d{2})$")
_OPEN_CLOSE = re.compile(r"Opening/Closing Date\s+(\d{2})/(\d{2})/(\d{2})\s*-\s*(\d{2})/(\d{2})/(\d{2})")
_SUM = {
    "prev": re.compile(r"Previous Balance\s+\$?(-?[\d,]*\.\d{2})"),
    "pay": re.compile(r"Payment,?\s*Credits\s+(-?\$?-?[\d,]*\.\d{2})"),
    "purch": re.compile(r"Purchases\s+\+?\$?(-?[\d,]*\.\d{2})"),
    "fees": re.compile(r"Fees Charged\s+\+?\$?(-?[\d,]*\.\d{2})"),
    "interest": re.compile(r"Interest Charged\s+\+?\$?(-?[\d,]*\.\d{2})"),
    "new": re.compile(r"New Balance\s+\$?(-?[\d,]*\.\d{2})"),
}

_PAYMENT_MARKERS = ("automatic payment", "payment thank you")
_FEE_MARKERS = ("late fee", "foreign transaction fee", "cash advance fee",
                "annual membership fee", "interest charge")


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", "").replace("$", ""))


def _year_for(mm: int, dd: int, o: date, c: date) -> int:
    """Pick the year so the txn date sits within (or nearest to) the statement
    period [o, c]. The period spans <=31 days and crosses at most one new year."""
    cands = {o.year, c.year}
    best, best_dist = None, None
    for y in cands:
        try:
            d = date(y, mm, dd)
        except ValueError:
            continue
        if o <= d <= c:
            return y
        dist = min(abs((d - o).days), abs((d - c).days))
        if best_dist is None or dist < best_dist:
            best, best_dist = y, dist
    return best if best is not None else c.year


def _summary(text: str) -> dict:
    out = {}
    for k, rx in _SUM.items():
        m = rx.search(text)
        out[k] = _money(m.group(1)) if m else None
    return out


def _kind(desc: str, amt: Decimal) -> str:
    dl = desc.lower()
    if any(m in dl for m in _PAYMENT_MARKERS):
        return "payment"
    if amt < 0:
        return "refund"
    if any(m in dl for m in _FEE_MARKERS):
        return "fee"
    return "charge"


def parse_chase_cc(pdf_path: str | Path) -> list[Txn]:
    src = Path(pdf_path).name
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    m = _OPEN_CLOSE.search(text)
    if not m:
        raise ValueError(f"Cannot find Opening/Closing Date in {pdf_path}")
    om, od, oy, cm, cd, cy = (int(x) for x in m.groups())
    open_d = date(2000 + oy, om, od)
    close_d = date(2000 + cy, cm, cd)

    txns: list[Txn] = []
    for line in text.splitlines():
        u = line.strip()
        rm = _ROW.match(u)
        if not rm:
            continue
        mm, dd, desc, amt_s = int(rm.group(1)), int(rm.group(2)), rm.group(3).strip(), rm.group(4)
        try:
            y = _year_for(mm, dd, open_d, close_d)
            txn_date = date(y, mm, dd)
        except ValueError:
            continue
        amt = _money(amt_s)
        txns.append(Txn(account="chase_cc", date=txn_date, amount=amt,
                        kind=_kind(desc, amt), description=desc, source=src))
    return txns


def reconcile(pdf_path: str | Path) -> dict:
    """Return per-statement reconciliation: parsed vs the ACCOUNT SUMMARY.
    charges (fees incl.) should equal Purchases + Fees + Interest; negatives
    should equal Payment,Credits; and prev + those = New Balance."""
    with pdfplumber.open(pdf_path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    s = _summary(text)
    txns = parse_chase_cc(pdf_path)
    charges = sum(t.amount for t in txns if t.amount > 0)
    credits = sum(t.amount for t in txns if t.amount < 0)
    exp_charges = (s["purch"] or 0) + (s["fees"] or 0) + (s["interest"] or 0)
    return {
        "charges": charges, "exp_charges": exp_charges,
        "credits": credits, "exp_credits": s["pay"],
        "charges_ok": charges == exp_charges,
        "credits_ok": credits == (s["pay"] or 0),
        "new_ok": (s["prev"] or 0) + charges + credits == (s["new"] or 0),
        "summary": s,
    }
