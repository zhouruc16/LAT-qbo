from decimal import Decimal
from robotx_qbo.ingest.eastwest_pdf import parse_eastwest


def test_january_counts_and_known_lines():
    txns = parse_eastwest("inputs/robotx/ewb-01.pdf")
    assert len(txns) == 15, f"January expected 15, got {len(txns)}"
    yushu = [t for t in txns if "YuShu" in t.description or "YUSHU" in t.description.upper()]
    assert len(yushu) == 1, f"Expected 1 YuShu wire, got {len(yushu)}"
    assert yushu[0].amount == Decimal("-70000.00")
    c165 = [t for t in txns if t.check_no == "165"][0]
    assert c165.payee == "Po Jen Yang"
    assert c165.amount == Decimal("-1254.58")
    assert sum(1 for t in txns if t.kind == "check") == 5


def test_all_months_counts():
    for f, n in [("ewb-01.pdf", 15), ("ewb-02.pdf", 15), ("ewb-03.pdf", 27), ("ewb-04.pdf", 22)]:
        txns = parse_eastwest(f"inputs/robotx/{f}")
        assert len(txns) == n, f"{f}: expected {n}, got {len(txns)}"
