"""Sift — a linter for tabular data files.

    from sift import lint
    findings = lint("orders.csv")

The library API is deliberately small: load a file, run the checks, get a list
of Findings. Everything else in the package supports those three steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .findings import Finding

__version__ = "0.1.0"
__all__ = ["lint", "diff", "Finding", "Severity", "Config", "__version__"]


def lint(path: str | Path, config: Config | None = None, **kwargs) -> list[Finding]:
    """Check one file and return its findings."""
    from .checks import run_checks
    from .config import Config as _Config
    from .sources import load_any

    table = load_any(Path(path), **kwargs)
    return run_checks(table, config or _Config())


def diff(
    baseline: str | Path, current: str | Path, config: Config | None = None, **kwargs
) -> list[Finding]:
    """Compare a file against a baseline and return what changed."""
    from .checks import sensitive_columns
    from .config import Config as _Config
    from .drift import compare
    from .findings import redact_sensitive_findings
    from .sources import load_any

    old = load_any(Path(baseline), **kwargs)
    new = load_any(Path(current), **kwargs)
    return redact_sensitive_findings(
        compare(old, new, config or _Config()), sensitive_columns(new)
    )


def __getattr__(name: str):
    if name in ("Finding", "Severity"):
        from . import findings as module

        return getattr(module, name)
    if name == "Config":
        from .config import Config

        return Config
    raise AttributeError(name)
