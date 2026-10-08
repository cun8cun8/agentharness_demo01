from pricing import discount

def test_discount():
    assert discount(100, 20) == 80

def test_zero():
    assert discount(100, 0) == 100

def test_full():
    assert discount(100, 100) == 0
