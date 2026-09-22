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
    parse_number,
    parse_number_any,
)

# Kinds a column can be given. "mixed" means no single kind explains it.
NUMERIC = "numeric"
DATE = "date"
BOOLEAN = "boolean"
CATEGORICAL = "categorical"
TEXT = "text"
EMPTY = "empty"
MIXED = "mixed"
# Internal only: a bare 8-digit run, equally a plausible YYYYMMDD and a
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

    numbers: list[float] = field(default_factory=list)
    number_flags: set[str] = field(default_factory=set)
    number_convention: str = "en"
    number_evidence: Counter = field(default_factory=Counter)
    dates: list[datetime] = field(default_factory=list)
    date_orders: set[str] = field(default_factory=set)
    ambiguous_date_rows: int = 0

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
    if dates:
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
        if len(profile.value_counts) < max_distinct:
            profile.value_counts[raw] += 1

    # Resolve deferred 8-digit values now the column is fully seen: a date
    # only if something else in the column is unambiguously a date,
    # otherwise a number.
    compact = kinds.pop(COMPACT, 0)
    if compact:
        resolved = DATE if kinds.get(DATE) else NUMERIC
        kinds[resolved] = kinds.get(resolved, 0) + compact
        parsed = [
            (row, raw, resolved if kind is COMPACT else kind, number, dates)
            for row, raw, kind, number, dates in parsed
        ]

    profile.distinct = len(profile.value_counts)
    present = len(parsed)
    if present == 0:
        profile.kind = EMPTY
        return profile

    dominant, dominant_n = kinds.most_common(1)[0]
    profile.dominant_kind = dominant
    profile.dominant_share = dominant_n / present

    if dominant == DATE and kinds.get(NUMERIC, 0) > kinds[DATE]:
        dominant = NUMERIC

    if dominant == NUMERIC or (dominant == DATE and kinds.get(NUMERIC)):
        evidence = profile.number_evidence
        english, european = evidence.get("en", 0), evidence.get("eu", 0)
        if european and not english:
            profile.number_convention = "eu"
        elif not english and evidence.get("group-eu") and not evidence.get("group-en"):
            profile.number_convention = "eu"
        for _, _, kind, readings, _ in parsed:
            number = readings.get(profile.number_convention) or (
                next(iter(readings.values())) if readings else None
            )
            if number is not None:
                profile.numbers.append(number.value)
                profile.number_flags |= number.flags

    if dominant == DATE:
        for _, _, kind, _, dates in parsed:
            if not dates:
                continue
            orders = {order for _, order in dates}
            profile.date_orders |= orders
            distinct_days = {dt.date() for dt, _ in dates}
            if len(distinct_days) > 1:
                profile.ambiguous_date_rows += 1
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
    if profile.kind == TEXT and profile.distinct <= 50 and profile.unique_ratio < 0.5:
        profile.kind = CATEGORICAL
    return profile


@dataclass
class NumberReading:
    convention: str | None
    label: str
    reason: str

    @property
    def usable(self) -> bool:
        return self.convention is not None and self.label == "certain"


def infer_number_convention(profile: ColumnProfile) -> NumberReading:
    evidence = profile.number_evidence
    english, european = evidence.get("en", 0), evidence.get("eu", 0)
    group_en, group_eu = evidence.get("group-en", 0), evidence.get("group-eu", 0)
    ambiguous = evidence.get("ambiguous", 0)

    if english and european:
        return NumberReading(
            None, "conflicting",
            f"{english} value(s) can only be read the English way and {european} "
            "only the European way, so no single convention fits the column.",
        )
    if english or european:
        convention = "eu" if european else "en"
        written = "European" if european else "English"
        return NumberReading(
            convention, "certain",
            f"{european or english} value(s) can only be read the {written} way.",
        )
    if group_en and group_eu:
        return NumberReading(None, "conflicting", "The column mixes both grouping conventions.")
    if group_eu:
        return NumberReading("eu", "certain", "Thousands are grouped with a dot.")
    if group_en:
        return NumberReading("en", "certain", "Thousands are grouped with a comma.")
    if ambiguous:
        return NumberReading(
            None, "undecidable",
            f"{ambiguous} value(s) read differently under each convention and "
            "nothing settles which was meant.",
        )
    return NumberReading("en", "none", "Nothing in the column is ambiguous.")
