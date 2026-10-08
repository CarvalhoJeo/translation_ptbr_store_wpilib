import pytest

from transifex.words import count_words


@pytest.mark.parametrize(
    "strings, expected",
    [
        ({"other": "Build Instructions"}, 2),
        ({"other": "Hello {name}, you have %(count)d items"}, 4),
        ({"other": "<b>Bold</b> text"}, 2),
        ({"other": "Use %s or %d here"}, 3),
        ({"one": "1 file", "other": "{n} files"}, 1),
        ({"other": "   "}, 0),
        ({"other": "— , ."}, 0),
        ({}, 0),
    ],
)
def test_count_words(strings, expected):
    assert count_words(strings) == expected
