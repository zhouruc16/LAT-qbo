from datetime import date
from decimal import Decimal
from pathlib import Path
from tiktok_qbo.models import StatementRow, PaymentRow
from tiktok_qbo.reconcile import check_identity_2, check_identity_1, run_reconcile, Mismatch

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
    # 100 + 10 + (-15) + 0 + (-5) = 90  (reserve signed: -5 = $5 withheld)
    stmts = [mk("S1","100","10","-15","0","-5","90")]
    assert check_identity_2(stmts) == []

def test_identity_2_passes_with_reserve_release():
    # 100 + 10 + (-15) + 0 + 7 = 102  (reserve +7 = $7 released)
    stmts = [mk("S1","100","10","-15","0","7","102")]
    assert check_identity_2(stmts) == []

def test_identity_2_flags_off_by_more_than_one_cent():
    # computed = 90, payable reported = 91.00 → diff 1.00
    stmts = [mk("S1","100","10","-15","0","-5","91.00")]
    diffs = check_identity_2(stmts)
    assert len(diffs) == 1
    assert isinstance(diffs[0], Mismatch)
    assert diffs[0].statement_id == "S1"
    assert diffs[0].diff == Decimal("1.00")

def test_identity_2_tolerates_penny():
    stmts = [mk("S1","100","10","-15","0","-5","90.01")]
    assert check_identity_2(stmts) == []


def mk_pay(pid, amount):
    return PaymentRow(
        shop_id="PLELNU", payment_id=pid, payment_amount=Decimal(amount),
        payment_initiation_date=date(2024,5,12),
        payment_completion_date=date(2024,5,13),
        bank_account_masked="********9247", status="Completed",
    )

def test_identity_1_passes_when_sum_payable_equals_payment():
    stmts = [
        mk("S1","50","5","-7","0","-2","46"),   # payable 46 (50+5-7+0+(-2))
        mk("S2","30","0","-3","0","-1","26"),   # payable 26 (30+0-3+0+(-1))
    ]
    for s in stmts: s.payment_id = "P1"
    pays = [mk_pay("P1","72")]
    assert check_identity_1(stmts, pays) == []

def test_identity_1_flags_payment_mismatch():
    stmts = [mk("S1","50","5","-7","0","-2","46")]
    stmts[0].payment_id = "P1"
    pays = [mk_pay("P1","50")]       # bank says 50, statements sum to 46 → diff 4
    diffs = check_identity_1(stmts, pays)
    assert len(diffs) == 1
    assert diffs[0].payment_id == "P1"
    assert diffs[0].diff == Decimal("4.00")

def test_run_reconcile_writes_pass_marker_and_csv(tmp_path):
    stmts = [mk("S1","50","5","-7","0","-2","46")]
    stmts[0].payment_id = "P1"
    pays = [mk_pay("P1","46")]
    result = run_reconcile(stmts, pays, hash="abc", state_dir=tmp_path)
    assert result.passed
    assert (tmp_path / "reconcile-abc.pass").exists()
    assert (tmp_path / "reconcile-abc.csv").exists()

def test_run_reconcile_writes_fail_marker_on_mismatch(tmp_path):
    stmts = [mk("S1","50","5","-7","0","-2","99")]   # identity_2 fails
    stmts[0].payment_id = "P1"
    pays = [mk_pay("P1","99")]
    result = run_reconcile(stmts, pays, hash="abc", state_dir=tmp_path)
    assert not result.passed
    assert (tmp_path / "reconcile-abc.fail").exists()
    body = (tmp_path / "reconcile-abc.csv").read_text(encoding="utf-8")
    assert "S1" in body
