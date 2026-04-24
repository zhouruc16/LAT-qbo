from decimal import Decimal
from tiktok_qbo.money import to_money, money_sum, close_enough

def test_to_money_quantizes_to_two_places():
    assert to_money("1.235") == Decimal("1.24")       # banker's rounding
    assert to_money(1) == Decimal("1.00")
    assert to_money("") == Decimal("0.00")
    assert to_money(None) == Decimal("0.00")

def test_money_sum_preserves_two_places():
    assert money_sum([Decimal("1.005"), Decimal("2.005")]) == Decimal("3.01")

def test_close_enough_tolerates_one_cent():
    assert close_enough(Decimal("10.00"), Decimal("10.01"))
    assert not close_enough(Decimal("10.00"), Decimal("10.02"))
