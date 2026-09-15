"""Reading the file, and the checks that only exist at the file level.

A ragged row or a stray BOM is invisible once the data is in a dataframe — the
loader has already guessed its way past it. So Sift looks at the bytes first.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .findings import Finding, Severity
from .profiling import ColumnProfile, profile_column

DELIMITERS = ",;\t|"

# Python's csv module refuses fields over 128 KB by default. Real files embed
# JSON blobs, base64 images and long free text in a single cell, and a crash
# with `_csv.Error: field larger than field limit` tells the reader nothing.
# The ceiling is raised to something a row can plausibly be, and still bounded
# so a corrupt file cannot make the parser allocate without limit.
MAX_FIELD_BYTES = 16 * 1024 * 1024
csv.field_size_limit(MAX_FIELD_BYTES)


@dataclass
class Column:
    index: int
    name: str
    key: str
    values: list[str]
    profile: ColumnProfile


@dataclass
class Table:
    path: Path
    header: list[str]
    columns: list[Column]
    n_rows: int
    delimiter: str = ","
    file_findings: list[Finding] = field(default_factory=list)

    def by_key(self, key: str) -> Column | None:
        for column in self.columns:
            if column.key == key or column.name == key:
                return column
        return None


class LoadError(Exception):
    """The file cannot be read at all — not a finding, a failure."""


def _sniff_delimiter(sample: str, override: str | None) -> str:
    if override:
        return override
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        # Sniffer gives up on single-column files; comma is the safe default.
        return ","


def _scan_bytes(path: Path) -> tuple[list[Finding], str]:
    """Look at the raw bytes before anything decodes them."""
    findings: list[Finding] = []
    raw = path.read_bytes()
    encoding = "utf-8"

    if raw.startswith(b"\xef\xbb\xbf"):
        findings.append(
            Finding(
                "bom",
                Severity.WARNING,
                "File starts with a UTF-8 byte order mark. Some readers will "
                "fold it into the first column name.",
            )
        )
        encoding = "utf-8-sig"

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        encoding = "latin-1"
        findings.append(
            Finding(
                "encoding",
                Severity.ERROR,
                f"File is not valid UTF-8 (first bad byte at offset {exc.start}). "
                "Read as Latin-1 to continue; re-export as UTF-8.",
                detail={"offset": exc.start},
            )
        )

    if b"\x00" in raw:
        findings.append(
            Finding(
                "null-bytes",
                Severity.ERROR,
                f"File contains {raw.count(chr(0).encode()):,} NUL byte(s). Text "
                "files do not, so this is either binary content with the wrong "
                "extension or a truncated write.",
                detail={"count": raw.count(b"\x00")},
            )
        )

    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    if crlf and lf:
        findings.append(
            Finding(
                "mixed-line-endings",
                Severity.WARNING,
                f"Mixed line endings: {crlf:,} CRLF and {lf:,} LF. "
                "Diffs and checksums will be noisy.",
                detail={"crlf": crlf, "lf": lf},
            )
        )
    if raw and not raw.endswith((b"\n", b"\r")):
        findings.append(
            Finding(
                "no-trailing-newline",
                Severity.INFO,
                "No newline at end of file. Concatenating this file with another "
                "will join two rows into one.",
            )
        )
    return findings, encoding


def load(path: Path, delimiter: str | None = None, max_rows: int | None = None) -> Table:
    path = Path(path)
    if not path.exists():
        raise LoadError(f"No such file: {path}")
    if path.stat().st_size == 0:
        raise LoadError(f"File is empty: {path}")

    file_findings, encoding = _scan_bytes(path)

    with path.open("r", encoding=encoding, newline="") as handle:
        sample = handle.read(64 * 1024)
        handle.seek(0)
        sep = _sniff_delimiter(sample, delimiter)
        reader = csv.reader(handle, delimiter=sep)
        try:
            header = next(reader)
        except StopIteration:
            raise LoadError(f"File has no header row: {path}") from None

        width = len(header)
        rows: list[list[str]] = []
        short_rows: list[int] = []
        long_rows: list[int] = []

        try:
            numbered = list(enumerate(reader, start=2))
        except csv.Error as exc:
            raise LoadError(
                f"{path.name} could not be parsed as delimited text ({exc}). "
                "A single field may exceed "
                f"{MAX_FIELD_BYTES // (1024 * 1024)} MB, or the file may be binary."
            ) from exc

        for line_no, row in numbered:
            if not row:
                continue
            if len(row) < width:
                short_rows.append(line_no)
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                long_rows.append(line_no)
                row = row[:width]
            rows.append(row)
            if max_rows and len(rows) >= max_rows:
                break

    if short_rows or long_rows:
        ragged = len(short_rows) + len(long_rows)
        first = sorted(short_rows + long_rows)[:5]
        file_findings.append(
            Finding(
                "ragged-rows",
                Severity.ERROR,
                (
                    f"1 row does not have {width} fields."
                    if ragged == 1
                    else f"{ragged:,} rows do not have {width} fields."
                )
                + " Usually an unescaped delimiter or an unclosed quote.",
                detail={"short": len(short_rows), "long": len(long_rows), "width": width},
                examples=[f"line {n}" for n in first],
            )
        )

    seen: dict[str, int] = {}
    columns: list[Column] = []
    for index, name in enumerate(header):
        key = name
        if name in seen:
            seen[name] += 1
            key = f"{name}#{seen[name]}"
        else:
            seen[name] = 0
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
        delimiter=sep,
        file_findings=file_findings,
    )
