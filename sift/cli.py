"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .checks import run_checks
from .config import Config, find_config, load_config
from .loading import LoadError, load
from .repair import HIGH, MEDIUM, plan_repairs, render_plan, write_csv, write_log
from .reporting import render_text, summarize, write_report

THRESHOLDS = {"error": 3, "warning": 2, "info": 1, "none": 99}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sift",
        description="Find the problems in a CSV before they reach your pipeline.",
    )
    parser.add_argument("--version", action="version", version=f"sift {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check_cmd = sub.add_parser("check", help="Lint one file.")
    check_cmd.add_argument("path", type=Path)
    check_cmd.add_argument("--key", action="append", default=[])
    check_cmd.add_argument("--delimiter")
    check_cmd.add_argument("--config", type=Path)
    check_cmd.add_argument("--no-config", action="store_true")
    check_cmd.add_argument("--format", choices=("text", "json", "html"), default="text")
    check_cmd.add_argument("--output", type=Path)
    check_cmd.add_argument("--fail-on", choices=tuple(THRESHOLDS), default=None)
    check_cmd.add_argument("--ignore", action="append", default=[])

    fix_cmd = sub.add_parser("fix", help="Write a repaired copy, and an audit log of every change.")
    fix_cmd.add_argument("path", type=Path)
    fix_cmd.add_argument("--output", type=Path)
    fix_cmd.add_argument("--log", type=Path)
    fix_cmd.add_argument("--min-confidence", choices=(HIGH, MEDIUM), default=HIGH)
    fix_cmd.add_argument("--dry-run", action="store_true")
    fix_cmd.add_argument("--force", action="store_true")
    fix_cmd.add_argument("--delimiter")
    fix_cmd.add_argument("--config", type=Path)
    fix_cmd.add_argument("--no-config", action="store_true")

    profile_cmd = sub.add_parser("profile", help="Describe each column, no judgement.")
    profile_cmd.add_argument("path", type=Path)
    profile_cmd.add_argument("--delimiter")

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


def exit_code(findings: list, config: Config) -> int:
    threshold = THRESHOLDS.get(config.fail_on, 3)
    return 1 if any(f.severity.rank >= threshold for f in findings) else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "profile":
            table = load(args.path, args.delimiter)
            print(f"{table.path}  {table.n_rows:,} rows, {len(table.columns)} columns")
            print()
            for column in table.columns:
                profile = column.profile
                print(
                    f"{profile.name[:28]:<28} {profile.kind:<12} "
                    f"{profile.null_rate:>6.1%} null  {profile.distinct:>7,} distinct"
                )
            return 0

        if args.command == "fix":
            config = resolve_config(args, args.path)
            table = load(args.path, args.delimiter)
            ragged = [f for f in table.file_findings if f.code == "ragged-rows"]
            if ragged and not args.force:
                print(f"sift: {ragged[0].message}", file=sys.stderr)
                return 2
            if not args.output and not args.dry_run:
                print("sift: pass --output PATH, or --dry-run to preview.", file=sys.stderr)
                return 2

            plan, rows = plan_repairs(table, config, args.min_confidence)
            destination = str(args.output) if args.output else "(dry run)"
            print(render_plan(table, plan, destination))
            if args.dry_run:
                return 0

            write_csv(args.output, table.header, rows, table.delimiter)
            if args.log:
                write_log(args.log, plan)
            before = run_checks(table, config)
            after = run_checks(load(args.output), config)
            print()
            print(f"Findings: {len(before)} before, {len(after)} after. {len(plan.refusals)} left for a human.")
            return 0

        if args.command == "check":
            config = resolve_config(args, args.path)
            table = load(args.path, args.delimiter)
            findings = run_checks(table, config)
            report = write_report(table, findings, args.format, args.output)
            if args.output:
                summary = summarize(findings)
                print(f"Wrote {args.output} ({summary['total']} findings).")
            else:
                print(report)
            return exit_code(findings, config)

    except LoadError as exc:
        print(f"sift: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"sift: {exc}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
