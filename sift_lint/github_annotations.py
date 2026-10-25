"""GitHub Actions workflow-command annotations for already-redacted findings.

This is an opt-in presentation format, not another check or severity policy.
Never include raw example values in annotations: CI logs can be public.
"""

from __future__ import annotations

from pathlib import Path

from .findings import Finding, sort_findings

_LEVELS = {"error": "error", "warning": "warning", "info": "notice"}


def _escape(value: object, *, property_value: bool = False) -> str:
    """Escape workflow-command delimiters and terminal control bytes.

    GitHub decodes %25, %0D and %0A, and property values also need comma and
    colon encoding. Escape literal percent first to prevent double decoding.
    Other control bytes (especially ESC) must not reach terminal output.
    """
    cleaned = "".join(
        f"\\x{ord(char):02x}" if ord(char) < 32 and char not in "\r\n" else char
        for char in str(value)
    )
    result = cleaned.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if property_value:
        result = result.replace(",", "%2C").replace(":", "%3A")
    return result


def _annotation(level: str, path: Path, title: str, message: str) -> str:
    return (
        f"::{level} file={_escape(path, property_value=True)},"
        f"title={_escape(title, property_value=True)}::"
        f"{_escape(message)}"
    )


def render_annotations(
    results: list[tuple[Path, str | None, list[Finding]]],
    failures: list[tuple[Path, str | None, str]] | None = None,
) -> str:
    """Emit annotations for checked files and unreadable inputs.

    Line numbers are omitted: most Sift checks aggregate whole columns and
    reporting a fabricated location would mislead GitHub's source viewer.
    """
    lines: list[str] = []
    for path, sheet, findings in results:
        for finding in sort_findings(findings):
            context = f"Worksheet {sheet!r}: " if sheet is not None else ""
            column = f"Column {finding.column!r}: " if finding.column is not None else ""
            lines.append(
                _annotation(
                    _LEVELS[finding.severity.value],
                    path,
                    f"sift: {finding.code}",
                    f"{context}{column}{finding.message}",
                )
            )
    for path, sheet, error in failures or []:
        context = f"Worksheet {sheet!r}: " if sheet is not None else ""
        lines.append(_annotation("error", path, "sift: unreadable", context + error))
    return "\n".join(lines)
