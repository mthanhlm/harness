"""Hidden grading tests. The model never sees these."""

import pytest

from text_utils import normalize_spaces, slugify, truncate


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello World", "hello-world"),
        ("  Leading and trailing  ", "leading-and-trailing"),
        ("Punctuation! Removed?", "punctuation-removed"),
        ("Multiple   spaces", "multiple-spaces"),
        ("already-hyphenated", "already-hyphenated"),
        ("Mixed CASE Title", "mixed-case-title"),
        ("--edges--", "edges"),
        ("a--b", "a-b"),
        ("Numbers 123 stay", "numbers-123-stay"),
        ("", ""),
    ],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected


def test_existing_behaviour_unbroken():
    assert normalize_spaces("  a   b  ") == "a b"
    assert truncate("abcdef", 4) == "abc…"
