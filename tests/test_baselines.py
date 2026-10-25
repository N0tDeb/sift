"""Batch 9: named baseline behavior, raw-data privacy and integrity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sift_lint.baselines import BaselineError, get_baseline, save_baseline
from sift_lint.cli import main


def _csv(tmp_path: Path, name: str, data: str) -> Path:
    path = tmp_path / name
    path.write_text(data, encoding="utf-8", newline="\n")
    return path


def test_baseline_lifecycle_and_repeatable_diff(tmp_path: Path, capsys) -> None:
    source = _csv(tmp_path, "day1.csv", "region,value\nNorth,10\nSouth,20\n")
    current = _csv(tmp_path, "day2.csv", "region,value\nNorth,10\nWest,20\n")
    store = tmp_path / "store"
    assert main(["baseline", "save", "first", str(source), "--store", str(store)]) == 0
    assert "Saved baseline 'first'" in capsys.readouterr().out
    manifest = json.loads((store / "first" / "manifest.json").read_text())
    saved = store / "first" / "data.csv"
    assert saved.read_bytes() == source.read_bytes()
    assert manifest["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["rows"] == 2
    assert manifest["source_name"] == "day1.csv"
    assert str(tmp_path) not in json.dumps(manifest)
    assert main(["baseline", "list", "--store", str(store)]) == 0
    assert "first" in capsys.readouterr().out
    assert main(["baseline", "show", "first", "--store", str(store)]) == 0
    assert "SHA-256" in capsys.readouterr().out
    assert main(["baseline", "verify", "first", "--store", str(store)]) == 0
    assert "verified" in capsys.readouterr().out
    assert main(["diff", "first", str(current), "--saved", "--baseline-store", str(store),
                 "--format", "json", "--no-config"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(f["code"] in {"new-category", "missing-category"}
               for f in payload["findings"])
    assert payload["baseline"] == {"name": "first", "sha256": manifest["sha256"]}
    # The stored content remains frozen even when the original is replaced.
    source.write_text("region,value\nCHANGED,3\n", encoding="utf-8", newline="\n")
    assert saved.read_bytes().startswith(b"region,value\nNorth")
    assert main(["baseline", "verify", "first", "--store", str(store)]) == 0
    capsys.readouterr()
    assert main(["baseline", "delete", "first", "--store", str(store)]) == 2
    assert saved.exists()
    capsys.readouterr()
    assert main(["baseline", "delete", "first", "--store", str(store), "--yes"]) == 0
    assert not saved.exists()


def test_default_store_is_git_ignored(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    source = _csv(tmp_path, "day.csv", "name\nNorth\n")
    assert main(["baseline", "save", "daily", str(source)]) == 0
    assert (tmp_path / ".sift/baselines/daily/data.csv").exists()
    assert main(["baseline", "list"]) == 0
    assert "daily" in capsys.readouterr().out
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
    assert ".sift/baselines/" in gitignore


def test_integrity_failure_refuses_diff_without_overwriting_report(
    tmp_path: Path, capsys
) -> None:
    source = _csv(tmp_path, "a.csv", "city\nNorth\n")
    current = _csv(tmp_path, "b.csv", "city\nNorth\n")
    store = tmp_path / "store"
    assert main(["baseline", "save", "test", str(source), "--store", str(store)]) == 0
    capsys.readouterr()
    (store / "test/data.csv").write_bytes(b"city\nCHANGED\n")
    report = tmp_path / "report.json"
    report.write_text("keep me", encoding="utf-8")
    status = main(["diff", "test", str(current), "--saved", "--baseline-store",
                   str(store), "--output", str(report), "--no-config"])
    assert status == 2
    assert "SHA-256" in capsys.readouterr().err
    assert report.read_text() == "keep me"


def test_existing_name_refused_and_old_content_preserved(tmp_path: Path, capsys) -> None:
    a = _csv(tmp_path, "a.csv", "key\nalpha\n")
    b = _csv(tmp_path, "b.csv", "key\nbeta\n")
    store = tmp_path / "store"
    assert main(["baseline", "save", "only", str(a), "--store", str(store)]) == 0
    before = (store / "only/data.csv").read_bytes()
    capsys.readouterr()
    assert main(["baseline", "save", "only", str(b), "--store", str(store)]) == 2
    assert (store / "only/data.csv").read_bytes() == before
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["../bad", "a/b", ".hidden", "a.b", "", "x" * 65])
def test_unsafe_names_rejected_without_store_mutation(
    tmp_path: Path, name: str
) -> None:
    source = _csv(tmp_path, "a.csv", "key\nalpha\n")
    store = tmp_path / "store"
    with pytest.raises(BaselineError):
        save_baseline(name, source, store=store)
    assert not store.exists()


def test_custom_delimiter_persisted_and_conflicts_rejected(tmp_path: Path, capsys) -> None:
    a = _csv(tmp_path, "a.csv", "city^qty\nNorth^1\nSouth^2\n")
    b = _csv(tmp_path, "b.csv", "city^qty\nNorth^1\nWest^2\n")
    store = tmp_path / "store"
    assert main(["baseline", "save", "caret", str(a), "--delimiter", "^",
                 "--store", str(store)]) == 0
    capsys.readouterr()
    assert main(["diff", "caret", str(b), "--saved", "--baseline-store", str(store),
                 "--format", "json", "--no-config"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["columns"]) == 2
    assert main(["diff", "caret", str(b), "--saved", "--delimiter", ",",
                 "--baseline-store", str(store)]) == 2
    assert "conflicts" in capsys.readouterr().err


def test_corrupt_manifest_and_symlink_rejected(tmp_path: Path) -> None:
    a = _csv(tmp_path, "a.csv", "key\nalpha\n")
    store = tmp_path / "store"
    save_baseline("valid", a, store=store)
    manifest = store / "valid/manifest.json"
    manifest.write_text('{"name": "../../bad"}', encoding="utf-8")
    with pytest.raises(BaselineError, match="invalid manifest"):
        get_baseline("valid", store=store)


def test_empty_or_unreadable_file_cannot_be_saved(tmp_path: Path) -> None:
    source = _csv(tmp_path, "header.csv", "name\n")
    store = tmp_path / "store"
    with pytest.raises(BaselineError, match="header-only"):
        save_baseline("empty", source, store=store)
    assert not (store / "empty").exists()
    assert not list(store.glob(".empty-*"))


def test_old_path_based_diff_still_supported(tmp_path: Path, capsys) -> None:
    a = _csv(tmp_path, "a.csv", "city\nNorth\n")
    b = _csv(tmp_path, "b.csv", "city\nNorth\n")
    assert main(["diff", str(a), str(b), "--format", "json", "--no-config"]) == 0
    assert json.loads(capsys.readouterr().out)["file"] == str(b)
    assert main(["diff", str(a), str(b), "--baseline-store", str(tmp_path)]) == 2
    assert "requires --saved" in capsys.readouterr().err


def test_saved_diff_text_html_and_report_metadata(tmp_path: Path, capsys) -> None:
    a = _csv(tmp_path, "a.csv", "country\nNorth\n")
    b = _csv(tmp_path, "b.csv", "country\nSouth\n")
    store = tmp_path / "store"
    assert main(["baseline", "save", "snap", str(a), "--store", str(store)]) == 0
    capsys.readouterr()
    for fmt in ("text", "html"):
        assert main(["diff", "snap", str(b), "--saved", "--baseline-store",
                     str(store), "--format", fmt, "--no-config"]) == 0
        report = capsys.readouterr().out
        assert "Saved baseline: snap" in report
        assert "SHA-256:" in report


def test_saved_diff_cannot_overwrite_snapshot_manifest(tmp_path: Path, capsys) -> None:
    a = _csv(tmp_path, "a.csv", "city\nNorth\n")
    b = _csv(tmp_path, "b.csv", "city\nSouth\n")
    store = tmp_path / "store"
    save_baseline("secure", a, store=store)
    manifest = store / "secure/manifest.json"
    before = manifest.read_bytes()
    status = main(["diff", "secure", str(b), "--saved", "--baseline-store",
                   str(store), "--output", str(manifest)])
    assert status == 2
    assert "outside the saved baseline" in capsys.readouterr().err
    assert manifest.read_bytes() == before
