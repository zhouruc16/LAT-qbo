from decimal import Decimal
from datetime import date
from tiktok_qbo.ingest.lat_xlsx import (
    read_order_details, read_statements, read_payments, read_reserves,
)

def test_read_order_details_produces_four_normalized_rows(tiny_xlsx):
    rows = read_order_details(tiny_xlsx, shop_id="PLELNU")
    assert len(rows) == 4
    classes = [r.classification for r in rows]
    assert classes == ["sale", "refund", "chargeback", "fee_only"]
    sale = rows[0]
    assert sale.order_id == "O1"
    assert sale.customer_payment == Decimal("15.99")
    assert sale.order_delivery_date == date(2024, 4, 30)

def test_read_statements(tiny_xlsx):
    stmts = read_statements(tiny_xlsx, shop_id="PLELNU")
    assert len(stmts) == 1
    assert stmts[0].statement_id == "S1"
    assert stmts[0].payable_amount == Decimal("6.00")

def test_read_payments(tiny_xlsx):
    pays = read_payments(tiny_xlsx, shop_id="PLELNU")
    assert len(pays) == 1
    assert pays[0].payment_id == "P1"
    assert pays[0].payment_completion_date == date(2024, 5, 13)

def test_read_reserves(tiny_xlsx):
    res = read_reserves(tiny_xlsx, shop_id="PLELNU")
    assert len(res) == 1
    assert res[0].reserve_id == "R1"

def test_shop_mismatch_raises(tiny_xlsx):
    import pytest
    with pytest.raises(ValueError, match="shop"):
        read_order_details(tiny_xlsx, shop_id="WRONG")
