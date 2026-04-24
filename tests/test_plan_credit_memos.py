from datetime import date
from decimal import Decimal
from tiktok_qbo.models import NormalizedRow
from tiktok_qbo.plan.credit_memos import group_credit_memos

def mk(order_id, classification, refund, stmt_date, sku="A"):
    return NormalizedRow(
        shop_id="PLELNU", order_id=order_id, sku_id=sku,
        statement_id="S1", payment_id="P1",
        statement_date=stmt_date,
        order_created_date=date(2024,4,1),
        order_shipment_date=date(2024,4,2),
        order_delivery_date=date(2024,4,3),
        row_type="Order", classification=classification,
        customer_payment=Decimal("0"), customer_refund=Decimal(refund),
        gross_sales=Decimal("0"), quantity=1, product_name=f"P-{sku}",
        raw={},
    )

def test_groups_refunds_and_chargebacks_by_statement_date():
    rows = [
        mk("O1", "refund", "9.99", date(2024,5,12)),
        mk("O2", "chargeback", "25.00", date(2024,5,12)),
        mk("O3", "refund", "5.00", date(2024,5,19)),
    ]
    batches = group_credit_memos(rows, shop_id="PLELNU")
    assert len(batches) == 2
    first = batches[0]
    assert first.statement_date == date(2024,5,12)
    assert first.doc_number == "CM-PLELNU-20240512"
    assert first.total_amount == Decimal("34.99")
    assert sorted(first.source_order_ids) == ["O1","O2"]

def test_refund_amount_is_positive_on_the_line():
    rows = [mk("O1","refund","9.99", date(2024,5,12))]
    batches = group_credit_memos(rows, shop_id="PLELNU")
    assert batches[0].lines[0].amount == Decimal("9.99")
