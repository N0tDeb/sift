"""Reading the file, and the checks that only exist at the file level.

A ragged row or a stray BOM is invisible once the data is in a dataframe — the
loader has already guessed its way past it. So Sift looks at the bytes first.
"""

from __future__ import annotations

import codecs
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
BYTE_SCAN_CHUNK = 64 * 1024
csv.field_size_limit(MAX_FIELD_BYTES)


@dataclass
class Column:
    index: int
    name: str
    key: str
    values: list[str]
    profile: ColumnProfile
    source_lines: list[int] = field(default_factory=list)


@dataclass
class Table:
    path: Path
    header: list[str]
    columns: list[Column]
    n_rows: int
    delimiter: str = ","
    file_findings: list[Finding] = field(default_factory=list)
    source_lines: list[int] = field(default_factory=list)

    def by_key(self, key: str) -> Column | None:
        for column in self.columns:
            if column.key == key or column.name == key:
                return column
        return None


class LoadError(Exception):
    """The file cannot be read at all — not a finding, a failure."""


def _sniff_delimiter(sample: str, override: str | None) -> str:
    if override is not None:
        if len(override) != 1 or override in "\r\n\0\"":
            raise LoadError("delimiter must be one non-newline character")
        return override
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        # Sniffer gives up on single-column files; comma is the safe default.
        return ","


def _validated_lines(handle, delimiter: str):
    """Stream physical lines while rejecting quotes that csv.reader overlooks.

    The standard CSV reader is permissive with a quote in an unquoted field;
    that can change record boundaries when the file is rewritten.  This small
    lexical check follows CSV's start / unquoted / quoted / after-quote states
    across physical lines.  It preserves properly quoted multiline cells.
    """
    state = "start"
    for line_no, line in enumerate(handle, start=1):
        for char in line:
            if state == "quoted":
                if char == '"':
                    state = "after_quote"
            elif state == "after_quote":
                if char == '"':
                    state = "quoted"  # doubled literal quote
                elif char == delimiter:
                    state = "start"
                elif char in "\r\n":
                    state = "start"
                else:
                    raise LoadError(
                        f"Malformed CSV quoting at physical line {line_no}: "
                        "text after a closing quote."
                    )
            elif state == "start":
                if char == '"':
                    state = "quoted"
                elif char not in (delimiter, "\r", "\n"):
                    state = "unquoted"
            elif char == '"':
                raise LoadError(
                    f"Malformed CSV quoting at physical line {line_no}: "
                    "quote in an unquoted field."
                )
            elif char == delimiter or char in "\r\n":
                state = "start"
        yield line
    if state == "quoted":
        raise LoadError("Malformed CSV quoting: unterminated quoted field at end of file.")


def _scan_bytes(path: Path) -> tuple[list[Finding], str]:
    """Inspect raw bytes incrementally before text decoding/parsing.

    Keep the file-level evidence that requires raw bytes without allocating a
    second whole-file copy. UTF-8 validation uses an incremental decoder so a
    multibyte character split across chunks is handled exactly like
    ``bytes.decode``.
    """
    findings: list[Finding] = []
    decoder = codecs.getincrementaldecoder("utf-8")()
    invalid_utf8_offset: int | None = None
    prefix = b""
    total_bytes = 0
    nul_count = 0
    crlf = 0
    newline_count = 0
    last_byte: int | None = None

    with path.open("rb") as handle:
        while chunk := handle.read(BYTE_SCAN_CHUNK):
            if len(prefix) < 3:
                prefix += chunk[: 3 - len(prefix)]

            nul_count += chunk.count(b"\x00")
            newline_count += chunk.count(b"\n")
            crlf += chunk.count(b"\r\n")
            if last_byte == ord("\r") and chunk[0] == ord("\n"):
                crlf += 1

            if invalid_utf8_offset is None:
                pending = decoder.getstate()[0]
                try:
                    decoder.decode(chunk, final=False)
                except UnicodeDecodeError as exc:
                    invalid_utf8_offset = total_bytes - len(pending) + exc.start

            total_bytes += len(chunk)
            last_byte = chunk[-1]

    if invalid_utf8_offset is None:
        pending = decoder.getstate()[0]
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            invalid_utf8_offset = total_bytes - len(pending) + exc.start

    encoding = "utf-8"
    if prefix.startswith(b"\xef\xbb\xbf"):
        findings.append(
            Finding(
                "bom",
                Severity.WARNING,
                "File starts with a UTF-8 byte order mark. Some readers will "
                "fold it into the first column name.",
            )
        )
        encoding = "utf-8-sig"

    if invalid_utf8_offset is not None:
        encoding = "latin-1"
        findings.append(
            Finding(
                "encoding",
                Severity.ERROR,
                f"File is not valid UTF-8 (first bad byte at offset "
                f"{invalid_utf8_offset}). Read as Latin-1 to continue; "
                "re-export as UTF-8.",
                detail={"offset": invalid_utf8_offset},
            )
        )

    if nul_count:
        findings.append(
            Finding(
                "null-bytes",
                Severity.ERROR,
                f"File contains {nul_count:,} NUL byte(s). Text files do not, "
                "so this is either binary content with the wrong extension or "
                "a truncated write.",
                detail={"count": nul_count},
            )
        )

    lf = newline_count - crlf
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
    if total_bytes and last_byte not in (ord("\n"), ord("\r")):
        findings.append(
            Finding(
                "no-trailing-newline",
                Severity.INFO,
                "No newline at end of file. Concatenating this file with another "
                "will join two rows into one.",
            )
        )
    return findings, encoding


def validate_max_rows(max_rows: int | None) -> None:
    """Require a positive row limit whenever one is supplied."""
    if max_rows is not None and max_rows <= 0:
        raise LoadError(f"max_rows must be a positive integer, got {max_rows}.")


def load(path: Path, delimiter: str | None = None, max_rows: int | None = None) -> Table:
    validate_max_rows(max_rows)
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
        reader = csv.reader(_validated_lines(handle, sep), delimiter=sep, strict=True)
        try:
            header = next(reader)
        except StopIteration:
            raise LoadError(f"File has no header row: {path}") from None
        except csv.Error as exc:
            raise LoadError(f"Malformed CSV header in {path.name}: {exc}") from exc

        # A newline, an all-blank heading or a delimiter-only heading does
        # not describe usable data.  Other blank column names remain visible
        # to existing schema checks rather than silently disappearing.
        if not header or not any(name.strip() for name in header):
            raise LoadError(f"File has no usable header row: {path}")

        width = len(header)
        rows: list[list[str]] = []
        short_rows: list[int] = []
        long_rows: list[int] = []
        source_lines: list[int] = []
        previous_end_line = getattr(reader, "line_num", 1)

        try:
            for logical_no, row in enumerate(reader, start=2):
                line_no = previous_end_line + 1
                previous_end_line = getattr(reader, "line_num", logical_no)
                if not row:
                    continue
                if len(row) < width:
                    short_rows.append(line_no)
                    row = row + [""] * (width - len(row))
                elif len(row) > width:
                    long_rows.append(line_no)
                    row = row[:width]
                rows.append(row)
                source_lines.append(line_no)
                if max_rows is not None and len(rows) >= max_rows:
                    break
        except csv.Error as exc:
            raise LoadError(
                f"{path.name} could not be parsed as delimited text ({exc}). "
                "A single field may exceed "
                f"{MAX_FIELD_BYTES // (1024 * 1024)} MB, or the file may be binary."
            ) from exc

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
                source_lines=source_lines,
            )
        )

    return Table(
        path=path,
        header=header,
        columns=columns,
        n_rows=len(rows),
        delimiter=sep,
        file_findings=file_findings,
        source_lines=source_lines,
    )
