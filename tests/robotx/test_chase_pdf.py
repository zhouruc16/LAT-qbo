from decimal import Decimal
from robotx_qbo.ingest.chase_pdf import parse_chase


def test_chase_counts():
    assert len(parse_chase("inputs/robotx/chase-01.pdf")) == 0
    feb = parse_chase("inputs/robotx/chase-02.pdf")
    assert len(feb) == 4
    dep = [t for t in feb if t.amount > 0]
    assert len(dep) == 1 and dep[0].amount == Decimal("170000.00")
    assert len(parse_chase("inputs/robotx/chase-03.pdf")) == 6
    assert len(parse_chase("inputs/robotx/chase-04.pdf")) == 9


def test_chase_2025_other_withdrawals_captured():
    """Dec 2025 has an 'OTHER WITHDRAWALS' section the old parser ignored."""
    dec = parse_chase("inputs/robotx_2025/chase-2025-12.pdf")
    amts = sorted(t.amount for t in dec)
    assert Decimal("-423001.00") in amts, "missing $423,001 Other Withdrawal"
    assert Decimal("-2500.00") in amts, "missing $2,500 Other Withdrawal"
    # 2 deposits + 3 electronic + 2 other = 7, netting begin 47,500 -> end 124,998
    assert len(dec) == 7
    assert sum(t.amount for t in dec) == Decimal("77498.00")


def test_chase_2026_06_checks_paid_captured():
    """June 2026 has a 'CHECKS PAID' section (9 checks, one merged into the
    section *end* marker) the old parser ignored."""
    jun = parse_chase("inputs/robotx_2025/chase-2026-06.pdf")
    checks = [t for t in jun if t.kind == "check"]
    assert len(checks) == 9, "expected 9 checks paid"
    assert sum(-t.amount for t in checks) == Decimal("15511.62"), "checks total"
    # 2 deposits + 8 electronic + 1 other + 9 checks = 20
    assert len(jun) == 20
    # Whole statement reconciles: begin 391,447.19 + net = end 748,500.63
    assert Decimal("391447.19") + sum(t.amount for t in jun) == Decimal("748500.63")
