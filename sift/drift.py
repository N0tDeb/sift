"""Comparing today's file against a known-good one.

Most pipeline breakages are not a file that is wrong on its own — it is a file
that is different from the one the pipeline was written against. A renamed
column, a category that appeared overnight, a null rate that tripled. None of
those are errors in isolation, which is exactly why they get through.
"""

from __future__ import annotations

from .config import Config
from .findings import Finding, Severity
from .loading import Table
from .profiling import BOOLEAN, CATEGORICAL, EMPTY, NUMERIC
from .text import fmt_number, plural

NULL_RATE_JUMP = 0.10
MEDIAN_SHIFT = 0.25


def compare(baseline: Table, current: Table, config: Config) -> list[Finding]:
    findings: list[Finding] = []

    old_names = [c.name for c in baseline.columns]
    new_names = [c.name for c in current.columns]

    removed = [n for n in old_names if n not in new_names]
    added = [n for n in new_names if n not in old_names]

    for name in removed:
        findings.append(
            Finding(
                "column-removed",
                Severity.ERROR,
                f"{name!r} was in the baseline and is gone. Anything selecting it "
                "will fail or return nothing.",
                column=name,
            )
        )
    for name in added:
        findings.append(
            Finding(
                "column-added",
                Severity.INFO,
                f"{name!r} is new since the baseline.",
                column=name,
            )
        )

    shared = [n for n in old_names if n in new_names]
    if shared and [n for n in old_names if n in shared] != [
        n for n in new_names if n in shared
    ]:
        findings.append(
            Finding(
                "column-order-changed",
                Severity.WARNING,
                "Shared columns are in a different order. Harmless for named "
                "access, fatal for anything reading by position.",
            )
        )

    for name in shared:
        old = baseline.by_key(name)
        new = current.by_key(name)
        if old is None or new is None:
            continue
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
            if old_median and new_median:
                shift = abs(new_median - old_median) / abs(old_median)
                if shift >= MEDIAN_SHIFT:
                    findings.append(
                        Finding(
                            "distribution-shift",
                            Severity.WARNING,
                            f"{name!r} median moved {shift:.0%}, from "
                            f"{fmt_number(old_median)} to {fmt_number(new_median)}. Check for a "
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
