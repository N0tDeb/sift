"""The unit of output: a Finding, plus the severity ordering used everywhere."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "info": 1}[self.value]

    def __lt__(self, other: Severity) -> bool:  # type: ignore[override]
        return self.rank < other.rank


@dataclass
class Finding:
    """One problem found in one place.

    `code` is stable and machine-readable (`mixed-types`, `duplicate-rows`).
    Users silence checks by code, so codes must not change casually.
    """

    code: str
    severity: Severity
    message: str
    column: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "column": self.column,
            "message": self.message,
            "detail": self.detail,
            "examples": self.examples,
        }


_SENSITIVE_CODES = {"sensitive-data", "sensitive-column-name"}
_SAFE_SENSITIVE_DETAIL_KEYS = {
    "column_index",
    "count",
    "rows",
    "distinct",
    "repeated",
    "groups",
    "pairs",
    "null_rate",
    "collapsed",
}


def redact_sensitive_findings(
    findings: list[Finding], sensitive_columns: set[str] | None = None
) -> list[Finding]:
    """Remove source values from findings attached to sensitive columns.

    The sensitive-data detector deliberately omits examples, but a different
    finding on the same column can still carry the raw value in its examples,
    message or detail.  Redaction is therefore a property of the column, not
    of one finding code.  Security findings themselves are already written
    without source values and are kept intact so they remain informative.
    """
    columns = set(sensitive_columns or ())
    columns.update(
        finding.column
        for finding in findings
        if finding.code in _SENSITIVE_CODES and finding.column is not None
    )
    if not columns:
        return findings

    redacted: list[Finding] = []
    for finding in findings:
        if finding.column not in columns or finding.code in _SENSITIVE_CODES:
            redacted.append(finding)
            continue

        detail = {
            key: value
            for key, value in finding.detail.items()
            if key in _SAFE_SENSITIVE_DETAIL_KEYS
        }
        redacted.append(
            replace(
                finding,
                message=(
                    f"{finding.column!r} also triggered {finding.code!r}. "
                    "Value details are redacted because this column appears "
                    "to contain sensitive data."
                ),
                detail=detail,
                examples=[],
            )
        )
    return redacted


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Errors first, then by column position as the caller recorded it."""
    return sorted(
        findings,
        key=lambda f: (-f.severity.rank, f.detail.get("column_index", 10**6), f.code),
    )
