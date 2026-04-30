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
    cm_created: int = 0
    cm_skipped: int = 0
    payment_created: int = 0
    payment_skipped: int = 0
    je_created: int = 0
    je_skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# DocNumber format constraint: QBO enforces a 21-char max length on
# DocNumber across all entity types.  We omit the storefront from the
# DocNumber (it's already in the customer name, account names, and
# PrivateNote on every entity) and use last-N tails of statement_id /
# payment_id for uniqueness.  See `test_all_doc_numbers_fit_qbo_21_char_limit`.

def _je_doc_number(payment_id: str) -> str:
    tail = payment_id[-12:] if len(payment_id) > 12 else payment_id
    return f"JE-{tail}"  # ≤ 15 chars


def _inv_doc_number(statement_id: str, delivery_date: date) -> str:
    tail = statement_id[-8:] if len(statement_id) > 8 else statement_id
    return f"INV-{tail}-{delivery_date.strftime('%y%m%d')}"  # ≤ 19 chars


def _cm_doc_number(statement_id: str, delivery_date: date) -> str:
    tail = statement_id[-8:] if len(statement_id) > 8 else statement_id
    return f"CM-{tail}-{delivery_date.strftime('%y%m%d')}"  # ≤ 18 chars


def _existing_credit_memo(client: QboClient, doc_number: str) -> dict | None:
    safe = doc_number.replace("'", "\\'")
    res = client.query(f"SELECT * FROM CreditMemo WHERE DocNumber = '{safe}'")
    rows = res.get("QueryResponse", {}).get("CreditMemo", [])
    return rows[0] if rows else None


def _payment_doc_number(statement_id: str) -> str:
    tail = statement_id[-8:] if len(statement_id) > 8 else statement_id
    return f"PAY-{tail}"  # ≤ 12 chars


def _existing_payment(client: QboClient, doc_number: str) -> dict | None:
    safe = doc_number.replace("'", "\\'")
    res = client.query(f"SELECT * FROM Payment WHERE DocNumber = '{safe}'")
    rows = res.get("QueryResponse", {}).get("Payment", [])
    return rows[0] if rows else None


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


def build_credit_memo_body(
    coa: CoaRefs,
    delivery_date: date,
    statement_id: str,
    refund_rows: list[NormalizedRow],
    cm_amount: Decimal,
) -> dict:
    """Build a QBO CreditMemo payload for one (statement, delivery_date) refund group.

    Amount = |Σ refund-row Net_sales| (positive). The CM line uses the same
    Sales account as invoices: posting a CM with a Sales line DRs Sales and
    CRs A/R, reversing exactly the portion of revenue + receivable that was
    refunded. Combined with invoices, A/R nets to statement.net_sales.
    """
    doc = _cm_doc_number(statement_id, delivery_date)
    return {
        "DocNumber": doc,
        "TxnDate": delivery_date.isoformat(),
        "CustomerRef": {"value": coa.customer_id},
        "PrivateNote": (
            f"TikTok refunds for statement {statement_id}; "
            f"{len(refund_rows)} refund rows; refund delivery date {delivery_date.isoformat()}"
        ),
        "Line": [
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": float(cm_amount),
                "Description": f"TikTok LELNU refunds for delivery {delivery_date.isoformat()}",
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
) -> list[tuple[str, Decimal]]:
    """Post one invoice per delivery-date group within this statement.

    Returns a list of (invoice_id, amount) tuples for both newly-created and
    pre-existing invoices, so the Receive Payment step can link to them.
    """
    invoice_links: list[tuple[str, Decimal]] = []
    for delivery_date in sorted(rows_by_delivery_date.keys()):
        group = rows_by_delivery_date[delivery_date]
        net_sales = to_money(money_sum(
            (Decimal(r.raw.get("Net sales", "0") or "0")) for r in group
        ))
        if net_sales == 0:
            # Skip zero-net-sales groups (refund-only days etc.) — handled by JE only
            continue

        doc = _inv_doc_number(statement.statement_id, delivery_date)
        existing = _existing_invoice(client, doc)
        if existing:
            invoice_links.append((existing["Id"], net_sales))
            stats.invoices_skipped += 1
            continue
        body = build_invoice_body(coa, delivery_date, statement.statement_id, group, net_sales)
        result = client.post("invoice", body)
        new_id = result.get("Invoice", {}).get("Id")
        if new_id:
            invoice_links.append((new_id, net_sales))
        stats.invoices_created += 1
    return invoice_links


def post_statement_credit_memos(
    client: QboClient,
    coa: CoaRefs,
    statement: StatementRow,
    refund_rows_by_delivery_date: dict[date, list[NormalizedRow]],
    stats: PostStats,
) -> list[tuple[str, Decimal]]:
    """Post one CreditMemo per refund-delivery-date group within this statement.

    Returns a list of (cm_id, amount) tuples for both newly-created and
    pre-existing CMs, so the Receive Payment step can link to them.
    """
    cm_links: list[tuple[str, Decimal]] = []
    for delivery_date in sorted(refund_rows_by_delivery_date.keys()):
        group = refund_rows_by_delivery_date[delivery_date]
        # Refund rows have negative Net_sales; CM amount is the absolute value
        # of the contribution to statement.net_sales.
        refund_net = money_sum(
            Decimal(r.raw.get("Net sales", "0") or "0") for r in group
        )
        cm_amount = to_money(abs(refund_net))
        if cm_amount == 0:
            continue

        doc = _cm_doc_number(statement.statement_id, delivery_date)
        existing = _existing_credit_memo(client, doc)
        if existing:
            cm_links.append((existing["Id"], cm_amount))
            stats.cm_skipped += 1
            continue
        body = build_credit_memo_body(
            coa, delivery_date, statement.statement_id, group, cm_amount
        )
        result = client.post("creditmemo", body)
        new_id = result.get("CreditMemo", {}).get("Id")
        if new_id:
            cm_links.append((new_id, cm_amount))
        stats.cm_created += 1
    return cm_links


def build_receive_payment_body(
    coa: CoaRefs,
    statement: StatementRow,
    invoice_links: list[tuple[str, Decimal]],
    cm_links: list[tuple[str, Decimal]],
) -> dict:
    """Build a QBO Payment payload that clears A/R for one statement into Clearing.

    Posts: DR Clearing, CR A/R for TotalAmt = Σ invoice_amount − Σ cm_amount.
    All Line.Amount values are POSITIVE; QBO infers the application direction
    from LinkedTxn.TxnType (Invoice = applied to receivable, CreditMemo =
    reduces the receipt). Negative amounts trigger ValidationFault 2240.
    """
    doc = _payment_doc_number(statement.statement_id)
    invoice_total = money_sum(a for _, a in invoice_links)
    cm_total = money_sum(a for _, a in cm_links)
    total_amt = to_money(invoice_total - cm_total)
    lines: list[dict] = []
    for inv_id, amt in invoice_links:
        lines.append({
            "Amount": float(amt),
            "LinkedTxn": [{"TxnId": inv_id, "TxnType": "Invoice"}],
        })
    for cm_id, amt in cm_links:
        lines.append({
            "Amount": float(amt),
            "LinkedTxn": [{"TxnId": cm_id, "TxnType": "CreditMemo"}],
        })
    return {
        "DocNumber": doc,
        "TxnDate": statement.statement_date.isoformat(),
        "CustomerRef": {"value": coa.customer_id},
        "TotalAmt": float(total_amt),
        "DepositToAccountRef": {"value": coa.clearing_id},
        "PrivateNote": (
            f"Receive payment for TikTok statement {statement.statement_id}; "
            f"applied to {len(invoice_links)} invoice(s) and {len(cm_links)} CM(s); "
            f"net = statement.net_sales"
        ),
        "Line": lines,
    }


def post_statement_receive_payment(
    client: QboClient,
    coa: CoaRefs,
    statement: StatementRow,
    invoice_links: list[tuple[str, Decimal]],
    cm_links: list[tuple[str, Decimal]],
    stats: PostStats,
) -> None:
    """Post one Receive Payment per statement to clear A/R into Clearing.

    Idempotent: if a Payment with the statement's DocNumber already exists,
    increments stats.payment_skipped and returns without posting.
    """
    if not invoice_links and not cm_links:
        return  # nothing to receive (e.g. zero-net-sales day)

    doc = _payment_doc_number(statement.statement_id)
    existing = _existing_payment(client, doc)
    if existing:
        stats.payment_skipped += 1
        return
    body = build_receive_payment_body(coa, statement, invoice_links, cm_links)
    if Decimal(str(body["TotalAmt"])) == 0:
        # Refund-only statement: net is zero, no cash receipt to post.
        # CMs and invoices remain at zero balance.
        return
    client.post("payment", body)
    stats.payment_created += 1


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

    # Bucket rows by sign of Net_sales (rather than classification): any row
    # with positive Net_sales goes to invoices, any with negative Net_sales goes
    # to credit memos. This catches:
    #   - 'adjustment' rows with non-zero Net_sales (TikTok-side adjustments)
    #   - any sale row with downward adjustment (rare but possible)
    # and is robust to future classification changes.
    # Fall back to statement_date when order_delivery_date is null (some rows
    # — typically replacement-order or platform-fee adjustments — lack one).
    rows_by_stmt: dict[str, dict[date, list[NormalizedRow]]] = defaultdict(lambda: defaultdict(list))
    refunds_by_stmt: dict[str, dict[date, list[NormalizedRow]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.statement_date is None:
            continue
        net_sales = Decimal(r.raw.get("Net sales", "0") or "0")
        if net_sales == 0:
            continue
        bucket_date = r.order_delivery_date or r.statement_date
        if net_sales > 0:
            rows_by_stmt[r.statement_id][bucket_date].append(r)
        else:
            refunds_by_stmt[r.statement_id][bucket_date].append(r)

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
            for s in stmt_group:
                by_dd = rows_by_stmt.get(s.statement_id, {})
                invoice_links = post_statement_invoices(client, coa, s, by_dd, stats)
                refunds_by_dd = refunds_by_stmt.get(s.statement_id, {})
                cm_links = post_statement_credit_memos(client, coa, s, refunds_by_dd, stats)
                # Clear A/R into Clearing for this statement's net.
                post_statement_receive_payment(
                    client, coa, s, invoice_links, cm_links, stats,
                )
            # JE clearing leg uses statement.net_sales (which already includes
            # refund-row contributions), not Σ sale-row Net_sales — the latter
            # excludes refund rows and breaks JE balance whenever the statement
            # contains refunds. See identity 1: net + ship + fees + adj + reserve = payable.
            stmt_net_sales_total = money_sum(s.net_sales for s in stmt_group)
            post_payment_je(client, coa, payment, stmt_group, stmt_net_sales_total, stats)
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"payment_id={payment_id}: {type(e).__name__}: {e}")

    return stats
