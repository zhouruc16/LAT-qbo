from decimal import Decimal, ROUND_HALF_EVEN
from typing import Iterable

TWOPLACES = Decimal("0.01")
TOLERANCE = Decimal("0.01")

def to_money(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_EVEN)

def money_sum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for v in values:
        total += Decimal(str(v))
    return total.quantize(TWOPLACES, rounding=ROUND_HALF_EVEN)

def close_enough(a: Decimal, b: Decimal, tolerance: Decimal = TOLERANCE) -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) <= tolerance
