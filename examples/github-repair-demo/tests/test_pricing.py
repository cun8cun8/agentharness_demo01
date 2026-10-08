from decimal import Decimal

from shop_demo.pricing import total_with_tax


def test_total_with_tax_interprets_rate_as_a_percentage() -> None:
    assert total_with_tax(Decimal("100"), 2, Decimal("8")) == Decimal("216.00")


def test_total_with_tax_rejects_invalid_quantity() -> None:
    try:
        total_with_tax(Decimal("100"), 0, Decimal("8"))
    except ValueError as exc:
        assert "quantity" in str(exc)
    else:
        raise AssertionError("expected invalid quantity to be rejected")
