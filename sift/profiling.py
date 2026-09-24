"""Column profiling.

Every check reads from a profile rather than re-scanning the column, so a file
is parsed once no matter how many checks run.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .inference import (
    Number,
    evidence_from,
    is_blank,
    is_null_token,
    parse_bool,
    parse_dates,
    parse_number_any,
)
from .text import plural

# Kinds a column can be given. "mixed" means no single kind explains it.
NUMERIC = "numeric"
DATE = "date"
BOOLEAN = "boolean"
CATEGORICAL = "categorical"
TEXT = "text"
EMPTY = "empty"
MIXED = "mixed"
# Internal only: a bare 8-digit run, which is both a plausible YYYYMMDD and a
# plausible number. Resolved to DATE or NUMERIC once the whole column is seen.
COMPACT = "compact"


@dataclass
class ColumnProfile:
    name: str
    index: int
    n_rows: int
    kind: str = TEXT
    n_null: int = 0
    n_disguised_null: int = 0
    distinct: int = 0
    value_counts: Counter = field(default_factory=Counter)

    # Positional bookkeeping so findings can point at a row number.
    numbers: list[float] = field(default_factory=list)
    number_flags: set[str] = field(default_factory=set)
    # Which decimal convention the column's numbers were read under, and the
    # per-value evidence that settled it.
    number_convention: str = "en"
    number_evidence: Counter = field(default_factory=Counter)
    dates: list[datetime] = field(default_factory=list)
    date_orders: set[str] = field(default_factory=set)
    ambiguous_date_rows: int = 0
    # 8-digit values that could equally be YYYYMMDD or plain numbers, and were
    # read as numbers for want of any other date evidence in the column.
    unresolved_compact: int = 0
    # How many rows *prove* a reading rather than merely permitting one.
    # "25/12/2024" can only be day-first; "04/03/2024" votes for nothing.
    date_evidence: Counter = field(default_factory=Counter)

    # Rows whose value does not match the column's dominant kind.
    offenders: list[tuple[int, str]] = field(default_factory=list)
    dominant_kind: str | None = None
    dominant_share: float = 0.0

    @property
    def n_present(self) -> int:
        return self.n_rows - self.n_null

    @property
    def null_rate(self) -> float:
        return self.n_null / self.n_rows if self.n_rows else 0.0

    @property
    def unique_ratio(self) -> float:
        return self.distinct / self.n_present if self.n_present else 0.0

    def stats(self) -> dict[str, float]:
        """Robust summary. Median and MAD, not mean and stdev: one bad row
        should not move the yardstick used to find bad rows."""
        if not self.numbers:
            return {}
        values = sorted(self.numbers)
        median = statistics.median(values)
        deviations = sorted(abs(v - median) for v in values)
        return {
            "min": values[0],
            "max": values[-1],
            "mean": statistics.fmean(values),
            "median": median,
            "mad": statistics.median(deviations),
        }


def _classify(value: str) -> tuple[str, dict[str, Number], list[tuple[datetime, str]]]:
    readings = parse_number_any(value)
    number = readings.get("en") or (next(iter(readings.values())) if readings else None)
    dates = parse_dates(value)
    # An 8-digit run reads as both 20240115 and the number 20,240,115.
    # Prefer the date only when nothing else in the column looks numeric;
    # that decision is made at column level, so report both here.
    if dates:
        # "20130327" is a valid date and a valid number, and nothing about the
        # value alone settles which. Defer to the column.
        if value.strip().isdigit() and len(value.strip()) == 8:
            return COMPACT, readings, dates
        return DATE, readings, dates
    if number is not None:
        return NUMERIC, readings, []
    if parse_bool(value) is not None:
        return BOOLEAN, {}, []
    return TEXT, {}, []


def profile_column(
    name: str, index: int, values: list[str], max_distinct: int = 5000
) -> ColumnProfile:
    profile = ColumnProfile(name=name, index=index, n_rows=len(values))

    kinds: Counter = Counter()
    seen: set[str] = set()
    parsed: list[tuple[int, str, str, Number | None, list[tuple[datetime, str]]]] = []

    for row, raw in enumerate(values):
        if is_null_token(raw):
            profile.n_null += 1
            if not is_blank(raw):
                profile.n_disguised_null += 1
            continue
        kind, readings, dates = _classify(raw)
        kinds[kind] += 1
        if readings:
            evidence = evidence_from(readings)
            if evidence:
                profile.number_evidence[evidence] += 1
        parsed.append((row, raw, kind, readings, dates))
        seen.add(raw)
        if len(profile.value_counts) < max_distinct:
            profile.value_counts[raw] += 1

    # Resolve the deferred 8-digit values now that the column is fully seen: a
    # date only if something else in the column is unambiguously a date,
    # otherwise a number. This is the difference between a YYYYMMDD date column
    # and a column of populations.
    compact = kinds.pop(COMPACT, 0)
    if compact:
        resolved = DATE if kinds.get(DATE) else NUMERIC
        if resolved is NUMERIC:
            profile.unresolved_compact = compact
        kinds[resolved] = kinds.get(resolved, 0) + compact
        parsed = [
            (row, raw, resolved if kind is COMPACT else kind, number, dates)
            for row, raw, kind, number, dates in parsed
        ]

    # Counted from the full set, not from the capped sample. The cap exists to
    # bound how many *frequencies* are tracked; letting it bound the distinct
    # count too made `unique_ratio` read 0.83 for an entirely unique column,
    # which silently switched off the candidate-key check on any file holding
    # more than max_distinct identifiers.
    profile.distinct = len(seen)
    present = len(parsed)
    if present == 0:
        profile.kind = EMPTY
        return profile

    dominant, dominant_n = kinds.most_common(1)[0]
    profile.dominant_kind = dominant
    profile.dominant_share = dominant_n / present

    # A numeric column that happens to contain 8-digit dates is still numeric.
    if dominant == DATE and kinds.get(NUMERIC, 0) > kinds[DATE]:
        dominant = NUMERIC

    if dominant == NUMERIC or (dominant == DATE and kinds.get(NUMERIC)):
        evidence = profile.number_evidence
        english = evidence.get("en", 0)
        european = evidence.get("eu", 0)
        # One decisive row settles the column. "1.234,56" can only be read
        # the European way, and every ambiguous value in the column follows it.
        if european and not english:
            profile.number_convention = "eu"
        elif not english and evidence.get("group-eu") and not evidence.get("group-en"):
            profile.number_convention = "eu"
        for *_, readings, _ in parsed:
            # Fall back to any available reading rather than dropping the
            # value: in a column that mixes both conventions no choice is
            # right, and silently losing rows would be worse than either.
            number = readings.get(profile.number_convention) or (
                next(iter(readings.values())) if readings else None
            )
            if number is not None:
                profile.numbers.append(number.value)
                profile.number_flags |= number.flags

    if dominant == DATE:
        for *_, dates in parsed:
            if not dates:
                continue
            orders = {order for _, order in dates}
            profile.date_orders |= orders
            distinct_days = {dt.date() for dt, _ in dates}
            if len(distinct_days) > 1:
                profile.ambiguous_date_rows += 1
            if orders == {"dmy"}:
                profile.date_evidence["dmy-only"] += 1
            elif orders == {"mdy"}:
                profile.date_evidence["mdy-only"] += 1
            elif orders == {"ymd"}:
                profile.date_evidence["iso"] += 1
            elif len(distinct_days) > 1:
                profile.date_evidence["either"] += 1
            profile.dates.append(dates[0][0])

    if profile.dominant_share == 1.0:
        profile.kind = dominant
    elif profile.dominant_share >= 0.7 and dominant in (NUMERIC, DATE, BOOLEAN):
        profile.kind = dominant
        profile.offenders = [
            (row, raw) for row, raw, kind, _, _ in parsed if kind != dominant
        ]
    elif dominant in (NUMERIC, DATE) and profile.dominant_share >= 0.4:
        profile.kind = MIXED
        profile.offenders = [
            (row, raw) for row, raw, kind, _, _ in parsed if kind != dominant
        ]
    else:
        profile.kind = TEXT

    if profile.kind == BOOLEAN and profile.distinct > 3:
        profile.kind = CATEGORICAL
    # Categorical means "a fixed set of labels". Repetition is the usual
    # evidence, but a short column has no room to repeat itself, so a small
    # distinct count is enough on its own.
    if profile.kind == TEXT and profile.distinct <= 50:
        if profile.distinct <= 20 or profile.unique_ratio < 0.5:
            profile.kind = CATEGORICAL
    return profile


@dataclass
class DateReading:
    """What a date column most likely means, and how sure we can be.

    Binary "ambiguous or not" is the wrong output here. A column where 30 rows
    can only be day-first and 10 could go either way is not ambiguous — it is
    day-first, and the 10 will follow. A column where 1 row is day-first and 1
    is month-first is not ambiguous either; it is corrupt, and no single
    reading will save it. Only the case with no decisive rows at all is truly
    undecidable, and that one deserves a different answer than the others.
    """

    order: str | None
    confidence: float
    label: str  # certain | likely | undecidable | conflicting
    reason: str

    @property
    def usable(self) -> bool:
        return self.order is not None and self.label in ("certain", "likely")


def infer_date_order(profile: ColumnProfile) -> DateReading:
    evidence = profile.date_evidence
    day_first = evidence.get("dmy-only", 0)
    month_first = evidence.get("mdy-only", 0)
    either = evidence.get("either", 0)
    iso = evidence.get("iso", 0)
    decisive = day_first + month_first
    slash = decisive + either

    if slash == 0:
        return DateReading(
            "ymd", 1.0, "certain", f"All {iso:,} dates are unambiguous ISO values."
        )

    if day_first and month_first:
        return DateReading(
            None,
            0.0,
            "conflicting",
            f"{day_first:,} rows can only be day-first and {month_first:,} can only "
            "be month-first, so no single reading fits. The column holds two "
            "formats mixed together and cannot be repaired without knowing which "
            "rows came from where.",
        )

    if not decisive:
        return DateReading(
            None,
            0.0,
            "undecidable",
            f"All {either:,} dates are valid both ways. Nothing in the file favours "
            "either reading, so any choice here would be a guess.",
        )

    order = "dmy" if day_first else "mdy"
    share = decisive / slash
    written = "day-first" if day_first else "month-first"
    reason = (
        f"{decisive:,} of {slash:,} dates can only be read as {written}; the "
        f"remaining {either:,} are valid both ways and would follow that reading."
    )
    if share >= 0.25:
        return DateReading(order, share, "certain", reason)
    return DateReading(
        order,
        share,
        "likely",
        reason + " That is thin evidence — only "
        f"{share:.0%} of the column proves the format.",
    )


@dataclass
class NumberReading:
    """Which decimal convention a column uses, and how sure we can be."""

    convention: str | None
    label: str  # certain | undecidable | conflicting | none
    reason: str

    @property
    def usable(self) -> bool:
        return self.convention is not None and self.label == "certain"


def infer_number_convention(profile: ColumnProfile) -> NumberReading:
    evidence = profile.number_evidence
    english = evidence.get("en", 0)
    european = evidence.get("eu", 0)
    group_en = evidence.get("group-en", 0)
    group_eu = evidence.get("group-eu", 0)
    ambiguous = evidence.get("ambiguous", 0)

    if english and european:
        return NumberReading(
            None,
            "conflicting",
            f"{plural(english, 'value')} can only be read the English way and "
            f"{european:,} only the European way, so no single convention fits "
            "the column. It holds two number formats mixed together.",
        )

    if english or european:
        convention = "eu" if european else "en"
        written = "European" if european else "English"
        decisive = european or english
        undecided = group_en + group_eu + ambiguous
        reason = f"{plural(decisive, 'value')} can only be read the {written} way"
        if undecided:
            reason += f", which settles the {undecided:,} that could go either way"
        return NumberReading(convention, "certain", reason + ".")

    if group_en and group_eu:
        return NumberReading(
            None,
            "conflicting",
            f"{plural(group_en, 'value')} group thousands with a comma and "
            f"{group_eu:,} group with a dot. The column mixes both conventions, "
            "so one of the two is being misread by a factor of a thousand.",
        )

    if group_eu:
        return NumberReading(
            "eu",
            "certain",
            f"{plural(group_eu, 'value')} group thousands with a dot, which is "
            "European convention. Read the English way they lose a factor of a "
            "thousand.",
        )
    if group_en:
        return NumberReading("en", "certain", "Thousands are grouped with a comma.")

    if ambiguous:
        return NumberReading(
            None,
            "undecidable",
            f"{plural(ambiguous, 'value')} like \"1.234\" mean one thing under "
            "English convention and a thousand times more under European "
            "convention, and nothing else in the column settles which was meant.",
        )
    return NumberReading("en", "none", "Nothing in the column is ambiguous.")
