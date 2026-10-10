"""Reject release tags that do not match the package version."""

from __future__ import annotations

import re
import sys

from sift_lint import __version__

TAG_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def check_tag(tag: str, version: str) -> None:
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError(f"Expected a stable release tag like v1.0.0, got {tag!r}")
    if int(tag[1:].split(".", 1)[0]) == 0:
        raise ValueError("Public releases start at version 1.0.0")
    if tag[1:] != version:
        raise ValueError(f"Release tag {tag!r} does not match package version {version!r}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python .github/scripts/check_release.py vX.Y.Z")
    try:
        check_tag(sys.argv[1], __version__)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Release tag {sys.argv[1]} matches package version {__version__}")


if __name__ == "__main__":
    main()
