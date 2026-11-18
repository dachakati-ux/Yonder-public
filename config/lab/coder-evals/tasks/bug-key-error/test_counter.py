from counter import get_count


def test_get_count():
    assert get_count({"a": 2}, "a") == 2
    assert get_count({"a": 2}, "missing") == 0
