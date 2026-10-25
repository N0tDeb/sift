"""Comparing today's file against a known-good one.

Most pipeline breakages are not a file that is wrong on its own — it is a file
that is different from the one the pipeline was written against. A renamed
column, a category that appeared overnight, a null rate that tripled. None of
those are errors in isolation, which is exactly why they get through.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .config import Config
from .findings import Finding, Severity
from .loading import LoadError, Table
from .profiling import BOOLEAN, CATEGORICAL, EMPTY, NUMERIC
from .text import fmt_number, plural

NULL_RATE_JUMP = 0.10
MEDIAN_SHIFT = 0.25


def compare(baseline: Table, current: Table, config: Config) -> list[Finding]:
    # A header-only baseline cannot establish a meaningful expected row count,
    # null rate, category distribution or numeric range.  Treat it as a bad
    # input rather than declaring a changed file "clean".
    if baseline.n_rows == 0:
        raise LoadError(
            f"Baseline {baseline.path} contains no data rows; "
            "choose a populated known-good baseline."
        )
    findings: list[Finding] = []

    old_names = [column.name for column in baseline.columns]
    new_names = [column.name for column in current.columns]
    old_counts, new_counts = Counter(old_names), Counter(new_names)

    # Headers are not sets. Losing one of two identically named columns is
    # schema loss even if the remaining name still exists. Compare occurrence
    # counts, without misreporting the removal as a column reorder.
    for name in dict.fromkeys(old_names):
        delta = old_counts[name] - new_counts[name]
        if delta > 0:
            findings.append(
                Finding(
                    "column-removed",
                    Severity.ERROR,
                    f"{name!r} occurs {old_counts[name]} time(s) in the baseline "
                    f"and {new_counts[name]} time(s) now: {delta} occurrence(s) "
                    "removed. Positional consumers may read the wrong column.",
                    column=name,
                    detail={"from": old_counts[name], "to": new_counts[name]},
                )
            )
    for name in dict.fromkeys(new_names):
        delta = new_counts[name] - old_counts[name]
        if delta > 0:
            findings.append(
                Finding(
                    "column-added",
                    Severity.INFO,
                    f"{name!r} occurs {old_counts[name]} time(s) in the baseline "
                    f"and {new_counts[name]} time(s) now: {delta} occurrence(s) added.",
                    column=name,
                    detail={"from": old_counts[name], "to": new_counts[name]},
                )
            )

    overlap = old_counts & new_counts

    def shared_sequence(names: list[str]) -> list[str]:
        seen: Counter[str] = Counter()
        result = []
        for name in names:
            seen[name] += 1
            if seen[name] <= overlap[name]:
                result.append(name)
        return result

    if shared_sequence(old_names) != shared_sequence(new_names):
        findings.append(
            Finding(
                "column-order-changed",
                Severity.WARNING,
                "Shared columns are in a different order. Harmless for named "
                "access, fatal for anything reading by position.",
            )
        )

    # Compare each shared occurrence once instead of comparing the first
    # duplicate repeatedly. Keep the original display name in diagnostics.
    old_groups = defaultdict(list)
    new_groups = defaultdict(list)
    for column in baseline.columns:
        old_groups[column.name].append(column)
    for column in current.columns:
        new_groups[column.name].append(column)
    for name in dict.fromkeys(old_names):
        for old, new in zip(old_groups[name], new_groups[name], strict=False):
            old_p, new_p = old.profile, new.profile

            if old_p.kind != new_p.kind and EMPTY not in (old_p.kind, new_p.kind):
                findings.append(
                    Finding(
                        "type-changed",
                        Severity.ERROR,
                        f"{name!r} was {old_p.kind} in the baseline and is now "
                        f"{new_p.kind}.",
                        column=name,
                        detail={"from": old_p.kind, "to": new_p.kind},
                    )
                )

            jump = new_p.null_rate - old_p.null_rate
            if jump >= NULL_RATE_JUMP:
                findings.append(
                    Finding(
                        "null-rate-jump",
                        Severity.WARNING,
                        f"{name!r} went from {old_p.null_rate:.1%} empty to "
                        f"{new_p.null_rate:.1%}. Something upstream stopped populating it.",
                        column=name,
                        detail={"from": round(old_p.null_rate, 4), "to": round(new_p.null_rate, 4)},
                    )
                )

            if old_p.kind == NUMERIC == new_p.kind:
                old_stats, new_stats = old_p.stats(), new_p.stats()
                old_median = old_stats.get("median")
                new_median = new_stats.get("median")
                # Relative movement is well-defined when the baseline median is
                # non-zero, including when the current median has fallen to zero.
                # A zero baseline has no meaningful percentage denominator, so do
                # not invent an absolute threshold here.
                if old_median is not None and old_median != 0 and new_median is not None:
                    shift = abs(new_median - old_median) / abs(old_median)
                    if shift >= MEDIAN_SHIFT:
                        findings.append(
                            Finding(
                                "distribution-shift",
                                Severity.WARNING,
                                f"{name!r} median moved {shift:.0%}, from "
                                f"{fmt_number(old_median)} to {fmt_number(new_median)}. "
                                "Check for a "
                                "unit change before you trust the trend.",
                                column=name,
                                detail={"from": old_median, "to": new_median},
                            )
                        )

            categorical = {CATEGORICAL, BOOLEAN}
            if old_p.kind in categorical and new_p.kind in categorical:
                new_levels = set(new_p.value_counts) - set(old_p.value_counts)
                gone_levels = set(old_p.value_counts) - set(new_p.value_counts)
                if new_levels:
                    findings.append(
                        Finding(
                            "new-category",
                            Severity.WARNING,
                            f"{name!r} has "
                            f"{plural(len(new_levels), 'category', 'categories')} "
                            "not seen in the baseline. Anything that maps this "
                            "column will not cover them.",
                            column=name,
                            examples=sorted(new_levels)[: config.max_examples],
                        )
                    )
                if gone_levels:
                    findings.append(
                        Finding(
                            "missing-category",
                            Severity.INFO,
                            f"{name!r} no longer contains "
                            f"{plural(len(gone_levels), 'category', 'categories')} present in the "
                            "baseline.",
                            column=name,
                            examples=sorted(gone_levels)[: config.max_examples],
                        )
                    )

    if baseline.n_rows:
        change = (current.n_rows - baseline.n_rows) / baseline.n_rows
        if abs(change) >= 0.5:
            findings.append(
                Finding(
                    "row-count-shift",
                    Severity.WARNING,
                    f"Row count went from {baseline.n_rows:,} to {current.n_rows:,} "
                    f"({change:+.0%}).",
                    detail={"from": baseline.n_rows, "to": current.n_rows},
                )
            )
    return config.filter(findings)
