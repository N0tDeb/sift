"""Reading formats that are not CSV.

There is a tension worth naming. Sift's whole method is to read every value as
text and infer the type from evidence, because a CSV has no types and the
reader's guess is exactly what goes wrong. Excel and Parquet *do* carry types,
so the obvious move is to trust them and skip the inference.

That would be a mistake for Excel and correct for Parquet, and the difference
is who did the typing.

Parquet's schema was written deliberately by a program. It is a real
declaration and Sift treats it as one — it reports what the schema says and
checks the values against it, rather than re-deriving types from strings.

Excel's types were assigned by a spreadsheet, mostly by accident, usually to
data a person pasted in. Excel is where leading zeros go to die, where a
product code becomes a date, and where a number silently becomes text because
one cell in the column had a space in it. Its types are not a declaration, they
are a record of what a spreadsheet decided to do — which makes them evidence
about the damage rather than a reason to skip looking for it.

Both readers are optional. Sift's CSV path stays dependency-free:

    pip install sift-lint[excel]
    pip install sift-lint[parquet]
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from .findings import Finding, Severity
from .loading import Column, LoadError, Table, column_keys, validate_max_rows
from .loading import load as load_csv
from .profiling import profile_column
from .text import plural

CSV_SUFFIXES = {".csv", ".tsv"}
# .txt is read as delimited text when named directly, but never picked up by a
# directory scan: a folder of data usually also holds a README or a log, and a
# linter that reports two errors in your notes file gets switched off.
NAMED_ONLY_SUFFIXES = {".txt", ".tab"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
PARQUET_SUFFIXES = {".parquet", ".pq"}
DISCOVERABLE = CSV_SUFFIXES | EXCEL_SUFFIXES | PARQUET_SUFFIXES
SUPPORTED = DISCOVERABLE | NAMED_ONLY_SUFFIXES

# What Excel puts in a cell when a formula fails. These survive an export and
# then sit in the data as literal strings.
FORMULA_ERRORS = {
    "#REF!", "#VALUE!", "#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!", "#SPILL!",
}


def _text(value: object) -> str:
    """Render a typed cell the way a person would see it.

    The float branch matters more than it looks: `str(1200.0)` is `"1200.0"`,
    which turns every whole number in a spreadsheet into something that reads
    like a measurement to two decimal places. Excel shows `1200`, so Sift does.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        if value.hour or value.minute or value.second:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    return str(value)


def _build(path: Path, header: list[str], rows: list[list[str]], findings: list[Finding]) -> Table:
    columns: list[Column] = []
    for index, (name, key) in enumerate(zip(header, column_keys(header), strict=True)):
        values = [row[index] for row in rows]
        columns.append(
            Column(
                index=index,
                name=name,
                key=key,
                values=values,
                profile=profile_column(name, index, values),
            )
        )
    return Table(
        path=path,
        header=header,
        columns=columns,
        n_rows=len(rows),
        file_findings=findings,
    )


def excel_sheet_names(path: Path) -> list[str]:
    """List every worksheet, including hidden sheets, without loading its cells.

    Workbook-wide inspection must not quietly skip a sheet the user could
    overlook in the Excel interface. OpenPyXL is an optional dependency.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise LoadError(
            "Reading .xlsx needs openpyxl: pip install 'sift-lint[excel]'"
        ) from exc

    try:
        workbook = load_workbook(path, read_only=True, data_only=False)
    except Exception as exc:
        raise LoadError(f"{path.name} could not be read as a workbook ({exc}).") from exc
    try:
        names = list(workbook.sheetnames)
    finally:
        workbook.close()
    if not names:
        raise LoadError(f"Workbook has no worksheets: {path}")
    return names


def load_excel(path: Path, sheet: str | None = None, max_rows: int | None = None) -> Table:
    validate_max_rows(max_rows)
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise LoadError(
            "Reading .xlsx needs openpyxl: pip install 'sift-lint[excel]'"
        ) from exc

    findings: list[Finding] = []
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        # Data-only mode can silently yield None for a formula with no cached
        # result. The formula-view is needed to detect possible data loss.
        try:
            formula_workbook = load_workbook(path, read_only=True, data_only=False)
        except Exception:
            workbook.close()
            raise
    except Exception as exc:  # openpyxl raises several unrelated types
        raise LoadError(
            f"{path.name} could not be read as a workbook ({exc}). If it is "
            "actually a CSV, rename it; if it is the old .xls format, save it "
            "as .xlsx."
        ) from exc

    try:
        names = workbook.sheetnames
        if sheet is not None:
            if sheet not in names:
                raise LoadError(f"No sheet named {sheet!r}. Sheets: {', '.join(names)}")
            selected_name = sheet
        else:
            selected_name = names[0]
        worksheet = workbook[selected_name]
        if len(names) > 1:
            remaining = [name for name in names if name != selected_name]
            findings.append(
                Finding(
                    "unread-sheets",
                    Severity.INFO,
                    f"Read sheet {selected_name!r}. This workbook has "
                    f"{plural(len(remaining), 'other sheet')} not inspected: "
                    + ", ".join(repr(name) for name in remaining)
                    + ". Formulas on those sheets are not checked or exported.",
                    detail={"sheets": names},
                )
            )

        # Inspect the entire selected sheet, including rows outside a
        # --max-rows sample and any preamble/header cells. A bounded preview
        # must not incorrectly claim that a later formula does not exist.
        formula_count = sum(
            1 for cells in formula_workbook[selected_name].iter_rows()
            for cell in cells if cell.data_type == "f"
        )
        if formula_count:
            findings.append(
                Finding(
                    "excel-formulas",
                    Severity.WARNING,
                    f"Selected Excel sheet {selected_name!r} contains "
                    f"{plural(formula_count, 'formula')}. CSV cannot preserve "
                    "workbook formulas or reliably preserve their calculated "
                    "values; fix refuses this sheet.",
                    detail={"sheet": selected_name, "count": formula_count},
                )
            )

        row_iter = iter(worksheet.iter_rows(values_only=True))

        # A spreadsheet built for humans often starts with a title row, a blank
        # line, then the real header. On a genuinely one-column worksheet, though,
        # every legitimate row has exactly one value, so treating all one-cell rows
        # as preamble would discard the entire table. The worksheet dimension gives
        # us the needed distinction without consuming the full sheet.
        skipped = 0
        raw_header: list[object] | None = None
        single_column = getattr(worksheet, "max_column", None) == 1
        for raw_row in row_iter:
            row = list(raw_row)
            nonempty = sum(1 for cell in row if cell is not None)
            if nonempty == 0 or (nonempty == 1 and not single_column):
                skipped += 1
                continue
            raw_header = row
            break

        if raw_header is None:
            raise LoadError(f"No usable rows in {path}")
        if skipped:
            findings.append(
                Finding(
                    "preamble-rows",
                    Severity.WARNING,
                    f"Skipped {plural(skipped, 'row')} above the header — a title or "
                    "spacer written for a human reader. Any tool that does not skip "
                    "them will read them as data.",
                    detail={"skipped": skipped},
                )
            )

        grid: list[list[object]] = []
        for raw_row in row_iter:
            grid.append(list(raw_row))
            if max_rows is not None and len(grid) >= max_rows:
                break
    finally:
        workbook.close()
        formula_workbook.close()

    width = len(raw_header)
    header = [_text(cell).strip() or f"column_{i + 1}" for i, cell in enumerate(raw_header)]

    rows: list[list[str]] = []
    typed_dates: dict[int, int] = {}
    formula_errors: dict[int, int] = {}

    for raw_row in grid:
        if all(cell is None for cell in raw_row):
            continue  # trailing blank rows are a spreadsheet artefact, not data
        row = []
        for index in range(width):
            cell = raw_row[index] if index < len(raw_row) else None
            if isinstance(cell, datetime | date):
                typed_dates[index] = typed_dates.get(index, 0) + 1
            text = _text(cell)
            if text.strip() in FORMULA_ERRORS:
                formula_errors[index] = formula_errors.get(index, 0) + 1
            row.append(text)
        rows.append(row)

    for index, count in formula_errors.items():
        findings.append(
            Finding(
                "formula-error",
                Severity.ERROR,
                f"{header[index]!r} contains {plural(count, 'broken formula result')} "
                "(#REF!, #N/A and friends). These export as literal text and will "
                "never parse as anything.",
                column=header[index],
                detail={"column_index": index, "count": count},
            )
        )

    # A date column where only some cells are real dates is the classic Excel
    # failure: the rest are text that looks identical on screen.
    for index, count in typed_dates.items():
        if 0 < count < len(rows):
            findings.append(
                Finding(
                    "partial-excel-dates",
                    Severity.ERROR,
                    f"{header[index]!r} holds real dates in {count:,} of "
                    f"{len(rows):,} cells; the rest are text that looks the same "
                    "on screen. Sorting or filtering this column will not do what "
                    "it appears to do.",
                    column=header[index],
                    detail={"column_index": index, "typed": count, "rows": len(rows)},
                )
            )

    return _build(path, header, rows, findings)


def load_parquet(path: Path, max_rows: int | None = None) -> Table:
    validate_max_rows(max_rows)
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise LoadError(
            "Reading .parquet needs pyarrow: pip install 'sift-lint[parquet]'"
        ) from exc

    findings: list[Finding] = []
    try:
        if max_rows is not None:
            parquet = pq.ParquetFile(path)
            schema = parquet.schema_arrow
            header = list(schema.names)
            data = next(parquet.iter_batches(batch_size=max_rows), None)
        else:
            data = pq.read_table(path)
            schema = data.schema
            header = list(data.column_names)
    except Exception as exc:  # pyarrow raises its own error hierarchy
        raise LoadError(f"{path.name} could not be read as Parquet ({exc}).") from exc

    columns_text: list[list[str]] = []
    if data is None:
        columns_text = [[] for _ in header]
    else:
        for index, _name in enumerate(header):
            column = data.column(index)
            columns_text.append([_text(value.as_py()) for value in column])

    rows = [list(row) for row in zip(*columns_text, strict=True)] if columns_text else []
    built = _build(path, header, rows, findings)

    # Parquet's schema was written on purpose, so the interesting question is
    # not "what type is this" but "does the declared type still fit". A string
    # column whose every value is a number is a schema that stopped matching
    # its data — the exact thing a typed format is supposed to prevent.
    for index, name in enumerate(header):
        # Field names are not unique in every valid Parquet schema. Resolve by
        # position to match the column extracted with data.column(index).
        declared = str(schema.field(index).type)
        profile = built.columns[index].profile
        if declared.startswith("string") and profile.kind == "numeric" and profile.n_present:
            findings.append(
                Finding(
                    "loose-schema",
                    Severity.WARNING,
                    f"{name!r} is declared as {declared} but every value in it is a "
                    "number. Anything reading this by schema will sort it "
                    "alphabetically, so 10 comes before 9.",
                    column=name,
                    detail={"column_index": index, "declared": declared},
                )
            )
    built.file_findings = findings
    return built


def load_any(path: str | Path, delimiter: str | None = None, sheet: str | None = None,
             max_rows: int | None = None) -> Table:
    """Read any supported file, dispatching on extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        return load_excel(path, sheet, max_rows)
    if suffix in PARQUET_SUFFIXES:
        return load_parquet(path, max_rows)
    if suffix == ".xls":
        raise LoadError(
            f"{path.name} is the old binary .xls format. Save it as .xlsx and try again."
        )
    return load_csv(path, delimiter, max_rows)


def discover(paths: list[Path], recursive: bool = True) -> list[Path]:
    """Expand directories into the files inside them, in a stable order."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            found.extend(
                sorted(
                    child
                    for child in path.glob(pattern)
                    if child.is_file() and child.suffix.lower() in DISCOVERABLE
                )
            )
        else:
            found.append(path)
    return found
