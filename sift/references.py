"""Checking that a value in one file exists in another.

This is the one defect that cannot be seen from inside a single file. An
`orders.csv` where every row looks perfect is still broken if a thousand of
its `customer_id` values name customers who were deleted last quarter. The
file is internally consistent and externally wrong.

What makes it worth catching is how the failure presents. An inner join drops
the orphans silently — no error, just a smaller result — so the report comes
out looking fine and merely understates revenue. A left join keeps them with
nulls in every joined column, which then reads as missing data in some
downstream column rather than as a broken relationship here.

No schema is required. The relationship is stated on the command line, which
is the point: declaring one relationship you care about should not require
adopting a schema language for the whole file.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .findings import Finding, Severity
from .inference import is_blank, normalize_label
from .loading import Table
from .text import plural


class ReferenceError(Exception):
    """The reference itself is malformed or unusable."""


@dataclass
class Reference:
    """`orders.customer_id` must exist in `customers.id`."""

    local: str
    path: Path
    foreign: str

    @classmethod
    def parse(cls, spec: str) -> Reference:
        """Parse `customers.csv:customer_id=id`.

        The local column is on the left of `=` because that is the one in the
        file being checked, which is the file the reader is thinking about.
        """
        file_part, _, columns = spec.partition(":")
        if not columns:
            raise ReferenceError(
                f"Malformed reference {spec!r}. Expected FILE:LOCAL=FOREIGN, "
                "for example customers.csv:customer_id=id"
            )
        local, _, foreign = columns.partition("=")
        if not local or not foreign:
            raise ReferenceError(
                f"Malformed reference {spec!r}. Expected FILE:LOCAL=FOREIGN, "
                "for example customers.csv:customer_id=id"
            )
        return cls(local=local.strip(), path=Path(file_part), foreign=foreign.strip())


def check_reference(
    table: Table, reference: Reference, config: Config, load
) -> list[Finding]:
    """Check one relationship. `load` is injected so this module does not
    care which format the other side is in."""
    findings: list[Finding] = []

    local = table.by_key(reference.local)
    if local is None:
        raise ReferenceError(
            f"{table.path.name} has no column {reference.local!r}."
        )

    other = load(reference.path)
    foreign = other.by_key(reference.foreign)
    if foreign is None:
        raise ReferenceError(
            f"{reference.path.name} has no column {reference.foreign!r}."
        )

    known = {value for value in foreign.values if not is_blank(value)}

    # A key that repeats on the far side is not a broken reference, but it is
    # why a join multiplies rows, and it is worth saying before someone spends
    # an afternoon on it.
    counts = Counter(value for value in foreign.values if not is_blank(value))
    repeats = {value: n for value, n in counts.items() if n > 1}
    if repeats:
        findings.append(
            Finding(
                "reference-not-unique",
                Severity.ERROR,
                f"{reference.foreign!r} repeats {plural(len(repeats), 'value')} in "
                f"{reference.path.name}, so it cannot identify a row. Joining on it "
                "multiplies rows rather than matching them.",
                column=reference.local,
                detail={"column_index": local.index, "repeated": len(repeats)},
                examples=list(repeats)[: config.max_examples],
            )
        )

    orphans = Counter()
    blanks = 0
    for value in local.values:
        if is_blank(value):
            blanks += 1
            continue
        if value not in known:
            orphans[value] += 1

    if orphans:
        rows = sum(orphans.values())
        findings.append(
            Finding(
                "orphaned-reference",
                Severity.ERROR,
                f"{plural(rows, 'row')} in {reference.local!r} reference a "
                f"{reference.foreign} that does not exist in {reference.path.name} "
                f"({plural(len(orphans), 'distinct value')}). An inner join drops "
                "those rows without saying so; a left join keeps them with nulls "
                "in every joined column.",
                column=reference.local,
                detail={
                    "column_index": local.index,
                    "rows": rows,
                    "distinct": len(orphans),
                    "against": str(reference.path),
                },
                examples=[value for value, _ in orphans.most_common(config.max_examples)],
            )
        )

        # A reference that matches only after normalising is a formatting
        # problem wearing a referential-integrity costume, and the fix is
        # completely different: clean the values, do not chase missing rows.
        folded = {normalize_label(value) for value in known}
        recoverable = [v for v in orphans if normalize_label(v) in folded]
        if recoverable:
            findings.append(
                Finding(
                    "reference-matches-after-cleaning",
                    Severity.WARNING,
                    f"{plural(len(recoverable), 'orphaned value')} would match "
                    f"{reference.path.name} if case and spacing were normalised. "
                    "These are not missing records, they are dirty keys.",
                    column=reference.local,
                    detail={"column_index": local.index, "count": len(recoverable)},
                    examples=recoverable[: config.max_examples],
                )
            )

    if blanks:
        findings.append(
            Finding(
                "reference-null",
                Severity.WARNING,
                f"{reference.local!r} is empty in {plural(blanks, 'row')}, so those "
                "rows join to nothing regardless of what is in "
                f"{reference.path.name}.",
                column=reference.local,
                detail={"column_index": local.index, "count": blanks},
            )
        )

    return config.filter(findings)
