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
from tiktok_qbo.qbo.items import ItemRefs


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


def _payment_doc_number(payment_id: str) -> str:
    """One Receive Payment per TikTok Payment ID (not per statement) so that
    bundled payments — where TikTok rolls negative-net statements into the
    next positive payout (Known Issue I12) — produce a single positive-total
    RP applied to all invoices + CMs across all bundled statements.
    """
    tail = payment_id[-12:] if len(payment_id) > 12 else payment_id
    return f"PAY-{tail}"  # ≤ 16 chars


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


def _aggregate_lines_by_sku(
    coa: CoaRefs,
    item_refs: ItemRefs | None,
    rows: list[NormalizedRow],
    signed_amount: Decimal,
) -> list[dict]:
    """Group `rows` by SKU, return one SalesItemLineDetail line per SKU.

    `signed_amount` is the row sum we want the lines to total to:
      - invoices pass Σ Net_sales (positive)
      - credit memos pass |Σ Net_sales| (positive, refund magnitude)

    For a credit memo, refund rows have negative Net sales; we flip to
    positive per-line `Amount` so the CM body looks like a normal positive
    SalesItem invoice (QBO infers DR/CR direction from CreditMemo entity).

    Rows without a numeric SKU (TikTok platform-adjustment rows) accumulate
    into one sentinel line using `item_refs.platform_adjustment_id`. If
    `item_refs` is None (legacy callers, or sentinel not yet bootstrapped),
    falls back to a single ItemAccountRef line — preserves the old shape.
    """
    if item_refs is None:
        return [{
            "DetailType": "SalesItemLineDetail",
            "Amount": float(signed_amount),
            "SalesItemLineDetail": {
                "ItemAccountRef": {"value": coa.sales_id},
            },
        }]

    by_sku_qty: dict[str, int] = defaultdict(int)
    by_sku_amt: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    by_sku_name: dict[str, str] = {}
    no_sku_amt = Decimal("0")
    for r in rows:
        net = Decimal(r.raw.get("Net sales", "0") or "0")
        if net == 0:
            continue
        sku = r.sku_id.strip() if r.sku_id else ""
        # Always make per-line Amount positive — the entity type (Invoice vs
        # CreditMemo) determines direction; QBO rejects negative line amounts.
        magnitude = abs(net)
        if not sku.isdigit():
            no_sku_amt += magnitude
            continue
        by_sku_qty[sku] += int(r.quantity or 0)
        by_sku_amt[sku] += magnitude
        if r.product_name and sku not in by_sku_name:
            by_sku_name[sku] = r.product_name

    lines: list[dict] = []
    for sku in sorted(by_sku_amt.keys()):
        item_id = item_refs.sku_to_item_id.get(sku)
        if not item_id:
            # Bootstrap missed this SKU — fall back to the sentinel item.
            # Caller is expected to have run bootstrap_items() on a SKU
            # universe that includes every SKU about to be posted.
            no_sku_amt += by_sku_amt[sku]
            continue
        qty = by_sku_qty[sku] or 1
        amt = to_money(by_sku_amt[sku])
        unit_price = to_money(amt / qty) if qty else amt
        description = (by_sku_name.get(sku) or f"SKU {sku}")[:4000]
        lines.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": float(amt),
            "Description": description,
            "SalesItemLineDetail": {
                "ItemRef": {"value": item_id},
                "Qty": qty,
                "UnitPrice": float(unit_price),
            },
        })

    if no_sku_amt != 0:
        lines.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": float(to_money(no_sku_amt)),
            "Description": "TikTok platform adjustment (no SKU)",
            "SalesItemLineDetail": {
                "ItemRef": {"value": item_refs.platform_adjustment_id},
            },
        })

    # Float arithmetic on QBO round-trip can introduce 0.01 drift. Apply a
    # final reconciling adjustment to the last SKU line if line sum differs
    # from signed_amount by more than one cent.
    line_sum = to_money(sum(Decimal(str(l["Amount"])) for l in lines))
    target = to_money(signed_amount)
    drift = target - line_sum
    if drift != 0 and lines:
        # Add drift to the largest line so per-unit math stays sensible.
        largest = max(range(len(lines)), key=lambda i: lines[i]["Amount"])
        new_amt = to_money(Decimal(str(lines[largest]["Amount"])) + drift)
        lines[largest]["Amount"] = float(new_amt)
        # Recompute UnitPrice if this is a per-SKU line
        detail = lines[largest].get("SalesItemLineDetail", {})
        if "Qty" in detail and detail["Qty"]:
            detail["UnitPrice"] = float(to_money(new_amt / detail["Qty"]))

    if not lines:
        # Degenerate case (e.g. signed_amount != 0 but no rows): fall back to
        # a single sentinel line so the invoice still balances.
        lines = [{
            "DetailType": "SalesItemLineDetail",
            "Amount": float(to_money(signed_amount)),
            "Description": "TikTok platform adjustment (no SKU)",
            "SalesItemLineDetail": {
                "ItemRef": {"value": item_refs.platform_adjustment_id},
            },
        }]

    return lines


def build_invoice_body(
    coa: CoaRefs,
    delivery_date: date,
    statement_id: str,
    rows: list[NormalizedRow],
    net_sales_amount: Decimal,
    item_refs: ItemRefs | None = None,
) -> dict:
    """Build a QBO Invoice payload for one delivery-date group.

    Amount = Σ Net sales of the rows (Net method sales-tax convention).
    When `item_refs` is provided, the invoice has one line per SKU (each
    referencing a Non-Inventory QBO Item via ItemRef + Qty + UnitPrice).
    When `item_refs` is None (legacy / direct unit-tests), falls back to
    the old single-summary-line shape using ItemAccountRef on coa.sales_id.
    """
    doc = _inv_doc_number(statement_id, delivery_date)
    lines = _aggregate_lines_by_sku(coa, item_refs, rows, net_sales_amount)
    return {
        "DocNumber": doc,
        "TxnDate": delivery_date.isoformat(),
        "CustomerRef": {"value": coa.customer_id},
        "PrivateNote": f"TikTok statement {statement_id}; {len(rows)} order rows; "
                       f"delivery date {delivery_date.isoformat()}",
        "Line": lines,
    }


def build_credit_memo_body(
    coa: CoaRefs,
    delivery_date: date,
    statement_id: str,
    refund_rows: list[NormalizedRow],
    cm_amount: Decimal,
    item_refs: ItemRefs | None = None,
) -> dict:
    """Build a QBO CreditMemo payload for one (statement, delivery_date) refund group.

    Amount = |Σ refund-row Net_sales| (positive). The CM line uses the same
    Sales account as invoices: posting a CM with a Sales line DRs Sales and
    CRs A/R, reversing exactly the portion of revenue + receivable that was
    refunded. Combined with invoices, A/R nets to statement.net_sales.

    When `item_refs` is provided, the CM has one line per refunded SKU.
    """
    doc = _cm_doc_number(statement_id, delivery_date)
    lines = _aggregate_lines_by_sku(coa, item_refs, refund_rows, cm_amount)
    return {
        "DocNumber": doc,
        "TxnDate": delivery_date.isoformat(),
        "CustomerRef": {"value": coa.customer_id},
        "PrivateNote": (
            f"TikTok refunds for statement {statement_id}; "
            f"{len(refund_rows)} refund rows; refund delivery date {delivery_date.isoformat()}"
        ),
        "Line": lines,
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
    item_refs: ItemRefs | None = None,
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
        body = build_invoice_body(
            coa, delivery_date, statement.statement_id, group, net_sales, item_refs,
        )
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
    item_refs: ItemRefs | None = None,
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
            coa, delivery_date, statement.statement_id, group, cm_amount, item_refs,
        )
        result = client.post("creditmemo", body)
        new_id = result.get("CreditMemo", {}).get("Id")
        if new_id:
            cm_links.append((new_id, cm_amount))
        stats.cm_created += 1
    return cm_links


def build_receive_payment_body(
    coa: CoaRefs,
    payment: PaymentRow,
    statement_ids: list[str],
    invoice_links: list[tuple[str, Decimal]],
    cm_links: list[tuple[str, Decimal]],
) -> dict:
    """Build a QBO Payment payload that clears A/R for one PAYMENT (which may
    bundle multiple statements) into Clearing.

    Posts: DR Clearing, CR A/R for TotalAmt = Σ invoice_amount − Σ cm_amount
    across ALL bundled statements. All Line.Amount values are POSITIVE;
    QBO infers application direction from LinkedTxn.TxnType (Invoice =
    applied to receivable, CreditMemo = reduces the receipt). Negative
    amounts trigger ValidationFault 2240.
    """
    doc = _payment_doc_number(payment.payment_id)
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
        "TxnDate": payment.payment_completion_date.isoformat(),
        "CustomerRef": {"value": coa.customer_id},
        "TotalAmt": float(total_amt),
        "DepositToAccountRef": {"value": coa.clearing_id},
        "PrivateNote": (
            f"Receive payment for TikTok payout {payment.payment_id}; "
            f"covers {len(statement_ids)} statement(s): {','.join(statement_ids)}; "
            f"applied to {len(invoice_links)} invoice(s) and {len(cm_links)} CM(s)"
        ),
        "Line": lines,
    }


def post_payment_receive_payment(
    client: QboClient,
    coa: CoaRefs,
    payment: PaymentRow,
    statement_ids: list[str],
    invoice_links: list[tuple[str, Decimal]],
    cm_links: list[tuple[str, Decimal]],
    stats: PostStats,
) -> None:
    """Post one Receive Payment per PAYMENT (not per statement) to clear A/R
    into Clearing.

    Idempotent: if a Payment with the payment's DocNumber already exists,
    increments stats.payment_skipped and returns without posting.
    """
    if not invoice_links and not cm_links:
        return  # nothing to receive (e.g. all-zero-net statements bundle)

    doc = _payment_doc_number(payment.payment_id)
    existing = _existing_payment(client, doc)
    if existing:
        stats.payment_skipped += 1
        return
    body = build_receive_payment_body(
        coa, payment, statement_ids, invoice_links, cm_links,
    )
    if Decimal(str(body["TotalAmt"])) == 0:
        # All bundled statements net to zero: no cash receipt to post.
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
    item_refs: ItemRefs | None = None,
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
            # Phase 1: post all invoices and CMs for every statement in the
            # bundle, collecting (id, amount) tuples for the Receive Payment.
            all_invoice_links: list[tuple[str, Decimal]] = []
            all_cm_links: list[tuple[str, Decimal]] = []
            for s in stmt_group:
                by_dd = rows_by_stmt.get(s.statement_id, {})
                all_invoice_links.extend(
                    post_statement_invoices(client, coa, s, by_dd, stats, item_refs)
                )
                refunds_by_dd = refunds_by_stmt.get(s.statement_id, {})
                all_cm_links.extend(
                    post_statement_credit_memos(client, coa, s, refunds_by_dd, stats, item_refs)
                )
            # Phase 2: ONE Receive Payment per Payment ID, applied to all
            # invoices + CMs across all bundled statements. This avoids
            # negative-TotalAmt RPs that would arise from refund-only
            # statements within a multi-stmt bundle (Known Issue I12).
            post_payment_receive_payment(
                client, coa, payment,
                [s.statement_id for s in stmt_group],
                all_invoice_links, all_cm_links, stats,
            )
            # Phase 3: JE clearing leg uses Σ statement.net_sales (which
            # already includes refund-row contributions). See identity 1:
            # net + ship + fees + adj + reserve = payable.
            stmt_net_sales_total = money_sum(s.net_sales for s in stmt_group)
            post_payment_je(client, coa, payment, stmt_group, stmt_net_sales_total, stats)
        except Exception as e:  # noqa: BLE001
            stats.errors.append(f"payment_id={payment_id}: {type(e).__name__}: {e}")

    return stats
