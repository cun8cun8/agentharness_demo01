from src.price import total_with_tax


def test_total_rounds_to_cents():
    assert total_with_tax(10.005, 0.1) == 11.01


def test_total_keeps_two_decimal_places():
    assert total_with_tax(19.99, 0.0825) == 21.64


def test_total_uses_decimal_safe_half_up_rounding():
    assert total_with_tax(2.675, 0) == 2.68
