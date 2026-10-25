"""Local, named, tamper-evident snapshots for repeatable ``diff`` runs.

A saved baseline is the original file, not a statistical summary. It may contain
secrets and is deliberately kept out of version control by default. SHA-256
checks accidental changes, not malicious edits to both data and metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .loading import LoadError
from .sources import CSV_SUFFIXES, EXCEL_SUFFIXES, NAMED_ONLY_SUFFIXES, SUPPORTED, load_any

DEFAULT_STORE = Path(".sift/baselines")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CHUNK_SIZE = 1024 * 1024


class BaselineError(Exception):
    """A snapshot is invalid, missing, modified, or unsafe to operate on."""


@dataclass(frozen=True)
class Baseline:
    name: str
    saved_at: str
    source_name: str
    filename: str
    sha256: str
    bytes: int
    rows: int
    columns: int
    delimiter: str | None
    sheet: str | None
    schema_version: int = 1


def _name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise BaselineError(
            "baseline name must be 1-64 characters: letters, digits, _ or -, "
            "starting with a letter or digit"
        )
    return name


def _root(store: Path | None, *, create: bool = False) -> Path:
    root = store if store is not None else DEFAULT_STORE
    if root.is_symlink():
        raise BaselineError(f"baseline store {root} must not be a symbolic link")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.exists() and not root.is_dir():
        raise BaselineError(f"baseline store {root} is not a directory")
    return root


def _directory(root: Path, name: str) -> Path:
    path = root / _name(name)
    if path.is_symlink():
        raise BaselineError(f"saved baseline {name!r} must not be a symbolic link")
    return path


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _read(root: Path, name: str) -> tuple[Baseline, Path]:
    folder = _directory(root, name)
    if not folder.is_dir():
        raise BaselineError(f"baseline {name!r} not found in {root}")
    manifest_path = folder / "manifest.json"
    if manifest_path.is_symlink():
        raise BaselineError(f"baseline {name!r} has a symbolic-link manifest")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = Baseline(**raw)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise BaselineError(f"baseline {name!r} has an invalid manifest") from exc
    if (
        type(record.schema_version) is not int
        or record.schema_version != 1
        or record.name != name
        or not isinstance(record.filename, str)
        or record.filename != f"data{Path(record.filename).suffix.lower()}"
        or Path(record.filename).suffix.lower() not in SUPPORTED
        or not _SHA256.fullmatch(record.sha256)
        or type(record.bytes) is not int
        or record.bytes < 0
        or type(record.rows) is not int
        or record.rows <= 0
        or type(record.columns) is not int
        or record.columns <= 0
        or not isinstance(record.saved_at, str)
        or not isinstance(record.source_name, str)
        or (record.delimiter is not None and (
            not isinstance(record.delimiter, str)
            or len(record.delimiter) != 1
            or record.delimiter in "\r\n\0\""
        ))
        or (record.sheet is not None and not isinstance(record.sheet, str))
    ):
        raise BaselineError(f"baseline {name!r} has invalid metadata")
    data = folder / record.filename
    if data.is_symlink() or not data.is_file():
        raise BaselineError(f"baseline {name!r} has no safe data file")
    return record, data


def save_baseline(
    name: str,
    source: Path,
    *,
    store: Path | None = None,
    delimiter: str | None = None,
    sheet: str | None = None,
) -> Baseline:
    """Save a complete, validated raw-file snapshot; refuse existing names."""
    _name(name)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED:
        raise BaselineError(f"cannot save unsupported file type {suffix!r}")
    if delimiter is not None and suffix not in CSV_SUFFIXES | NAMED_ONLY_SUFFIXES:
        raise BaselineError("--delimiter applies only to delimited text baselines")
    if sheet is not None and suffix not in EXCEL_SUFFIXES:
        raise BaselineError("--sheet applies only to Excel baselines")
    if not source.is_file():
        raise BaselineError(f"source {source} is not a regular file")
    root = _root(store, create=True)
    target = _directory(root, name)
    if target.exists():
        raise BaselineError(f"baseline {name!r} already exists; choose another name")

    # Exclusive lock prevents two CLI writers racing to replace one named snapshot.
    lock = root / f".{name}.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BaselineError(f"baseline {name!r} is already being saved") from exc
    else:
        os.close(fd)
    staging: Path | None = None
    try:
        if target.exists():
            raise BaselineError(f"baseline {name!r} already exists")
        staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
        data_path = staging / f"data{suffix}"
        original_before = source.stat()
        with source.open("rb") as source_handle, data_path.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, CHUNK_SIZE)
        if os.name != "nt":
            data_path.chmod(0o600)
        original_after = source.stat()
        if (original_before.st_size, original_before.st_mtime_ns) != (
            original_after.st_size, original_after.st_mtime_ns
        ):
            raise BaselineError("source file changed while saving the baseline")
        # Validate the actual snapshot, never the possibly-changing source path.
        try:
            table = load_any(data_path, delimiter, sheet)
        except LoadError as exc:
            raise BaselineError(f"baseline source is not readable: {exc}") from exc
        if table.n_rows == 0:
            raise BaselineError("cannot save a header-only baseline")
        digest, size = _hash_file(data_path)
        record = Baseline(
            name=name,
            saved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            source_name=source.name,
            filename=data_path.name,
            sha256=digest,
            bytes=size,
            rows=table.n_rows,
            columns=len(table.columns),
            delimiter=delimiter,
            sheet=sheet,
        )
        manifest = staging / "manifest.json"
        manifest.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
        if os.name != "nt":
            manifest.chmod(0o600)
        if target.exists():
            raise BaselineError(f"baseline {name!r} already exists")
        staging.rename(target)
        staging = None
        return record
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        lock.unlink(missing_ok=True)


def list_baselines(*, store: Path | None = None) -> list[Baseline]:
    root = _root(store)
    if not root.exists():
        return []
    result = []
    for folder in sorted(root.iterdir()):
        if folder.name.startswith("."):
            continue
        record, _ = _read(root, folder.name)
        result.append(record)
    return result


def get_baseline(
    name: str, *, store: Path | None = None, verify: bool = True
) -> tuple[Baseline, Path]:
    record, path = _read(_root(store), name)
    if verify:
        digest, size = _hash_file(path)
        if digest != record.sha256 or size != record.bytes:
            raise BaselineError(
                f"baseline {name!r} failed SHA-256 verification; snapshot changed. "
                "Save a new baseline from a trusted source instead."
            )
    return record, path


def delete_baseline(name: str, *, store: Path | None = None) -> None:
    root = _root(store)
    _read(root, name)  # Validate before deleting; never follow a symlink.
    shutil.rmtree(_directory(root, name))
