"""Verify QBO JE bodies balance for real H1 data (no API calls)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tiktok_qbo.qbo.coa import CoaRefs
from tiktok_qbo.qbo.post import build_je_body, build_invoice_body
from tiktok_qbo.models import StatementRow, PaymentRow


_REFS = CoaRefs(
    bank_checking_id="100",
    clearing_id="200",
    reserve_id="201",
    sales_id="300",
    shipping_income_id="301",
    adjustments_id="302",
    fees_id="400",
    shipping_expense_id="401",
    customer_id="C1",
)


def _stmt(stmt_id: str, payment_id: str, *,
          net=0, ship=0, fees=0, adj=0, reserve=0, payable=None) -> StatementRow:
    if payable is None:
        payable = net + ship + fees + adj + reserve
    return StatementRow(
        shop_id="PLELNU", statement_id=stmt_id, payment_id=payment_id,
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal(net + ship + fees + adj),
        net_sales=Decimal(net), shipping=Decimal(ship),
        fees=Decimal(fees), adjustments=Decimal(adj),
        reserve_amount=Decimal(reserve), payable_amount=Decimal(payable),
    )


def _pay(pid: str, amount) -> PaymentRow:
    return PaymentRow(
        shop_id="PLELNU", payment_id=pid, payment_amount=Decimal(amount),
        payment_initiation_date=date(2024, 5, 11),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )


def _balance(body):
    dr = sum(Decimal(str(l["Amount"])) for l in body["Line"]
             if l["JournalEntryLineDetail"]["PostingType"] == "Debit")
    cr = sum(Decimal(str(l["Amount"])) for l in body["Line"]
             if l["JournalEntryLineDetail"]["PostingType"] == "Credit")
    return dr, cr


def test_je_balances_with_reserve_withheld():
    s = _stmt("S1", "P1", net=100, ship=10, fees=-15, adj=0, reserve=-5)
    p = _pay("P1", 90)
    body = build_je_body(_REFS, p, [s], net_sales_total=Decimal("100"))
    dr, cr = _balance(body)
    assert dr == cr, f"DR {dr} != CR {cr}"
    assert dr == Decimal("110")


def test_je_balances_with_reserve_released():
    s = _stmt("S1", "P1", net=100, ship=10, fees=-15, adj=0, reserve=7)
    p = _pay("P1", 102)
    body = build_je_body(_REFS, p, [s], net_sales_total=Decimal("100"))
    dr, cr = _balance(body)
    assert dr == cr


def test_je_balances_with_negative_shipping():
    s = _stmt("S1", "P1", net=100, ship=-7, fees=-15, adj=-3, reserve=0)
    p = _pay("P1", 75)
    body = build_je_body(_REFS, p, [s], net_sales_total=Decimal("100"))
    dr, cr = _balance(body)
    assert dr == cr


def test_je_uses_unique_doc_number():
    s = _stmt("S1", "3459076539020317035", net=100, ship=10, fees=-15, adj=0, reserve=-5)
    p = _pay("3459076539020317035", 90)
    body = build_je_body(_REFS, p, [s], net_sales_total=Decimal("100"))
    assert body["DocNumber"] == "JE-LELNU-539020317035"


def test_invoice_body_uses_net_sales():
    body = build_invoice_body(_REFS, date(2024, 5, 1), "STMT1", [], Decimal("123.45"))
    assert body["DocNumber"] == "INV-LELNU-STMT1-240501"
    assert body["TxnDate"] == "2024-05-01"
    assert body["CustomerRef"]["value"] == "C1"
    assert len(body["Line"]) == 1
    assert body["Line"][0]["Amount"] == 123.45
    assert body["Line"][0]["SalesItemLineDetail"]["ItemAccountRef"]["value"] == "300"


@pytest.mark.skipif(
    not Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx").exists(),
    reason="Real xlsx not available",
)
def test_je_balances_for_all_h1_payments():
    """End-to-end: every H1 payment produces a balanced JE."""
    from tiktok_qbo.ingest.lat_xlsx import read_statements, read_payments
    from collections import defaultdict
    q1 = r"C:\Users\zhour\Downloads\1-3-2024.xlsx"
    q2 = r"C:\Users\zhour\Downloads\4-6-2024.xlsx"
    h1_start, h1_end = date(2024,1,1), date(2024,6,30)
    stmts = [s for s in (read_statements(q1, shop_id="PLELNU") + read_statements(q2, shop_id="PLELNU"))
             if h1_start <= s.statement_date <= h1_end]
    pays = [p for p in (read_payments(q1, shop_id="PLELNU") + read_payments(q2, shop_id="PLELNU"))
            if h1_start <= p.payment_initiation_date <= h1_end]
    by_payment = defaultdict(list)
    for s in stmts:
        by_payment[s.payment_id].append(s)
    pay_map = {p.payment_id: p for p in pays}
    failures = []
    for pid, group in by_payment.items():
        if pid not in pay_map: continue
        from tiktok_qbo.money import money_sum
        net_total = money_sum(s.net_sales for s in group)
        body = build_je_body(_REFS, pay_map[pid], group, net_total)
        dr, cr = _balance(body)
        if dr != cr:
            failures.append((pid, dr, cr))
    assert not failures, f"{len(failures)} JEs don't balance: {failures[:3]}"
