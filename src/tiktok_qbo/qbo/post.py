"""Post H1 LELNU data to QBO.

Per Statement (joined to Payment via Payment ID):
  - One Invoice per (Statement ID, Order delivery date), amount = Σ Net sales.
  - One Sales Receipt or Payment per Invoice (auto-clears via clearing).

Per Payment ID:
  - One JournalEntry implementing the bank-deposit JE:
      DR Bank, DR Fees, [DR Reserve if withheld]
      CR TikTok Clearing (= Σ Net sales of statement),
      CR Shipping Income (or DR Shipping Expense if net negative),
      CR TikTok Adjustments (or DR if negative),
      [CR Reserve if released]

Idempotency: every JE uses DocNumber = JE-LELNU-<last 12 of Payment ID>.
Before posting, we query for an existing JE with that DocNumber and skip if found.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from tiktok_qbo.models import (
    NormalizedRow, StatementRow, PaymentRow,
)
from tiktok_qbo.money import money_sum, to_money
from tiktok_qbo.qbo.client import QboClient
from tiktok_qbo.qbo.coa import CoaRefs


@dataclass
class PostStats:
    invoices_created: int = 0
    invoices_skipped: int = 0
    je_created: int = 0
    je_skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def _je_doc_number(payment_id: str) -> str:
    tail = payment_id[-12:] if len(payment_id) > 12 else payment_id
    return f"JE-LELNU-{tail}"


def _inv_doc_number(statement_id: str, delivery_date: date) -> str:
    tail = statement_id[-8:] if len(statement_id) > 8 else statement_id
    return f"INV-LELNU-{tail}-{delivery_date.strftime('%y%m%d')}"


def _existing_je(client: QboClient, doc_number: str) -> dict | None:
    safe = doc_number.replace("'", "\\'")
    res = client.query(f"SELECT * FROM JournalEntry WHERE DocNumber = '{safe}'")
    rows = res.get("QueryResponse", {}).get("JournalEntry", [])
    return rows[0] if rows else None


def _existing_invoice(client: QboClient, doc_number: str) -> dict | None:
    safe = doc_number.replace("'", "\\'")
    res = client.query(f"SELECT * FROM Invoice WHERE DocNumber = '{safe}'")
    rows = res.get("QueryResponse", {}).get("Invoice", [])
    return rows[0] if rows else None


def _line_dr(account_id: str, amount: Decimal, memo: str = "") -> dict:
    return {
        "DetailType": "JournalEntryLineDetail",
        "Amount": float(amount),
        "Description": memo,
        "JournalEntryLineDetail": {
            "PostingType": "Debit",
            "AccountRef": {"value": account_id},
        },
    }


def _line_cr(account_id: str, amount: Decimal, memo: str = "") -> dict:
    return {
        "DetailType": "JournalEntryLineDetail",
        "Amount": float(amount),
        "Description": memo,
        "JournalEntryLineDetail": {
            "PostingType": "Credit",
            "AccountRef": {"value": account_id},
        },
    }


def build_invoice_body(
    coa: CoaRefs,
    delivery_date: date,
    statement_id: str,
    rows: list[NormalizedRow],
    net_sales_amount: Decimal,
) -> dict:
    """Build a QBO Invoice payload for one delivery-date group.

    Amount = Σ Net sales of the rows (Net method sales-tax convention).
    """
    doc = _inv_doc_number(statement_id, delivery_date)
    return {
        "DocNumber": doc,
        "TxnDate": delivery_date.isoformat(),
        "CustomerRef": {"value": coa.customer_id},
        "PrivateNote": f"TikTok statement {statement_id}; {len(rows)} order rows; "
                       f"delivery date {delivery_date.isoformat()}",
        "Line": [
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": float(net_sales_amount),
                "Description": f"TikTok LELNU sales delivered {delivery_date.isoformat()}",
                "SalesItemLineDetail": {
                    "ItemAccountRef": {"value": coa.sales_id},
                },
            }
        ],
    }


def build_je_body(
    coa: CoaRefs,
    payment: PaymentRow,
    statements: list[StatementRow],
    net_sales_total: Decimal,
) -> dict:
    """Build the bank-deposit JE for one Payment ID.

    DR side gets: bank, |fees|, reserve-withheld, shipping-expense (if net neg),
                  adjustments-expense (if neg)
    CR side gets: net_sales (clearing), shipping-income (if net pos),
                  adjustments-income (if pos), reserve-released
    """
    bank = to_money(payment.payment_amount)
    fees = to_money(money_sum(s.fees for s in statements))      # negative
    shipping = to_money(money_sum(s.shipping for s in statements))
    adjustments = to_money(money_sum(s.adjustments for s in statements))
    reserve = to_money(money_sum(s.reserve_amount for s in statements))  # signed

    lines: list[dict] = []

    # DR Bank (always positive)
    lines.append(_line_dr(coa.bank_checking_id, bank,
                          f"TikTok payout {payment.payment_id}"))

    # DR Marketplace Fees (|fees|)
    if fees != 0:
        lines.append(_line_dr(coa.fees_id, abs(fees), "TikTok marketplace fees"))

    # CR TikTok Clearing (= Σ Net sales — clears the invoice receipts)
    if net_sales_total != 0:
        lines.append(_line_cr(coa.clearing_id, net_sales_total,
                              "Clear TikTok invoices for this statement"))

    # Shipping: CR income if positive, DR expense if negative
    if shipping > 0:
        lines.append(_line_cr(coa.shipping_income_id, shipping,
                              "TikTok shipping subsidies + customer-paid shipping"))
    elif shipping < 0:
        lines.append(_line_dr(coa.shipping_expense_id, abs(shipping),
                              "Net shipping expense"))

    # Adjustments: CR income if positive, DR expense if negative
    if adjustments > 0:
        lines.append(_line_cr(coa.adjustments_id, adjustments,
                              "TikTok adjustments (income)"))
    elif adjustments < 0:
        lines.append(_line_dr(coa.adjustments_id, abs(adjustments),
                              "TikTok adjustments (expense)"))

    # Reserve: DR if withheld (reserve negative), CR if released (reserve positive)
    if reserve < 0:
        lines.append(_line_dr(coa.reserve_id, abs(reserve),
                              "TikTok reserve withheld"))
    elif reserve > 0:
        lines.append(_line_cr(coa.reserve_id, reserve,
                              "TikTok reserve released"))

    return {
        "DocNumber": _je_doc_number(payment.payment_id),
        "TxnDate": payment.payment_completion_date.isoformat(),
        "PrivateNote": (
            f"TikTok Payment {payment.payment_id}; "
            f"statements: {','.join(s.statement_id for s in statements)}"
        ),
        "Line": lines,
    }


def post_statement_invoices(
    client: QboClient,
    coa: CoaRefs,
    statement: StatementRow,
    rows_by_delivery_date: dict[date, list[NormalizedRow]],
    stats: PostStats,
) -> Decimal:
    """Post one invoice per delivery-date group within this statement.

    Returns the total Net sales posted (to be used as the JE clearing-leg amount).
    """
    total_net_sales = Decimal("0")
    for delivery_date in sorted(rows_by_delivery_date.keys()):
        group = rows_by_delivery_date[delivery_date]
        net_sales = to_money(money_sum(
            (Decimal(r.raw.get("Net sales", "0") or "0")) for r in group
        ))
        if net_sales == 0:
            # Skip zero-net-sales groups (refund-only days etc.) — handled by JE only
            continue
        total_net_sales += net_sales

        doc = _inv_doc_number(statement.statement_id, delivery_date)
        existing = _existing_invoice(client, doc)
        if existing:
            stats.invoices_skipped += 1
            continue
        body = build_invoice_body(coa, delivery_date, statement.statement_id, group, net_sales)
        client.post("invoice", body)
        stats.invoices_created += 1
    return total_net_sales


def post_payment_je(
    client: QboClient,
    coa: CoaRefs,
    payment: PaymentRow,
    statements: list[StatementRow],
    net_sales_total: Decimal,
    stats: PostStats,
) -> None:
    doc = _je_doc_number(payment.payment_id)
    existing = _existing_je(client, doc)
    if existing:
        stats.je_skipped += 1
        return
    body = build_je_body(coa, payment, statements, net_sales_total)
    client.post("journalentry", body)
    stats.je_created += 1


def post_h1(
    client: QboClient,
    coa: CoaRefs,
    rows: list[NormalizedRow],
    statements: list[StatementRow],
    payments: list[PaymentRow],
) -> PostStats:
    stats = PostStats()

    rows_by_stmt: dict[str, dict[date, list[NormalizedRow]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.classification not in ("sale",):
            continue
        if r.order_delivery_date is None:
            continue
        rows_by_stmt[r.statement_id][r.order_delivery_date].append(r)

    stmts_by_payment: dict[str, list[StatementRow]] = defaultdict(list)
    for s in statements:
        if s.payment_id:
            stmts_by_payment[s.payment_id].append(s)

    pay_by_id = {p.payment_id: p for p in payments}

    for payment_id in sorted(stmts_by_payment.keys()):
        payment = pay_by_id.get(payment_id)
        if payment is None:
            stats.errors.append(f"no Payment row for payment_id={payment_id}; skipping")
            continue
        stmt_group = stmts_by_payment[payment_id]
        try:
            net_sales_total = Decimal("0")
            for s in stmt_group:
                by_dd = rows_by_stmt.get(s.statement_id, {})
                net_sales_total += post_statement_invoices(
                    client, coa, s, by_dd, stats
                )
            post_payment_je(client, coa, payment, stmt_group, net_sales_total, stats)
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"payment_id={payment_id}: {type(e).__name__}: {e}")

    return stats
