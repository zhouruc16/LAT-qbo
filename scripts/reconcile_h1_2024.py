"""End-to-end H1 2024 LELNU reconciliation.

Reads:
  - Q1 LELNU xlsx
  - Q2 LELNU xlsx
  - All 7 BofA PDFs (Jan-Jul 2024)

Filters to USLCPLELNU storefront, runs internal identity checks
(net+ship+fees+adj+reserve = payable; sum_payable = payment_amount),
joins payments to bank lines by Payment ID, and emits:
  - state_h1/reconcile-summary.csv
  - state_h1/reconcile-unmatched-bank.csv
  - state_h1/reconcile-h1-summary.txt

Usage:
    python scripts/reconcile_h1_2024.py
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from tiktok_qbo.ingest.lat_xlsx import (
    read_order_details, read_statements, read_payments, read_reserves,
)
from tiktok_qbo.ingest.bank_pdf import parse_bank_pdfs
from tiktok_qbo.ingest.bank_reconcile import reconcile_payments_to_bank
from tiktok_qbo.reconcile import check_identity_1, check_identity_2
from tiktok_qbo.money import money_sum

DOWNLOADS = Path(r"C:\Users\zhour\Downloads")
Q1_XLSX = DOWNLOADS / "1-3-2024.xlsx"
Q2_XLSX = DOWNLOADS / "4-6-2024.xlsx"
BANK_PDFS = sorted(DOWNLOADS.glob("eStmt_2024-*.pdf"))
SHOP_ID = "PLELNU"
STOREFRONT = "USLCPLELNU"
H1_START = date(2024, 1, 1)
H1_END = date(2024, 6, 30)

OUT_DIR = Path("state_h1")


def _in_h1(d: date | None) -> bool:
    return d is not None and H1_START <= d <= H1_END


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Ingesting xlsx files...")
    rows = read_order_details(Q1_XLSX, shop_id=SHOP_ID) + read_order_details(Q2_XLSX, shop_id=SHOP_ID)
    stmts = read_statements(Q1_XLSX, shop_id=SHOP_ID) + read_statements(Q2_XLSX, shop_id=SHOP_ID)
    pays = read_payments(Q1_XLSX, shop_id=SHOP_ID) + read_payments(Q2_XLSX, shop_id=SHOP_ID)

    # Filter to H1 by statement_date / payment_initiation_date.
    rows = [r for r in rows if _in_h1(r.statement_date)]
    stmts_h1 = [s for s in stmts if _in_h1(s.statement_date)]
    pays_h1 = [p for p in pays if _in_h1(p.payment_initiation_date)]

    print(f"  rows={len(rows)} statements={len(stmts_h1)} payments={len(pays_h1)}")

    print("\nIdentity checks (xlsx-internal)...")
    id2 = check_identity_2(stmts_h1)
    id1 = check_identity_1(stmts_h1, pays_h1)
    print(f"  identity_2 (Net+Ship+Fees+Adj+Reserve = Payable): {len(id2)} mismatches")
    print(f"  identity_1 (sum Payable per Payment ID = Payment amount): {len(id1)} mismatches")

    print(f"\nParsing {len(BANK_PDFS)} bank PDFs...")
    bank_lines = parse_bank_pdfs(BANK_PDFS)
    sf_lines = [b for b in bank_lines if b.storefront == STOREFRONT]
    print(f"  total TikTok lines: {len(bank_lines)}")
    print(f"  {STOREFRONT} lines: {len(sf_lines)}, sum=${sum(b.amount for b in sf_lines):,.2f}")

    print("\nReconciling Payments <-> Bank lines by Payment ID...")
    matches, unmatched_bank = reconcile_payments_to_bank(pays_h1, bank_lines, storefront=STOREFRONT)
    matched = [m for m in matches if m.matched]
    unmatched_pay = [m for m in matches if not m.matched]
    amount_diffs = [m for m in matched if not m.amount_ok]
    print(f"  payments matched: {len(matched)}/{len(matches)}")
    print(f"  amount mismatches: {len(amount_diffs)}")
    print(f"  payments without bank line: {len(unmatched_pay)}")
    print(f"  bank lines without payment (probably pre-2024 or post-H1): {len(unmatched_bank)}")

    # Per-Payment-ID summary CSV
    summary = OUT_DIR / "reconcile-summary.csv"
    with summary.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "payment_id", "statement_date", "statement_id",
            "net_sales", "shipping", "fees", "adjustments",
            "total_settlement", "reserve_amount", "payable_amount",
            "payment_completion_date", "bank_posted_date",
            "bank_amount", "match_status", "note",
        ])
        stmt_by_pid: dict[str, list] = {}
        for s in stmts_h1:
            stmt_by_pid.setdefault(s.payment_id, []).append(s)
        match_by_pid = {m.payment_id: m for m in matches}
        for p in sorted(pays_h1, key=lambda x: (x.payment_initiation_date, x.payment_id)):
            sgrp = stmt_by_pid.get(p.payment_id, [])
            if not sgrp:
                continue
            s0 = sgrp[0]
            net = money_sum(s.net_sales for s in sgrp)
            ship = money_sum(s.shipping for s in sgrp)
            fees = money_sum(s.fees for s in sgrp)
            adj = money_sum(s.adjustments for s in sgrp)
            tot = money_sum(s.total_settlement_amount for s in sgrp)
            res = money_sum(s.reserve_amount for s in sgrp)
            pay_total = money_sum(s.payable_amount for s in sgrp)
            m = match_by_pid.get(p.payment_id)
            status = (
                "OK" if m and m.matched and m.amount_ok else
                "AMOUNT_DIFF" if m and m.matched else
                "NO_BANK_LINE"
            )
            w.writerow([
                p.payment_id, s0.statement_date,
                ",".join(s.statement_id for s in sgrp),
                str(net), str(ship), str(fees), str(adj),
                str(tot), str(res), str(pay_total),
                p.payment_completion_date,
                m.bank_posted_date if m and m.bank_posted_date else "",
                str(m.bank_amount) if m and m.bank_amount is not None else "",
                status,
                m.note if m else "",
            ])
    print(f"\nWrote {summary}")

    # Unmatched bank lines
    unmatched_csv = OUT_DIR / "reconcile-unmatched-bank.csv"
    with unmatched_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["posted_date", "amount", "payout_id", "bank_payment_id", "source_pdf"])
        for b in unmatched_bank:
            w.writerow([b.posted_date, str(b.amount), b.payout_id, b.bank_payment_id, b.source_pdf])
    print(f"Wrote {unmatched_csv}")

    # Aggregate H1 totals
    total_settlement = money_sum(s.total_settlement_amount for s in stmts_h1)
    total_reserve = money_sum(s.reserve_amount for s in stmts_h1)
    total_payable_stmts = money_sum(s.payable_amount for s in stmts_h1)
    total_payment_amounts = money_sum(p.payment_amount for p in pays_h1)
    total_bank_matched = money_sum(
        Decimal(m.bank_amount) for m in matched if m.bank_amount is not None
    )

    # Categorize unmatched items
    early_jan_cutoff = date(2024, 1, 15)
    pre_h1_bank = [b for b in unmatched_bank if b.posted_date < H1_START]
    post_h1_bank = [b for b in unmatched_bank if b.posted_date > H1_END]
    in_h1_bank = [b for b in unmatched_bank if H1_START <= b.posted_date <= H1_END]
    early_jan_pay = [m for m in unmatched_pay
                     if m.payment_completion_date <= early_jan_cutoff]
    other_unmatched_pay = [m for m in unmatched_pay
                           if m.payment_completion_date > early_jan_cutoff]

    summary_txt = OUT_DIR / "reconcile-h1-summary.txt"
    with summary_txt.open("w", encoding="utf-8") as f:
        f.write("H1 2024 LELNU TikTok Reconciliation Summary\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Period: {H1_START} to {H1_END}\n")
        f.write(f"Storefront: {STOREFRONT}\n\n")
        f.write(f"Order rows:                 {len(rows):>12,}\n")
        f.write(f"Statements:                 {len(stmts_h1):>12,}\n")
        f.write(f"Payments:                   {len(pays_h1):>12,}\n\n")
        f.write(f"Total settlement amount:    ${total_settlement:>15,.2f}\n")
        f.write(f"Total reserve (signed):     ${total_reserve:>15,.2f}\n")
        f.write(f"Total payable (statements): ${total_payable_stmts:>15,.2f}\n")
        f.write(f"Total payment amounts:      ${total_payment_amounts:>15,.2f}\n")
        f.write(f"Total matched bank deposits:${total_bank_matched:>15,.2f}\n")
        f.write(f"  (payment vs bank diff:    ${(total_payment_amounts - total_bank_matched):>15,.2f})\n\n")
        f.write("Identity checks (xlsx-internal):\n")
        f.write(f"  identity_2 mismatches:    {len(id2)}\n")
        f.write(f"  identity_1 mismatches:    {len(id1)}\n\n")
        f.write("Bank reconciliation:\n")
        f.write(f"  payments matched:         {len(matched)}/{len(matches)}\n")
        f.write(f"  amount mismatches:        {len(amount_diffs)}\n\n")
        f.write(f"Unmatched payments ({len(unmatched_pay)}):\n")
        f.write(f"  early-Jan (pre-2024-01-16, likely different bank): {len(early_jan_pay)}, "
                f"sum=${sum(m.payment_amount for m in early_jan_pay):,.2f}\n")
        f.write(f"  other (manual review needed):                       {len(other_unmatched_pay)}, "
                f"sum=${sum(m.payment_amount for m in other_unmatched_pay):,.2f}\n\n")
        f.write(f"Unmatched bank lines ({len(unmatched_bank)}):\n")
        f.write(f"  pre-H1 deposits  (statements before Jan):  {len(pre_h1_bank)}, "
                f"sum=${sum(b.amount for b in pre_h1_bank):,.2f}\n")
        f.write(f"  post-H1 deposits (June stmts paying July): {len(post_h1_bank)}, "
                f"sum=${sum(b.amount for b in post_h1_bank):,.2f}\n")
        f.write(f"  in-H1 unmatched  (xlsx export gap):        {len(in_h1_bank)}, "
                f"sum=${sum(b.amount for b in in_h1_bank):,.2f}\n")
    print(f"Wrote {summary_txt}\n")
    print(summary_txt.read_text(encoding="utf-8"))

    overall_ok = (
        not id2 and not id1
        and not unmatched_pay and not amount_diffs
    )
    return 0 if overall_ok else 2


if __name__ == "__main__":
    sys.exit(main())
