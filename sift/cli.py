"""Command line entry point.

Exit codes are the contract with CI:
  0  nothing at or above the failure threshold
  1  findings at or above it
  2  the file could not be read at all

That distinction matters: a broken pipeline should not look like a clean file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .checks import run_checks
from .config import FAIL_ON_LEVELS, Config, ConfigError, find_config, load_config
from .drift import compare
from .impact import ImpactError, duplicate_impact, group_impact, sum_impact
from .impact import render as render_impact
from .initialize import build as build_config
from .initialize import describe, summarise
from .loading import LoadError, Table
from .profiling import ColumnProfile
from .references import Reference, ReferenceError, check_reference
from .repair import HIGH, MEDIUM, plan_repairs, render_plan, write_csv, write_log
from .reporting import render_json, render_text, summarize, write_report
from .sources import EXCEL_SUFFIXES, PARQUET_SUFFIXES, SUPPORTED, discover, load_any
from .text import fmt_number, plural

THRESHOLDS = {"error": 3, "warning": 2, "info": 1, "none": 99}
assert set(THRESHOLDS) == set(FAIL_ON_LEVELS)  # the CLI and the config agree
NON_CSV_SUFFIXES = EXCEL_SUFFIXES | PARQUET_SUFFIXES | {".xls"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sift",
        description="Find the problems in a CSV before they reach your pipeline.",
    )
    parser.add_argument("--version", action="version", version=f"sift {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--delimiter", help="Override delimiter detection.")
        sp.add_argument("--config", type=Path, help="Path to a sift.toml.")
        sp.add_argument("--no-config", action="store_true", help="Ignore any sift.toml.")
        sp.add_argument(
            "--format", choices=("text", "json", "html"), default="text",
        )
        sp.add_argument("--output", type=Path, help="Write the report to a file.")
        sp.add_argument(
            "--fail-on",
            choices=FAIL_ON_LEVELS,
            help="Lowest severity that exits non-zero (default: error).",
        )
        sp.add_argument("--ignore", action="append", default=[], metavar="CODE")
        sp.add_argument("--max-rows", type=int, help="Read at most this many rows.")

    check_cmd = sub.add_parser("check", help="Lint one or more files.")
    check_cmd.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="PATH",
        help="Files or directories. Directories are searched for supported files.",
    )
    check_cmd.add_argument("--sheet", help="Worksheet to read from an .xlsx file.")
    check_cmd.add_argument(
        "--references",
        action="append",
        default=[],
        metavar="FILE:LOCAL=FOREIGN",
        help="Check that a column's values exist in another file, "
        "e.g. customers.csv:customer_id=id",
    )
    check_cmd.add_argument(
        "--key", action="append", default=[], help="Column that must be unique and present."
    )
    common(check_cmd)

    diff_cmd = sub.add_parser("diff", help="Compare a file against a known-good baseline.")
    diff_cmd.add_argument("baseline", type=Path)
    diff_cmd.add_argument("current", type=Path)
    diff_cmd.add_argument("--sheet")
    common(diff_cmd)

    fix_cmd = sub.add_parser(
        "fix", help="Write a repaired copy, and an audit log of every change."
    )
    fix_cmd.add_argument("path", type=Path)
    fix_cmd.add_argument("--output", type=Path, help="Where to write the repaired file.")
    fix_cmd.add_argument("--log", type=Path, help="Write a CSV of every cell changed.")
    fix_cmd.add_argument(
        "--min-confidence",
        choices=(HIGH, MEDIUM),
        default=HIGH,
        help="Lowest confidence rule to apply (default: high).",
    )
    fix_cmd.add_argument(
        "--drop-duplicates", action="store_true", help="Keep the first of each repeated row."
    )
    fix_cmd.add_argument(
        "--dry-run", action="store_true", help="Report the plan without writing anything."
    )
    fix_cmd.add_argument(
        "--force",
        action="store_true",
        help="Rewrite even when the source could not be parsed cleanly.",
    )
    fix_cmd.add_argument("--delimiter")
    fix_cmd.add_argument("--sheet")
    fix_cmd.add_argument("--max-rows", type=int)
    fix_cmd.add_argument("--config", type=Path)
    fix_cmd.add_argument("--no-config", action="store_true")

    impact_cmd = sub.add_parser(
        "impact",
        help="Quantify how wrong one aggregate is: SUM, GROUP BY, row counts.",
    )
    impact_cmd.add_argument("path", type=Path)
    impact_cmd.add_argument("--sum", metavar="COLUMN", help="Numeric column to total.")
    impact_cmd.add_argument("--group-by", metavar="COLUMN", help="Column to group on.")
    impact_cmd.add_argument("--delimiter")
    impact_cmd.add_argument("--sheet")
    impact_cmd.add_argument("--max-rows", type=int)
    impact_cmd.add_argument("--config", type=Path)
    impact_cmd.add_argument("--no-config", action="store_true")

    init_cmd = sub.add_parser(
        "init", help="Write a sift.toml learned from a file you already trust."
    )
    init_cmd.add_argument("path", type=Path)
    init_cmd.add_argument(
        "--output", type=Path, default=Path("sift.toml"), help="Where to write it."
    )
    init_cmd.add_argument("--key", action="append", default=[])
    init_cmd.add_argument(
        "--force", action="store_true", help="Write even if the baseline has problems."
    )
    init_cmd.add_argument("--dry-run", action="store_true")
    init_cmd.add_argument("--delimiter")
    init_cmd.add_argument("--sheet")

    profile_cmd = sub.add_parser("profile", help="Describe each column, no judgement.")
    profile_cmd.add_argument("path", type=Path)
    profile_cmd.add_argument("--sheet")
    profile_cmd.add_argument("--delimiter")
    profile_cmd.add_argument("--max-rows", type=int)

    return parser


def resolve_config(args: argparse.Namespace, anchor: Path) -> Config:
    path = args.config
    if path is None and not getattr(args, "no_config", False):
        path = find_config(anchor)
    config = load_config(path)

    if getattr(args, "key", None):
        config.key = args.key
    if getattr(args, "ignore", None):
        config.ignore.extend(args.ignore)
    if getattr(args, "fail_on", None):
        config.fail_on = args.fail_on
    return config


def guard_output(output: Path | None, *inputs: Path | None) -> None:
    """Refuse to write over a file we were asked to read.

    `sift check data.csv --format html --output data.csv` is an easy typo and
    it silently replaced the user's data with an HTML report. There is no
    reading of that command where destroying the input is the intent.
    """
    if output is None:
        return
    for source in inputs:
        if source is None:
            continue
        try:
            same = output.resolve() == source.resolve()
        except OSError:  # pragma: no cover - unresolvable path
            same = False
        if same:
            raise OutputCollision(
                f"--output would overwrite the input file {source}. "
                "Choose a different path."
            )


class OutputCollision(Exception):
    """The requested output path is also an input."""


def exit_code(findings: list, config: Config) -> int:
    threshold = THRESHOLDS.get(config.fail_on, 3)
    return 1 if any(f.severity.rank >= threshold for f in findings) else 0


def _profile_row(profile: ColumnProfile) -> str:
    stats = profile.stats()
    extra = ""
    if stats:
        extra = (
            f"  median {fmt_number(stats['median'])}"
            f"  range {fmt_number(stats['min'])} to {fmt_number(stats['max'])}"
        )
    return (
        f"{profile.name[:28]:<28} {profile.kind:<12} "
        f"{profile.null_rate:>6.1%} null  {profile.distinct:>7,} distinct{extra}"
    )


def check_many(targets: list[Path], config: Config, args: argparse.Namespace) -> int:
    """Check a set of files and report across them.

    The per-file counts matter less than the last section. A pipeline that
    writes a file a day has the same defect in every one of them, and seeing
    "number-as-text in 11 of 12 files" points at the export that produced them
    rather than at twelve separate cleanup jobs.
    """
    results: list[tuple[Path, list]] = []
    failures: list[tuple[Path, str]] = []

    for target in targets:
        try:
            table = load_any(target, args.delimiter, args.sheet, args.max_rows)
        except (LoadError, OSError) as exc:
            failures.append((target, str(exc)))
            continue
        findings = run_checks(table, config)
        for reference in getattr(args, "_references", []):
            try:
                findings.extend(check_reference(table, reference, config, load_any))
            except ReferenceError as exc:
                failures.append((target, str(exc)))
        results.append((target, findings))

    if args.format == "json":
        payload = {
            "files": [
                {
                    "file": str(path),
                    "summary": summarize(findings),
                    "findings": [f.to_dict() for f in findings],
                }
                for path, findings in results
            ],
            "unreadable": [{"file": str(p), "error": e} for p, e in failures],
        }
        body = json.dumps(payload, indent=2, default=str)
        if args.output:
            args.output.write_text(body, encoding="utf-8")
            print(f"Wrote {args.output}")
        else:
            print(body)
    else:
        width = max(len(str(path)) for path, _ in results) if results else 20
        for path, findings in results:
            counts = summarize(findings)
            tally = ", ".join(
                f"{counts[k]} {k}" for k in ("error", "warning", "info") if counts[k]
            )
            print(f"{str(path):<{width}}  {tally or 'clean'}")
        for path, error in failures:
            print(f"{str(path):<{width}}  unreadable: {error}")

        spread: Counter = Counter()
        for _, findings in results:
            spread.update({f.code for f in findings})
        shared = [(code, n) for code, n in spread.most_common() if n > 1]
        if shared:
            print()
            print("Across files:")
            for code, count in shared[:8]:
                print(f"  {code:<26} {count} of {len(results)} files")

    worst = [f for _, findings in results for f in findings]
    code = exit_code(worst, config)
    return 2 if failures and not results else code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "profile":
            table = load_any(args.path, args.delimiter, args.sheet, args.max_rows)
            print(
                f"{table.path}  {plural(table.n_rows, 'row')}, "
                f"{plural(len(table.columns), 'column')}"
            )
            print()
            for column in table.columns:
                print(_profile_row(column.profile))
            return 0

        if args.command == "init":
            guard_output(args.output, args.path)
            table = load_any(args.path, args.delimiter, args.sheet)
            findings = run_checks(table, Config())
            _, refusable = describe(findings)

            print(summarise(table, findings, Config()))

            if refusable and not args.force:
                print()
                print(
                    "sift: refusing to learn from a file with unresolved problems. "
                    "Pass --force to accept them anyway.",
                    file=sys.stderr,
                )
                return 2

            body = build_config(table, findings, args.key or None)
            if args.dry_run:
                print()
                print(body)
                return 0
            if args.output.exists() and not args.force:
                print(
                    f"sift: {args.output} already exists. Pass --force to replace it.",
                    file=sys.stderr,
                )
                return 2
            args.output.write_text(body, encoding="utf-8")
            print()
            print(f"Wrote {args.output}. Read it, then commit it.")
            return 0

        if args.command == "impact":
            if not args.sum and not args.group_by:
                print("sift: pass --sum COLUMN and/or --group-by COLUMN.", file=sys.stderr)
                return 2
            config = resolve_config(args, args.path)
            table = load_any(args.path, args.delimiter, args.sheet, args.max_rows)
            total = sum_impact(table, config, args.sum) if args.sum else None
            groups = (
                group_impact(table, config, args.group_by, args.sum)
                if args.group_by
                else None
            )
            duplicates = duplicate_impact(table, config, args.sum)
            print(render_impact(table, total, groups, duplicates))
            return 0

        if args.command == "fix":
            config = resolve_config(args, args.path)
            table = load_any(args.path, args.delimiter, args.sheet, args.max_rows)
            ragged = [f for f in table.file_findings if f.code == "ragged-rows"]
            if ragged and not args.force:
                # Refusing here rather than writing a plausible-looking file: the
                # rows Sift could not parse were padded or truncated on read, so
                # rewriting would make that loss permanent and invisible.
                print(f"sift: {ragged[0].message}", file=sys.stderr)
                print(
                    "sift: refusing to rewrite a file that did not parse cleanly. "
                    "Fix the quoting at the source, or pass --force to accept the "
                    "truncation.",
                    file=sys.stderr,
                )
                return 2

            if not args.output and not args.dry_run:
                print("sift: pass --output PATH, or --dry-run to preview.", file=sys.stderr)
                return 2

            # `fix` writes delimited text whatever it read. Letting someone
            # write CSV bytes to a path ending .xlsx produces a file that every
            # tool downstream will open, fail on, and blame them for.
            if args.output and args.output.suffix.lower() in NON_CSV_SUFFIXES:
                print(
                    f"sift: fix writes delimited text, so {args.output.name} would "
                    "not be a real workbook. Choose a .csv or .tsv path.",
                    file=sys.stderr,
                )
                return 2

            guard_output(args.output, args.path)
            guard_output(args.log, args.path)

            plan, rows = plan_repairs(
                table, config, args.min_confidence, args.drop_duplicates
            )
            destination = str(args.output) if args.output else "(dry run)"

            # Write before reporting. `sift fix ... | head` closes the pipe part
            # way through the summary, and a half-printed report must not mean a
            # file that never got written.
            if not args.dry_run:
                write_csv(args.output, table.header, rows, table.delimiter)
                if args.log:
                    write_log(args.log, plan)

            print(render_plan(table, plan, destination))
            if args.dry_run:
                return 0

            before = run_checks(table, config)
            after = run_checks(load_any(args.output), config)
            print()
            print(
                f"Findings: {len(before)} before, {len(after)} after. "
                f"{len(plan.refusals)} left for a human."
            )
            if args.log:
                print(f"Audit log: {args.log}")
            return 0

        if args.command == "check":
            targets = discover(args.paths)
            if not targets:
                print(
                    "sift: nothing to check. Supported extensions: "
                    + ", ".join(sorted(SUPPORTED)),
                    file=sys.stderr,
                )
                return 2

            config = resolve_config(args, targets[0])

            references = [Reference.parse(spec) for spec in args.references]

            for target in targets:
                guard_output(args.output, target)

            if len(targets) == 1:
                table = load_any(targets[0], args.delimiter, args.sheet, args.max_rows)
                findings = run_checks(table, config)
                for reference in references:
                    findings.extend(check_reference(table, reference, config, load_any))
                report = write_report(table, findings, args.format, args.output)
                if args.output:
                    summary = summarize(findings)
                    print(f"Wrote {args.output} ({summary['total']} findings).")
                else:
                    print(report)
                return exit_code(findings, config)

            args._references = references
            return check_many(targets, config, args)

        if args.command == "diff":
            config = resolve_config(args, args.current)
            guard_output(args.output, args.baseline, args.current)
            baseline = load_any(args.baseline, args.delimiter, args.sheet, args.max_rows)
            current = load_any(args.current, args.delimiter, args.sheet, args.max_rows)
            findings = compare(baseline, current, config)
            view = Table(
                path=current.path,
                header=current.header,
                columns=current.columns,
                n_rows=current.n_rows,
                delimiter=current.delimiter,
            )
            if args.format == "json":
                report = render_json(view, findings)
            elif args.format == "html":
                from .reporting import render_html

                report = render_html(view, findings)
            else:
                report = render_text(view, findings)
            if args.output:
                args.output.write_text(report, encoding="utf-8")
                print(f"Wrote {args.output} ({len(findings)} findings).")
            else:
                print(report)
            return exit_code(findings, config)

    except BrokenPipeError:
        # `sift check big.csv | head` is a normal thing to do, and should not
        # look like a failure.
        return 0
    except (
        ConfigError,
        ReferenceError,
        ImpactError,
        LoadError,
        OutputCollision,
        OSError,
    ) as exc:
        # Every one of these means "we could not do the job you asked for",
        # which is exit 2 — deliberately distinct from exit 1, "we did the job
        # and the file has problems". A pipeline must be able to tell a broken
        # tool from broken data.
        print(f"sift: {exc}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
