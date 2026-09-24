"""Known ways to break a clean file.

Every corruption here states what it did and which finding code should catch
it. That pairing is the whole point: it converts "does Sift work?" from an
opinion formed by reading output into a number that can go up or down between
commits.

The corruptions are deliberately mundane. They are not adversarial edge cases
designed to be hard — they are the things that actually happen when a file
passes through a spreadsheet, a locale, a bad export, or a tired person.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

Grid = list[list[str]]


@dataclass
class Case:
    """One corruption, and what should be reported about it."""

    name: str
    expect: str  # the finding code that must fire
    column: str | None = None  # the column it should fire on, if any
    note: str = ""
    raw_suffix: bytes = b""  # appended to the encoded file, for byte-level cases
    header: list[str] = field(default_factory=list)
    rows: Grid = field(default_factory=list)


Corruption = Callable[[list[str], Grid, random.Random], Case]

CASES: list[Corruption] = []


def corruption(fn: Corruption) -> Corruption:
    CASES.append(fn)
    return fn


def _index(header: list[str], name: str) -> int:
    return header.index(name)


def _sample_rows(rows: Grid, rng: random.Random, count: int) -> list[int]:
    return rng.sample(range(len(rows)), min(count, len(rows)))


# --- numbers ---------------------------------------------------------------


@corruption
def currency_formatting(header, rows, rng):
    """A spreadsheet formatted the column as money."""
    index = _index(header, "amount")
    out = [list(row) for row in rows]
    for row in out:
        row[index] = f"${float(row[index]):,.2f}"
    return Case("currency formatting", "number-as-text", "amount", header=header, rows=out)


@corruption
def thousands_separators(header, rows, rng):
    index = _index(header, "amount")
    out = [list(row) for row in rows]
    for row in out:
        # The base amounts are in the hundreds, so they need scaling up before
        # a thousands separator appears at all — the first run of this
        # benchmark "missed" this case because the fixture never produced one.
        row[index] = f"{float(row[index]) * 1000:,.2f}"
    return Case("thousands separators", "number-as-text", "amount", header=header, rows=out)


@corruption
def european_decimals(header, rows, rng):
    """The file came from a locale where the dot and comma are reversed."""
    index = _index(header, "amount")
    out = [list(row) for row in rows]
    for row in out:
        whole, _, fraction = f"{float(row[index]) * 1000:,.2f}".partition(".")
        row[index] = whole.replace(",", ".") + "," + fraction
    return Case("European decimal separators", "european-numbers", "amount",
                header=header, rows=out)


@corruption
def mixed_decimal_conventions(header, rows, rng):
    """Half the rows written each way — one half is wrong by 1000x."""
    index = _index(header, "amount")
    out = [list(row) for row in rows]
    for position, row in enumerate(out):
        scaled = float(row[index]) * 1000
        row[index] = (
            f"{scaled:,.2f}" if position % 2
            else f"{scaled:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
        )
    return Case("mixed decimal conventions", "conflicting-number-formats", "amount",
                header=header, rows=out)


@corruption
def text_in_a_numeric_column(header, rows, rng):
    index = _index(header, "amount")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 3):
        out[position][index] = "not recorded"
    return Case("text in a number column", "mixed-types", "amount", header=header, rows=out)


@corruption
def numeric_sentinels(header, rows, rng):
    """-999 used upstream to mean "unknown"."""
    index = _index(header, "amount")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 6):
        out[position][index] = "-999"
    return Case("-999 placeholders", "numeric-sentinel", "amount", header=header, rows=out)


@corruption
def an_extreme_outlier(header, rows, rng):
    index = _index(header, "amount")
    out = [list(row) for row in rows]
    out[rng.randrange(len(out))][index] = "999999999"
    return Case("extreme outlier", "outliers", "amount", header=header, rows=out)


@corruption
def leading_zeros(header, rows, rng):
    index = _index(header, "postcode")
    out = [list(row) for row in rows]
    for row in out:
        # Same fixture bug: the base postcodes are already 5 digits, so zfill(5)
        # was a no-op and nothing was actually corrupted.
        row[index] = row[index][1:].zfill(5)
    return Case("zero-padded codes", "leading-zeros", "postcode", header=header, rows=out)


# --- missing data ----------------------------------------------------------


@corruption
def blanked_values(header, rows, rng):
    index = _index(header, "note")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, max(1, len(out) // 2)):
        out[position][index] = ""
    return Case("half the column emptied", "missing-values", "note", header=header, rows=out)


@corruption
def nulls_written_as_text(header, rows, rng):
    index = _index(header, "region")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 5):
        out[position][index] = "N/A"
    return Case("N/A instead of empty", "disguised-null", "region", header=header, rows=out)


@corruption
def an_empty_column(header, rows, rng):
    index = _index(header, "note")
    out = [list(row) for row in rows]
    for row in out:
        row[index] = ""
    return Case("column emptied entirely", "empty-column", "note", header=header, rows=out)


@corruption
def a_constant_column(header, rows, rng):
    index = _index(header, "region")
    out = [list(row) for row in rows]
    for row in out:
        row[index] = "North"
    return Case(
        "column collapsed to one value", "constant-column", "region", header=header, rows=out
    )


# --- labels ----------------------------------------------------------------


@corruption
def casing_variants(header, rows, rng):
    index = _index(header, "region")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 8):
        out[position][index] = out[position][index].lower()
    return Case("mixed casing", "label-variants", "region", header=header, rows=out)


@corruption
def a_rare_typo(header, rows, rng):
    """One misspelling against a spelling everything else agrees on."""
    index = _index(header, "customer")
    out = [list(row) for row in rows]
    for row in out:
        row[index] = "Northwind Traders"
    out[0][index] = "Northwnid Traders"
    return Case("single misspelling", "near-duplicate-labels", "customer", header=header, rows=out)


@corruption
def padded_values(header, rows, rng):
    index = _index(header, "region")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 4):
        out[position][index] = out[position][index] + "  "
    return Case("trailing spaces", "whitespace", "region", header=header, rows=out)


@corruption
def mojibake(header, rows, rng):
    index = _index(header, "customer")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 3):
        out[position][index] = "CafÃ© Rouge"
    return Case("bad encoding round-trip", "mojibake", "customer", header=header, rows=out)


# --- dates -----------------------------------------------------------------


@corruption
def undecidable_dates(header, rows, rng):
    """Every date valid both day-first and month-first."""
    index = _index(header, "when")
    out = [list(row) for row in rows]
    for position, row in enumerate(out):
        day = (position % 12) + 1
        month = ((position + 3) % 12) + 1
        row[index] = f"{day:02d}/{month:02d}/2024"
    return Case("ambiguous date order", "ambiguous-dates", "when", header=header, rows=out)


@corruption
def conflicting_dates(header, rows, rng):
    """Some rows can only be day-first, others only month-first."""
    index = _index(header, "when")
    out = [list(row) for row in rows]
    for position, row in enumerate(out):
        row[index] = "25/12/2024" if position % 2 else "12/25/2024"
    return Case(
        "two date formats mixed", "conflicting-date-formats", "when", header=header, rows=out
    )


@corruption
def future_dates(header, rows, rng):
    index = _index(header, "when")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 3):
        out[position][index] = "2089-04-11"
    return Case("dates in the future", "future-dates", "when", header=header, rows=out)


@corruption
def placeholder_dates(header, rows, rng):
    index = _index(header, "when")
    out = [list(row) for row in rows]
    for position in _sample_rows(out, rng, 3):
        out[position][index] = "1970-01-01"
    return Case("epoch placeholder dates", "sentinel-dates", "when", header=header, rows=out)


# --- structure -------------------------------------------------------------


@corruption
def duplicated_rows(header, rows, rng):
    out = [list(row) for row in rows]
    out.append(list(out[0]))
    out.append(list(out[1]))
    return Case("repeated rows", "duplicate-rows", None, header=header, rows=out)


@corruption
def a_repeated_key(header, rows, rng):
    index = _index(header, "id")
    out = [list(row) for row in rows]
    out[5][index] = out[4][index]
    return Case("key value repeated", "key-not-unique", "id", header=header, rows=out)


@corruption
def a_duplicate_column_name(header, rows, rng):
    new_header = list(header)
    new_header[_index(header, "note")] = "region"
    return Case("two columns named the same", "duplicate-column-name", "region",
                header=new_header, rows=[list(row) for row in rows])


@corruption
def an_unnamed_column(header, rows, rng):
    new_header = list(header)
    new_header[_index(header, "note")] = ""
    return Case("column with no name", "unnamed-column", None,
                header=new_header, rows=[list(row) for row in rows])


@corruption
def headers_differing_only_by_case(header, rows, rng):
    new_header = list(header)
    new_header[_index(header, "note")] = "Region"
    return Case("headers differing by case", "similar-column-names", None,
                header=new_header, rows=[list(row) for row in rows])


@corruption
def a_ragged_row(header, rows, rng):
    out = [list(row) for row in rows]
    out[3] = out[3] + ["surprise"]
    return Case("row with an extra field", "ragged-rows", None, header=header, rows=out)


@corruption
def a_byte_order_mark(header, rows, rng):
    return Case("UTF-8 BOM", "bom", None, header=header,
                rows=[list(row) for row in rows], raw_suffix=b"BOM")


@corruption
def mixed_line_endings(header, rows, rng):
    return Case("CRLF and LF mixed", "mixed-line-endings", None, header=header,
                rows=[list(row) for row in rows], raw_suffix=b"CRLF")
