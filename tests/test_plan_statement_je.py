from datetime import date
from decimal import Decimal
from tiktok_qbo.models import StatementRow, PaymentRow
from tiktok_qbo.plan.statement_je import build_statement_jes

def mk_stmt(stmt_id, payment_id, net, shipping, fees, adj, reserve, payable):
    return StatementRow(
        shop_id="PLELNU", statement_id=stmt_id, payment_id=payment_id,
        statement_date=date(2024,5,12), status="Paid",
        total_settlement_amount=Decimal(payable),
        net_sales=Decimal(net), shipping=Decimal(shipping),
        fees=Decimal(fees), adjustments=Decimal(adj),
        reserve_amount=Decimal(reserve), payable_amount=Decimal(payable),
    )

def mk_payment(payment_id, amount):
    return PaymentRow(
        shop_id="PLELNU", payment_id=payment_id,
        payment_amount=Decimal(amount),
        payment_initiation_date=date(2024,5,12),
        payment_completion_date=date(2024,5,13),
        bank_account_masked="********9247", status="Completed",
    )

def test_single_payment_single_statement_six_legs():
    stmts = [mk_stmt("S1","P1","100","10","-15","0","5","90")]
    pays  = [mk_payment("P1","90")]
    jes = build_statement_jes(stmts, pays, shop_id="PLELNU")
    assert len(jes) == 1
    je = jes[0]
    assert je.doc_number == "JE-PLELNU-P1"
    assert je.txn_date == date(2024,5,13)
    assert je.bank_amount == Decimal("90.00")

    by_role = {l.account_role: l for l in je.lines}
    assert by_role["bank"].side == "DR" and by_role["bank"].amount == Decimal("90.00")
    assert by_role["reserve"].side == "DR" and by_role["reserve"].amount == Decimal("5.00")
    assert by_role["fees"].side == "DR" and by_role["fees"].amount == Decimal("15.00")
    assert by_role["shipping"].side == "DR" and by_role["shipping"].amount == Decimal("10.00")
    assert by_role["adjustments"].side == "DR" and by_role["adjustments"].amount == Decimal("0.00")
    assert by_role["clearing"].side == "CR" and by_role["clearing"].amount == Decimal("120.00")

    debits = sum(l.amount for l in je.lines if l.side == "DR")
    credits = sum(l.amount for l in je.lines if l.side == "CR")
    assert debits == credits

def test_shipping_and_adjustments_flip_side_when_negative():
    # Shipping/adjustments with negative amounts become CR legs.
    stmts = [mk_stmt("S1","P1","100","-7","-15","-3","0","75")]
    pays  = [mk_payment("P1","75")]
    je = build_statement_jes(stmts, pays, shop_id="PLELNU")[0]
    by_role = {l.account_role: l for l in je.lines}
    assert by_role["shipping"].side == "CR" and by_role["shipping"].amount == Decimal("7.00")
    assert by_role["adjustments"].side == "CR" and by_role["adjustments"].amount == Decimal("3.00")
    debits = sum(l.amount for l in je.lines if l.side == "DR")
    credits = sum(l.amount for l in je.lines if l.side == "CR")
    assert debits == credits

def test_multiple_statements_per_payment_roll_up():
    stmts = [
        mk_stmt("S1","P1","50","5","-7","0","2","46"),
        mk_stmt("S2","P1","30","0","-3","0","1","26"),
    ]
    pays = [mk_payment("P1","72")]
    je = build_statement_jes(stmts, pays, shop_id="PLELNU")[0]
    by_role = {l.account_role: l for l in je.lines}
    assert by_role["bank"].amount == Decimal("72.00")
    assert by_role["fees"].amount == Decimal("10.00")
    assert by_role["reserve"].amount == Decimal("3.00")
    # Clearing is the balancing plug: DR_total - CR_total.
    # DR = 72 + 3 + 10 + 5 + 0 = 90; CR pre-plug = 0. Plug = 90.
    assert by_role["clearing"].amount == Decimal("90.00")
    assert sorted(je.statement_ids) == ["S1","S2"]
    debits = sum(l.amount for l in je.lines if l.side == "DR")
    credits = sum(l.amount for l in je.lines if l.side == "CR")
    assert debits == credits

def test_pending_statement_without_payment_is_skipped():
    stmts = [mk_stmt("S1","", "100","0","-5","0","0","95")]  # payment_id blank
    pays  = []
    assert build_statement_jes(stmts, pays, shop_id="PLELNU") == []
