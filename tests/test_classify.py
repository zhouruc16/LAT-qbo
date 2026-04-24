from decimal import Decimal
from tiktok_qbo.ingest.classify import classify

def mk(row_type="Order", gross_sales="0", gross_sales_refund="0",
       customer_payment="0", quantity=1):
    return {
        "Type": row_type,
        "Gross sales": Decimal(gross_sales),
        "Gross sales refund": Decimal(gross_sales_refund),
        "Customer payment": Decimal(customer_payment),
        "Quantity": quantity,
    }

def test_sale_positive_gross():
    assert classify(mk(gross_sales="15.99", customer_payment="15.99")) == "sale"

def test_refund_negative_gross_refund():
    assert classify(mk(gross_sales_refund="-15.99")) == "refund"

def test_chargeback_row_type():
    assert classify(mk(row_type="Chargeback")) == "chargeback"

def test_tiktok_reimbursement():
    assert classify(mk(row_type="TikTok Shop reimbursement")) == "reimbursement"

def test_logistics_reimbursement():
    assert classify(mk(row_type="Logistics reimbursement")) == "reimbursement"

def test_fee_only_zero_everything():
    assert classify(mk(gross_sales="0", customer_payment="0")) == "fee_only"

def test_zero_quantity_sale_becomes_adjustment():
    # E9: Quantity=0 disqualifies a sale classification.
    assert classify(mk(gross_sales="15.99", customer_payment="15.99", quantity=0)) == "adjustment"

def test_unknown_row_type_raises():
    import pytest
    with pytest.raises(ValueError):
        classify(mk(row_type="SomethingNew"))
