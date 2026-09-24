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

# Values that mean "missing" but survive as real numbers into your averages.
NUMERIC_SENTINELS = {-1.0, -99.0, -999.0, -9999.0, 999.0, 9999.0, 99999.0, 999999.0}

# Dates used as placeholders rather than dates.
DATE_SENTINELS = {"1900-01-01", "1901-01-01", "1970-01-01", "2099-12-31", "9999-12-31"}

TRUE_TOKENS = {"true", "t", "yes", "y"}
FALSE_TOKENS = {"false", "f", "no", "n"}

# Two conventions, and no way to prefer one in advance.
#
#   1,234.56   English: comma groups thousands, dot marks the decimal
#   1.234,56   most of Europe and Latin America: exactly reversed
#
# Read with the wrong one, "12.500,00" — twelve and a half thousand — becomes
# 12.5. A thousandfold error, no exception raised. So numbers are parsed under
# both conventions and the column decides, the same way date order is settled.
CONVENTIONS = {"en": (",", "."), "eu": (".", ",")}


def _number_re(thousands: str, decimal: str) -> re.Pattern:
    group, point = re.escape(thousands), re.escape(decimal)
    return re.compile(
        rf"""^
        (?P<open>\()?                      # accounting negative: (1,234.00)
        \s*
        (?P<sign>[-+])?
        \s*
        (?P<cur_pre>[$\u20ac\u00a3\u00a5])?
        \s*
        (?P<num>
            # Thousands grouping never starts with a lone or leading zero:
            # nobody writes 247 as "0.247". Allowing it made every ordinary
            # sub-one decimal — 0.247, 0.445 — look like a European number.
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
    ("%b %d %Y", "mdy"),
    ("%B %d %Y", "mdy"),
    ("%d %B %Y", "dmy"),
]

MOJIBAKE_MARKERS = ("â€™", "â€œ", "â€\x9d", "Ã©", "Ã¨", "Ã¼", "Ã±", "Â£", "Â ", "ï»¿")


def is_blank(value: str) -> bool:
    return value.strip() == ""


# Spellings of "nan" that a machine produces. Anything else capitalised that
# way is a person: the US Congress has had several members named Nan.
_MACHINE_NAN = {"nan", "NAN", "NaN", "NAN "}


def is_null_token(value: str) -> bool:
    """True for blanks and for the words people use to mean blank."""
    text = value.strip()
    if text.lower() == "nan":
        return text in _MACHINE_NAN
    return text.lower() in NULL_TOKENS


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
        return None  # unbalanced parenthesis is not a number, it's damage

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
    # A value with neither separator reads identically under both conventions,
    # which is most numeric data — parsing it twice is pure waste.
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
    """Every reading of this value as a number, keyed by convention.

    Returning both is the point. "1.234" is 1.234 in English and 1234 in
    German, and the value alone cannot settle it — but "1.234,56" can only be
    German, and one row like that decides the whole column.

    The result is cached and must not be mutated by callers; columns repeat
    themselves heavily and re-parsing every occurrence dominated the run.
    """
    return _parse_number_any_cached(value.strip())


def parse_number(value: str, convention: str = "en") -> Number | None:
    """Parse a human-written number under one convention.

    Records how it was written, because `$1,200` and `1200` mean the same
    thing to a person and very different things to `float()`.
    """
    return _parse_with(value, convention)


def evidence_from(readings: dict[str, Number]) -> str | None:
    """What a parsed value proves about its column's convention.

    Takes readings rather than text so a caller that has already parsed does
    not pay for it twice — profiling every value four times over turned a
    0.7-second test suite into a 6.5-second one.
    """
    if not readings:
        return None
    if len(readings) == 1:
        return next(iter(readings))  # decisive: only one convention can read it

    english, european = readings["en"], readings["eu"]
    if english.value == european.value:
        return "neutral"

    # "1,200" reads as grouped thousands in English and as 1.2 in European.
    # Neither is impossible, but three digits after a separator is what
    # grouping looks like and a three-decimal price is not. That is weaker
    # evidence than a value only one convention can read, so it is kept
    # separate and only used when nothing decisive exists.
    if "thousands" in english.flags and "thousands" not in european.flags:
        return "group-en"
    if "thousands" in european.flags and "thousands" not in english.flags:
        return "group-eu"
    return "ambiguous"


def number_evidence(value: str) -> str | None:
    """What this one value proves about the column's convention.

    Returns "en", "eu", "ambiguous" (valid both ways, different values),
    "neutral" (valid both ways, same value), or None (not a number).
    """
    return evidence_from(parse_number_any(value))


_DATE_SEPARATORS = frozenset("/-., ")

# A shape regex per format, so a format that cannot possibly match is rejected
# by a compiled regex instead of by `strptime` raising ValueError. strptime
# rebuilds its own regex on every call, so letting it fail 19 times per value
# is what made this the slowest part of the program.
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
    """A cheap gate before the expensive part.

    `strptime` is slow — it rebuilds and recompiles a regex per call — and
    trying 18 formats against every value in a file means millions of those
    calls, almost all of them on values that could never be a date. A team
    name has no digits; a score is too short; an ID is a long run of digits
    that only the compact YYYYMMDD format could ever match. Rejecting those
    here took a 126,000-row file from four minutes to seconds.
    """
    length = len(text)
    if length < 6 or length > 30:
        return False
    if text.isdigit():
        return length == 8  # only %Y%m%d has a chance
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
            # A bare run of digits is a number that happens to look like a
            # date. Population figures caught this: 11,620,823 "parses" as
            # 1162-08-23 and turned an entirely numeric column into a
            # mixed-type error.
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
        or token.endswith("id") and len(token) <= 12
    )
