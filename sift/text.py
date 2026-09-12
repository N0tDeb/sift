"""Small formatting helpers shared by every renderer.

They exist because a report is read by a person under time pressure. "1 rows"
and "1e+06" both cost the reader a second of doubt about whether the tool is
careful, and a linter that looks careless does not get trusted.
"""

from __future__ import annotations


def fmt_number(value: float) -> str:
    """Readable, never scientific notation — the reader is going to search the
    file for this value, so it has to look like it does in the file."""
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.2f}"


def plural(count: int, singular: str, many: str | None = None) -> str:
    word = singular if count == 1 else (many or singular + "s")
    return f"{count:,} {word}"
