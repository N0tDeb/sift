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
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta

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
from .profiling import (
    BOOLEAN,
    CATEGORICAL,
    DATE,
    EMPTY,
    MIXED,
    NUMERIC,
    TEXT,
    infer_date_order,
    infer_number_convention,
)
from .text import fmt_number, plural

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

    A similarity *ratio* is the wrong test here, and real data proved it: in a
    restaurant menu, "chips and tomatillo green chili salsa" and "...red chili
    salsa" score 0.93 similar, because they share 35 characters. They are not a
    typo of each other, they are two products. Counting the characters in the
    differing spans instead makes length irrelevant — a typo is one or two
    characters wrong no matter how long the label is.
    """
    total = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, left, right).get_opcodes():
        if tag != "equal":
            total += max(i2 - i1, j2 - j1)
    return total


def _looks_like_typo(counts, label: str, other: str) -> bool:
    """A character-level near-match is not enough on its own — real data
    proved it. A diamond's clarity grades "vvs1"/"vvs2", a sex column's
    "male"/"female", and majors "biology"/"ecology"/"zoology" are all one or
    two characters apart and are not typos of anything; they are evenly-used,
    intentional categories.

    The signal that actually separates a typo from a category is frequency
    skew: a typo is a rare variant of a spelling most rows agree on, not a
    second spelling used about as often as the first. Requiring the rarer of
    the pair to be both a small count and a small share of the more common
    one keeps "Acme Corp" (rare "Acme Crop") flagged while leaving evenly
    split categories alone.
    """
    count_a = sum(n for value, n in counts.items() if normalize_label(value) == label)
    count_b = sum(n for value, n in counts.items() if normalize_label(value) == other)
    minority = min(count_a, count_b)
    majority = max(count_a, count_b)
    if minority == 0 or minority == majority:
        # Equal counts is the strongest case against a typo: shampoo's
        # "1-01"/"1-02" and recent-grads' one-row-per-major "biology"/
        # "ecology" both occur exactly once each. A typo is asymmetric by
        # definition — one spelling caught on, the other didn't.
        return False
    if majority < 5:
        # Titanic's Cabin column caught this: "B101"/"E101" are each held by
        # one or two passengers, so minority <= 2 alone would call every
        # near-miss cabin number a typo. Without a spelling that a real
        # number of rows actually agreed on, there's nothing for the rare
        # one to be a typo *of* — it's just two more singletons.
        return False
    return minority <= 2 or minority / majority < 0.15


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "0%"
    share = part / whole * 100
    if part and share < 0.1:
        # 70 rows out of 161,568 is not "0.0%", which reads like a bug in the
        # tool rather than a fact about the data.
        return "<0.1%"
    return f"{share:.1f}%"


_num = fmt_number
_plural = plural


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
def check_no_rows(table: Table, config: Config) -> list[Finding]:
    if table.n_rows or not table.columns:
        return []
    return [
        Finding(
            "no-data-rows",
            Severity.ERROR,
            f"The file has a header of {len(table.columns)} columns and no data "
            "rows at all. Anything reading it gets an empty result rather than "
            "an error.",
            detail={"columns": len(table.columns)},
        )
    ]


@check
def check_missing(table: Table, config: Config) -> list[Finding]:
    findings: list[Finding] = []
    if not table.n_rows:
        return []  # reported once by check_no_rows instead of per column
    for column in table.columns:
        profile = column.profile
        if profile.kind == EMPTY:
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
                    f"{_plural(profile.n_disguised_null, 'row')}, so they will load "
                    "as strings rather than nulls.",
                    column=column.name,
                    detail={
                        "column_index": column.index,
                        "count": profile.n_disguised_null,
                    },
                    examples=written,
                )
            )
    return findings


@check
def check_constant(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind != EMPTY and profile.distinct == 1 and profile.n_present > 1:
            findings.append(
                Finding(
                    "constant-column",
                    Severity.INFO,
                    f"{column.name!r} is {next(iter(profile.value_counts))!r} in "
                    "every row. It carries no information; check whether a filter "
                    "upstream collapsed it.",
                    column=column.name,
                    detail={"column_index": column.index},
                )
            )
    return findings


@check
def check_duplicate_rows(table: Table, config: Config) -> list[Finding]:
    if not table.columns or table.n_rows == 0:
        return []
    rows = zip(*[column.values for column in table.columns], strict=True)
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
            f"{_plural(extra, 'redundant row')} across "
            f"{_plural(len(repeated), 'group')} of identical rows. Any total taken "
            "from this file is inflated.",
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
                    f"Key column {column.name!r} is empty in {_plural(blanks, 'row')}.",
                    column=column.name,
                    detail={"column_index": column.index, "count": blanks},
                )
            )

        counts = Counter(v for v in column.values if not is_blank(v))
        dupes = {v: n for v, n in counts.items() if n > 1}
        if dupes:
            findings.append(
                Finding(
                    "key-not-unique",
                    Severity.ERROR,
                    f"Key column {column.name!r} repeats {_plural(len(dupes), 'value')}. "
                    "Joining on it will multiply rows.",
                    column=column.name,
                    detail={"column_index": column.index, "repeated": len(dupes)},
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
        severity = Severity.ERROR if profile.kind != MIXED else Severity.ERROR
        headline = (
            f"{column.name!r} is {_pct(len(offenders), profile.n_present)} "
            f"non-{expected} ({_plural(len(offenders), 'row')})."
        )
        if profile.kind == MIXED:
            headline = (
                f"{column.name!r} has no consistent type: only "
                f"{profile.dominant_share:.0%} of values parse as {expected}."
            )
        findings.append(
            Finding(
                "mixed-types",
                severity,
                headline + " A typed loader will fall back to text for the whole "
                "column, or drop these rows.",
                column=column.name,
                detail={
                    "column_index": column.index,
                    "expected": expected,
                    "count": len(offenders),
                    "rows": [row + 2 for row, _ in offenders[: config.max_examples]],
                },
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
                detail={"column_index": column.index, "flags": sorted(profile.number_flags)},
                examples=_samples(
                    [v for v in column.values if not is_blank(v)], config.max_examples
                ),
            )
        )
    return findings


@check
def check_decimal_convention(table: Table, config: Config) -> list[Finding]:
    """Which way round the dot and the comma are.

    This is the highest-cost mistake in the whole tool's remit and the easiest
    to miss, because nothing about it looks wrong. "12.500,00" is twelve and a
    half thousand; read with English convention it is 12.5. The file loads, no
    error is raised, and the number is off by a factor of a thousand.
    """
    findings = []
    for column in table.columns:
        profile = column.profile
        if profile.kind != NUMERIC or not profile.numbers:
            continue
        reading = infer_number_convention(profile)
        examples = _samples(
            [v for v in column.values if not is_blank(v)], config.max_examples
        )
        detail = {"column_index": column.index, "convention": reading.convention}

        if reading.label == "conflicting":
            findings.append(
                Finding(
                    "conflicting-number-formats",
                    Severity.ERROR,
                    f"{column.name!r} mixes decimal conventions. " + reading.reason,
                    column=column.name,
                    detail=detail,
                    examples=examples,
                )
            )
        elif reading.label == "undecidable":
            findings.append(
                Finding(
                    "ambiguous-decimal-separator",
                    Severity.ERROR,
                    f"{column.name!r} cannot be read safely. " + reading.reason,
                    column=column.name,
                    detail=detail,
                    examples=examples,
                )
            )
        elif profile.number_convention == "eu":
            findings.append(
                Finding(
                    "european-numbers",
                    Severity.WARNING,
                    f"{column.name!r} uses a comma for the decimal point and a dot "
                    "for thousands. Any reader assuming English convention will "
                    "misread these by a factor of a thousand, without erroring.",
                    column=column.name,
                    detail=detail,
                    examples=examples,
                )
            )
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
                f"{column.name!r} is all digits and {_plural(len(padded), 'value')} start "
                "with a zero. Reading it as a number destroys them — keep it text.",
                column=column.name,
                detail={"column_index": column.index, "count": len(padded)},
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
                if repr(value) not in offenders and len(offenders) < config.max_examples:
                    offenders.append(repr(value))
        if not problems:
            continue
        total = sum(problems.values())
        description = "; ".join(f"{p} ({n:,})" for p, n in problems.most_common())
        findings.append(
            Finding(
                "whitespace",
                Severity.WARNING,
                f"{column.name!r} has {_plural(total, 'value')} with stray whitespace: "
                f"{description}. Grouping and joining will treat them as distinct.",
                column=column.name,
                detail={"column_index": column.index, "count": total},
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
        # Sequential IDs are "near" each other by construction — A-1032 and
        # A-1033 are not a typo of one another, they're consecutive. Real
        # data proved this: a duplicated order_id fired near-duplicate-labels
        # against its numeric neighbour before this guard existed.
        if looks_like_id_name(column.name) or (
            profile.n_present >= 20 and profile.unique_ratio > 0.9
        ):
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
                    f"{column.name!r} spells {_plural(len(merged), 'value')} more than one way "
                    "(case or spacing only). Any group-by will split them.",
                    column=column.name,
                    detail={"column_index": column.index, "groups": len(merged)},
                    examples=examples,
                )
            )

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
                    detail={"column_index": column.index, "pairs": len(pairs)},
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

        # The same graded reading the repair engine uses, so `check` and `fix`
        # cannot disagree about how bad a date column is. Before this, a column
        # holding two incompatible formats — the one case that cannot be
        # repaired at all — was reported as a mild "mixed layouts" warning
        # while `fix` was correctly refusing to touch it.
        reading = infer_date_order(profile)
        examples = _samples(
            [v for v in column.values if not is_blank(v)], config.max_examples
        )
        detail = {
            "column_index": column.index,
            "confidence": round(reading.confidence, 3),
            "order": reading.order,
        }

        if reading.label == "conflicting":
            findings.append(
                Finding(
                    "conflicting-date-formats",
                    Severity.ERROR,
                    f"{column.name!r} holds two date formats mixed together. "
                    + reading.reason
                    + " Every row is individually valid, so nothing will error — "
                    "the dates will simply be wrong.",
                    column=column.name,
                    detail=detail,
                    examples=examples,
                )
            )
        elif reading.label == "undecidable":
            findings.append(
                Finding(
                    "ambiguous-dates",
                    Severity.ERROR,
                    f"{column.name!r} can be read as either day-first or "
                    "month-first. " + reading.reason,
                    column=column.name,
                    detail=detail,
                    examples=examples,
                )
            )
        elif reading.label == "likely":
            findings.append(
                Finding(
                    "weak-date-evidence",
                    Severity.WARNING,
                    f"{column.name!r} reads as "
                    f"{'day-first' if reading.order == 'dmy' else 'month-first'}, "
                    "but on thin evidence. " + reading.reason,
                    column=column.name,
                    detail=detail,
                    examples=examples,
                )
            )
        elif reading.order != "ymd" and profile.date_evidence.get("iso"):
            findings.append(
                Finding(
                    "mixed-date-formats",
                    Severity.WARNING,
                    f"{column.name!r} mixes ISO dates with "
                    f"{'day-first' if reading.order == 'dmy' else 'month-first'} "
                    "ones. Parsing with a single format will drop the rest.",
                    column=column.name,
                    detail=detail,
                    examples=examples,
                )
            )

        future = [d for d in profile.dates if d > now + timedelta(days=1)]
        if future:
            findings.append(
                Finding(
                    "future-dates",
                    Severity.WARNING,
                    f"{column.name!r} has {_plural(len(future), 'date')} in the future, "
                    f"latest {max(future).date()}.",
                    column=column.name,
                    detail={"column_index": column.index, "count": len(future)},
                )
            )

        ancient = [d for d in profile.dates if d.year < 1900]
        if ancient:
            findings.append(
                Finding(
                    "implausible-dates",
                    Severity.WARNING,
                    f"{column.name!r} has {_plural(len(ancient), 'date')} before 1900, "
                    f"earliest {min(ancient).date()}.",
                    column=column.name,
                    detail={"column_index": column.index, "count": len(ancient)},
                )
            )

        sentinels = [d for d in profile.dates if str(d.date()) in DATE_SENTINELS]
        if sentinels:
            findings.append(
                Finding(
                    "sentinel-dates",
                    Severity.WARNING,
                    f"{column.name!r} uses placeholder dates in "
                    f"{_plural(len(sentinels), 'row')}. They will be treated as real "
                    "dates in any range filter.",
                    column=column.name,
                    detail={"column_index": column.index, "count": len(sentinels)},
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
        stats = profile.stats()
        median, mad = stats["median"], stats["mad"]

        # Statistics over a column that is not consistently numeric describe a
        # subset the author never meant to be one. Titanic's Ticket column is
        # 74% numeric and 26% strings like "STON/O2. 3101282"; the "outliers"
        # in the numeric part are just ticket numbers.
        if profile.offenders:
            continue

        # An ID that happens to be numeric is not a measurement either. Nothing
        # useful comes of telling someone their invoice numbers are unusual.
        # This suppresses the *statistics* only: whether a value is a -999
        # placeholder, or a negative in a column named like a quantity, is a
        # fact about the value and holds however unique the column is. Gating
        # all three on this guard silently switched off both of the others for
        # any column of distinct whole numbers, which is most quantity columns.
        looks_like_identifier = looks_like_id_name(column.name) or (
            profile.unique_ratio > 0.9 and all(v == int(v) for v in profile.numbers)
        )

        if mad > 0 and not looks_like_identifier:
            # Modified z-score. The 0.6745 makes MAD comparable to a standard
            # deviation for normally distributed data.
            scored = [
                (abs(0.6745 * (v - median) / mad), v) for v in profile.numbers
            ]
            extreme = sorted(
                (pair for pair in scored if pair[0] > config.outlier_z), reverse=True
            )
            # If a quarter of the column is "extreme", the column is skewed and
            # the finding is noise. Real GDP data flagged 3,798 of 13,979 rows
            # before this line existed, which is not an outlier report.
            if len(extreme) > config.outlier_share * len(profile.numbers):
                extreme = []
            if extreme:
                findings.append(
                    Finding(
                        "outliers",
                        Severity.INFO,
                        f"{column.name!r} has {_plural(len(extreme), 'value')} far "
                        f"from the median of {_num(median)}. Worth a look before you "
                        "average this column.",
                        column=column.name,
                        detail={
                            "column_index": column.index,
                            "count": len(extreme),
                            "median": median,
                        },
                        examples=[_num(value) for _, value in extreme[: config.max_examples]],
                    )
                )

        hits = Counter(v for v in profile.numbers if v in NUMERIC_SENTINELS)
        for value, count in hits.items():
            if count >= max(2, 0.01 * len(profile.numbers)):
                findings.append(
                    Finding(
                        "numeric-sentinel",
                        Severity.WARNING,
                        f"{column.name!r} repeats {_num(value)} in "
                        f"{_plural(count, 'row')}. "
                        "That is a placeholder for missing, and it will be summed "
                        "and averaged as if it were real.",
                        column=column.name,
                        detail={"column_index": column.index, "value": value, "count": count},
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
                        f"{_plural(len(negatives), 'negative value')}, lowest "
                        f"{_num(min(negatives))}. Refunds, or a sign error?",
                        column=column.name,
                        detail={"column_index": column.index, "count": len(negatives)},
                    )
                )
    return findings


@check
def check_compact_dates(table: Table, config: Config) -> list[Finding]:
    findings = []
    for column in table.columns:
        profile = column.profile
        if not profile.unresolved_compact:
            continue
        if profile.unresolved_compact < profile.n_present:
            continue
        findings.append(
            Finding(
                "compact-date-or-number",
                Severity.INFO,
                f"Every value in {column.name!r} is 8 digits, which reads equally "
                "well as a YYYYMMDD date or as a plain number. Sift treated it as "
                "a number; nothing in the file settles it either way.",
                column=column.name,
                detail={"column_index": column.index, "rows": profile.unresolved_compact},
                examples=_samples(
                    [v for v in column.values if not is_blank(v)], config.max_examples
                ),
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
                    detail={"column_index": column.index, "distinct": profile.distinct},
                )
            )
    return findings


@check
def check_formula_injection(table: Table, config: Config) -> list[Finding]:
    """Values a spreadsheet will execute rather than display.

    A cell beginning `=`, `+` or `@` is a formula to Excel, LibreOffice and
    Sheets. `=cmd|'/c calc'!A0` in a CSV is a remote-code-execution vector
    against whoever opens the export, and it survives every other check here
    because as *data* it is a perfectly ordinary string.

    Restricted to columns that are not numeric so that ordinary signed numbers
    are never flagged, and `-` is excluded entirely for the same reason.
    """
    findings = []
    for column in table.columns:
        if column.profile.kind in (NUMERIC, DATE, EMPTY):
            continue
        risky = [
            value
            for value in column.values
            if value.lstrip("\t\r ")[:1] in {"=", "+", "@"}
        ]
        if not risky:
            continue
        findings.append(
            Finding(
                "formula-injection",
                Severity.WARNING,
                f"{column.name!r} has {plural(len(risky), 'value')} starting with "
                "=, + or @, which a spreadsheet runs as a formula rather than "
                "showing as text. Prefix them with an apostrophe before anyone "
                "opens this in Excel.",
                column=column.name,
                detail={"column_index": column.index, "count": len(risky)},
                examples=_samples(risky, config.max_examples),
            )
        )
    return findings


# --- sensitive data --------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")
# Phone numbers are the hardest of these to recognise, because "digits with
# punctuation in them" also describes ZIP+4 codes, SKUs, ISBNs, version
# strings and year ranges — all of which the first version of this check
# called phone numbers. There is no shape that separates a bare 555-123-4567
# from a three-three-four product code, so shape alone is not enough:
#
#   +44 20 7946 0958   an international prefix is unambiguous on its own
#   (555) 123-4567     a parenthesised area code is close enough
#   555-123-4567       only counts if the column is named like a phone field
_PHONE_INTERNATIONAL = re.compile(r"^\+\d[\d\s().-]{6,16}\d$")
_PHONE_PARENS = re.compile(r"^\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}$")
_PHONE_NAMES = {"phone", "telephone", "tel", "mobile", "cell", "fax", "contact number",
                "phone number", "contact"}
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")

# Issuer prefixes, so a random 16-digit reference that happens to pass Luhn
# still has to look like a card before it is called one.
_CARD_PREFIXES = (
    ("4", (13, 16, 19)),          # Visa
    ("34", (15,)), ("37", (15,)),  # Amex
    ("51", (16,)), ("52", (16,)), ("53", (16,)), ("54", (16,)), ("55", (16,)),
    ("6011", (16,)), ("65", (16,)),  # Discover
)

SENSITIVE_NAMES = {
    "ssn", "social security", "social security number", "national id",
    "passport", "passport number", "date of birth", "dob", "iban",
    "credit card", "card number", "cardnumber", "cvv", "password", "sort code",
    "tax id", "nino", "aadhaar", "pan number",
}


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _looks_like_card(value: str) -> bool:
    digits = re.sub(r"[ -]", "", value.strip())
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    if not any(
        digits.startswith(prefix) and len(digits) in lengths
        for prefix, lengths in _CARD_PREFIXES
    ):
        return False
    return _luhn(digits)


def _valid_iban(value: str) -> bool:
    text = re.sub(r"\s", "", value.strip().upper())
    if not _IBAN_RE.match(text):
        return False
    rearranged = text[4:] + text[:4]
    converted = "".join(
        str(ord(c) - 55) if c.isalpha() else c for c in rearranged
    )
    try:
        return int(converted) % 97 == 1
    except ValueError:
        return False


@check
def check_sensitive_data(table: Table, config: Config) -> list[Finding]:
    """Data that is not broken, but that you should know is there.

    Nothing here is a defect. The file loads, the values are valid, every
    other check passes — and the column holds customer email addresses that
    are about to be committed to a repository or emailed to a contractor.
    That is the same shape as everything else Sift reports: nothing looks
    wrong, and you are about to do something you cannot undo.

    No examples are ever attached to these findings. A tool that prints
    someone's card number into a CI log has made the problem worse.
    """
    findings: list[Finding] = []
    detectors = (
        ("email addresses", lambda v: bool(_EMAIL_RE.match(v.strip()))),
        ("payment card numbers", _looks_like_card),
        ("IBANs", _valid_iban),
    )

    for column in table.columns:
        profile = column.profile
        if profile.kind == EMPTY:
            continue
        present = profile.n_present
        if not present:
            continue

        for label, matches in detectors:
            # Only ever look at columns Sift has already decided are text.
            # A phone number written for a human — "+1 555-123-4567" — is
            # text; a latitude, an Elo rating and an ISO date are not, and
            # the first version of this check called all three phone numbers
            # because their punctuation happened to fit. A card number can
            # legitimately be stored as bare digits, so that one detector is
            # allowed to look at numeric columns; its prefix, length and Luhn
            # requirements are strict enough to stand on their own.
            if profile.kind in (NUMERIC, DATE) and label != "payment card numbers":
                continue
            hits = sum(1 for value in column.values if not is_blank(value) and matches(value))
            if not hits:
                continue
            share = hits / present
            # Two independent bars: enough hits that it is not a coincidence,
            # and enough of the column that it is the column's purpose.
            if hits < 3 or share < 0.20:
                continue
            findings.append(
                Finding(
                    "sensitive-data",
                    Severity.WARNING,
                    f"{column.name!r} holds {label} in {_pct(hits, present)} of rows "
                    f"({hits:,}). Nothing is wrong with the data — but check where "
                    "this file is going before it leaves your machine.",
                    column=column.name,
                    detail={"column_index": column.index, "kind": label, "count": hits},
                )
            )
            break  # one verdict per column

        # Phone numbers are handled separately because whether a shape counts
        # depends on the column's name, which the generic detectors do not see.
        named_phone = normalize_label(column.name) in _PHONE_NAMES
        if profile.kind not in (NUMERIC, DATE):
            phones = sum(
                1
                for value in column.values
                if not is_blank(value)
                and (
                    _PHONE_INTERNATIONAL.match(value.strip())
                    or (named_phone and _PHONE_PARENS.match(value.strip()))
                )
            )
            if phones >= 3 and phones / present >= 0.20:
                findings.append(
                    Finding(
                        "sensitive-data",
                        Severity.WARNING,
                        f"{column.name!r} holds phone numbers in "
                        f"{_pct(phones, present)} of rows ({phones:,}). Nothing is "
                        "wrong with the data — but check where this file is going "
                        "before it leaves your machine.",
                        column=column.name,
                        detail={
                            "column_index": column.index,
                            "kind": "phone numbers",
                            "count": phones,
                        },
                    )
                )

        if normalize_label(column.name) in SENSITIVE_NAMES:
            findings.append(
                Finding(
                    "sensitive-column-name",
                    Severity.INFO,
                    f"{column.name!r} is named like a field that carries personal "
                    "or financial data. Sift has not looked at the values.",
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
                f"{column.name!r} contains {_plural(len(broken), 'value')} with mangled "
                "characters — UTF-8 text that was decoded as Latin-1 upstream. "
                "Re-export rather than find-and-replace.",
                column=column.name,
                detail={"column_index": column.index, "count": len(broken)},
                examples=_samples(broken, config.max_examples),
            )
        )
    return findings


# Above this many columns sharing one finding, the list is the story rather
# than each entry. A 1,093-column survey export produced 97 constant-column
# notes and 107 outlier notes; nobody reads the 204th line of a report.
COLLAPSE_AFTER = 8


def collapse(findings: list[Finding]) -> list[Finding]:
    """Fold a finding repeated across many columns into one line.

    Errors are never folded — if a column will break something, its name has
    to be in the output. Warnings and notes are, because at this many the
    pattern is a property of the file rather than of any one column, and the
    full list stays in `detail` for anything reading the JSON.
    """
    counts: Counter = Counter(
        f.code for f in findings if f.column and f.severity is not Severity.ERROR
    )
    crowded = {code for code, n in counts.items() if n > COLLAPSE_AFTER}
    if not crowded:
        return findings

    out: list[Finding] = []
    done: set[str] = set()
    for finding in findings:
        if finding.code not in crowded or not finding.column:
            out.append(finding)
            continue
        if finding.code in done:
            continue
        done.add(finding.code)
        group = [f for f in findings if f.code == finding.code and f.column]
        names = [f.column for f in group]
        shown = ", ".join(names[:6])
        out.append(
            Finding(
                finding.code,
                finding.severity,
                f"{plural(len(group), 'column')} report this: {shown}, and "
                f"{len(group) - 6} more. Listed together because at this many it "
                "is a property of the file rather than of any one column.",
                detail={"columns": names, "collapsed": len(group)},
            )
        )
    return out


def run_checks(table: Table, config: Config) -> list[Finding]:
    findings = list(table.file_findings)
    for check_fn in CHECKS:
        findings.extend(check_fn(table, config))
    return collapse(config.filter(findings))
