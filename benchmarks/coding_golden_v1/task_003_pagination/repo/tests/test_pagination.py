from src.pagination import page_count


def test_exact_page_count():
    assert page_count(20, 10) == 2


def test_partial_final_page():
    assert page_count(21, 10) == 3


def test_empty_collection_has_one_page():
    assert page_count(0, 10) == 1
