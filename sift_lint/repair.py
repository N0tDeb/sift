"""Repairing what can be repaired, and refusing the rest out loud.

Every tool in this space validates. Almost none of them fix, and the reason is
good: an automatic cleaner that guesses wrong is worse than no cleaner, because
it destroys the evidence that anything was ever wrong.

So the rule here is that a repair must be *derivable from the file itself*.
Stripping a currency marker is safe only when the column also establishes its
number convention. A value like `$1,200` alone can be ambiguous. Choosing
between day-first and month-first when
nothing in the column proves either is not derivable, and Sift will not do it
at any confidence setting. Refusals are output, not silence: the report lists
what was left alone and why, so the person knows exactly which problems they
still own.

Each changed cell and deleted duplicate row can be written to an optional
audit log with its responsible rule. Before/after values are preserved except
for columns identified as sensitive, whose values are redacted. A
cleaned file you cannot account for against the original is just a different
unverified file.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .inference import (
    CONVENTIONS,
    NUMERIC_SENTINELS,
    has_mojibake,
    is_blank,
    is_disguised_null,
    is_formula_injection,
    normalize_label,
    parse_dates,
    parse_number,
)
from .loading import Column, Table
from .output import atomic_text_writer
from .profiling import (
    CATEGORICAL,
    DATE,
    MIXED,
    NUMERIC,
    TEXT,
    infer_date_order,
    infer_number_convention,
)
from .text import plural, terminal_safe

HIGH = "high"
MEDIUM = "medium"
RANK = {HIGH: 2, MEDIUM: 1}
REDACTED_SENSITIVE_VALUE = "[redacted sensitive value]"

CURRENCY = "$\u20ac\u00a3\u00a5"


@dataclass
class Change:
    """One cell, rewritten. The unit of the audit log."""

    line: int  # line number in the source file, so it can be found again
    column: str
    before: str
    after: str
    rule: str
    confidence: str
    column_key: str | None = None  # unique identity when headers repeat


@dataclass
class Refusal:
    """Something Sift could have touched and deliberately did not."""

    column: str | None
    rule: str
    reason: str


@dataclass
class DroppedRow:
    """One deleted source record and the earlier record it duplicated."""

    line: int
    duplicate_of_line: int
    original_values: tuple[str, ...]
    matched_after_normalization: bool


@dataclass
class RepairPlan:
    changes: list[Change] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    dropped_lines: list[int] = field(default_factory=list)
    dropped_rows: list[DroppedRow] = field(default_factory=list)

    def by_rule(self) -> dict[str, list[Change]]:
        grouped: dict[str, list[Change]] = defaultdict(list)
        for change in self.changes:
            grouped[change.rule].append(change)
        return dict(grouped)


def _line(row: int, column: Column | None = None) -> int:
    if column is not None and row < len(column.source_lines):
        return column.source_lines[row]
    return row + 2  # Fallback for formats without physical CSV row locations.


def plain_number(raw: str, convention: str = "en") -> str | None:
    """Strip presentation from a number without going through a float.

    Reformatting via `float()` would round `0.1 + 0.2` style values and change
    how many decimals the file had. Editing the text keeps the value the author
    wrote; the parse is only used to confirm the edit was safe.
    """
    parsed = parse_number(raw, convention)
    if parsed is None:
        return None
    thousands, decimal = CONVENTIONS[convention]
    text = raw.strip()
    negative = text.startswith("(") and text.endswith(")")
    body = text.strip("()").strip()
    for symbol in CURRENCY:
        body = body.replace(symbol, "")
    body = body.replace(thousands, "").replace("\u00a0", "").strip()
    if decimal != ".":
        # "1.234,56" becomes "1234.56": strip the grouping, then move the
        # decimal mark to the one every other tool expects.
        body = body.replace(decimal, ".")
    if negative:
        body = "-" + body.lstrip("+-")
    try:
        if float(body) != parsed.value:
            return None  # the edit changed the value, so do not make it
    except ValueError:
        return None
    return body if body != raw else None


# --- rules -----------------------------------------------------------------
# Each rule inspects one column and appends to the plan. Rules run in the order
# listed in RULES: whitespace first, so later rules see tidy values.


def rule_trim_whitespace(column: Column, values: list[str], plan: RepairPlan) -> None:
    for row, value in enumerate(values):
        cleaned = value.replace("\u00a0", " ").strip()
        if cleaned != value:
            plan.changes.append(
                Change(_line(row, column), column.name, value, cleaned, "trim-whitespace", HIGH)
            )
            values[row] = cleaned


def rule_collapse_spaces(column: Column, values: list[str], plan: RepairPlan) -> None:
    for row, value in enumerate(values):
        cleaned = " ".join(value.split())
        if cleaned != value:
            plan.changes.append(
                Change(_line(row, column), column.name, value, cleaned, "collapse-spaces", MEDIUM)
            )
            values[row] = cleaned


def rule_blank_null(column: Column, values: list[str], plan: RepairPlan) -> None:
    for row, value in enumerate(values):
        if is_disguised_null(value):
            plan.changes.append(
                Change(_line(row, column), column.name, value, "", "blank-null", HIGH)
            )
            values[row] = ""


def rule_plain_number(column: Column, values: list[str], plan: RepairPlan) -> None:
    profile = column.profile
    if profile.kind != NUMERIC:
        return

    # Decide the convention before anything else. A column nothing can settle
    # must produce a stated refusal, not silence — "nothing to change" would
    # read as "nothing wrong", which is the opposite of the truth.
    reading = infer_number_convention(profile)
    if reading.label in ("undecidable", "conflicting"):
        plan.refusals.append(Refusal(column.name, "plain-number", reading.reason))
        return

    convention = profile.number_convention
    if not profile.number_flags and convention == "en":
        return
    if "percent" in profile.number_flags:
        plan.refusals.append(
            Refusal(
                column.name,
                "plain-number",
                "Values are written as percentages. Whether 12% should become 12 "
                "or 0.12 depends on what reads the column, and the file does not "
                "say. Decide, then convert deliberately.",
            )
        )
        return
    for row, value in enumerate(values):
        if is_blank(value):
            continue
        cleaned = plain_number(value, convention)
        if cleaned is not None:
            plan.changes.append(
                Change(_line(row, column), column.name, value, cleaned, "plain-number", HIGH)
            )
            values[row] = cleaned


def rule_iso_date(column: Column, values: list[str], plan: RepairPlan) -> None:
    profile = column.profile
    if profile.kind != DATE:
        return
    reading = infer_date_order(profile)
    if not reading.usable:
        plan.refusals.append(
            Refusal(column.name, "iso-date", reading.reason)
        )
        return
    if reading.order == "ymd" and not profile.date_evidence.get("either"):
        return  # already ISO throughout

    confidence = HIGH if reading.label == "certain" else MEDIUM
    for row, value in enumerate(values):
        if is_blank(value):
            continue
        readings = parse_dates(value)
        if not readings:
            continue
        chosen = next(
            (dt for dt, order in readings if order == reading.order),
            readings[0][0] if len({o for _, o in readings}) == 1 else None,
        )
        if chosen is None:
            continue
        iso = chosen.strftime("%Y-%m-%d")
        if iso != value:
            plan.changes.append(
                Change(_line(row, column), column.name, value, iso, "iso-date", confidence)
            )
            values[row] = iso


def rule_canonical_label(column: Column, values: list[str], plan: RepairPlan) -> None:
    if column.profile.kind not in (CATEGORICAL, TEXT):
        return
    groups: dict[str, Counter] = defaultdict(Counter)
    for value in values:
        if not is_blank(value):
            groups[normalize_label(value)][value] += 1

    def quality(spelling: str, count: int) -> tuple:
        # Capitalisation is presentation, not data: "bluefin ltd" appearing
        # more often than "Bluefin Ltd" is evidence about how tired the person
        # entering it was, not about which spelling is correct. So the
        # well-formed spelling wins and frequency only breaks ties.
        words = [w for w in spelling.split() if w[:1].isalpha()]
        capitalised = sum(1 for w in words if w[:1].isupper()) / len(words) if words else 0
        return (-capitalised, -count, len(spelling))

    canonical: dict[str, str] = {}
    for spellings in groups.values():
        if len(spellings) < 2:
            continue
        best = sorted(spellings.items(), key=lambda kv: quality(*kv))[0][0]
        for spelling in spellings:
            if spelling != best:
                canonical[spelling] = best

    for row, value in enumerate(values):
        if value in canonical:
            plan.changes.append(
                Change(
                    _line(row, column),
                    column.name,
                    value,
                    canonical[value],
                    "canonical-label",
                    MEDIUM,
                )
            )
            values[row] = canonical[value]


def rule_sentinel_null(column: Column, values: list[str], plan: RepairPlan) -> None:
    profile = column.profile
    if profile.kind != NUMERIC or not profile.numbers:
        return
    hits = Counter(v for v in profile.numbers if v in NUMERIC_SENTINELS)
    targets = {
        value for value, count in hits.items() if count >= max(2, 0.01 * len(profile.numbers))
    }
    if not targets:
        return
    for row, value in enumerate(values):
        parsed = parse_number(value)
        if parsed is not None and parsed.value in targets:
            plan.changes.append(
                Change(_line(row, column), column.name, value, "", "sentinel-null", MEDIUM)
            )
            values[row] = ""


def rule_refuse_unfixable(column: Column, values: list[str], plan: RepairPlan) -> None:
    """Things a fixer could plausibly attack, and shouldn't."""
    profile = column.profile
    if any(has_mojibake(value) for value in values):
        plan.refusals.append(
            Refusal(
                column.name,
                "mojibake",
                "Characters were mangled by a bad encoding round-trip. The "
                "original bytes are gone, so any repair here is invention. "
                "Re-export the source as UTF-8.",
            )
        )
    if profile.offenders and profile.kind in (NUMERIC, DATE, MIXED):
        examples = ", ".join(repr(raw) for _, raw in profile.offenders[:3])
        plan.refusals.append(
            Refusal(
                column.name,
                "mixed-types",
                f"{plural(len(profile.offenders), 'value')} "
                f"{'does' if len(profile.offenders) == 1 else 'do'} not parse as "
                f"{profile.dominant_kind} ({examples}). Whether those rows are "
                "typos, notes, or a second unit is a question about the world, "
                "not about the file.",
            )
        )


RULES = [
    rule_refuse_unfixable,
    rule_trim_whitespace,
    rule_collapse_spaces,
    rule_blank_null,
    rule_plain_number,
    rule_iso_date,
    rule_canonical_label,
    rule_sentinel_null,
]


def plan_repairs(
    table: Table,
    config: Config,
    min_confidence: str = HIGH,
    drop_duplicates: bool = False,
) -> tuple[RepairPlan, list[list[str]]]:
    """Work out every change, apply it to a copy, return both.

    The plan and the rewritten rows come back together because a repair is only
    trustworthy if you can see exactly what it did.
    """
    floor = RANK[min_confidence]
    plan = RepairPlan()
    grid = [list(column.values) for column in table.columns]
    # Privacy flags also indicate columns whose exact bytes can carry semantic
    # meaning (e.g., passwords with leading spaces). These are read-only by
    # default, irrespective of the requested repair confidence.
    from .checks import sensitive_columns

    private = sensitive_columns(table)

    for index, column in enumerate(table.columns):
        # Protect even predominantly numeric or date columns. A lone formula
        # among numeric values must not become executable after whitespace
        # cleanup or normalization. The value-level detector exempts genuine
        # signed numbers without trusting column-wide type inference.
        risky = any(is_formula_injection(value) for value in grid[index])
        if risky:
            # Preserve the existing non-mutating refusal checks for this column
            # even though no repair rule is allowed to rewrite it.
            rule_refuse_unfixable(column, grid[index], plan)
            plan.refusals.append(
                Refusal(
                    column.name,
                    "formula-injection",
                    "This column contains a formula-like value (=, +, -, or @, "
                    "possibly after whitespace), which spreadsheet software "
                    "may execute as a formula. Sift "
                    "will not rewrite the column because trimming or normalising "
                    "it could make that content easier to execute. Neutralise "
                    "the formula deliberately, then run fix again.",
                )
            )
            continue

        if column.name in private:
            rule_refuse_unfixable(column, grid[index], plan)
            candidate = RepairPlan()
            working = list(grid[index])
            for rule in RULES[1:]:
                rule(column, working, candidate)
            if any(RANK[change.confidence] >= floor for change in candidate.changes):
                plan.refusals.append(
                    Refusal(
                        column.name,
                        "sensitive-value-preserved",
                        "Sensitive or opaque column values are preserved exactly. "
                        "Review them manually; automatic normalization may "
                        "change their meaning.",
                    )
                )
            continue

        for rule in RULES:
            mark = len(plan.changes)
            working = list(grid[index])
            rule(column, working, plan)
            proposed = plan.changes[mark:]
            # A rule applies to a whole column or not at all. Half-applying one
            # would leave the column in a state that is neither the original
            # nor the repair, which is the worst of the three.
            if proposed and RANK[proposed[0].confidence] < floor:
                del plan.changes[mark:]
            else:
                grid[index] = working
                for change in proposed:
                    change.column_key = column.key

    rows = [list(values) for values in zip(*grid, strict=True)] if grid else []

    if drop_duplicates:
        # Retain source-row identity even after normalizations create a
        # duplicate. Audit entries are recorded for the rows actually removed,
        # not merely for the first row of a duplicate group.
        seen: dict[tuple[str, ...], int] = {}
        kept_rows = []
        for row_index, row in enumerate(rows):
            key = tuple(row)
            original = tuple(column.values[row_index] for column in table.columns)
            line = (
                table.source_lines[row_index] if row_index < len(table.source_lines)
                else _line(row_index)
            )
            if key in seen:
                first_index = seen[key]
                first_original = tuple(
                    column.values[first_index] for column in table.columns
                )
                first_line = (
                    table.source_lines[first_index]
                    if first_index < len(table.source_lines) else _line(first_index)
                )
                plan.dropped_lines.append(line)
                plan.dropped_rows.append(
                    DroppedRow(line, first_line, original, original != first_original)
                )
                continue
            seen[key] = row_index
            kept_rows.append(row)
        rows = kept_rows
    return plan, rows


def write_csv(path: Path, header: list[str], rows: list[list[str]], delimiter: str = ",") -> None:
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _safe_log_value(value: str) -> str:
    """Avoid turning a data-change log into a spreadsheet formula payload.

    `json:` is an explicit, reversible encoding for potentially executable
    values; that prefix is itself escaped to keep decoding unambiguous.
    """
    if is_formula_injection(value) or value.startswith("json:"):
        return "json:" + json.dumps(value, ensure_ascii=False)
    return value


def _source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_log(
    path: Path,
    plan: RepairPlan,
    sensitive_columns: set[str] | None = None,
    *,
    source_path: Path | None = None,
    header: list[str] | None = None,
) -> None:
    """Write cell changes and row deletions with the original 6-column header.

    Source and row deletion entries have distinct rule names. To reconstruct
    source values, retain the original file; redacted log values are deliberate.
    """
    sensitive = sensitive_columns or set()
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["line", "column", "rule", "confidence", "before", "after"])
        if source_path is not None:
            writer.writerow([
                0, "[source]", "source-sha256", "high", _source_digest(source_path), ""
            ])
        for change in plan.changes:
            before = change.before
            after = change.after
            if change.column in sensitive:
                before = REDACTED_SENSITIVE_VALUE
                after = REDACTED_SENSITIVE_VALUE
            writer.writerow(
                [
                    change.line,
                    _safe_log_value(change.column_key or change.column),
                    change.rule,
                    change.confidence,
                    _safe_log_value(before),
                    _safe_log_value(after),
                ]
            )
        for dropped in plan.dropped_rows:
            # Positional values are important: duplicate column names cannot
            # safely be represented by a dict keyed by header text.
            values = [
                REDACTED_SENSITIVE_VALUE
                if (header is None and sensitive)
                or (header is not None and index < len(header) and header[index] in sensitive)
                else value
                for index, value in enumerate(dropped.original_values)
            ]
            snapshot = json.dumps(
                {
                    "values": values,
                    "duplicate_of_line": dropped.duplicate_of_line,
                    "matched_after_normalization": dropped.matched_after_normalization,
                },
                ensure_ascii=False,
            )
            writer.writerow([
                dropped.line, "[row]", "drop-duplicate-row", "high", snapshot, "",
            ])


def render_plan(
    table: Table,
    plan: RepairPlan,
    destination: str,
    sensitive_columns: set[str] | None = None,
) -> str:
    if sensitive_columns is None:
        # The standalone formatter should not leak secrets when callers omit
        # the optional privacy argument. The CLI passes this explicitly.
        from .checks import sensitive_columns as detect_sensitive_columns

        sensitive_columns = detect_sensitive_columns(table)
    lines = [f"{terminal_safe(table.path)} to {terminal_safe(destination)}", ""]
    grouped = plan.by_rule()
    if not grouped and not plan.dropped_lines:
        lines.append("Nothing to change.")
    else:
        total = len(plan.changes)
        lines.append(f"{plural(total, 'cell')} changed")
        for rule, changes in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            columns = sorted({change.column for change in changes})
            shown = ", ".join(terminal_safe(column) for column in columns[:3])
            shown += ", ..." if len(columns) > 3 else ""
            confidence = changes[0].confidence
            lines.append(f"  {rule:<17} {len(changes):>5}  {confidence:<7} {shown}")
        if plan.dropped_lines:
            lines.append(f"  {'dropped rows':<17} {len(plan.dropped_lines):>5}")

    if plan.refusals:
        width = min(shutil.get_terminal_size((88, 24)).columns, 100)
        lines.append("")
        lines.append(f"Left alone ({len(plan.refusals)}):")
        for refusal in plan.refusals:
            where = terminal_safe(refusal.column or "file")
            lines.append(f"  {where} [{terminal_safe(refusal.rule)}]")
            reason = (
                "Value details withheld because the column appears sensitive."
                if refusal.column in (sensitive_columns or set())
                else refusal.reason
            )
            lines.extend(
                textwrap.wrap(terminal_safe(reason), width=width - 4,
                              initial_indent="    ",
                              subsequent_indent="    ")
            )
    return "\n".join(lines)
