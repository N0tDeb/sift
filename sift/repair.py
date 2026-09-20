"""Repairing what can be repaired, and refusing the rest out loud.

Every tool in this space validates. Almost none of them fix, and the reason is
good: an automatic cleaner that guesses wrong is worse than no cleaner, because
it destroys the evidence that anything was ever wrong.

So the rule here is that a repair must be *derivable from the file itself*.
Stripping `$` from `$1,200` is derivable — the number was always 1200 and the
formatting was decoration. Choosing between day-first and month-first when
nothing in the column proves either is not derivable, and Sift will not do it
at any confidence setting.

Every changed cell is written to an audit log with its before, after, and the
rule responsible.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .inference import (
    NUMERIC_SENTINELS,
    has_mojibake,
    is_blank,
    is_disguised_null,
    normalize_label,
    parse_dates,
    parse_number,
)
from .loading import Column, Table
from .profiling import CATEGORICAL, DATE, MIXED, NUMERIC, TEXT

HIGH = "high"
MEDIUM = "medium"
RANK = {HIGH: 2, MEDIUM: 1}

CURRENCY = "$\u20ac\u00a3\u00a5"


@dataclass
class Change:
    line: int
    column: str
    before: str
    after: str
    rule: str
    confidence: str


@dataclass
class Refusal:
    column: str | None
    rule: str
    reason: str


@dataclass
class RepairPlan:
    changes: list[Change] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)

    def by_rule(self) -> dict[str, list[Change]]:
        grouped: dict[str, list[Change]] = defaultdict(list)
        for change in self.changes:
            grouped[change.rule].append(change)
        return dict(grouped)


def _line(row: int) -> int:
    return row + 2


def plain_number(raw: str) -> str | None:
    parsed = parse_number(raw)
    if parsed is None:
        return None
    text = raw.strip()
    negative = text.startswith("(") and text.endswith(")")
    body = text.strip("()").strip()
    for symbol in CURRENCY:
        body = body.replace(symbol, "")
    body = body.replace(",", "").replace("\u00a0", "").strip()
    if negative:
        body = "-" + body.lstrip("+-")
    try:
        if float(body) != parsed.value:
            return None
    except ValueError:
        return None
    return body if body != raw else None


def rule_trim_whitespace(column: Column, values: list[str], plan: RepairPlan) -> None:
    for row, value in enumerate(values):
        cleaned = value.replace("\u00a0", " ").strip()
        if cleaned != value:
            plan.changes.append(
                Change(_line(row), column.name, value, cleaned, "trim-whitespace", HIGH)
            )
            values[row] = cleaned


def rule_blank_null(column: Column, values: list[str], plan: RepairPlan) -> None:
    for row, value in enumerate(values):
        if is_disguised_null(value):
            plan.changes.append(
                Change(_line(row), column.name, value, "", "blank-null", HIGH)
            )
            values[row] = ""


def rule_plain_number(column: Column, values: list[str], plan: RepairPlan) -> None:
    profile = column.profile
    if profile.kind != NUMERIC or not profile.number_flags:
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
        cleaned = plain_number(value)
        if cleaned is not None:
            plan.changes.append(
                Change(_line(row), column.name, value, cleaned, "plain-number", HIGH)
            )
            values[row] = cleaned


def rule_iso_date(column: Column, values: list[str], plan: RepairPlan) -> None:
    profile = column.profile
    if profile.kind != DATE:
        return
    orders = profile.date_orders
    if {"dmy", "mdy"} <= orders:
        plan.refusals.append(
            Refusal(
                column.name,
                "iso-date",
                "Both day-first and month-first readings appear in this column, "
                "and nothing settles which is meant. Guessing here would be "
                "worse than leaving it.",
            )
        )
        return
    if not orders or orders == {"ymd"}:
        return
    order = "dmy" if "dmy" in orders else "mdy"
    for row, value in enumerate(values):
        if is_blank(value):
            continue
        readings = parse_dates(value)
        chosen = next((dt for dt, o in readings if o == order), None)
        if chosen is None:
            continue
        iso = chosen.strftime("%Y-%m-%d")
        if iso != value:
            plan.changes.append(
                Change(_line(row), column.name, value, iso, "iso-date", MEDIUM)
            )
            values[row] = iso


def rule_canonical_label(column: Column, values: list[str], plan: RepairPlan) -> None:
    if column.profile.kind not in (CATEGORICAL, TEXT):
        return
    groups: dict[str, Counter] = defaultdict(Counter)
    for value in values:
        if not is_blank(value):
            groups[normalize_label(value)][value] += 1

    canonical: dict[str, str] = {}
    for spellings in groups.values():
        if len(spellings) < 2:
            continue
        best = sorted(spellings.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        for spelling in spellings:
            if spelling != best:
                canonical[spelling] = best

    for row, value in enumerate(values):
        if value in canonical:
            plan.changes.append(
                Change(_line(row), column.name, value, canonical[value], "canonical-label", MEDIUM)
            )
            values[row] = canonical[value]


def rule_sentinel_null(column: Column, values: list[str], plan: RepairPlan) -> None:
    profile = column.profile
    if profile.kind != NUMERIC or not profile.numbers:
        return
    hits = Counter(v for v in profile.numbers if v in NUMERIC_SENTINELS)
    targets = {v for v, n in hits.items() if n >= max(2, 0.01 * len(profile.numbers))}
    if not targets:
        return
    for row, value in enumerate(values):
        parsed = parse_number(value)
        if parsed is not None and parsed.value in targets:
            plan.changes.append(
                Change(_line(row), column.name, value, "", "sentinel-null", MEDIUM)
            )
            values[row] = ""


def rule_refuse_unfixable(column: Column, values: list[str], plan: RepairPlan) -> None:
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
                f"{len(profile.offenders)} value(s) do not parse as "
                f"{profile.dominant_kind} ({examples}). Whether those rows are "
                "typos, notes, or a second unit is a question about the world, "
                "not about the file.",
            )
        )


RULES = [
    rule_refuse_unfixable,
    rule_trim_whitespace,
    rule_blank_null,
    rule_plain_number,
    rule_iso_date,
    rule_canonical_label,
    rule_sentinel_null,
]


def plan_repairs(
    table: Table, config: Config, min_confidence: str = HIGH
) -> tuple[RepairPlan, list[list[str]]]:
    floor = RANK[min_confidence]
    plan = RepairPlan()
    grid = [list(column.values) for column in table.columns]

    for index, column in enumerate(table.columns):
        for rule in RULES:
            mark = len(plan.changes)
            working = list(grid[index])
            rule(column, working, plan)
            proposed = plan.changes[mark:]
            if proposed and RANK[proposed[0].confidence] < floor:
                del plan.changes[mark:]
            else:
                grid[index] = working

    rows = [list(values) for values in zip(*grid)] if grid else []
    return plan, rows


def write_csv(path: Path, header: list[str], rows: list[list[str]], delimiter: str = ",") -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_log(path: Path, plan: RepairPlan) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["line", "column", "rule", "confidence", "before", "after"])
        for change in plan.changes:
            writer.writerow(
                [change.line, change.column, change.rule, change.confidence, change.before, change.after]
            )


def render_plan(table: Table, plan: RepairPlan, destination: str) -> str:
    lines = [f"{table.path} to {destination}", ""]
    grouped = plan.by_rule()
    if not grouped:
        lines.append("Nothing to change.")
    else:
        total = len(plan.changes)
        lines.append(f"{total} cell(s) changed")
        for rule, changes in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            columns = sorted({change.column for change in changes})
            shown = ", ".join(columns[:3]) + (", ..." if len(columns) > 3 else "")
            lines.append(f"  {rule:<17} {len(changes):>5}  {changes[0].confidence:<7} {shown}")

    if plan.refusals:
        lines.append("")
        lines.append(f"Left alone ({len(plan.refusals)}):")
        for refusal in plan.refusals:
            where = refusal.column or "file"
            lines.append(f"  {where} [{refusal.rule}]")
            lines.append(f"    {refusal.reason}")
    return "\n".join(lines)
