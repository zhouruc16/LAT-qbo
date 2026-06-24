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


def test_2025_checks_not_dropped():
    """Numberless / repeated-number checks must all be captured (the old
    de-dup-by-number logic silently dropped them)."""
    # December: 13 checks summing to $25,073.50 (incl. the 3 previously dropped)
    dec = parse_eastwest("inputs/robotx_2025/ewb-2025-12.pdf")
    dec_checks = [t for t in dec if t.kind == "check"]
    assert len(dec_checks) == 13
    assert sum(-t.amount for t in dec_checks) == Decimal("25073.50")
    # October: both 10-27 checks present (old parser kept only one)
    oct_ = parse_eastwest("inputs/robotx_2025/ewb-2025-10.pdf")
    oct_checks = sorted(-t.amount for t in oct_ if t.kind == "check")
    assert oct_checks == [Decimal("1252.72"), Decimal("1626.21")]
