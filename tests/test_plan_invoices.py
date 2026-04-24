from datetime import date
from decimal import Decimal
from tiktok_qbo.models import NormalizedRow
from tiktok_qbo.plan.invoices import group_invoices

def mk_sale(order_id, sku, qty, amount, delivery):
    return NormalizedRow(
        shop_id="PLELNU", order_id=order_id, sku_id=sku,
        statement_id="S1", payment_id="P1",
        statement_date=date(2024,5,12),
        order_created_date=date(2024,4,28),
        order_shipment_date=date(2024,4,29),
        order_delivery_date=delivery,
        row_type="Order", classification="sale",
        customer_payment=Decimal(amount), customer_refund=Decimal("0"),
        gross_sales=Decimal(amount), quantity=qty, product_name=f"P-{sku}",
        raw={},
    )

def test_groups_sales_by_delivery_date():
    rows = [
        mk_sale("O1","A",1,"10.00", date(2024,4,30)),
        mk_sale("O2","B",2,"20.00", date(2024,4,30)),
        mk_sale("O3","A",1,"15.00", date(2024,5,1)),
    ]
    batches = group_invoices(rows, shop_id="PLELNU")
    assert len(batches) == 2
    b1, b2 = batches
    assert b1.delivery_date == date(2024,4,30)
    assert b1.total_amount == Decimal("30.00")
    assert b1.doc_number == "INV-PLELNU-20240430"
    assert sorted(b1.source_order_ids) == ["O1","O2"]
    assert b2.delivery_date == date(2024,5,1)
    assert b2.total_amount == Decimal("15.00")

def test_rows_without_delivery_date_are_deferred(tmp_path):
    rows = [
        mk_sale("O1","A",1,"10.00", date(2024,4,30)),
        mk_sale("ONoDate","A",1,"99.00", None),
    ]
    batches = group_invoices(rows, shop_id="PLELNU")
    assert len(batches) == 1
    assert "ONoDate" not in batches[0].source_order_ids

def test_only_sale_classification_included():
    rows = [
        mk_sale("O1","A",1,"10.00", date(2024,4,30)),
    ]
    rows[0].classification = "adjustment"
    assert group_invoices(rows, shop_id="PLELNU") == []
