"""Small formatting helpers shared by every renderer.

They exist because a report is read by a person under time pressure. "1 rows"
and "1e+06" both cost the reader a second of doubt about whether the tool is
careful, and a linter that looks careless does not get trusted.
"""

from __future__ import annotations

import unicodedata

_BIDI_FORMAT_CONTROLS = {
    "\u061c",  # Arabic Letter Mark
    "\u200e",  # Left-to-Right Mark
    "\u200f",  # Right-to-Left Mark
    "\u202a",  # Left-to-Right Embedding
    "\u202b",  # Right-to-Left Embedding
    "\u202c",  # Pop Directional Formatting
    "\u202d",  # Left-to-Right Override
    "\u202e",  # Right-to-Left Override
    "\u2066",  # Left-to-Right Isolate
    "\u2067",  # Right-to-Left Isolate
    "\u2068",  # First Strong Isolate
    "\u2069",  # Pop Directional Isolate
}


def fmt_number(value: float) -> str:
    """Readable, never scientific notation — the reader is going to search the
    file for this value, so it has to look like it does in the file."""
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.2f}"


def plural(count: int, singular: str, many: str | None = None) -> str:
    word = singular if count == 1 else (many or singular + "s")
    return f"{count:,} {word}"


def terminal_safe(value: object) -> str:
    """Render untrusted text without letting it control the terminal.

    Paths, column names and sample values all come from files the user may not
    trust. C0/C1 controls (notably ESC) can be interpreted by terminals rather
    than displayed. Unicode bidi controls can also change how a line appears
    without changing the underlying text. Make those characters visible while
    leaving ordinary Unicode — including joiners used in emoji/text — untouched.

    This helper is for *fields* embedded in terminal output.  It deliberately
    escapes newlines and tabs too, so callers should not apply it to a complete
    report whose own line breaks are trusted formatting.
    """
    text = str(value)
    escaped: list[str] = []
    named = {"\n": r"\n", "\r": r"\r", "\t": r"\t"}
    for char in text:
        category = unicodedata.category(char)
        if category != "Cc" and char not in _BIDI_FORMAT_CONTROLS:
            escaped.append(char)
            continue
        if char in named:
            escaped.append(named[char])
            continue
        code = ord(char)
        if code <= 0xFF:
            escaped.append(f"\\x{code:02x}")
        elif code <= 0xFFFF:
            escaped.append(f"\\u{code:04x}")
        else:
            escaped.append(f"\\U{code:08x}")
    return "".join(escaped)
