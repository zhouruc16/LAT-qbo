from datetime import date
from decimal import Decimal
from tiktok_qbo.models import StatementRow
from tiktok_qbo.reconcile import check_identity_2, Mismatch

def mk(stmt_id, net, shipping, fees, adj, reserve, payable):
    return StatementRow(
        shop_id="PLELNU", statement_id=stmt_id, payment_id="P1",
        statement_date=date(2024,5,12), status="Paid",
        total_settlement_amount=Decimal(payable),
        net_sales=Decimal(net), shipping=Decimal(shipping),
        fees=Decimal(fees), adjustments=Decimal(adj),
        reserve_amount=Decimal(reserve), payable_amount=Decimal(payable),
    )

def test_identity_2_passes_when_payable_matches_computed():
    # 100 + 10 + (-15) + 0 - 5 = 90
    stmts = [mk("S1","100","10","-15","0","5","90")]
    assert check_identity_2(stmts) == []

def test_identity_2_flags_off_by_more_than_one_cent():
    # computed = 90, payable reported = 91.00 → diff 1.00
    stmts = [mk("S1","100","10","-15","0","5","91.00")]
    diffs = check_identity_2(stmts)
    assert len(diffs) == 1
    assert isinstance(diffs[0], Mismatch)
    assert diffs[0].statement_id == "S1"
    assert diffs[0].diff == Decimal("1.00")

def test_identity_2_tolerates_penny():
    stmts = [mk("S1","100","10","-15","0","5","90.01")]
    assert check_identity_2(stmts) == []
