"""Reconcile TikTok Payments (xlsx) against parsed bank lines (PDF).

Match key: payment_id <-> bank_line.payout_id (1:1).
Tolerance: ±$0.01 on amount; date is informational.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from tiktok_qbo.models import PaymentRow
from tiktok_qbo.ingest.bank_pdf import BankLine
from tiktok_qbo.money import close_enough


@dataclass(frozen=True)
class BankMatch:
    payment_id: str
    payment_amount: Decimal
    payment_completion_date: object  # date
    bank_amount: Decimal | None
    bank_posted_date: object | None
    bank_source_pdf: str | None
    matched: bool
    amount_ok: bool
    note: str = ""


def reconcile_payments_to_bank(
    payments: Iterable[PaymentRow],
    bank_lines: Iterable[BankLine],
    storefront: str = "USLCPLELNU",
    date_window_days: int = 4,
) -> tuple[list[BankMatch], list[BankLine]]:
    """Return (matches, unmatched_bank_lines).

    Two-pass match:
      1. Primary: payment_id == payout_id (1:1, exact).
      2. Fallback (for HYPERWALLET / no-payout-ID bank lines): match by
         (storefront, amount, |bank_date - payment_completion_date| <= window).
    """
    from datetime import timedelta

    sf_lines = [b for b in bank_lines if b.storefront == storefront]
    by_payout: dict[str, BankLine] = {}
    no_payout: list[BankLine] = []
    dupes: dict[str, int] = {}
    for b in sf_lines:
        if b.payout_id:
            if b.payout_id in by_payout:
                dupes[b.payout_id] = dupes.get(b.payout_id, 1) + 1
            else:
                by_payout[b.payout_id] = b
        else:
            no_payout.append(b)

    matches: list[BankMatch] = []
    used_ids: set[str] = set()
    used_no_payout_idx: set[int] = set()

    for p in payments:
        # Pass 1: payment_id == payout_id
        b = by_payout.get(p.payment_id)
        if b is not None:
            used_ids.add(p.payment_id)
            amount_ok = close_enough(p.payment_amount, b.amount)
            note = ""
            if not amount_ok:
                note = f"amount diff ${(p.payment_amount - b.amount):.2f}"
            if p.payment_id in dupes:
                note = (note + "; " if note else "") + f"{dupes[p.payment_id]} bank dupes"
            matches.append(BankMatch(
                payment_id=p.payment_id,
                payment_amount=p.payment_amount,
                payment_completion_date=p.payment_completion_date,
                bank_amount=b.amount,
                bank_posted_date=b.posted_date,
                bank_source_pdf=b.source_pdf,
                matched=True,
                amount_ok=amount_ok,
                note=note,
            ))
            continue

        # Pass 2: fallback to (amount, date_window) match against no-payout lines.
        candidate_idx = None
        best_diff = None
        for idx, bl in enumerate(no_payout):
            if idx in used_no_payout_idx:
                continue
            if not close_enough(p.payment_amount, bl.amount):
                continue
            diff_days = abs((bl.posted_date - p.payment_completion_date).days)
            if diff_days > date_window_days:
                continue
            if best_diff is None or diff_days < best_diff:
                candidate_idx = idx
                best_diff = diff_days

        if candidate_idx is not None:
            bl = no_payout[candidate_idx]
            used_no_payout_idx.add(candidate_idx)
            note = f"matched by amount+date ({bl.raw_description.split(' DES:')[0][-20:].strip()})"
            matches.append(BankMatch(
                payment_id=p.payment_id,
                payment_amount=p.payment_amount,
                payment_completion_date=p.payment_completion_date,
                bank_amount=bl.amount,
                bank_posted_date=bl.posted_date,
                bank_source_pdf=bl.source_pdf,
                matched=True,
                amount_ok=True,
                note=note,
            ))
            continue

        # No match either way
        matches.append(BankMatch(
            payment_id=p.payment_id,
            payment_amount=p.payment_amount,
            payment_completion_date=p.payment_completion_date,
            bank_amount=None,
            bank_posted_date=None,
            bank_source_pdf=None,
            matched=False,
            amount_ok=False,
            note="no bank line found",
        ))

    unmatched_bank = [b for b in sf_lines if b.payout_id not in used_ids]
    # Remove bank lines we matched via the no-payout fallback
    unmatched_bank = [
        b for b in unmatched_bank
        if not (b.payout_id == "" and b in [no_payout[i] for i in used_no_payout_idx])
    ]
    return matches, unmatched_bank
