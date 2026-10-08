from src.inventory import should_reorder


def test_reorders_at_threshold_boundary():
    assert should_reorder(10, 10) is True


def test_does_not_reorder_above_threshold():
    assert should_reorder(11, 10) is False

