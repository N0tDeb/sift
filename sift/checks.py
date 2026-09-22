"""The checks.

Each check is a plain function from a table to a list of findings, registered
in CHECKS. Adding a rule means writing one function; nothing else changes.

The rules are chosen by one standard: would this quietly produce a wrong number
downstream? A column that is 40% null is obvious the moment anyone looks. A
date column where half the rows are day-first and half are month-first is not,
and it will move your revenue into the wrong quarter.
"""

from __future__ import annotations

import difflib
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Callable

from .config import Config
from .findings import Finding, Severity
from .inference import (
    DATE_SENTINELS,
    NUMERIC_SENTINELS,
    has_mojibake,
    is_blank,
    is_disguised_null,
    looks_like_id_name,
    normalize_label,
    whitespace_problem,
)
from .loading import Table
from .profiling import BOOLEAN, CATEGORICAL, DATE, MIXED, NUMERIC, TEXT, infer_number_convention

CheckFn = Callable[[Table, Config], list[Finding]]
CHECKS: list[CheckFn] = []

COUNT_LIKE = ("qty", "quantity", "count", "units", "age", "price", "amount", "total")


def check(fn: CheckFn) -> CheckFn:
    CHECKS.append(fn)
    return fn


def _samples(values: list[str], limit: int) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
        if len(seen) >= limit:
            break
    return seen


def _edit_size(left: str, right: str) -> int:
    """How many characters actually differ between two labels.

    A similarity *ratio* is the wrong test: "chips and tomatillo green chili
    salsa" and the red variant score 0.93 similar, because they share 35
    characters. They are not a typo of each other, they are two products.
    Counting the differing spans instead makes length irrelevant — a typo is
    one or two characters wrong no matter how long the label is.
    """
    total = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, left, right).get_opcodes():
        if tag != "equal":
            total += max(i2 - i1, j2 - j1)
    return total


def _looks_like_typo(counts, label: str, other: str) -> bool:
    """A character-level near-match is not enough on its own.

    A diamond's clarity grades "vvs1"/"vvs2", a sex column's "male"/"female",
    and majors "biology"/"ecology"/"zoology" are all one or two characters
    apart and are not typos; they are evenly-used, intentional categories.
    The signal that separates a typo from a category is frequency skew: a
    typo is a rare variant of a spelling most rows agree on, not a second
    spelling used about as often as the first.
    """
    count_a = sum(n for value, n in counts.items() if normalize_label(value) == label)
    count_b = sum(n for value, n in counts.items() if normalize_label(value) == other)
    minority, majority = min(count_a, count_b), max(count_a, count_b)
    if minority == 0 or minority == majority or majority < 5:
        return False
    return minority <= 2 or minority / majority < 0.15


def _pct(part: int, whole: int) -> str:
    return f"{(part / whole * 100):.1f}%" if whole else "0%"


@check
def check_header(table: Table, config: Config) -> list[Finding]:
    findings: list[Finding] = []
    counts = Counter(table.header)

    for name, count in counts.items():
        if count > 1:
            findings.append(
                Finding(
                    "duplicate-column-name",
                    Severity.ERROR,
                    f"Column name {name!r} appears {count} times. Most readers keep "
                    "only one of them, silently.",
                    column=name,
                    detail={"occurrences": count},
                )
            )

    for index, name in enumerate(table.header):
        if is_blank(name):
            findings.append(
                Finding(
                    "unnamed-column",
                    Severity.WARNING,
                    f"Column {index + 1} has no name.",
                    detail={"column_index": index},
                )
            )
        elif name != name.strip():
            findings.append(
                Finding(
                    "padded-column-name",
                    Severity.WARNING,
                    f"Column name {name!r} has surrounding whitespace, so lookups "
                    "by name will miss it.",
                    column=name,
                    detail={"column_index": index},
                )
            )

    collisions: dict[str, list[str]] = defaultdict(list)
    for name in table.header:
        if not is_blank(name):
            collisions[normalize_label(name)].append(name)
    for normalized, names in collisions.items():
        if len(set(names)) > 1:
            findings.append(
                Finding(
                    "similar-column-names",
                    Severity.WARNING,
                    "Columns differ only by case or spacing: "
                    + ", ".join(repr(n) for n in sorted(set(names))),
                    detail={"normalized": normalized},
                )
            )
    return findings


@check
def check_missing(table: Table, config: Config) -> list[Finding]:
    findings: list[Finding] = []
    for column in table.columns:
        profile = column.profile
        if profile.kind == "empty":
            findings.append(
                Finding(
                    "empty-column",
                    Severity.WARNING,
                    f"{column.name!r} has no values at all.",
                    column=column.name,
                    detail={"column_index": column.index},
                )
            )
            continue

        rate = profile.null_rate
        if rate >= config.null_error:
            severity = Severity.ERROR
        elif rate >= config.null_warn:
            severity = Severity.WARNING
        else:
            severity = None
        if severity:
            findings.append(
                Finding(
                    "missing-values",
                    severity,
                    f"{column.name!r} is {_pct(profile.n_null, profile.n_rows)} empty "
                    f"({profile.n_null:,} of {profile.n_rows:,} rows).",
                    column=column.name,
                    detail={"column_index": column.index, "null_rate": round(rate, 4)},
                )
            )

        if profile.n_disguised_null:
            written = _samples(
                [v for v in column.values if is_disguised_null(v)], config.max_examples
            )
            findings.append(
                Finding(
                    "disguised-null",
                    Severity.WARNING,
                    f"{column.name!r} writes missing values as text in "
                    f"{profile.n_disguised_null:,} row(s), so they will load as "
                    "strings rather than nulls.",
                    column=column.name,
                    detail={"column_index": column.index},
                    examples=written,
                )
            )
    return findings


@check
def check_constant(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind != "empty" and profile.distinct == 1 and profile.n_present > 1:
            only = next(iter(profile.value_counts))
            findings.append(
                Finding(
                    "constant-column",
                    Severity.INFO,
                    f"{column.name!r} is {only!r} in every row. It carries no "
                    "information; check whether a filter upstream collapsed it.",
                    column=column.name,
                    detail={"column_index": column.index},
                )
            )
    return findings


@check
def check_duplicate_rows(table: Table, config: Config) -> list[Finding]:
    if not table.columns or table.n_rows == 0:
        return []
    rows = zip(*[column.values for column in table.columns])
    counts: Counter = Counter(rows)
    repeated = {row: n for row, n in counts.items() if n > 1}
    if not repeated:
        return []
    extra = sum(n - 1 for n in repeated.values())
    example = next(iter(repeated))
    return [
        Finding(
            "duplicate-rows",
            Severity.WARNING,
            f"{extra:,} duplicate row(s) across {len(repeated):,} distinct value "
            "combinations. Any sum over this file is inflated.",
            detail={"extra_rows": extra, "groups": len(repeated)},
            examples=[", ".join(example[:4])[:120]],
        )
    ]


@check
def check_key(table: Table, config: Config) -> list[Finding]:
    findings: list[Finding] = []

    for name in config.key:
        column = table.by_key(name)
        if column is None:
            findings.append(
                Finding(
                    "missing-key-column",
                    Severity.ERROR,
                    f"Declared key column {name!r} is not in this file.",
                    column=name,
                )
            )
            continue

        blanks = sum(1 for value in column.values if is_blank(value))
        if blanks:
            findings.append(
                Finding(
                    "key-null",
                    Severity.ERROR,
                    f"Key column {column.name!r} is empty in {blanks:,} row(s).",
                    column=column.name,
                    detail={"column_index": column.index},
                )
            )

        counts = Counter(v for v in column.values if not is_blank(v))
        dupes = {v: n for v, n in counts.items() if n > 1}
        if dupes:
            findings.append(
                Finding(
                    "key-not-unique",
                    Severity.ERROR,
                    f"Key column {column.name!r} repeats {len(dupes):,} value(s). "
                    "Joining on it will multiply rows.",
                    column=column.name,
                    detail={"column_index": column.index},
                    examples=list(dupes)[: config.max_examples],
                )
            )

    if not config.key:
        for column in table.columns:
            profile = column.profile
            if (
                looks_like_id_name(column.name)
                and profile.n_present == table.n_rows
                and profile.distinct == table.n_rows
                and table.n_rows > 1
            ):
                findings.append(
                    Finding(
                        "candidate-key",
                        Severity.INFO,
                        f"{column.name!r} is unique and complete. Pass "
                        f"--key {column.name} to enforce that on future files.",
                        column=column.name,
                        detail={"column_index": column.index},
                    )
                )
                break
    return findings


@check
def check_mixed_types(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if not profile.offenders:
            continue
        offenders = profile.offenders
        expected = profile.dominant_kind or profile.kind
        findings.append(
            Finding(
                "mixed-types",
                Severity.ERROR,
                f"{column.name!r} is {_pct(len(offenders), profile.n_present)} "
                f"non-{expected} ({len(offenders):,} row(s)). A typed loader will "
                "fall back to text for the whole column, or drop these rows.",
                column=column.name,
                detail={"column_index": column.index, "expected": expected},
                examples=_samples([raw for _, raw in offenders], config.max_examples),
            )
        )
    return findings


@check
def check_numeric_formatting(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind != NUMERIC or not profile.number_flags:
            continue
        described = {
            "currency": "currency symbols",
            "thousands": "thousands separators",
            "percent": "percent signs",
            "parens": "parentheses for negatives",
        }
        parts = [described[flag] for flag in sorted(profile.number_flags) if flag in described]
        findings.append(
            Finding(
                "number-as-text",
                Severity.WARNING,
                f"{column.name!r} holds numbers written with "
                + " and ".join(parts)
                + ". It will load as text and silently break any arithmetic.",
                column=column.name,
                detail={"column_index": column.index},
                examples=_samples(
                    [v for v in column.values if not is_blank(v)], config.max_examples
                ),
            )
        )
    return findings


@check
def check_decimal_convention(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind != NUMERIC or not profile.numbers:
            continue
        reading = infer_number_convention(profile)
        if reading.label == "conflicting":
            findings.append(Finding(
                "conflicting-number-formats", Severity.ERROR,
                f"{column.name!r} mixes decimal conventions. " + reading.reason,
                column=column.name, detail={"column_index": column.index},
            ))
        elif reading.label == "undecidable":
            findings.append(Finding(
                "ambiguous-decimal-separator", Severity.ERROR,
                f"{column.name!r} cannot be read safely. " + reading.reason,
                column=column.name, detail={"column_index": column.index},
            ))
        elif profile.number_convention == "eu":
            findings.append(Finding(
                "european-numbers", Severity.WARNING,
                f"{column.name!r} uses a comma for the decimal point and a dot "
                "for thousands. Any reader assuming English convention will "
                "misread these by a factor of a thousand, without erroring.",
                column=column.name, detail={"column_index": column.index},
            ))
    return findings


@check
def check_leading_zeros(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        values = [v.strip() for v in column.values if not is_blank(v)]
        if not values or not all(v.isdigit() for v in values):
            continue
        padded = [v for v in values if len(v) > 1 and v.startswith("0")]
        if not padded:
            continue
        findings.append(
            Finding(
                "leading-zeros",
                Severity.WARNING,
                f"{column.name!r} is all digits and {len(padded):,} value(s) start "
                "with a zero. Reading it as a number destroys them — keep it text.",
                column=column.name,
                detail={"column_index": column.index},
                examples=_samples(padded, config.max_examples),
            )
        )
    return findings


@check
def check_whitespace(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        problems: Counter = Counter()
        offenders: list[str] = []
        for value in column.values:
            if is_blank(value):
                continue
            problem = whitespace_problem(value)
            if problem:
                problems[problem] += 1
                if len(offenders) < config.max_examples:
                    offenders.append(repr(value))
        if not problems:
            continue
        total = sum(problems.values())
        description = "; ".join(f"{p} ({n:,})" for p, n in problems.most_common())
        findings.append(
            Finding(
                "whitespace",
                Severity.WARNING,
                f"{column.name!r} has {total:,} value(s) with stray whitespace: "
                f"{description}. Grouping and joining will treat them as distinct.",
                column=column.name,
                detail={"column_index": column.index},
                examples=offenders,
            )
        )
    return findings


@check
def check_label_variants(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind not in (CATEGORICAL, TEXT, BOOLEAN):
            continue
        if profile.distinct < 2 or profile.distinct > 200:
            continue

        groups: dict[str, set[str]] = defaultdict(set)
        for value in profile.value_counts:
            groups[normalize_label(value)].add(value)
        merged = {k: v for k, v in groups.items() if len(v) > 1}
        if merged:
            examples = [" / ".join(sorted(v)) for v in list(merged.values())[: config.max_examples]]
            findings.append(
                Finding(
                    "label-variants",
                    Severity.WARNING,
                    f"{column.name!r} spells {len(merged)} value(s) more than one way "
                    "(case or spacing only). Any group-by will split them.",
                    column=column.name,
                    detail={"column_index": column.index},
                    examples=examples,
                )
            )

        # Sequential IDs are "near" each other by construction; a duplicated
        # order_id once fired this check against its numeric neighbour.
        if looks_like_id_name(column.name) or (
            profile.n_present >= 20 and profile.unique_ratio > 0.9
        ):
            continue

        labels = sorted(groups)
        pairs: list[str] = []
        for i, label in enumerate(labels):
            for other in labels[i + 1 :]:
                if abs(len(label) - len(other)) > 2 or min(len(label), len(other)) < 4:
                    continue
                if _edit_size(label, other) > 2:
                    continue
                if not _looks_like_typo(profile.value_counts, label, other):
                    continue
                pairs.append(f"{label} / {other}")
        if pairs:
            findings.append(
                Finding(
                    "near-duplicate-labels",
                    Severity.INFO,
                    f"{column.name!r} contains values that differ by a character or "
                    "two. Likely typos rather than distinct categories.",
                    column=column.name,
                    detail={"column_index": column.index},
                    examples=pairs[: config.max_examples],
                )
            )
    return findings


@check
def check_dates(table: Table, config: Config) -> list[Finding]:
    findings = []
    now = datetime.now()
    for column in table.columns:
        profile = column.profile
        if profile.kind != DATE or not profile.dates:
            continue

        if profile.ambiguous_date_rows and {"dmy", "mdy"} <= profile.date_orders:
            findings.append(
                Finding(
                    "ambiguous-dates",
                    Severity.ERROR,
                    f"{column.name!r} can be read as either day-first or month-first: "
                    f"{profile.ambiguous_date_rows:,} row(s) are valid both ways, and "
                    "some are not. The file does not say which is meant.",
                    column=column.name,
                    detail={"column_index": column.index},
                    examples=_samples(
                        [v for v in column.values if not is_blank(v)], config.max_examples
                    ),
                )
            )

        future = [d for d in profile.dates if d > now + timedelta(days=1)]
        if future:
            findings.append(
                Finding(
                    "future-dates",
                    Severity.WARNING,
                    f"{column.name!r} has {len(future):,} date(s) in the future, "
                    f"latest {max(future).date()}.",
                    column=column.name,
                    detail={"column_index": column.index},
                )
            )

        ancient = [d for d in profile.dates if d.year < 1900]
        if ancient:
            findings.append(
                Finding(
                    "implausible-dates",
                    Severity.WARNING,
                    f"{column.name!r} has {len(ancient):,} date(s) before 1900, "
                    f"earliest {min(ancient).date()}.",
                    column=column.name,
                    detail={"column_index": column.index},
                )
            )

        sentinels = [d for d in profile.dates if str(d.date()) in DATE_SENTINELS]
        if sentinels:
            findings.append(
                Finding(
                    "sentinel-dates",
                    Severity.WARNING,
                    f"{column.name!r} uses placeholder dates in {len(sentinels):,} "
                    "row(s). They will be treated as real dates in any range filter.",
                    column=column.name,
                    detail={"column_index": column.index},
                    examples=sorted({str(d.date()) for d in sentinels})[: config.max_examples],
                )
            )
    return findings


@check
def check_numeric_values(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind != NUMERIC or len(profile.numbers) < 8:
            continue
        # Statistics over a column that is not consistently numeric describe a
        # subset the author never intended. Titanic's Ticket is 74% numeric
        # and 26% strings like "STON/O2. 3101282"; the "outliers" in the
        # numeric part were just ticket numbers.
        if profile.offenders:
            continue
        stats = profile.stats()
        median, mad = stats["median"], stats["mad"]

        if mad > 0:
            scored = [
                (abs(0.6745 * (v - median) / mad), v) for v in profile.numbers
            ]
            extreme = sorted(
                (pair for pair in scored if pair[0] > config.outlier_z), reverse=True
            )
            # A quarter of a column flagged is not an outlier report, it means
            # the column is skewed. GDP data (exponentially distributed) hit
            # 3,798 of 13,979 rows before this line existed.
            if len(extreme) > config.outlier_share * len(profile.numbers):
                extreme = []
            if extreme:
                findings.append(
                    Finding(
                        "outliers",
                        Severity.INFO,
                        f"{column.name!r} has {len(extreme):,} value(s) far from the "
                        f"median of {median:,.2f}. Worth a look before you average it.",
                        column=column.name,
                        detail={"column_index": column.index},
                        examples=[f"{value:,.4g}" for _, value in extreme[: config.max_examples]],
                    )
                )

        hits = Counter(v for v in profile.numbers if v in NUMERIC_SENTINELS)
        for value, count in hits.items():
            if count >= max(2, 0.01 * len(profile.numbers)):
                findings.append(
                    Finding(
                        "numeric-sentinel",
                        Severity.WARNING,
                        f"{column.name!r} repeats {value:g} in {count:,} row(s). "
                        "That is a placeholder for missing, and it will be summed "
                        "and averaged as if it were real.",
                        column=column.name,
                        detail={"column_index": column.index},
                    )
                )

        name = normalize_label(column.name)
        if any(token in name for token in COUNT_LIKE):
            negatives = [v for v in profile.numbers if v < 0]
            if negatives:
                findings.append(
                    Finding(
                        "unexpected-negative",
                        Severity.INFO,
                        f"{column.name!r} reads like a quantity but has "
                        f"{len(negatives):,} negative value(s), lowest "
                        f"{min(negatives):,.2f}. Refunds, or a sign error?",
                        column=column.name,
                        detail={"column_index": column.index},
                    )
                )
    return findings


@check
def check_cardinality(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind != TEXT or profile.n_present < 20:
            continue
        if profile.unique_ratio > 0.95 and not looks_like_id_name(column.name):
            findings.append(
                Finding(
                    "high-cardinality",
                    Severity.INFO,
                    f"{column.name!r} is unique in "
                    f"{_pct(profile.distinct, profile.n_present)} of rows — free text "
                    "or an identifier, not a category. Grouping on it will do nothing.",
                    column=column.name,
                    detail={"column_index": column.index},
                )
            )
    return findings


@check
def check_value_encoding(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        broken = [v for v in column.values if has_mojibake(v)]
        if not broken:
            continue
        findings.append(
            Finding(
                "mojibake",
                Severity.ERROR,
                f"{column.name!r} contains {len(broken):,} value(s) with mangled "
                "characters — UTF-8 text that was decoded as Latin-1 upstream. "
                "Re-export rather than find-and-replace.",
                column=column.name,
                detail={"column_index": column.index},
                examples=_samples(broken, config.max_examples),
            )
        )
    return findings


def run_checks(table: Table, config: Config) -> list[Finding]:
    findings = list(table.file_findings)
    for check_fn in CHECKS:
        findings.extend(check_fn(table, config))
    return config.filter(findings)
