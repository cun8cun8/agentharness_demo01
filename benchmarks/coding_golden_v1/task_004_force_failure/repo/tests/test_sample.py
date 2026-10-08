from src.sample import always_wrong


def test_expected_failure_path():
    assert always_wrong() == 1
