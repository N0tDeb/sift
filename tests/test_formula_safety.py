"""Batch 1: spreadsheet formula checks and repair/output safety.

Inputs are synthetic and never evaluate formulas. No Excel application is used.
"""
from __future__ import annotations

import csv

import pytest

from sift_lint.checks import run_checks
from sift_lint.cli import main
from sift_lint.config import Config
from sift_lint.inference import is_formula_injection
from sift_lint.loading import load
from sift_lint.sources import load_excel


@pytest.mark.parametrize(
    "value",
    [
        " +HYPERLINK(\"https://example.invalid\",\"click\")",
        "\n=1+1",
        "\u00a0=1+1",
        "\t@SUM(1,2)",
        "-SUM(1,2)",
        "\r\n+CMD(1)",
    ],
)
def test_formula_detector_handles_numeric_mixed_and_hidden_prefixes(value):
    assert is_formula_injection(value)


@pytest.mark.parametrize("value", ["+15", "-15", "  -12.50 ", " +1,234.50 "])
def test_signed_numbers_not_flagged_as_formulas(value):
    assert not is_formula_injection(value)


@pytest.mark.parametrize("value", [" +HYPERLINK(1,2)", "\n=1+1", "\u00a0=1+1", "-SUM(1,2)"])
def test_formula_like_cells_in_predominantly_numeric_column_block_repair(tmp_path, capsys, value):
    src = tmp_path / "source.csv"
    with src.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "amount"])
        for i in range(1, 13):
            writer.writerow([str(i), str(i * 10) if i != 8 else value])
    table = load(src)
    assert any(f.code == "formula-injection" for f in run_checks(table, Config()))
    output = tmp_path / "repaired.csv"
    assert main(["fix", str(src), "--output", str(output), "--no-config"]) == 2
    assert not output.exists()
    assert "formula-injection" in capsys.readouterr().out
    # An explicit acceptance never normalizes an existing risky cell.
    assert main(["fix", str(src), "--output", str(output), "--no-config", "--force"]) == 0
    with output.open(newline="", encoding="utf-8") as handle:
        data = list(csv.reader(handle))
    assert data[8][1] == value


def test_risky_formula_header_refused_without_force(tmp_path):
    src = tmp_path / "source.csv"
    src.write_text('"=1+1",name\n1, Alice \n', encoding="utf-8")
    out = tmp_path / "fixed.csv"
    assert main(["fix", str(src), "--output", str(out), "--no-config"]) == 2
    assert not out.exists()


def test_new_formula_payload_never_written_even_with_force(tmp_path, monkeypatch):
    import sift_lint.cli as cli
    from sift_lint.repair import Change, RepairPlan

    src = tmp_path / "source.csv"
    src.write_text("id,label\n1,safe\n", encoding="utf-8")
    out = tmp_path / "fixed.csv"

    def unsafe_plan(*_args, **_kwargs):
        plan = RepairPlan(changes=[Change(2, "label", "safe", "=1+1", "dummy", "high")])
        return plan, [["1", "=1+1"]]

    monkeypatch.setattr(cli, "plan_repairs", unsafe_plan)
    assert main(["fix", str(src), "--output", str(out), "--no-config", "--force"]) == 2
    assert not out.exists()


def _make_workbook(path, formula_sheet="Data", header_formula=False, trailing=False):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = "Data"
    ws.append(["id", "amount"])
    ws.append([1, 22])
    if header_formula:
        ws.cell(1, 2, "=1+1")
    elif formula_sheet == "Data":
        ws.cell(2 if not trailing else 25, 2, "=2+3")
    other = workbook.create_sheet("Other")
    other.append(["id", "amount"])
    other.append([1, "=SUM(1,2)"])
    workbook.save(path)
    workbook.close()


@pytest.mark.parametrize("extra_args", [[], ["--force"]])
def test_excel_formula_without_cached_value_never_written(tmp_path, capsys, extra_args):
    src = tmp_path / "has-formula.xlsx"
    _make_workbook(src)
    table = load_excel(src)
    assert "excel-formulas" in {f.code for f in table.file_findings}
    out = tmp_path / "fixed.csv"
    assert main(["fix", str(src), "--output", str(out), "--no-config", *extra_args]) == 2
    assert not out.exists()
    assert "refusing to export" in capsys.readouterr().err
    assert main(["fix", str(src), "--dry-run", "--no-config"]) == 0
    assert "excel-formulas" in capsys.readouterr().out


def test_selected_formula_free_excel_sheet_can_be_repaired(tmp_path):
    src = tmp_path / "has-other-formulas.xlsx"
    _make_workbook(src, formula_sheet="Other")
    selected = load_excel(src, sheet="Data")
    assert "excel-formulas" not in {f.code for f in selected.file_findings}
    assert "unread-sheets" in {f.code for f in selected.file_findings}
    out = tmp_path / "fixed.csv"
    assert main(["fix", str(src), "--sheet", "Data", "--output", str(out), "--no-config"]) == 0
    assert out.exists()
    assert main([
        "fix", str(src), "--sheet", "Other", "--output", str(tmp_path / "other.csv"),
        "--force", "--no-config",
    ]) == 2


def test_excel_formula_in_header_blocks_fix(tmp_path):
    src = tmp_path / "header-formula.xlsx"
    _make_workbook(src, header_formula=True)
    out = tmp_path / "fixed.csv"
    assert main(["fix", str(src), "--output", str(out), "--no-config"]) == 2
    assert not out.exists()


def test_excel_formula_beyond_row_sample_blocks_full_fix(tmp_path):
    src = tmp_path / "later-formula.xlsx"
    _make_workbook(src, trailing=True)
    table = load_excel(src, max_rows=1)
    assert "excel-formulas" in {f.code for f in table.file_findings}
    out = tmp_path / "fixed.csv"
    assert main(["fix", str(src), "--output", str(out), "--no-config"]) == 2
    assert not out.exists()
