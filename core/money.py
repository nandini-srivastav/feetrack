"""Money helpers. All amounts are Decimal rupees; never floats."""
from decimal import Decimal, ROUND_HALF_UP

PAISA = Decimal("0.01")
RUPEE = Decimal("1")


def to_paisa(value) -> Decimal:
    return Decimal(value).quantize(PAISA, rounding=ROUND_HALF_UP)


def to_rupee(value) -> Decimal:
    return Decimal(value).quantize(RUPEE, rounding=ROUND_HALF_UP)


def format_inr(value, decimals=False) -> str:
    """Indian digit grouping: 120000 -> '1,20,000'."""
    value = Decimal(value)
    negative = value < 0
    value = abs(value)
    q = to_paisa(value) if decimals else to_rupee(value)
    whole, _, frac = f"{q:.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = f"₹{whole}" + (f".{frac}" if decimals else "")
    return f"-{out}" if negative else out
