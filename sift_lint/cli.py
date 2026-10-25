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
from .baselines import (
    BaselineError,
    delete_baseline,
    get_baseline,
    list_baselines,
    save_baseline,
)
from .checks import run_checks, sensitive_columns
from .config import FAIL_ON_LEVELS, Config, ConfigError, find_config, load_config
from .drift import compare
from .findings import redact_sensitive_findings
from .github_annotations import render_annotations
from .impact import (
    ImpactError,
    aggregate_impact,
    duplicate_impact,
    group_impact,
    prepare_repaired_rows,
    sum_impact,
)
from .impact import (
    render as render_impact,
)
from .inference import is_formula_injection
from .initialize import build as build_config
from .initialize import describe, summarise
from .loading import LoadError, Table
from .output import atomic_write_text, preflight_output_paths
from .profiling import ColumnProfile
from .references import Reference, ReferenceError, check_reference
from .repair import (
    HIGH,
    MEDIUM,
    Refusal,
    plan_repairs,
    render_plan,
    render_plan_json,
    render_rule_explanations,
    write_csv,
    write_log,
)
from .reporting import render_batch_html, render_json, render_text, summarize, write_report
from .sources import (
    EXCEL_SUFFIXES,
    PARQUET_SUFFIXES,
    SUPPORTED,
    discover,
    excel_sheet_names,
    load_any,
)
from .text import fmt_number, plural, terminal_safe

THRESHOLDS = {"error": 3, "warning": 2, "info": 1, "none": 99}
assert set(THRESHOLDS) == set(FAIL_ON_LEVELS)  # the CLI and the config agree
NON_CSV_SUFFIXES = EXCEL_SUFFIXES | PARQUET_SUFFIXES | {".xls"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sift-lint",
        description="Find data-quality problems in CSV, Excel and Parquet files.",
    )
    parser.add_argument("--version", action="version", version=f"sift-lint {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser, *, github: bool = False) -> None:
        sp.add_argument("--delimiter", help="Override delimiter detection.")
        sp.add_argument("--config", type=Path, help="Path to a sift.toml.")
        sp.add_argument("--no-config", action="store_true", help="Ignore any sift.toml.")
        sp.add_argument(
            "--format", choices=("text", "json", "html", "github") if github
            else ("text", "json", "html"), default="text",
        )
        sp.add_argument("--output", type=Path, help="Write the report to a file.")
        sp.add_argument(
            "--fail-on",
            choices=FAIL_ON_LEVELS,
            help="Lowest severity that exits non-zero (default: error).",
        )
        sp.add_argument("--ignore", action="append", default=[], metavar="CODE")
        sp.add_argument(
            "--max-rows", type=int, help="Read at most this many rows (must be positive)."
        )

    check_cmd = sub.add_parser("check", help="Lint one or more files.")
    check_cmd.add_argument(
        "paths",
        type=Path,
        nargs="+",
        metavar="PATH",
        help="Files or directories. Directories are searched for supported files.",
    )
    check_sheet = check_cmd.add_mutually_exclusive_group()
    check_sheet.add_argument("--sheet", help="Worksheet to read from an .xlsx file.")
    check_sheet.add_argument(
        "--all-sheets", action="store_true",
        help="Inspect every worksheet in Excel workbooks, including hidden sheets.",
    )
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
    common(check_cmd, github=True)


    baseline_cmd = sub.add_parser(
        "baseline", help="Manage local named baselines for repeatable comparisons."
    )
    baseline_actions = baseline_cmd.add_subparsers(dest="baseline_action", required=True)
    save_cmd = baseline_actions.add_parser("save", help="Save an immutable named file snapshot.")
    save_cmd.add_argument("name", help="Unique baseline name.")
    save_cmd.add_argument("path", type=Path, help="Source data file to snapshot.")
    save_cmd.add_argument("--delimiter", help="CSV delimiter to retain for future diff runs.")
    save_cmd.add_argument("--sheet", help="Excel worksheet to retain for future diff runs.")
    list_cmd = baseline_actions.add_parser("list", help="List saved baselines.")
    show_cmd = baseline_actions.add_parser("show", help="Show a baseline's metadata.")
    show_cmd.add_argument("name")
    verify_cmd = baseline_actions.add_parser("verify", help="Verify a saved snapshot's SHA-256.")
    verify_cmd.add_argument("name")
    remove_cmd = baseline_actions.add_parser("delete", help="Delete a saved baseline.")
    remove_cmd.add_argument("name")
    remove_cmd.add_argument(
        "--yes", action="store_true", help="Confirm deletion of the raw snapshot."
    )
    for cmd in (save_cmd, list_cmd, show_cmd, verify_cmd, remove_cmd):
        cmd.add_argument(
            "--store", type=Path, help="Baseline storage folder (default: .sift/baselines)."
        )

    diff_cmd = sub.add_parser("diff", help="Compare a file against a known-good baseline.")
    diff_cmd.add_argument("baseline", type=Path)
    diff_cmd.add_argument("current", type=Path)
    diff_cmd.add_argument("--sheet")
    diff_cmd.add_argument(
        "--saved", action="store_true",
        help="Interpret BASELINE as a saved name rather than a file path.",
    )
    diff_cmd.add_argument(
        "--baseline-store", type=Path,
        help="Storage folder for --saved (default: .sift/baselines).",
    )
    common(diff_cmd)

    fix_cmd = sub.add_parser(
        "fix", help="Write a repaired copy; use --log to record individual changes."
    )
    fix_cmd.add_argument("path", type=Path)
    fix_cmd.add_argument("--output", type=Path, help="Where to write the repaired file.")
    fix_cmd.add_argument(
        "--log", type=Path,
        help="Write a CSV audit of cell changes and deleted duplicate rows.",
    )
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
        "--format", choices=("text", "json"), default="text",
        help="Preview format; JSON requires --dry-run (default: text).",
    )
    fix_cmd.add_argument(
        "--explain", action="store_true",
        help="Explain the proposed repair rules in the text plan.",
    )
    fix_cmd.add_argument(
        "--show-values", action="store_true",
        help="Opt in to showing non-sensitive before/after values in JSON dry-run previews.",
    )
    fix_cmd.add_argument(
        "--force",
        action="store_true",
        help=(
            "Accept certain unresolved CSV rewrite hazards (for example ragged "
            "rows or existing formula-like values). Never overrides Excel "
            "formula-loss protection or newly introduced formula hazards."
        ),
    )
    fix_cmd.add_argument("--delimiter")
    fix_cmd.add_argument("--sheet")
    fix_cmd.add_argument("--max-rows", type=int)
    fix_cmd.add_argument("--config", type=Path)
    fix_cmd.add_argument("--no-config", action="store_true")

    impact_cmd = sub.add_parser(
        "impact",
        help="Compare SUM, AVG, MIN, MAX and GROUP BY before and after repair.",
    )
    impact_cmd.add_argument("path", type=Path)
    impact_cmd.add_argument("--sum", metavar="COLUMN", help="Numeric column to total.")
    impact_cmd.add_argument(
        "--avg", "--mean", metavar="COLUMN", dest="avg",
        help="Compare numeric average before and after repair.",
    )
    impact_cmd.add_argument("--min", metavar="COLUMN", help="Compare numeric minimum.")
    impact_cmd.add_argument("--max", metavar="COLUMN", help="Compare numeric maximum.")
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
    profile_sheet = profile_cmd.add_mutually_exclusive_group()
    profile_sheet.add_argument("--sheet")
    profile_sheet.add_argument(
        "--all-sheets", action="store_true",
        help="Profile every worksheet in an Excel workbook, including hidden sheets.",
    )
    profile_cmd.add_argument("--delimiter")
    profile_cmd.add_argument("--max-rows", type=int)
    profile_cmd.add_argument("--config", type=Path)
    profile_cmd.add_argument("--no-config", action="store_true")

    return parser


def effective_config_path(args: argparse.Namespace, anchor: Path) -> Path | None:
    """Return the config file read by an invocation, if any."""
    path = getattr(args, "config", None)
    if path is None and not getattr(args, "no_config", False):
        path = find_config(anchor)
    return path


def resolve_config(args: argparse.Namespace, anchor: Path) -> Config:
    config = load_config(effective_config_path(args, anchor))

    if getattr(args, "key", None):
        config.key = args.key
    if getattr(args, "ignore", None):
        config.ignore.extend(args.ignore)
    if getattr(args, "fail_on", None):
        config.fail_on = args.fail_on
    return config


def guard_output(output: Path | None, *inputs: Path | None) -> None:
    """Refuse to write over a file we were asked to read.

    `sift-lint check data.csv --format html --output data.csv` is an easy typo and
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


def _profile_row(profile: ColumnProfile, *, sensitive: bool = False) -> str:
    stats = None if sensitive else profile.stats()
    extra = ""
    if stats:
        extra = (
            f"  median {fmt_number(stats['median'])}"
            f"  range {fmt_number(stats['min'])} to {fmt_number(stats['max'])}"
        )
    return (
        f"{terminal_safe(profile.name)[:28]:<28} {profile.kind:<12} "
        f"{profile.null_rate:>6.1%} null  {profile.distinct:>7,} distinct{extra}"
    )


def _cached_reference_loader():
    """Load each foreign reference table once for one check invocation.

    Reference checks are read-only, so reusing the same Table avoids repeating
    file I/O and profiling while also giving every local file a consistent
    snapshot of the foreign side. Failed loads are not cached.
    """
    cache: dict[Path, Table] = {}

    def load_reference(path: str | Path) -> Table:
        key = Path(path)
        if key not in cache:
            cache[key] = load_any(key)
        return cache[key]

    return load_reference


def check_many(
    targets: list[Path],
    args: argparse.Namespace,
    references: list[Reference],
    reference_load,
) -> int:
    """Check a set of files and report across them.

    The per-file counts matter less than the last section. A pipeline that
    writes a file a day has the same defect in every one of them, and seeing
    "number-as-text in 11 of 12 files" points at the export that produced them
    rather than at twelve separate cleanup jobs.
    """
    results: list[tuple[Path, list]] = []
    result_codes: list[int] = []
    failures: list[tuple[Path, str]] = []

    for target in targets:
        config = resolve_config(args, target)
        try:
            table = load_any(target, args.delimiter, args.sheet, args.max_rows)
        except (LoadError, OSError) as exc:
            failures.append((target, str(exc)))
            continue
        findings = run_checks(table, config)
        for reference in references:
            try:
                findings.extend(check_reference(table, reference, config, reference_load))
            except (ReferenceError, LoadError, OSError) as exc:
                failures.append((target, str(exc)))
        findings = redact_sensitive_findings(findings, sensitive_columns(table, config))
        results.append((target, findings))
        result_codes.append(exit_code(findings, config))

    if args.format == "github":
        body = render_annotations(
            [(path, None, findings) for path, findings in results],
            [(path, None, error) for path, error in failures],
        )
    elif args.format == "json":
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
    elif args.format == "html":
        body = render_batch_html(results, failures)
    else:
        display_paths = {path: terminal_safe(path) for path, _ in results}
        width = max((len(name) for name in display_paths.values()), default=20)
        lines: list[str] = []
        for path, findings in results:
            counts = summarize(findings)
            tally = ", ".join(
                f"{counts[k]} {k}" for k in ("error", "warning", "info") if counts[k]
            )
            lines.append(f"{display_paths[path]:<{width}}  {tally or 'clean'}")
        for path, error in failures:
            safe_path = terminal_safe(path)
            lines.append(f"{safe_path:<{width}}  unreadable: {terminal_safe(error)}")

        spread: Counter = Counter()
        for _, findings in results:
            spread.update({f.code for f in findings})
        shared = [(code, n) for code, n in spread.most_common() if n > 1]
        if shared:
            lines.extend(("", "Across files:"))
            for code, count in shared[:8]:
                lines.append(f"  {code:<26} {count} of {len(results)} files")
        body = "\n".join(lines)

    if args.output:
        atomic_write_text(args.output, body)
        print(f"Wrote {terminal_safe(args.output)}")
    else:
        print(body)

    # Even one unreadable file means the requested batch was not fully checked.
    return 2 if failures else max(result_codes, default=0)


def check_all_sheets(
    targets: list[Path], args: argparse.Namespace,
    references: list[Reference], reference_load,
) -> int:
    """Inspect every Excel sheet, keeping file and worksheet identities distinct.

    Keep this opt-in report separate from single-file reports: a clean sheet
    must not hide an unreadable or damaged sibling. Other supported file types
    in a directory continue to be checked once.
    """
    results: list[tuple[Path, str | None, Table, list]] = []
    failures: list[tuple[Path, str | None, str]] = []
    return_codes: list[int] = []

    for target in targets:
        if target.suffix.lower() in EXCEL_SUFFIXES:
            try:
                sheets: list[str | None] = excel_sheet_names(target)
            except (LoadError, OSError) as exc:
                failures.append((target, None, str(exc)))
                continue
        else:
            sheets = [None]
        config = resolve_config(args, target)
        for sheet in sheets:
            try:
                table = load_any(target, args.delimiter, sheet, args.max_rows)
            except (LoadError, OSError) as exc:
                failures.append((target, sheet, str(exc)))
                continue
            # No worksheet is omitted in --all-sheets mode, so the ordinary
            # single-sheet warning would be misleading in these reports.
            findings = [f for f in run_checks(table, config) if f.code != "unread-sheets"]
            for reference in references:
                try:
                    findings.extend(check_reference(table, reference, config, reference_load))
                except (ReferenceError, LoadError, OSError) as exc:
                    failures.append((target, sheet, str(exc)))
            findings = redact_sensitive_findings(findings, sensitive_columns(table, config))
            results.append((target, sheet, table, findings))
            return_codes.append(exit_code(findings, config))

    def display(path: Path, sheet: str | None) -> str:
        return f"{path} [sheet: {sheet}]" if sheet is not None else str(path)

    if args.format == "github":
        body = render_annotations(
            [(path, sheet, findings) for path, sheet, _, findings in results],
            failures,
        )
    elif args.format == "json":
        payload = {
            "files": [
                {
                    "file": str(path),
                    **({"sheet": sheet} if sheet is not None else {}),
                    "rows": table.n_rows,
                    "columns": [col.name for col in table.columns],
                    "summary": summarize(findings),
                    "findings": [finding.to_dict() for finding in findings],
                }
                for path, sheet, table, findings in results
            ],
            "unreadable": [
                {
                    "file": str(path),
                    **({"sheet": sheet} if sheet is not None else {}),
                    "error": error,
                }
                for path, sheet, error in failures
            ],
        }
        body = json.dumps(payload, indent=2, default=str)
    elif args.format == "html":
        body = render_batch_html(
            [(Path(display(path, sheet)), findings) for path, sheet, _, findings in results],
            [(Path(display(path, sheet)), error) for path, sheet, error in failures],
        )
    else:
        lines = []
        for path, sheet, _table, findings in results:
            tally = summarize(findings)
            counts = ", ".join(
                f"{tally[k]} {k}" for k in ("error", "warning", "info") if tally[k]
            )
            lines.append(f"{terminal_safe(display(path, sheet))}  {counts or 'clean'}")
        for path, sheet, error in failures:
            lines.append(
                f"{terminal_safe(display(path, sheet))}  unreadable: {terminal_safe(error)}"
            )
        body = "\n".join(lines)

    if args.output:
        atomic_write_text(args.output, body)
        print(f"Wrote {terminal_safe(args.output)}")
    else:
        print(body)
    return 2 if failures else max(return_codes, default=0)


def _run_profile(args: argparse.Namespace) -> int:
    if args.all_sheets:
        if args.path.suffix.lower() not in EXCEL_SUFFIXES:
            raise LoadError("--all-sheets for profile requires an .xlsx or .xlsm workbook")
        config = resolve_config(args, args.path)
        failures = False
        for sheet in excel_sheet_names(args.path):
            try:
                table = load_any(args.path, args.delimiter, sheet, args.max_rows)
            except (LoadError, OSError) as exc:
                print(f"sift-lint: sheet {terminal_safe(sheet)}: {terminal_safe(exc)}",
                      file=sys.stderr)
                failures = True
                continue
            print(f"{terminal_safe(table.path)} [sheet: {terminal_safe(sheet)}]  "
                  f"{plural(table.n_rows, 'row')}, "
                  f"{plural(len(table.columns), 'column')}")
            private = sensitive_columns(table, config)
            for column in table.columns:
                print(_profile_row(column.profile, sensitive=column.name in private))
            print()
        return 2 if failures else 0
    table = load_any(args.path, args.delimiter, args.sheet, args.max_rows)
    print(
        f"{terminal_safe(table.path)}  {plural(table.n_rows, 'row')}, "
        f"{plural(len(table.columns), 'column')}"
    )
    print()
    config = resolve_config(args, args.path)
    private = sensitive_columns(table, config)
    for column in table.columns:
        print(_profile_row(column.profile, sensitive=column.name in private))
    return 0


def _run_init(args: argparse.Namespace) -> int:
    guard_output(args.output, args.path)
    table = load_any(args.path, args.delimiter, args.sheet)
    findings = redact_sensitive_findings(
        run_checks(table, Config()), sensitive_columns(table)
    )
    _, refusable = describe(findings)

    print(summarise(table, findings, Config()))

    if refusable and not args.force:
        print()
        print(
            "sift-lint: refusing to learn from a file with unresolved problems. "
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
            f"sift-lint: {terminal_safe(args.output)} already exists. "
            "Pass --force to replace it.",
            file=sys.stderr,
        )
        return 2
    atomic_write_text(args.output, body)
    print()
    print(f"Wrote {terminal_safe(args.output)}. Read it, then commit it.")
    return 0


def _run_impact(args: argparse.Namespace) -> int:
    if not any((args.sum, args.avg, args.min, args.max, args.group_by)):
        print(
            "sift-lint: pass --sum, --avg, --min, --max and/or --group-by COLUMN.",
            file=sys.stderr,
        )
        return 2
    config = resolve_config(args, args.path)
    table = load_any(args.path, args.delimiter, args.sheet, args.max_rows)
    for name in (args.sum, args.avg, args.min, args.max):
        if name and table.by_key(name) is None:
            raise ImpactError(f"No column named {name!r} in {table.path}.")
    if args.group_by and table.by_key(args.group_by) is None:
        raise ImpactError(f"No column named {args.group_by!r} in {table.path}.")
    repaired_rows = prepare_repaired_rows(table, config)
    total = (
        sum_impact(table, config, args.sum, repaired_rows=repaired_rows)
        if args.sum
        else None
    )
    aggregates = [
        aggregate_impact(table, config, name, metric, repaired_rows=repaired_rows)
        for metric, name in (("avg", args.avg), ("min", args.min), ("max", args.max))
        if name
    ]
    groups = (
        group_impact(
            table,
            config,
            args.group_by,
            args.sum,
            repaired_rows=repaired_rows,
        )
        if args.group_by
        else None
    )
    duplicates = duplicate_impact(
        table, config, args.sum, repaired_rows=repaired_rows
    )
    private = sensitive_columns(table, config)
    print(render_impact(
        table, total, groups, duplicates,
        redact_groups=args.group_by in private,
        aggregates=aggregates,
    ))
    return 0


def _run_fix(args: argparse.Namespace) -> int:
    if args.format == "json" and not args.dry_run:
        print("sift-lint: fix --format json requires --dry-run.", file=sys.stderr)
        return 2
    if args.show_values and (args.format != "json" or not args.dry_run):
        print("sift-lint: --show-values requires --dry-run --format json.", file=sys.stderr)
        return 2
    if args.explain and args.format == "json":
        print("sift-lint: JSON previews include explanations; omit --explain.",
              file=sys.stderr)
        return 2
    # A limited read is valid for inspection but must never be written as though
    # it contained the full file. That would silently discard the unread tail.
    if args.max_rows is not None and not args.dry_run:
        print("sift-lint: --max-rows is only supported with --dry-run for fix.",
              file=sys.stderr)
        return 2
    config = resolve_config(args, args.path)
    config_path = effective_config_path(args, args.path)
    guard_output(args.output, config_path)
    guard_output(args.log, config_path)
    table = load_any(args.path, args.delimiter, args.sheet, args.max_rows)
    excel_formulas = [f for f in table.file_findings if f.code == "excel-formulas"]
    ragged = [f for f in table.file_findings if f.code == "ragged-rows"]
    if ragged and not args.force:
        # Refusing here rather than writing a plausible-looking file: the
        # rows Sift could not parse were padded or truncated on read, so
        # rewriting would make that loss permanent and invisible.
        print(f"sift-lint: {terminal_safe(ragged[0].message)}", file=sys.stderr)
        print(
            "sift-lint: refusing to rewrite a file that did not parse cleanly. "
            "Fix the quoting at the source, or pass --force to accept the "
            "truncation.",
            file=sys.stderr,
        )
        return 2

    if not args.output and not args.dry_run:
        print("sift-lint: pass --output PATH, or --dry-run to preview.", file=sys.stderr)
        return 2

    # `fix` writes delimited text whatever it read. Letting someone
    # write CSV bytes to a path ending .xlsx produces a file that every
    # tool downstream will open, fail on, and blame them for.
    if args.output and args.output.suffix.lower() in NON_CSV_SUFFIXES:
        print(
            f"sift-lint: fix writes delimited text, so "
            f"{terminal_safe(args.output.name)} would "
            "not be a real workbook. Choose a .csv or .tsv path.",
            file=sys.stderr,
        )
        return 2

    guard_output(args.output, args.path)
    guard_output(args.log, args.path)
    guard_output(args.output, args.log)
    if not args.dry_run and args.output is not None:
        # Both paths must be usable before writing the repaired data. Without
        # this check a bad --log path can fail after the CSV was replaced.
        preflight_output_paths(
            args.output, *([args.log] if args.log is not None else [])
        )

    plan, rows = plan_repairs(table, config, args.min_confidence, args.drop_duplicates)
    private = sensitive_columns(table, config)
    destination = str(args.output) if args.output else "(dry run)"

    if excel_formulas:
        # A general --force switch is not informed consent to destroy Excel
        # formulas (including formulas with no cached value). Inspection and
        # --dry-run remain available, with a visible explanation.
        plan.refusals.append(
            Refusal(None, "excel-formulas", excel_formulas[0].message)
        )
        if not args.dry_run:
            print(render_plan(table, plan, destination, private))
            print(
                "sift-lint: refusing to export a selected Excel sheet containing "
                "formulas, even with --force. Select a formula-free sheet or "
                "prepare an explicit values-only export separately.",
                file=sys.stderr,
            )
            return 2

    # Even if an individual rule unexpectedly introduces a new formula-like
    # value, it must never reach a CSV output. This final-state invariant is
    # independent of --force; existing risky inputs can only be preserved.
    # No need to materialize a second copy of large columns for this check.
    # Dropped rows no longer match their old positions; for that optional mode
    # validate the recorded individual changes instead.
    if args.drop_duplicates:
        introduced = any(
            is_formula_injection(change.after) and not is_formula_injection(change.before)
            for change in plan.changes
        )
    else:
        introduced = any(
            is_formula_injection(after) and not is_formula_injection(before)
            for index, column in enumerate(table.columns)
            for before, after in zip(column.values, (row[index] for row in rows), strict=True)
        )

    # The header is written as the first CSV record, so it needs protection as
    # much as the data. A pre-existing risky header is only exported via force.
    if any(is_formula_injection(name) for name in table.header):
        plan.refusals.append(
            Refusal(None, "formula-injection", "The source header contains a "
                    "formula-like field name. It is preserved, but is unsafe "
                    "to open as a spreadsheet without explicit acceptance.")
        )
    header_hazard = any(is_formula_injection(name) for name in table.header)
    introduced = introduced or (header_hazard and not args.force)
    if introduced and not args.dry_run:
        print(render_plan(table, plan, destination, private))
        print(
            "sift-lint: refusing newly introduced formula-like CSV content or "
            "an unsafe header. Correct the source or repair policy first.",
            file=sys.stderr,
        )
        return 2

    formula_refusals = [
        refusal for refusal in plan.refusals if refusal.rule == "formula-injection"
    ]
    if formula_refusals and not args.dry_run and not args.force:
        print(render_plan(table, plan, destination, private))
        print(
            "sift-lint: refusing to write a repaired file while spreadsheet-formula "
            "content remains. Neutralise those cells first, use --dry-run to "
            "inspect the plan, or pass --force to accept the unresolved risk.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run and args.format == "json":
        blockers = []
        if excel_formulas:
            blockers.append("excel-formulas")
        if introduced:
            blockers.append("formula-injection-introduced")
        if formula_refusals and not args.force:
            blockers.append("formula-injection-unresolved")
        accepted_risks = []
        if args.force and ragged:
            accepted_risks.append("ragged-rows")
        if args.force and header_hazard:
            accepted_risks.append("formula-injection-header")
        if args.force and formula_refusals:
            accepted_risks.append("formula-injection-existing")
        print(render_plan_json(
            table, plan, destination, private, include_values=args.show_values,
            blockers=blockers, accepted_risks=accepted_risks,
            min_confidence=args.min_confidence,
            drop_duplicates=args.drop_duplicates,
            partial_read=args.max_rows is not None,
            output_selected=args.output is not None,
        ))
        return 0

    # Write before reporting. `sift-lint fix ... | head` closes the pipe part
    # way through the summary, and a half-printed report must not mean a
    # file that never got written.
    output_delimiter = ("\t" if args.output and args.output.suffix.lower() == ".tsv"
                        else table.delimiter)
    if not args.dry_run:
        write_csv(args.output, table.header, rows, output_delimiter)
        if args.log:
            write_log(
                args.log, plan, private, source_path=table.path, header=table.header
            )

    print(render_plan(table, plan, destination, private))
    if args.explain:
        print()
        print(render_rule_explanations(plan))
    if args.dry_run:
        return 0

    before = run_checks(table, config)
    after = run_checks(load_any(args.output, delimiter=output_delimiter), config)
    print()
    print(
        f"Findings: {len(before)} before, {len(after)} after. "
        f"{len(plan.refusals)} left for a human."
    )
    if args.log:
        print(f"Audit log: {terminal_safe(args.log)}")
    return 0


def _run_check(args: argparse.Namespace) -> int:
    targets = discover(args.paths)
    if not targets:
        print(
            "sift-lint: nothing to check. Supported extensions: "
            + ", ".join(sorted(SUPPORTED)),
            file=sys.stderr,
        )
        return 2

    references = [Reference.parse(spec) for spec in args.references]
    reference_load = _cached_reference_loader()

    for target in targets:
        guard_output(args.output, target)
        guard_output(args.output, effective_config_path(args, target))
    for reference in references:
        guard_output(args.output, reference.path)

    if args.all_sheets:
        return check_all_sheets(targets, args, references, reference_load)

    if len(targets) == 1:
        config = resolve_config(args, targets[0])
        table = load_any(targets[0], args.delimiter, args.sheet, args.max_rows)
        findings = run_checks(table, config)
        for reference in references:
            findings.extend(check_reference(table, reference, config, reference_load))
        findings = redact_sensitive_findings(findings, sensitive_columns(table, config))
        if args.format == "github":
            report = render_annotations([(targets[0], None, findings)])
            if args.output:
                atomic_write_text(args.output, report)
        else:
            report = write_report(table, findings, args.format, args.output)
        if args.output:
            summary = summarize(findings)
            print(
                f"Wrote {terminal_safe(args.output)} "
                f"({summary['total']} findings)."
            )
        else:
            print(report)
        return exit_code(findings, config)

    return check_many(targets, args, references, reference_load)


def _run_diff(args: argparse.Namespace) -> int:
    if args.baseline_store is not None and not args.saved:
        raise BaselineError("--baseline-store requires --saved")
    baseline_path = args.baseline
    record = None
    delimiter, sheet = args.delimiter, args.sheet
    if args.saved:
        record, baseline_path = get_baseline(str(args.baseline), store=args.baseline_store)
        if delimiter is not None and record.delimiter is not None and delimiter != record.delimiter:
            raise BaselineError("--delimiter conflicts with the saved baseline delimiter")
        if sheet is not None and record.sheet is not None and sheet != record.sheet:
            raise BaselineError("--sheet conflicts with the saved baseline worksheet")
        delimiter = delimiter if delimiter is not None else record.delimiter
        sheet = sheet if sheet is not None else record.sheet
    config = resolve_config(args, args.current)
    guard_output(args.output, baseline_path, args.current)
    if record is not None and args.output is not None:
        # Saved snapshots are read-only: no report may replace their manifest
        # or add misleading extra files inside a named baseline folder.
        if args.output.resolve().is_relative_to(baseline_path.parent.resolve()):
            raise OutputCollision("--output must be outside the saved baseline directory")
    guard_output(args.output, effective_config_path(args, args.current))
    baseline = load_any(baseline_path, delimiter, sheet, args.max_rows)
    current = load_any(args.current, delimiter, sheet, args.max_rows)
    findings = redact_sensitive_findings(
        compare(baseline, current, config),
        sensitive_columns(baseline, config) | sensitive_columns(current, config)
    )
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
    if record is not None:
        # Every saved comparison report identifies its verified input snapshot.
        if args.format == "json":
            payload = json.loads(report)
            payload["baseline"] = {"name": record.name, "sha256": record.sha256}
            report = json.dumps(payload, indent=2, default=str)
        elif args.format == "html":
            report = report.replace(
                "</body>",
                f"<p>Saved baseline: {record.name} "
                f"(SHA-256: {record.sha256})</p></body>",
            )
        else:
            report = (f"Saved baseline: {record.name} "
                      f"(SHA-256: {record.sha256})\n\n" + report)
    if args.output:
        atomic_write_text(args.output, report)
        print(f"Wrote {terminal_safe(args.output)} ({len(findings)} findings).")
    else:
        print(report)
    return exit_code(findings, config)


def _run_baseline(args: argparse.Namespace) -> int:
    action = args.baseline_action
    if action == "save":
        record = save_baseline(
            args.name, args.path, store=args.store, delimiter=args.delimiter, sheet=args.sheet
        )
        print(f"Saved baseline {record.name!r} ({record.rows:,} rows, "
              f"SHA-256 {record.sha256[:12]}...). Raw data is stored locally; "
              "keep it private and out of version control.")
        return 0
    if action == "list":
        records = list_baselines(store=args.store)
        if not records:
            print("No saved baselines.")
        else:
            for record in records:
                print(f"{record.name:<24} {record.rows:>9,} rows  "
                      f"{record.saved_at[:10]}  {record.sha256[:12]}...")
        return 0
    if action == "delete":
        if not args.yes:
            raise BaselineError("baseline delete requires --yes; this removes raw data")
        delete_baseline(args.name, store=args.store)
        print(f"Deleted baseline {args.name!r}.")
        return 0
    record, _ = get_baseline(args.name, store=args.store, verify=True)
    if action == "verify":
        print(f"Baseline {record.name!r}: SHA-256 verified ({record.sha256}).")
        return 0
    print(f"Name: {record.name}\nSaved: {record.saved_at}\n"
          f"Source filename: {terminal_safe(record.source_name)}\n"
          f"Rows: {record.rows:,}\nColumns: {record.columns:,}\n"
          f"Bytes: {record.bytes:,}\nSHA-256: {record.sha256}\n"
          f"Delimiter: {record.delimiter!r}\nWorksheet: {terminal_safe(record.sheet)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        handlers = {
            "profile": _run_profile,
            "init": _run_init,
            "impact": _run_impact,
            "fix": _run_fix,
            "check": _run_check,
            "diff": _run_diff,
            "baseline": _run_baseline,
        }
        handler = handlers.get(args.command)
        return handler(args) if handler is not None else 2
    except BrokenPipeError:
        # `sift-lint check big.csv | head` is a normal thing to do, and should not
        # look like a failure.
        return 0
    except (
        BaselineError,
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
        print(f"sift-lint: {terminal_safe(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
