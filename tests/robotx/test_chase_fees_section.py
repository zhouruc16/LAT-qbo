from decimal import Decimal
from robotx_qbo.ingest.chase_pdf import parse_chase


def test_robotxai_2025_11_fees_captured():
    """Robotxai Nov 2025 has a 'FEES' section (3 incoming-wire fees @ $15)
    the parser previously ignored."""
    nov = parse_chase("inputs/robotxai/chase-3205-2025-11.pdf")
    fees = [t for t in nov if t.kind == "fee"]
    assert len(fees) == 3, "expected 3 wire fees"
    assert sum(-t.amount for t in fees) == Decimal("45.00")
    # 3 deposits + 3 electronic + 3 fees = 9; begin 1,296.37 -> end 675,601.37
    assert len(nov) == 9
    assert Decimal("1296.37") + sum(t.amount for t in nov) == Decimal("675601.37")


def test_robotxai_2026_06_service_charge_fee():
    """Jun 2026 fees section has a single 'Service Charges For The Month' row."""
    jun = parse_chase("inputs/robotxai/chase-3205-2026-06.pdf")
    fees = [t for t in jun if t.kind == "fee"]
    assert sum(-t.amount for t in fees) == Decimal("95.00")
    assert Decimal("30006.20") + sum(t.amount for t in jun) == Decimal("190406.26")
