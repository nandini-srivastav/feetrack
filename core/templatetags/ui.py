from django import template

from core.money import format_inr

register = template.Library()


@register.filter
def inr(value):
    if value in (None, ""):
        return "—"
    return format_inr(value)


@register.filter
def inr2(value):
    if value in (None, ""):
        return "—"
    return format_inr(value, decimals=True)


@register.filter
def lakh(value):
    """Compact: 1210000 -> '₹12.1 L', 12100000 -> '₹1.21 cr'."""
    from decimal import Decimal

    v = Decimal(value or 0)
    if v >= Decimal("10000000"):
        return f"₹{(v / Decimal('10000000')):.2f} cr"
    if v >= Decimal("100000"):
        return f"₹{(v / Decimal('100000')):.1f} L"
    return format_inr(v)


@register.filter
def get(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None
