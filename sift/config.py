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

# Severity names accepted by `fail_on`, lowest-exiting first. Kept here rather
# than in the CLI so that loading a config can reject a typo immediately.
FAIL_ON_LEVELS = ("error", "warning", "info", "none")

_RATIOS = ("null_warn", "null_error", "outlier_share")


class ConfigError(Exception):
    """The config file exists but cannot be trusted.

    Every one of these is raised rather than swallowed on purpose. A config is
    a statement of intent — someone wrote `fail_on = "warnings"` meaning to
    tighten the build. Quietly falling back to the default would leave them
    believing a rule is in force when it is not, which is worse than not
    having written it.
    """


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


def _number(section: dict, name: str, path: Path) -> float:
    value = section[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: {name} must be a number, got {value!r}.")
    return float(value)


def _string_list(section: dict, name: str, path: Path) -> list[str]:
    value = section[name]
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(
            f"{path}: {name} must be a string or a list of strings, got {value!r}."
        )
    return list(value)


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path} could not be read: {exc}") from exc

    if not isinstance(data.get("sift", data), dict):
        raise ConfigError(f"{path}: the [sift] section must be a table.")
    section = data.get("sift", data)

    config = Config()
    for name in ("null_warn", "null_error", "outlier_z", "outlier_share"):
        if name in section:
            setattr(config, name, _number(section, name, path))

    for name in _RATIOS:
        value = getattr(config, name)
        if not 0.0 <= value <= 1.0:
            raise ConfigError(
                f"{path}: {name} is a share of a column and must be between 0 and "
                f"1, got {value}."
            )
    if config.null_warn > config.null_error:
        raise ConfigError(
            f"{path}: null_warn ({config.null_warn}) is above null_error "
            f"({config.null_error}), so no column could ever reach the warning."
        )
    if config.outlier_z <= 0:
        raise ConfigError(f"{path}: outlier_z must be positive, got {config.outlier_z}.")

    if "max_examples" in section:
        value = section["max_examples"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(
                f"{path}: max_examples must be a non-negative whole number, "
                f"got {value!r}."
            )
        config.max_examples = value

    if "fail_on" in section:
        value = section["fail_on"]
        if value not in FAIL_ON_LEVELS:
            raise ConfigError(
                f"{path}: fail_on must be one of "
                + ", ".join(FAIL_ON_LEVELS)
                + f", got {value!r}."
            )
        config.fail_on = value

    if "key" in section:
        config.key = _string_list(section, "key", path)
    if "ignore" in section:
        config.ignore = _string_list(section, "ignore", path)

    columns = section.get("ignore_columns") or {}
    if not isinstance(columns, dict):
        raise ConfigError(f"{path}: ignore_columns must be a table of code = [columns].")
    for code in columns:
        config.ignore_columns[code] = _string_list(columns, code, path)
    return config
