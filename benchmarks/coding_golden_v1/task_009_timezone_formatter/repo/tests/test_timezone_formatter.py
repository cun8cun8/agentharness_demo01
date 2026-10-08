from src.timezone_formatter import format_offset


def test_formats_positive_offset_with_zero_padding():
    assert format_offset(330) == "+05:30"


def test_formats_negative_offset_with_absolute_minutes():
    assert format_offset(-480) == "-08:00"

