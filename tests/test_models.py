from datetime import date
from decimal import Decimal
from tiktok_qbo.models import (
    NormalizedRow, StatementRow, PaymentRow, ReserveRow,
    InvoiceLine, InvoiceBatch,
    CreditMemoLine, CreditMemoBatch,
    JELine, StatementJE,
)

def test_normalized_row_json_roundtrip():
    r = NormalizedRow(
        shop_id="PLELNU", order_id="577xyz", sku_id="SKU1",
        statement_id="S1", payment_id="P1",
        statement_date=date(2024,5,1), order_created_date=date(2024,4,28),
        order_shipment_date=date(2024,4,29), order_delivery_date=date(2024,4,30),
        row_type="Order", classification="sale",
        customer_payment=Decimal("15.99"), customer_refund=Decimal("0.00"),
        gross_sales=Decimal("15.99"), quantity=1, product_name="Widget",
        raw={"Type": "Order"},
    )
    data = r.model_dump(mode="json")
    r2 = NormalizedRow.model_validate(data)
    assert r2 == r

def test_statement_je_requires_six_lines_allowed_to_be_empty_before_build():
    # Empty lines list is allowed at construction; invariant checked by builder.
    je = StatementJE(
        shop_id="PLELNU", payment_id="P1", doc_number="JE-PLELNU-000000000001",
        txn_date=date(2024,5,1), lines=[], statement_ids=["S1"],
        bank_amount=Decimal("100.00"),
    )
    assert je.payment_id == "P1"

def test_invoice_batch_total_is_sum_of_lines():
    batch = InvoiceBatch(
        shop_id="PLELNU", delivery_date=date(2024,4,30),
        doc_number="INV-PLELNU-20240430",
        lines=[
            InvoiceLine(sku_id="A", product_name="A", quantity=1,
                        unit_price=Decimal("10.00"), amount=Decimal("10.00")),
            InvoiceLine(sku_id="B", product_name="B", quantity=2,
                        unit_price=Decimal("5.00"), amount=Decimal("10.00")),
        ],
        total_amount=Decimal("20.00"),
        source_order_ids=["o1","o2"],
    )
    assert batch.total_amount == sum(l.amount for l in batch.lines)
