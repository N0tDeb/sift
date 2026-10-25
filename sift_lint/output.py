"""Safe, single-file output replacement for user-requested artifacts.

Write entirely to a temporary file in the destination directory before the
replacement. An interrupted write leaves an existing destination untouched.
This is atomic *per file*, not a transaction across multiple output files.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def atomic_text_writer(path: Path, newline: str | None = None) -> Iterator[TextIO]:
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_write_text(path: Path, content: str) -> None:
    with atomic_text_writer(path) as handle:
        handle.write(content)
