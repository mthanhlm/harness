"""Text helpers used across the reporting pipeline."""

import re


def normalize_spaces(value):
    """Collapse runs of whitespace into a single space."""
    return re.sub(r"\s+", " ", value).strip()


def truncate(value, limit):
    """Shorten to `limit` characters, adding an ellipsis when it had to cut."""
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
