from decimal import Decimal, ROUND_HALF_UP


def total_with_tax(unit_price: Decimal, quantity: int, tax_rate_percent: Decimal) -> Decimal:
    """Return a tax-inclusive total where 8 means an eight percent tax rate."""
    if unit_price < 0:
        raise ValueError("unit_price must not be negative")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if tax_rate_percent < 0:
        raise ValueError("tax_rate_percent must not be negative")

    subtotal = unit_price * quantity
    total = subtotal * (Decimal("1") + tax_rate_percent)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
