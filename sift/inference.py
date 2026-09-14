"""Value-level parsing.

Everything is read from the file as text, on purpose. A CSV has no types; the
types only appear when something tries to read it. Keeping the raw string lets
Sift see the things a typed loader would silently destroy: the leading zero on
a zip code, the dollar sign that turns a number into a string, the fact that
`03/04/2024` has two equally valid readings.
"""

from __future__ import annotations

import re
from datetime import datetime

# Strings people type when they mean "no value". A loader reads them as text,
# so the column silently stops being numeric and the nulls stop being null.
NULL_TOKENS = {
    "", "-", "--", "?", "n/a", "n.a.", "na", "#n/a", "nan", "nil", "none",
    "null", "missing", "unknown", "tbd", "not available", "not applicable",
}

NUMERIC_SENTINELS = {-1.0, -99.0, -999.0, -9999.0, 999.0, 9999.0, 99999.0, 999999.0}
DATE_SENTINELS = {"1900-01-01", "1901-01-01", "1970-01-01", "2099-12-31", "9999-12-31"}

TRUE_TOKENS = {"true", "t", "yes", "y"}
FALSE_TOKENS = {"false", "f", "no", "n"}

_NUMBER_RE = re.compile(
    r"""^
    (?P<open>\()?
    \s*
    (?P<sign>[-+])?
    \s*
    (?P<cur_pre>[$\u20ac\u00a3\u00a5])?
    \s*
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?
      | \d+(?:\.\d+)?
      | \.\d+
    )
    \s*
    (?P<pct>%)?
    \s*
    (?P<cur_post>[$\u20ac\u00a3\u00a5])?
    (?P<close>\))?
    $""",
    re.VERBOSE,
)

# (strptime format, day/month order). Order matters for ambiguity detection.
DATE_FORMATS: list[tuple[str, str]] = [
    ("%Y-%m-%d", "ymd"),
    ("%Y/%m/%d", "ymd"),
    ("%Y-%m-%dT%H:%M:%S", "ymd"),
    ("%Y-%m-%dT%H:%M:%SZ", "ymd"),
    ("%Y-%m-%d %H:%M:%S", "ymd"),
    ("%Y%m%d", "ymd"),
    ("%d/%m/%Y", "dmy"),
    ("%m/%d/%Y", "mdy"),
    ("%d-%m-%Y", "dmy"),
    ("%m-%d-%Y", "mdy"),
    ("%d.%m.%Y", "dmy"),
    ("%d/%m/%y", "dmy"),
    ("%m/%d/%y", "mdy"),
    ("%d %b %Y", "dmy"),
    ("%d-%b-%Y", "dmy"),
    ("%b %d, %Y", "mdy"),
    ("%B %d, %Y", "mdy"),
    ("%d %B %Y", "dmy"),
]

MOJIBAKE_MARKERS = ("â€™", "â€œ", "â€\x9d", "Ã©", "Ã¨", "Ã¼", "Ã±", "Â£", "Â ", "ï»¿")


def is_blank(value: str) -> bool:
    return value.strip() == ""


def is_null_token(value: str) -> bool:
    """True for blanks and for the words people use to mean blank."""
    return value.strip().lower() in NULL_TOKENS


def is_disguised_null(value: str) -> bool:
    """A null token that is not actually empty — the kind a loader keeps."""
    return not is_blank(value) and is_null_token(value)


class Number:
    """A parsed number and the formatting that was wrapped around it."""

    __slots__ = ("value", "flags")

    def __init__(self, value: float, flags: set[str]):
        self.value = value
        self.flags = flags

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Number({self.value!r}, {sorted(self.flags)!r})"


def parse_number(value: str) -> Number | None:
    """Parse a human-written number. Returns None if it isn't one.

    Records how it was written, because `$1,200` and `1200` mean the same
    thing to a person and very different things to `float()`.
    """
    match = _NUMBER_RE.match(value.strip())
    if not match:
        return None
    if bool(match["open"]) != bool(match["close"]):
        return None  # unbalanced parenthesis is not a number, it's damage

    flags: set[str] = set()
    raw = match["num"]
    if "," in raw:
        flags.add("thousands")
    if match["cur_pre"] or match["cur_post"]:
        flags.add("currency")

    number = float(raw.replace(",", ""))
    if match["pct"]:
        flags.add("percent")
    if match["sign"] == "-":
        number = -number
    if match["open"]:
        flags.add("parens")
        number = -abs(number)
    return Number(number, flags)


def parse_dates(value: str) -> list[tuple[datetime, str]]:
    """Every reading of this value as a date, with the order each assumes.

    Returning all of them is the point: if `04/03/2024` parses under both
    day-first and month-first, the file cannot tell you which one it meant.
    """
    text = value.strip()
    if not text or (text.isdigit() and len(text) != 8):
        return []
    out: list[tuple[datetime, str]] = []
    for fmt, order in DATE_FORMATS:
        try:
            out.append((datetime.strptime(text, fmt), order))
        except ValueError:
            continue
    return out


def parse_bool(value: str) -> bool | None:
    token = value.strip().lower()
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    return None


def has_mojibake(value: str) -> bool:
    """UTF-8 that was decoded as Latin-1 somewhere upstream."""
    return any(marker in value for marker in MOJIBAKE_MARKERS)


def whitespace_problem(value: str) -> str | None:
    if value != value.strip():
        return "padded with spaces"
    if "\u00a0" in value:
        return "contains non-breaking spaces"
    if re.search(r"\s{2,}", value):
        return "contains repeated spaces"
    if "\t" in value or "\n" in value:
        return "contains tabs or newlines"
    return None


def normalize_label(value: str) -> str:
    """Fold the differences that people never intend to be differences."""
    text = value.strip().lower().replace("\u00a0", " ")
    text = re.sub(r"[\s_-]+", " ", text)
    text = re.sub(r"[.,]+$", "", text)
    return text.strip()


def looks_like_id_name(name: str) -> bool:
    token = normalize_label(name)
    return (
        token in {"id", "key", "uuid", "guid", "code", "ref"}
        or token.endswith(" id")
        or token.endswith(" key")
        or token.endswith(" code")
        or (token.endswith("id") and len(token) <= 12)
    )
