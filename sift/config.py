"""Thresholds and silencing rules.

Every threshold in Sift is here rather than hard-coded in a check, because a
20% null rate is alarming in a payments table and expected in a survey export.
A tool that cannot be tuned to the data gets ignored, and an ignored linter is
worse than no linter.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAMES = ("sift.toml", ".sift.toml")


@dataclass
class Config:
    null_warn: float = 0.10
    null_error: float = 0.50
    outlier_z: float = 6.0
    # Above this share of a column, "outliers" means "skewed distribution".
    outlier_share: float = 0.05
    max_examples: int = 3
    key: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    # code -> list of columns exempt from it; "*" exempts everywhere.
    ignore_columns: dict[str, list[str]] = field(default_factory=dict)
    fail_on: str = "error"

    def silenced(self, code: str, column: str | None) -> bool:
        if code in self.ignore:
            return True
        exempt = self.ignore_columns.get(code, [])
        return column is not None and column in exempt

    def filter(self, findings: list) -> list:
        return [f for f in findings if not self.silenced(f.code, f.column)]


def find_config(start: Path) -> Path | None:
    directory = start if start.is_dir() else start.parent
    for parent in [directory, *directory.parents]:
        for name in CONFIG_NAMES:
            candidate = parent / name
            if candidate.exists():
                return candidate
    return None


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = data.get("sift", data)

    config = Config()
    for field_name in ("null_warn", "null_error", "outlier_z", "outlier_share"):
        if field_name in section:
            setattr(config, field_name, float(section[field_name]))
    if "max_examples" in section:
        config.max_examples = int(section["max_examples"])
    if "fail_on" in section:
        config.fail_on = str(section["fail_on"])
    if "key" in section:
        key = section["key"]
        config.key = [key] if isinstance(key, str) else list(key)
    if "ignore" in section:
        config.ignore = list(section["ignore"])
    for code, columns in (section.get("ignore_columns") or {}).items():
        config.ignore_columns[code] = list(columns)
    return config
