from datetime import date

from src.date_parser import parse_date


def test_valid_iso_date():
    assert parse_date("2026-08-27") == date(2026, 8, 27)


def test_empty_string_returns_none():
    assert parse_date("") is None


def test_invalid_string_returns_none():
    assert parse_date("not-a-date") is None
