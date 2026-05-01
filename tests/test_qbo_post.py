"""Verify QBO JE bodies balance for real H1 data (no API calls)."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tiktok_qbo.qbo.coa import CoaRefs
from tiktok_qbo.qbo.post import build_je_body, build_invoice_body, post_h1
from tiktok_qbo.models import NormalizedRow, StatementRow, PaymentRow


class RecordingClient:
    """Fake QboClient that records every post() call and answers query() from
    a preloaded existing-entity map. Lets us drive post_h1 end-to-end without
    touching the network and assert on what would have been posted.
    """

    def __init__(self, existing: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.existing = existing or {}  # (entity_capitalized, doc_number) -> dict
        self._counter = 0

    def query(self, sql: str) -> dict:
        m = re.search(r"FROM\s+(\w+)\s+WHERE\s+DocNumber\s*=\s*'([^']*)'", sql)
        if m:
            entity, doc = m.group(1), m.group(2)
            row = self.existing.get((entity, doc))
            if row is not None:
                return {"QueryResponse": {entity: [row]}}
        return {"QueryResponse": {}}

    def post(self, path: str, body: dict) -> dict:
        from tiktok_qbo.qbo.client import _qbo_entity_key
        self.calls.append((path, body))
        self._counter += 1
        entity_key = _qbo_entity_key(path)
        return {entity_key: {**body, "Id": f"REC-{path}-{self._counter}"}}

    def calls_for(self, path: str) -> list[dict]:
        return [body for p, body in self.calls if p == path]


def _je_balance(body: dict) -> tuple[Decimal, Decimal]:
    dr = sum(Decimal(str(L["Amount"])) for L in body["Line"]
             if L["JournalEntryLineDetail"]["PostingType"] == "Debit")
    cr = sum(Decimal(str(L["Amount"])) for L in body["Line"]
             if L["JournalEntryLineDetail"]["PostingType"] == "Credit")
    return dr, cr


def _row(*, statement_id: str, payment_id: str, classification: str,
         net_sales: str, delivery_date: date, customer_refund: str = "0",
         sku: str = "SKU", order_id: str = "O", shop: str = "PLELNU") -> NormalizedRow:
    raw = {"Net sales": net_sales}
    return NormalizedRow(
        shop_id=shop, order_id=order_id, sku_id=sku,
        statement_id=statement_id, payment_id=payment_id,
        statement_date=date(2024, 5, 12),
        order_created_date=date(2024, 4, 28),
        order_shipment_date=date(2024, 4, 29),
        order_delivery_date=delivery_date,
        row_type="Order", classification=classification,  # type: ignore[arg-type]
        customer_payment=Decimal("0"),
        customer_refund=Decimal(customer_refund),
        gross_sales=Decimal("0"), quantity=1,
        product_name="Test Item", raw=raw,
    )


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
    assert body["DocNumber"] == "JE-539020317035"


def test_invoice_body_uses_net_sales():
    body = build_invoice_body(_REFS, date(2024, 5, 1), "STMT1", [], Decimal("123.45"))
    assert body["DocNumber"] == "INV-STMT1-240501"
    assert body["TxnDate"] == "2024-05-01"
    assert body["CustomerRef"]["value"] == "C1"
    assert len(body["Line"]) == 1
    assert body["Line"][0]["Amount"] == 123.45
    assert body["Line"][0]["SalesItemLineDetail"]["ItemAccountRef"]["value"] == "300"


def test_post_h1_emits_credit_memo_for_refund_rows():
    """Refund rows must produce CreditMemos so A/R nets to statement.net_sales.

    Per delivery date with refund rows: one CM with amount = |Σ refund Net_sales|.
    Without CMs, A/R would stay open at sale-row sum (overstated by refund total).
    """
    rows = [
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="110", delivery_date=date(2024, 4, 30), order_id="O-S1"),
        _row(statement_id="S1", payment_id="P1", classification="refund",
             net_sales="-10", delivery_date=date(2024, 4, 12), order_id="O-R1",
             customer_refund="10"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("110"),
        net_sales=Decimal("100"), shipping=Decimal("10"),
        fees=Decimal("-15"), adjustments=Decimal("0"),
        reserve_amount=Decimal("-5"), payable_amount=Decimal("90"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("90"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]

    client = RecordingClient()
    stats = post_h1(client, _REFS, rows, stmts, pays)
    assert stats.errors == [], stats.errors

    cm_calls = client.calls_for("creditmemo")
    assert len(cm_calls) == 1, f"expected 1 credit memo, got {len(cm_calls)}"
    cm = cm_calls[0]
    assert cm["DocNumber"].startswith("CM-"), cm["DocNumber"]
    assert cm["TxnDate"] == "2024-04-12", cm["TxnDate"]
    assert cm["CustomerRef"]["value"] == _REFS.customer_id
    assert len(cm["Line"]) == 1
    line = cm["Line"][0]
    assert Decimal(str(line["Amount"])) == Decimal("10"), line["Amount"]
    assert line["SalesItemLineDetail"]["ItemAccountRef"]["value"] == _REFS.sales_id


def test_post_h1_emits_one_credit_memo_per_refund_delivery_date():
    """Two refund rows with different delivery dates → two CMs."""
    rows = [
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="200", delivery_date=date(2024, 4, 30), order_id="O-S1"),
        _row(statement_id="S1", payment_id="P1", classification="refund",
             net_sales="-7", delivery_date=date(2024, 4, 10), order_id="O-R1",
             customer_refund="7"),
        _row(statement_id="S1", payment_id="P1", classification="refund",
             net_sales="-3", delivery_date=date(2024, 4, 11), order_id="O-R2",
             customer_refund="3"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("200"),
        net_sales=Decimal("190"), shipping=Decimal("10"),
        fees=Decimal("-15"), adjustments=Decimal("0"),
        reserve_amount=Decimal("-5"), payable_amount=Decimal("180"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("180"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]
    client = RecordingClient()
    post_h1(client, _REFS, rows, stmts, pays)
    cms = client.calls_for("creditmemo")
    assert len(cms) == 2, f"expected 2 CMs, got {len(cms)}: docs={[c['DocNumber'] for c in cms]}"
    by_date = {c["TxnDate"]: Decimal(str(c["Line"][0]["Amount"])) for c in cms}
    assert by_date == {"2024-04-10": Decimal("7"), "2024-04-11": Decimal("3")}, by_date


def test_post_h1_skips_credit_memo_with_existing_doc_number():
    """Idempotency: pre-existing CreditMemo with same DocNumber must not be re-posted."""
    rows = [
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="110", delivery_date=date(2024, 4, 30), order_id="O-S1"),
        _row(statement_id="S1", payment_id="P1", classification="refund",
             net_sales="-10", delivery_date=date(2024, 4, 12), order_id="O-R1",
             customer_refund="10"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("110"),
        net_sales=Decimal("100"), shipping=Decimal("10"),
        fees=Decimal("-15"), adjustments=Decimal("0"),
        reserve_amount=Decimal("-5"), payable_amount=Decimal("90"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("90"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]
    # Pre-populate as if the CM already existed in QBO.
    expected_doc = "CM-S1-240412"
    client = RecordingClient(existing={
        ("CreditMemo", expected_doc): {"Id": "999", "DocNumber": expected_doc},
    })
    stats = post_h1(client, _REFS, rows, stmts, pays)
    assert client.calls_for("creditmemo") == [], "CM should be skipped when one exists"
    assert stats.cm_skipped == 1, f"expected cm_skipped=1, got {stats.cm_skipped}"
    assert stats.cm_created == 0


def test_all_doc_numbers_fit_qbo_21_char_limit():
    """QBO enforces DocNumber max length of 21 characters; longer values are
    rejected with a 400 ValidationFault. Must hold for invoices, CMs, payments,
    and JEs across realistic input sizes (long statement IDs / payment IDs).
    """
    long_stmt = "7363814232686872362"  # real H1 LELNU statement_id (19 chars)
    long_pay = "3459076539020317035"   # real H1 LELNU payment_id  (19 chars)
    rows = [
        _row(statement_id=long_stmt, payment_id=long_pay, classification="sale",
             net_sales="100", delivery_date=date(2024, 4, 30), order_id="O1"),
        _row(statement_id=long_stmt, payment_id=long_pay, classification="refund",
             net_sales="-10", delivery_date=date(2024, 4, 12), order_id="O2",
             customer_refund="10"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id=long_stmt, payment_id=long_pay,
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("100"),
        net_sales=Decimal("90"), shipping=Decimal("0"),
        fees=Decimal("0"), adjustments=Decimal("0"),
        reserve_amount=Decimal("0"), payable_amount=Decimal("90"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id=long_pay, payment_amount=Decimal("90"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]
    client = RecordingClient()
    post_h1(client, _REFS, rows, stmts, pays)
    over_limit = []
    for path, body in client.calls:
        doc = body.get("DocNumber", "")
        if len(doc) > 21:
            over_limit.append((path, doc, len(doc)))
    assert not over_limit, (
        f"{len(over_limit)} DocNumber(s) exceed QBO's 21-char limit: {over_limit}"
    )


def test_post_h1_multi_stmt_payment_emits_single_receive_payment():
    """A payment that bundles multiple statements (TikTok rolls negative-net
    days into the next positive payout per Known Issue I12) must produce
    exactly ONE Receive Payment, not one per statement.

    Without this: refund-only statements within a bundle generate
    negative-TotalAmt Payments which QBO rejects (Min:0 per ValidationFault
    2240, see I20).
    """
    # Payment P1 bundles 2 statements:
    #   S_pos: net=+200 (1 invoice $200)
    #   S_neg: net=-50  (1 CM $50, refund-only, no invoice)
    # Per-stmt model: RP for S_pos with TotalAmt=$200; RP for S_neg with TotalAmt=-$50  ← rejected
    # Per-pay model:  RP for P1 with TotalAmt=$150, links 1 invoice + 1 CM
    rows = [
        _row(statement_id="S_pos", payment_id="P1", classification="sale",
             net_sales="200", delivery_date=date(2024, 4, 30), order_id="O-S1"),
        _row(statement_id="S_neg", payment_id="P1", classification="refund",
             net_sales="-50", delivery_date=date(2024, 4, 1), order_id="O-R1",
             customer_refund="50"),
    ]
    stmts = [
        StatementRow(
            shop_id="PLELNU", statement_id="S_pos", payment_id="P1",
            statement_date=date(2024, 5, 12), status="Paid",
            total_settlement_amount=Decimal("200"),
            net_sales=Decimal("200"), shipping=Decimal("0"),
            fees=Decimal("0"), adjustments=Decimal("0"),
            reserve_amount=Decimal("0"), payable_amount=Decimal("200"),
        ),
        StatementRow(
            shop_id="PLELNU", statement_id="S_neg", payment_id="P1",
            statement_date=date(2024, 5, 11), status="Paid",
            total_settlement_amount=Decimal("-50"),
            net_sales=Decimal("-50"), shipping=Decimal("0"),
            fees=Decimal("0"), adjustments=Decimal("0"),
            reserve_amount=Decimal("0"), payable_amount=Decimal("-50"),
        ),
    ]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("150"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]
    client = RecordingClient()
    stats = post_h1(client, _REFS, rows, stmts, pays)
    assert stats.errors == [], stats.errors

    payments = client.calls_for("payment")
    assert len(payments) == 1, (
        f"expected exactly 1 Receive Payment per Payment ID (not 1 per stmt); "
        f"got {len(payments)}: docs={[p['DocNumber'] for p in payments]}"
    )
    p = payments[0]
    assert Decimal(str(p["TotalAmt"])) == Decimal("150"), (
        f"single RP must equal Σ statement.net_sales = $150; got {p['TotalAmt']}"
    )
    # Must include both the invoice from S_pos and the CM from S_neg
    invoice_lines = [L for L in p["Line"] if L["LinkedTxn"][0]["TxnType"] == "Invoice"]
    cm_lines = [L for L in p["Line"] if L["LinkedTxn"][0]["TxnType"] == "CreditMemo"]
    assert len(invoice_lines) == 1, f"missing invoice link from positive stmt"
    assert len(cm_lines) == 1, f"missing CM link from refund-only stmt"


def test_post_h1_receive_payment_doc_number_uses_payment_id_tail():
    """DocNumber must encode payment_id (so 1 RP per payout, idempotency by payment)."""
    long_pay = "3459043172707176811"  # real H1 payment_id
    rows = [
        _row(statement_id="S1", payment_id=long_pay, classification="sale",
             net_sales="100", delivery_date=date(2024, 4, 30), order_id="O1"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id=long_pay,
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("100"),
        net_sales=Decimal("100"), shipping=Decimal("0"),
        fees=Decimal("0"), adjustments=Decimal("0"),
        reserve_amount=Decimal("0"), payable_amount=Decimal("100"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id=long_pay, payment_amount=Decimal("100"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]
    client = RecordingClient()
    post_h1(client, _REFS, rows, stmts, pays)
    p = client.calls_for("payment")[0]
    # Must contain a tail of the payment_id (not the statement_id)
    assert long_pay[-10:] in p["DocNumber"], (
        f"DocNumber {p['DocNumber']} must include payment_id tail {long_pay[-10:]}"
    )
    assert "S1" not in p["DocNumber"], (
        f"DocNumber {p['DocNumber']} should NOT include statement_id (1 RP per payment, not per stmt)"
    )
    assert len(p["DocNumber"]) <= 21, f"DocNumber {p['DocNumber']} exceeds 21 chars"


def test_post_h1_emits_receive_payment_per_statement():
    """One Receive Payment per statement, depositing statement.net_sales into Clearing.

    Without this, A/R stays open at sale-row sum and Clearing accumulates a
    negative balance — the trial balance won't show A/R aging at $0.
    """
    rows = [
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="110", delivery_date=date(2024, 4, 30), order_id="O-S1"),
        _row(statement_id="S1", payment_id="P1", classification="refund",
             net_sales="-10", delivery_date=date(2024, 4, 12), order_id="O-R1",
             customer_refund="10"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("110"),
        net_sales=Decimal("100"), shipping=Decimal("10"),
        fees=Decimal("-15"), adjustments=Decimal("0"),
        reserve_amount=Decimal("-5"), payable_amount=Decimal("90"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("90"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]

    client = RecordingClient()
    stats = post_h1(client, _REFS, rows, stmts, pays)
    assert stats.errors == [], stats.errors

    payments = client.calls_for("payment")
    assert len(payments) == 1, f"expected 1 Payment, got {len(payments)}"
    p = payments[0]
    assert p["DocNumber"].startswith("PAY-"), p["DocNumber"]
    assert p["CustomerRef"]["value"] == _REFS.customer_id
    assert Decimal(str(p["TotalAmt"])) == Decimal("100"), (
        f"Payment.TotalAmt must equal statement.net_sales, got {p['TotalAmt']}"
    )
    assert p["DepositToAccountRef"]["value"] == _REFS.clearing_id


def test_post_h1_receive_payment_links_all_invoices_and_credit_memos():
    """Payment.Line must reference every invoice (positive) and CM (negative)
    posted for that statement, so QBO marks them paid/applied."""
    rows = [
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="60", delivery_date=date(2024, 4, 30), order_id="O-S1"),
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="50", delivery_date=date(2024, 5, 1), order_id="O-S2"),
        _row(statement_id="S1", payment_id="P1", classification="refund",
             net_sales="-10", delivery_date=date(2024, 4, 12), order_id="O-R1",
             customer_refund="10"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("110"),
        net_sales=Decimal("100"), shipping=Decimal("10"),
        fees=Decimal("-15"), adjustments=Decimal("0"),
        reserve_amount=Decimal("-5"), payable_amount=Decimal("90"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("90"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]
    client = RecordingClient()
    post_h1(client, _REFS, rows, stmts, pays)

    payments = client.calls_for("payment")
    assert len(payments) == 1
    p = payments[0]

    # 2 invoice links + 1 CM link, all with POSITIVE Line.Amount values.
    # QBO infers application direction from LinkedTxn.TxnType; supplying a
    # negative Line.Amount triggers ValidationFault code 2240 ("Number out
    # of range. Min:0 Max:999,999,999"). See I20 in known-issues.
    invoice_lines = [L for L in p["Line"]
                     if L["LinkedTxn"][0]["TxnType"] == "Invoice"]
    cm_lines = [L for L in p["Line"]
                if L["LinkedTxn"][0]["TxnType"] == "CreditMemo"]
    assert len(invoice_lines) == 2, f"expected 2 invoice links, got {len(invoice_lines)}"
    assert len(cm_lines) == 1, f"expected 1 CM link, got {len(cm_lines)}"

    for L in p["Line"]:
        assert Decimal(str(L["Amount"])) >= 0, (
            f"all Payment.Line.Amount values must be non-negative; got {L['Amount']} "
            f"on {L['LinkedTxn'][0]['TxnType']} link"
        )

    invoice_amounts = sorted(Decimal(str(L["Amount"])) for L in invoice_lines)
    assert invoice_amounts == [Decimal("50"), Decimal("60")]
    assert Decimal(str(cm_lines[0]["Amount"])) == Decimal("10")

    # TotalAmt is the cash actually received: invoices applied minus CMs applied.
    inv_sum = sum(Decimal(str(L["Amount"])) for L in invoice_lines)
    cm_sum = sum(Decimal(str(L["Amount"])) for L in cm_lines)
    assert Decimal(str(p["TotalAmt"])) == inv_sum - cm_sum == Decimal("100")


def test_post_h1_skips_receive_payment_with_existing_doc_number():
    """Idempotency: pre-existing Payment with same DocNumber must not be re-posted."""
    rows = [
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="100", delivery_date=date(2024, 4, 30), order_id="O-S1"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("100"),
        net_sales=Decimal("100"), shipping=Decimal("0"),
        fees=Decimal("0"), adjustments=Decimal("0"),
        reserve_amount=Decimal("0"), payable_amount=Decimal("100"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("100"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]
    expected_doc = "PAY-P1"  # per-payment, not per-stmt
    client = RecordingClient(existing={
        ("Payment", expected_doc): {"Id": "999", "DocNumber": expected_doc},
    })
    stats = post_h1(client, _REFS, rows, stmts, pays)
    assert client.calls_for("payment") == [], "Payment should be skipped when one exists"
    assert stats.payment_skipped == 1, f"expected payment_skipped=1, got {stats.payment_skipped}"
    assert stats.payment_created == 0


def test_post_h1_je_balances_when_statement_has_refund_rows():
    """Regression: JE clearing leg must use statement.net_sales, not Σ sale-row Net_sales.

    Without this, statements that contain refund rows produce JEs with
    DR ≠ CR (off by Σ refund-row Net_sales), which QBO rejects at post time.
    """
    # Statement S1 has net_sales = 100 = (110 sale rows) + (-10 refund rows).
    # If JE.CR_Clearing uses sale-row sum (110), JE imbalance = 10.
    # If JE.CR_Clearing uses statement.net_sales (100), JE balances.
    rows = [
        _row(statement_id="S1", payment_id="P1", classification="sale",
             net_sales="110", delivery_date=date(2024, 4, 30), order_id="O-S1"),
        _row(statement_id="S1", payment_id="P1", classification="refund",
             net_sales="-10", delivery_date=date(2024, 4, 12), order_id="O-R1",
             customer_refund="10"),
    ]
    stmts = [StatementRow(
        shop_id="PLELNU", statement_id="S1", payment_id="P1",
        statement_date=date(2024, 5, 12), status="Paid",
        total_settlement_amount=Decimal("110"),
        net_sales=Decimal("100"), shipping=Decimal("10"),
        fees=Decimal("-15"), adjustments=Decimal("0"),
        reserve_amount=Decimal("-5"), payable_amount=Decimal("90"),
    )]
    pays = [PaymentRow(
        shop_id="PLELNU", payment_id="P1", payment_amount=Decimal("90"),
        payment_initiation_date=date(2024, 5, 12),
        payment_completion_date=date(2024, 5, 12),
        bank_account_masked="********9247", status="Paid",
    )]

    client = RecordingClient()
    stats = post_h1(client, _REFS, rows, stmts, pays)

    assert stats.errors == [], f"unexpected errors: {stats.errors}"
    je_calls = client.calls_for("journalentry")
    assert len(je_calls) == 1, f"expected 1 JE, got {len(je_calls)}"
    je = je_calls[0]

    dr, cr = _je_balance(je)
    assert dr == cr, (
        f"JE does not balance: DR={dr} CR={cr} diff={dr-cr}. "
        f"Lines: {je['Line']}"
    )

    clearing_lines = [L for L in je["Line"]
                      if L["JournalEntryLineDetail"]["PostingType"] == "Credit"
                      and L["JournalEntryLineDetail"]["AccountRef"]["value"] == _REFS.clearing_id]
    assert len(clearing_lines) == 1, "expected exactly one CR Clearing line"
    assert Decimal(str(clearing_lines[0]["Amount"])) == Decimal("100"), (
        f"CR Clearing must equal statement.net_sales (100), got "
        f"{clearing_lines[0]['Amount']}"
    )


@pytest.mark.skipif(
    not Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx").exists(),
    reason="Real xlsx not available",
)
def test_je_balances_for_all_h1_payments():
    """End-to-end: every H1 payment produces a balanced JE (builder math)."""
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


@pytest.mark.skipif(
    not Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx").exists(),
    reason="Real xlsx not available",
)
def test_post_h1_production_path_every_je_balances_for_real_h1():
    """Run the ACTUAL post_h1 code path against real H1 xlsx data and assert
    every JE that gets emitted balances DR=CR.

    The original test_je_balances_for_all_h1_payments above exercises the
    builder in isolation with statement-summed net_sales — it never hit the
    sale-row-vs-statement.net_sales bug because it bypassed post_h1.
    This test fills the gap.
    """
    from tiktok_qbo.ingest.lat_xlsx import read_order_details, read_statements, read_payments
    q1 = Path(r"C:\Users\zhour\Downloads\1-3-2024.xlsx")
    q2 = Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx")
    h1_start, h1_end = date(2024, 1, 1), date(2024, 6, 30)

    rows = read_order_details(q1, shop_id="PLELNU") + read_order_details(q2, shop_id="PLELNU")
    rows = [r for r in rows if r.statement_date and h1_start <= r.statement_date <= h1_end]
    stmts = [s for s in (read_statements(q1, shop_id="PLELNU") + read_statements(q2, shop_id="PLELNU"))
             if h1_start <= s.statement_date <= h1_end]
    pays = [p for p in (read_payments(q1, shop_id="PLELNU") + read_payments(q2, shop_id="PLELNU"))
            if h1_start <= p.payment_initiation_date <= h1_end]

    # Drop statements whose payment falls outside H1 (boundary case: statements
    # near 2024-06-30 sometimes pay out in early July → payment_initiation_date
    # outside H1). post_h1 would skip them with an error; the test asserts the
    # behavior on the fully-paired subset, which is what gets posted in practice.
    pay_ids = {p.payment_id for p in pays}
    stmts = [s for s in stmts if s.payment_id in pay_ids]
    stmt_ids_kept = {s.statement_id for s in stmts}
    rows = [r for r in rows if r.statement_id in stmt_ids_kept]

    client = RecordingClient()
    stats = post_h1(client, _REFS, rows, stmts, pays)

    assert stats.errors == [], f"unexpected errors: {stats.errors[:5]}"

    je_calls = client.calls_for("journalentry")
    assert len(je_calls) >= 100, (
        f"expected JEs for ~176 payments, got {len(je_calls)}"
    )
    failures = []
    for je in je_calls:
        dr, cr = _balance(je)
        if dr != cr:
            failures.append((je["DocNumber"], dr, cr))
    assert not failures, (
        f"{len(failures)} of {len(je_calls)} H1 JEs imbalance via post_h1; "
        f"first 3: {failures[:3]}"
    )


@pytest.mark.skipif(
    not Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx").exists(),
    reason="Real xlsx not available",
)
def test_post_h1_no_payment_has_negative_total_amount_in_h1():
    """Real-H1 check: every Receive Payment posted must have TotalAmt >= 0.

    Negative TotalAmt triggers QBO ValidationFault 2240. The 1 multi-stmt
    H1 payment (3459043172707176811) bundles 2 refund-only statements; under
    the per-statement RP model, those would emit negative-TotalAmt RPs.
    Per-payment RPs eliminate the issue.
    """
    from tiktok_qbo.ingest.lat_xlsx import read_order_details, read_statements, read_payments
    q1 = Path(r"C:\Users\zhour\Downloads\1-3-2024.xlsx")
    q2 = Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx")
    h1_start, h1_end = date(2024, 1, 1), date(2024, 6, 30)
    rows = read_order_details(q1, shop_id="PLELNU") + read_order_details(q2, shop_id="PLELNU")
    rows = [r for r in rows if r.statement_date and h1_start <= r.statement_date <= h1_end]
    stmts = [s for s in (read_statements(q1, shop_id="PLELNU") + read_statements(q2, shop_id="PLELNU"))
             if h1_start <= s.statement_date <= h1_end]
    pays = [p for p in (read_payments(q1, shop_id="PLELNU") + read_payments(q2, shop_id="PLELNU"))
            if h1_start <= p.payment_initiation_date <= h1_end]
    pay_ids = {p.payment_id for p in pays}
    stmts = [s for s in stmts if s.payment_id in pay_ids]
    stmt_ids_kept = {s.statement_id for s in stmts}
    rows = [r for r in rows if r.statement_id in stmt_ids_kept]

    client = RecordingClient()
    post_h1(client, _REFS, rows, stmts, pays)

    bad = [(b["DocNumber"], b["TotalAmt"]) for p, b in client.calls
           if p == "payment" and Decimal(str(b["TotalAmt"])) < 0]
    assert not bad, f"{len(bad)} Payment(s) with negative TotalAmt would be rejected: {bad}"


@pytest.mark.skipif(
    not Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx").exists(),
    reason="Real xlsx not available",
)
def test_post_h1_trial_balance_nets_to_zero_per_statement():
    """For every statement, the per-statement contribution to the trial balance
    must net to zero across all entities posted (Invoice + CM + Payment + JE).

    Each entity's effect on each account is computed offline (we don't actually
    talk to QBO; we model what QBO would do given the bodies we generate).
    """
    from tiktok_qbo.ingest.lat_xlsx import read_order_details, read_statements, read_payments
    from collections import defaultdict
    q1 = Path(r"C:\Users\zhour\Downloads\1-3-2024.xlsx")
    q2 = Path(r"C:\Users\zhour\Downloads\4-6-2024.xlsx")
    h1_start, h1_end = date(2024, 1, 1), date(2024, 6, 30)

    rows = read_order_details(q1, shop_id="PLELNU") + read_order_details(q2, shop_id="PLELNU")
    rows = [r for r in rows if r.statement_date and h1_start <= r.statement_date <= h1_end]
    stmts = [s for s in (read_statements(q1, shop_id="PLELNU") + read_statements(q2, shop_id="PLELNU"))
             if h1_start <= s.statement_date <= h1_end]
    pays = [p for p in (read_payments(q1, shop_id="PLELNU") + read_payments(q2, shop_id="PLELNU"))
            if h1_start <= p.payment_initiation_date <= h1_end]

    # Drop statements whose payment falls outside H1 (boundary case: statements
    # near 2024-06-30 sometimes pay out in early July → payment_initiation_date
    # outside H1). post_h1 would skip them with an error; the test asserts the
    # behavior on the fully-paired subset, which is what gets posted in practice.
    pay_ids = {p.payment_id for p in pays}
    stmts = [s for s in stmts if s.payment_id in pay_ids]
    stmt_ids_kept = {s.statement_id for s in stmts}
    rows = [r for r in rows if r.statement_id in stmt_ids_kept]

    client = RecordingClient()
    stats = post_h1(client, _REFS, rows, stmts, pays)
    assert stats.errors == [], stats.errors

    # Aggregate signed account deltas across all entity bodies posted.
    # A/R is implicit (counter-account for Invoice/CM/Payment); we tally it manually.
    AR = "AR"
    deltas: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for path, body in client.calls:
        if path == "invoice":
            amount = Decimal(str(body["Line"][0]["Amount"]))
            sales_acct = body["Line"][0]["SalesItemLineDetail"]["ItemAccountRef"]["value"]
            deltas[AR] += amount                  # DR A/R
            deltas[sales_acct] -= amount          # CR Sales
        elif path == "creditmemo":
            amount = Decimal(str(body["Line"][0]["Amount"]))
            sales_acct = body["Line"][0]["SalesItemLineDetail"]["ItemAccountRef"]["value"]
            deltas[AR] -= amount                  # CR A/R
            deltas[sales_acct] += amount          # DR Sales
        elif path == "payment":
            total = Decimal(str(body["TotalAmt"]))
            deposit = body["DepositToAccountRef"]["value"]
            deltas[deposit] += total              # DR Clearing
            deltas[AR] -= total                   # CR A/R (net)
        elif path == "journalentry":
            for L in body["Line"]:
                amt = Decimal(str(L["Amount"]))
                acct = L["JournalEntryLineDetail"]["AccountRef"]["value"]
                if L["JournalEntryLineDetail"]["PostingType"] == "Debit":
                    deltas[acct] += amt
                else:
                    deltas[acct] -= amt

    total = sum(deltas.values())
    assert total == 0, (
        f"trial balance does not net to zero: total delta = {total}; "
        f"non-zero accounts: { {k: v for k, v in deltas.items() if v != 0} }"
    )

    # Specific assertions matching the user's verification targets:
    assert deltas[_REFS.clearing_id] == 0, (
        f"Clearing account residue = {deltas[_REFS.clearing_id]} (must be 0)"
    )
    assert deltas[AR] == 0, (
        f"A/R residue = {deltas[AR]} (must be 0)"
    )
    # Sales account should equal the H1 statement-level total (negative = CR).
    from tiktok_qbo.money import money_sum
    expected_sales = -money_sum(s.net_sales for s in stmts)
    assert deltas[_REFS.sales_id] == expected_sales, (
        f"Sales delta = {deltas[_REFS.sales_id]}, expected {expected_sales} "
        f"(should equal -Σ statement.net_sales)"
    )
