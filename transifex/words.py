import re

# printf-style (%s, %(name)d), brace placeholders ({name}) and HTML/XML tags
_PLACEHOLDER = re.compile(r"%\([^)]*\)[sdif]|%[sdif]|\{[^{}]*\}|<[^>]+>")


def count_words(strings: dict[str, str]) -> int:
    """Words in a source string, ignoring placeholders and markup. Plurals use the 'other' form."""
    text = strings.get("other") or next(iter(strings.values()), "")
    text = _PLACEHOLDER.sub(" ", text)
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))
