from text_utils import normalize_spaces, truncate


def test_normalize_spaces():
    assert normalize_spaces("  a   b  ") == "a b"


def test_truncate():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("ab", 4) == "ab"
