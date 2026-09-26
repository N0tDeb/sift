"""Blast radius: what the file does to one specific number.

A finding says a column has a problem. That is a claim about a file, and the
person reading it still has to work out whether they care. This module answers
the question they actually have — "how wrong is my total?" — by computing the
aggregate three ways and showing where they diverge:

  as it loads     what a typed reader gets from the raw file today
  coerced         the same, with unparseable values dropped, which is what
                  `pd.to_numeric(errors="coerce")` and most SQL loaders do
  repaired        the same aggregate over the file `sift fix` would produce

The gap between the second and third is the money. Nothing here is a heuristic:
these are three real computations over the same file, and the difference
between them is arithmetic, not judgement.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .config import Config
from .inference import is_blank, parse_number
from .loading import Table
from .repair import MEDIUM, plan_repairs
from .text import fmt_number, plural, terminal_safe


class ImpactError(Exception):
    """The requested aggregate cannot be computed at all."""


@dataclass
class Cause:
    """One reason two of the totals disagree, in the units of the aggregate."""

    label: str
    rows: int
    amount: float


@dataclass
class SumImpact:
    column: str
    loads_as_number: bool
    coerced_total: float
    coerced_rows: int
    repaired_total: float
    repaired_rows: int
    total_rows: int
    causes: list[Cause] = field(default_factory=list)

    @property
    def difference(self) -> float:
        return self.repaired_total - self.coerced_total

    @property
    def relative(self) -> float | None:
        if self.repaired_total == 0:
            return None
        return self.difference / abs(self.repaired_total)


@dataclass
class GroupImpact:
    column: str
    measure: str | None
    groups_now: int
    groups_after: int
    merges: list[tuple[str, list[str], float]] = field(default_factory=list)
    misplaced_amount: float = 0.0
    total_amount: float = 0.0

    @property
    def misplaced_share(self) -> float | None:
        if not self.total_amount:
            return None
        return self.misplaced_amount / abs(self.total_amount)


@dataclass
class DuplicateImpact:
    extra_rows: int
    inflated_amount: float
    measure: str | None


def _values(table: Table, name: str) -> list[str]:
    column = table.by_key(name)
    if column is None:
        raise ImpactError(f"No column named {name!r} in {table.path}.")
    return list(column.values)


def _coerce(values: list[str]) -> tuple[list[float], int]:
    """What a typed loader keeps. `float()` is deliberately strict here: it is
    exactly as strict as the loader whose behaviour we are predicting, so
    `$1,200` is dropped rather than helpfully understood."""
    kept: list[float] = []
    dropped = 0
    for value in values:
        if is_blank(value):
            continue
        try:
            kept.append(float(value))
        except ValueError:
            dropped += 1
    return kept, dropped


def _repaired_values(table: Table, config: Config, name: str) -> list[str]:
    _, rows = plan_repairs(table, config, MEDIUM)
    index = next(i for i, column in enumerate(table.columns) if column.name == name)
    return [row[index] for row in rows]


def sum_impact(table: Table, config: Config, column: str) -> SumImpact:
    raw = _values(table, column)
    repaired = _repaired_values(table, config, column)

    coerced, dropped = _coerce(raw)
    loads_as_number = dropped == 0

    after: list[float] = []
    for value in repaired:
        parsed = parse_number(value)
        if parsed is not None:
            after.append(parsed.value)

    impact = SumImpact(
        column=column,
        loads_as_number=loads_as_number,
        coerced_total=sum(coerced),
        coerced_rows=len(coerced),
        repaired_total=sum(after),
        repaired_rows=len(after),
        total_rows=len(raw),
    )

    # Attribute the gap. Each raw value falls into exactly one bucket, so the
    # causes add up to the difference rather than merely gesturing at it.
    recovered = Cause("formatted numbers recovered that a loader drops", 0, 0.0)
    removed = Cause("placeholder values removed that a loader counts as real", 0, 0.0)
    still_lost = Cause("values that remain unparseable", 0, 0.0)

    for original, fixed in zip(raw, repaired, strict=True):
        raw_ok = True
        raw_value = 0.0
        try:
            raw_value = float(original)
        except ValueError:
            raw_ok = False
        if is_blank(original):
            raw_ok, raw_value = True, 0.0

        fixed_parsed = parse_number(fixed) if not is_blank(fixed) else None
        fixed_value = fixed_parsed.value if fixed_parsed else None

        if not raw_ok and fixed_value is not None:
            recovered.rows += 1
            recovered.amount += fixed_value
        elif raw_ok and fixed_value is None and raw_value:
            removed.rows += 1
            removed.amount -= raw_value
        elif not raw_ok and fixed_value is None and not is_blank(original):
            still_lost.rows += 1

    impact.causes = [cause for cause in (recovered, removed, still_lost) if cause.rows]
    return impact


def group_impact(
    table: Table, config: Config, column: str, measure: str | None = None
) -> GroupImpact:
    raw = _values(table, column)
    repaired = _repaired_values(table, config, column)

    weights: list[float] = []
    if measure:
        for value in _repaired_values(table, config, measure):
            parsed = parse_number(value) if not is_blank(value) else None
            weights.append(parsed.value if parsed else 0.0)
    else:
        weights = [1.0] * len(raw)

    now = Counter(value for value in raw if not is_blank(value))
    after = Counter(value for value in repaired if not is_blank(value))

    # Which raw spellings were folded into which surviving label, and how much
    # of the measure was sitting under the wrong one.
    folded: dict[str, dict[str, float]] = {}
    misplaced = 0.0
    for original, fixed, weight in zip(raw, repaired, weights, strict=True):
        if original != fixed and not is_blank(fixed):
            folded.setdefault(fixed, {}).setdefault(original, 0.0)
            folded[fixed][original] += weight
            misplaced += weight

    merges = [
        (canonical, sorted(sources), sum(sources.values()))
        for canonical, sources in folded.items()
    ]
    merges.sort(key=lambda item: -item[2])

    return GroupImpact(
        column=column,
        measure=measure,
        groups_now=len(now),
        groups_after=len(after),
        merges=merges,
        misplaced_amount=misplaced,
        total_amount=sum(weights),
    )


def duplicate_impact(table: Table, config: Config, measure: str | None) -> DuplicateImpact:
    rows = list(zip(*[column.values for column in table.columns], strict=True))
    counts = Counter(rows)
    extra_indexes = []
    seen: set[tuple[str, ...]] = set()
    for index, row in enumerate(rows):
        if row in seen and counts[row] > 1:
            extra_indexes.append(index)
        seen.add(row)

    amount = 0.0
    if measure and extra_indexes:
        values = _repaired_values(table, config, measure)
        for index in extra_indexes:
            parsed = parse_number(values[index]) if not is_blank(values[index]) else None
            if parsed:
                amount += parsed.value
    return DuplicateImpact(len(extra_indexes), amount, measure)


def render(
    table: Table,
    total: SumImpact | None,
    groups: GroupImpact | None,
    duplicates: DuplicateImpact | None,
) -> str:
    lines = [f"{terminal_safe(table.path)}  {plural(table.n_rows, 'row')}", ""]

    if total:
        lines.append(f"SUM({terminal_safe(total.column)})")
        if total.loads_as_number:
            lines.append(
                f"  as it loads      {fmt_number(total.coerced_total):>16}"
                f"   from {total.coerced_rows:,} of {total.total_rows:,} rows"
            )
        else:
            lines.append(
                "  as it loads         not a number — the column reads as text, so a "
                "loader drops"
            )
            lines.append(
                f"                      every value it cannot parse: "
                f"{total.coerced_rows:,} of {total.total_rows:,} rows survive."
            )
            lines.append(
                f"  coerced          {fmt_number(total.coerced_total):>16}"
                f"   what errors='coerce' would give you"
            )
        lines.append(
            f"  after sift fix   {fmt_number(total.repaired_total):>16}"
            f"   from {total.repaired_rows:,} of {total.total_rows:,} rows"
        )
        relative = total.relative
        share = f" ({relative:+.1%})" if relative is not None else ""
        lines.append(
            f"  difference       {fmt_number(total.difference):>16}{share}"
        )
        if total.causes:
            lines.append("")
            for cause in total.causes:
                # Signed, because the causes are a decomposition of the
                # difference and the reader should be able to add them up.
                amount = f"  {cause.amount:+,.2f}" if cause.amount else ""
                lines.append(f"    {plural(cause.rows, 'row'):>9}  {cause.label}{amount}")
        lines.append("")

    if groups:
        measure = f", SUM({terminal_safe(groups.measure)})" if groups.measure else ""
        lines.append(f"GROUP BY {terminal_safe(groups.column)}{measure}")
        lines.append(
            f"  {groups.groups_now:,} groups today, {groups.groups_after:,} after "
            "repair."
        )
        if groups.merges:
            share = groups.misplaced_share
            unit = "of the total" if groups.measure else "of rows"
            suffix = f" ({share:.1%} {unit})" if share is not None else ""
            lines.append(
                f"  {fmt_number(groups.misplaced_amount)} sits under a label that is a "
                f"spelling of another{suffix}:"
            )
            for canonical, sources, amount in groups.merges[:5]:
                spellings = ", ".join(terminal_safe(repr(source)) for source in sources)
                lines.append(
                    f"    {spellings} belongs to {terminal_safe(repr(canonical))}  "
                    f"{fmt_number(amount)}"
                )
        lines.append("")

    if duplicates and duplicates.extra_rows:
        lines.append("DUPLICATE ROWS")
        detail = (
            f", inflating SUM({terminal_safe(duplicates.measure)}) by "
            f"{fmt_number(duplicates.inflated_amount)}"
            if duplicates.measure
            else ""
        )
        lines.append(
            f"  {plural(duplicates.extra_rows, 'row')} repeated exactly{detail}."
        )
        lines.append("")

    return "\n".join(lines).rstrip()
