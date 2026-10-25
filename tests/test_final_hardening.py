"""Final hardening: schema identity and drift with repeated column names."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from sift_lint.cli import main
from sift_lint.config import Config
from sift_lint.drift import compare
from sift_lint.loading import load
from sift_lint.sources import _build, load_excel, load_parquet


def test_csv_column_keys_never_hide_literal_header_names(tmp_path: Path) -> None:
    source = tmp_path / "collisions.csv"
    source.write_bytes(b"v,v,v#1,v#2,v\n10,20,30,40,50\n")
    table = load(source)
    assert [(c.name, c.key) for c in table.columns] == [
        ("v", "v"),
        ("v", "v#3"),
        ("v#1", "v#1"),
        ("v#2", "v#2"),
        ("v", "v#4"),
    ]
    assert len({column.key for column in table.columns}) == len(table.columns)
    assert table.by_key("v#1").values == ["30"]
    assert table.by_key("v#2").values == ["40"]
    assert table.by_key("v#3").values == ["20"]
    assert table.by_key("v#4").values == ["50"]


def test_typed_builder_uses_same_unique_key_policy(tmp_path: Path) -> None:
    table = _build(
        tmp_path / "source.parquet",
        ["v", "v", "v#1"],
        [["1", "2", "3"]],
        [],
    )
    assert [column.key for column in table.columns] == ["v", "v#2", "v#1"]


def test_excel_duplicate_and_literal_header_keys(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "headers.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["v", "v", "v#1"])
    workbook.active.append([10, 20, 30])
    workbook.save(source)
    table = load_excel(source)
    assert [column.key for column in table.columns] == ["v", "v#2", "v#1"]
    assert table.by_key("v#1").values == ["30"]
    assert table.by_key("v#2").values == ["20"]


def test_parquet_loader_looks_up_declared_types_by_index(monkeypatch, tmp_path: Path) -> None:
    # Use a small stand-in for the Arrow API to exercise name-duplicate
    # handling without depending on a particular Parquet writer version.
    # Normal Parquet integration is also exercised by the CI distribution job.
    fake_pyarrow = ModuleType("pyarrow")
    fake_pyarrow.__path__ = []
    fake_parquet = ModuleType("pyarrow.parquet")

    class FakeScalar:
        def __init__(self, value):
            self.value = value

        def as_py(self):
            return self.value

    class FakeSchema:
        names = ["v", "v", "v#1"]

        def field(self, index):
            assert isinstance(index, int), "duplicate names need positional field lookup"
            return type("Field", (), {"type": "int64"})()

    class FakeData:
        schema = FakeSchema()
        column_names = schema.names

        def column(self, index):
            return [FakeScalar(index * 10 + 1), FakeScalar(index * 10 + 2)]

    fake_parquet.read_table = lambda path: FakeData()
    monkeypatch.setitem(sys.modules, "pyarrow", fake_pyarrow)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", fake_parquet)
    table = load_parquet(tmp_path / "example.parquet")
    assert [column.key for column in table.columns] == ["v", "v#2", "v#1"]
    assert table.by_key("v#1").values == ["21", "22"]


def test_diff_notices_removed_repeated_header_not_a_reorder(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_bytes(b"v,v\n1,2\n")
    new.write_bytes(b"v\n1\n")
    findings = compare(load(old), load(new), Config())
    assert [f.code for f in findings].count("column-removed") == 1
    assert not any(f.code == "column-order-changed" for f in findings)
    removed = next(f for f in findings if f.code == "column-removed")
    assert removed.detail == {"from": 2, "to": 1}


def test_diff_notices_new_repeated_header(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_bytes(b"v\n1\n")
    new.write_bytes(b"v,v\n1,2\n")
    findings = compare(load(old), load(new), Config())
    assert [f.code for f in findings].count("column-added") == 1
    assert not any(f.code == "column-order-changed" for f in findings)
    added = next(f for f in findings if f.code == "column-added")
    assert added.detail == {"from": 1, "to": 2}


def test_diff_compares_each_duplicate_occurrence_once(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_bytes(b"v,v\n1,2\n1,2\n")
    new.write_bytes(b"v,v\n1,9\n1,9\n")
    findings = compare(load(old), load(new), Config())
    shifts = [f for f in findings if f.code == "distribution-shift"]
    assert len(shifts) == 1
    assert shifts[0].detail == {"from": 2.0, "to": 9.0}


def test_schema_reordering_with_duplicates_is_still_reported(tmp_path: Path) -> None:
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_bytes(b"v,other,v\n1,a,2\n")
    new.write_bytes(b"v,v,other\n1,2,a\n")
    findings = compare(load(old), load(new), Config())
    assert any(f.code == "column-order-changed" for f in findings)

def test_cli_impact_can_select_reserved_literal_column(tmp_path: Path, capsys) -> None:
    source = tmp_path / "collisions.csv"
    source.write_bytes(b"v,v,v#1\n10,20,30\n10,20,30\n")
    assert main(["impact", str(source), "--sum", "v#1", "--no-config"]) == 0
    assert "60" in capsys.readouterr().out
