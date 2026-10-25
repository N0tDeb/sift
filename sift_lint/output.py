"""Safe, single-file output replacement for user-requested artifacts.

Write entirely to a temporary file in the destination directory before the
replacement. An interrupted write leaves an existing destination untouched.
This is atomic *per file*, not a transaction across multiple output files.
"""
from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO


def preflight_output_paths(*paths: Path) -> None:
    """Reject unusable destinations before a multi-file operation writes any.

    This is best-effort prevalidation, not an atomic transaction across files:
    disk failures and concurrent filesystem changes can still happen later.
    """
    for path in paths:
        path = Path(path)
        if path.is_dir():
            raise OSError(f"Output destination is a directory: {path}")
        # Probe the parent using the same temporary-file mechanism as the
        # eventual atomic write. Never truncate or alter an existing output.
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            os.close(fd)
        finally:
            os.unlink(name)


@contextmanager
def atomic_text_writer(path: Path, newline: str | None = None) -> Iterator[TextIO]:
    path = Path(path)
    mode = None
    if path.is_file() and not path.is_symlink():
        mode = stat.S_IMODE(path.stat().st_mode)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            # Preserve the prior file's access bits. On Windows, chmod is
            # more reliable after closing the temporary file's handle.
            os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_write_text(path: Path, content: str) -> None:
    with atomic_text_writer(path) as handle:
        handle.write(content)
