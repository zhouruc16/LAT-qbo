import glob
from decimal import Decimal
from robotx_qbo.ingest.chase_cc_pdf import parse_chase_cc, reconcile

CC = sorted(glob.glob("inputs/robotx_cc/*.pdf"))


def test_all_statements_reconcile():
    """Every CC statement's parsed charges/credits tie to the ACCOUNT SUMMARY,
    and prev + charges + credits = new balance."""
    assert len(CC) == 8
    for f in CC:
        r = reconcile(f)
        assert r["charges_ok"], f"{f}: charges {r['charges']} != {r['exp_charges']}"
        assert r["credits_ok"], f"{f}: credits {r['credits']} != {r['exp_credits']}"
        assert r["new_ok"], f"{f}: new-balance identity failed"


def test_amount_without_leading_zero_captured():
    """Feb has a 'FOREIGN TRANSACTION FEE .15' — amount with no leading zero."""
    feb = [f for f in CC if "20260217" in f][0]
    txns = parse_chase_cc(feb)
    assert any(t.amount == Decimal("0.15") and t.kind == "fee" for t in txns)


def test_payment_and_refund_kinds():
    jun = [f for f in CC if "20260617" in f][0]
    txns = parse_chase_cc(jun)
    # the autopay line is a 'payment' (settled bank-side, not a card charge)
    assert any(t.kind == "payment" and t.amount == Decimal("-5646.00") for t in txns)
    # the Amazon -109.51 is a merchant refund
    assert any(t.kind == "refund" and t.amount == Decimal("-109.51") for t in txns)
