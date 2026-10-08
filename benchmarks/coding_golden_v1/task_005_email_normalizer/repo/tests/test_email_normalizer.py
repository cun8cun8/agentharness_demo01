from src.email_normalizer import normalize_email


def test_trims_and_lowercases_email():
    assert normalize_email("  USER@Example.COM  ") == "user@example.com"


def test_empty_value_returns_empty_string():
    assert normalize_email("   ") == ""

