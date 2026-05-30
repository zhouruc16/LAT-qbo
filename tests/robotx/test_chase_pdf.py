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
