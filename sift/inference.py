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
from functools import lru_cache

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

# Two conventions, and no way to prefer one in advance.
#   1,234.56   English: comma groups thousands, dot marks the decimal
#   1.234,56   most of Europe and Latin America: exactly reversed
CONVENTIONS = {"en": (",", "."), "eu": (".", ",")}


def _number_re(thousands: str, decimal: str) -> re.Pattern:
    group, point = re.escape(thousands), re.escape(decimal)
    return re.compile(
        rf"""^
        (?P<open>\()?
        \s*
        (?P<sign>[-+])?
        \s*
        (?P<cur_pre>[$\u20ac\u00a3\u00a5])?
        \s*
        (?P<num>
            [1-9]\d{{0,2}}(?:{group}\d{{3}})+(?:{point}\d+)?
          | \d+(?:{point}\d+)?
          | {point}\d+
        )
        \s*
        (?P<pct>%)?
        \s*
        (?P<cur_post>[$\u20ac\u00a3\u00a5])?
        (?P<close>\))?
        $""",
        re.VERBOSE,
    )


_NUMBER_RES = {name: _number_re(*seps) for name, seps in CONVENTIONS.items()}

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


def _parse_with(value: str, convention: str) -> Number | None:
    thousands, decimal = CONVENTIONS[convention]
    match = _NUMBER_RES[convention].match(value.strip())
    if not match:
        return None
    if bool(match["open"]) != bool(match["close"]):
        return None

    flags: set[str] = set()
    raw = match["num"]
    if thousands in raw:
        flags.add("thousands")
    if match["cur_pre"] or match["cur_post"]:
        flags.add("currency")

    number = float(raw.replace(thousands, "").replace(decimal, "."))
    if match["pct"]:
        flags.add("percent")
    if match["sign"] == "-":
        number = -number
    if match["open"]:
        flags.add("parens")
        number = -abs(number)
    return Number(number, flags)


@lru_cache(maxsize=16384)
def _parse_number_any_cached(text: str) -> dict[str, Number]:
    if "," not in text and "." not in text:
        parsed = _parse_with(text, "en")
        return {} if parsed is None else {"en": parsed, "eu": parsed}
    readings = {}
    for convention in CONVENTIONS:
        parsed = _parse_with(text, convention)
        if parsed is not None:
            readings[convention] = parsed
    return readings


def parse_number_any(value: str) -> dict[str, Number]:
    """Every reading of this value as a number, keyed by convention."""
    return _parse_number_any_cached(value.strip())


def evidence_from(readings: dict[str, Number]) -> str | None:
    """What a parsed value proves about its column's convention."""
    if not readings:
        return None
    if len(readings) == 1:
        return next(iter(readings))
    english, european = readings["en"], readings["eu"]
    if english.value == european.value:
        return "neutral"
    if "thousands" in english.flags and "thousands" not in european.flags:
        return "group-en"
    if "thousands" in european.flags and "thousands" not in english.flags:
        return "group-eu"
    return "ambiguous"


def number_evidence(value: str) -> str | None:
    return evidence_from(parse_number_any(value))


def parse_number(value: str, convention: str = "en") -> Number | None:
    """Parse a human-written number under one convention."""
    return _parse_with(value, convention)


_DATE_SEPARATORS = frozenset("/-., ")

_DIRECTIVES = {
    "%Y": r"\d{4}", "%y": r"\d{2}", "%m": r"\d{1,2}", "%d": r"\d{1,2}",
    "%H": r"\d{1,2}", "%M": r"\d{1,2}", "%S": r"\d{1,2}",
    "%b": r"[A-Za-z]{3,}", "%B": r"[A-Za-z]{3,}",
}


def _shape(fmt: str) -> re.Pattern:
    pattern, index = "", 0
    while index < len(fmt):
        token = fmt[index : index + 2]
        if token in _DIRECTIVES:
            pattern += _DIRECTIVES[token]
            index += 2
        else:
            pattern += re.escape(fmt[index])
            index += 1
    return re.compile(f"^{pattern}$")


_SHAPED_FORMATS = [(_shape(fmt), fmt, order) for fmt, order in DATE_FORMATS]


def _could_be_date(text: str) -> bool:
    length = len(text)
    if length < 6 or length > 30:
        return False
    if text.isdigit():
        return length == 8
    if not any(character.isdigit() for character in text):
        return False
    return any(character in _DATE_SEPARATORS for character in text)


@lru_cache(maxsize=16384)
def _parse_dates_cached(text: str) -> tuple[tuple[datetime, str], ...]:
    out: list[tuple[datetime, str]] = []
    for shape, fmt, order in _SHAPED_FORMATS:
        if not shape.match(text):
            continue
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y%m%d" and not (1900 <= parsed.year <= 2100):
            continue
        out.append((parsed, order))
    return tuple(out)


def parse_dates(value: str) -> list[tuple[datetime, str]]:
    """Every reading of this value as a date, with the order each assumes.

    Returning all of them is the point: if `04/03/2024` parses under both
    day-first and month-first, the file cannot tell you which one it meant.
    """
    text = value.strip()
    if not _could_be_date(text):
        return []
    return list(_parse_dates_cached(text))


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
