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
    is_blank,
    is_null_token,
    parse_bool,
    parse_dates,
    parse_number,
)

# Kinds a column can be given. "mixed" means no single kind explains it.
NUMERIC = "numeric"
DATE = "date"
BOOLEAN = "boolean"
CATEGORICAL = "categorical"
TEXT = "text"
EMPTY = "empty"
MIXED = "mixed"


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


def _classify(value: str) -> tuple[str, Number | None, list[tuple[datetime, str]]]:
    number = parse_number(value)
    dates = parse_dates(value)
    if dates:
        return DATE, number, dates
    if number is not None:
        return NUMERIC, number, []
    if parse_bool(value) is not None:
        return BOOLEAN, None, []
    return TEXT, None, []


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
        kind, number, dates = _classify(raw)
        kinds[kind] += 1
        parsed.append((row, raw, kind, number, dates))
        if len(profile.value_counts) < max_distinct:
            profile.value_counts[raw] += 1

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
        for _, _, kind, number, _ in parsed:
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
