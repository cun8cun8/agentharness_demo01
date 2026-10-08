from src.retry import backoff_delay


def test_first_retry_uses_base_delay():
    assert backoff_delay(1, base=2, cap=30) == 2


def test_backoff_is_capped():
    assert backoff_delay(8, base=2, cap=30) == 30

