"""The unit of output: a Finding, plus the severity ordering used everywhere."""

from __future__ import annotations

from dataclasses import dataclass, field
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


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Errors first, then by column position as the caller recorded it."""
    return sorted(
        findings,
        key=lambda f: (-f.severity.rank, f.detail.get("column_index", 10**6), f.code),
    )
