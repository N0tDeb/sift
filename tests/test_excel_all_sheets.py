"""Workbook-wide inspection without missing or rewriting worksheets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift_lint.cli import main
from sift_lint.sources import excel_sheet_names


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Orders"
    sheet.append(["id", "amount"])
    sheet.append(["A1", 20])
    sheet.append(["A2", 30])

    hidden = book.create_sheet("Hidden & Formula")
    hidden.sheet_state = "hidden"
    hidden.append(["id", "amount"])
    hidden.append(["B1", 42])
    hidden.append(["B2", "=21*2"])

    path = tmp_path / "data.xlsx"
    book.save(path)
    book.close()
    return path


def test_list_includes_hidden_sheet(workbook: Path) -> None:
    assert excel_sheet_names(workbook) == ["Orders", "Hidden & Formula"]


def test_check_all_sheets_json_with_formulas(workbook: Path, capsys) -> None:
    before = workbook.read_bytes()
    rc = main(["check", str(workbook), "--all-sheets", "--format", "json",
               "--fail-on", "none"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert [item["sheet"] for item in result["files"]] == [
        "Orders", "Hidden & Formula"
    ]
    assert all(item["file"] == str(workbook) for item in result["files"])
    assert [item["rows"] for item in result["files"]] == [2, 2]
    assert not result["unreadable"]
    assert not any(f["code"] == "unread-sheets" for i in result["files"]
                   for f in i["findings"])
    assert not any(f["code"] == "excel-formulas" for f in result["files"][0]["findings"])
    assert any(f["code"] == "excel-formulas" for f in result["files"][1]["findings"])
    assert workbook.read_bytes() == before


def test_check_all_sheets_exit_threshold(workbook: Path, capsys) -> None:
    assert main(["check", str(workbook), "--all-sheets", "--fail-on", "warning"]) == 1
    output = capsys.readouterr().out
    assert "[sheet: Orders]" in output
    assert "[sheet: Hidden & Formula]" in output


def test_default_first_sheet_behavior_unchanged(workbook: Path, capsys) -> None:
    assert main(["check", str(workbook), "--format", "json", "--fail-on", "none"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert "files" not in result
    assert "file" in result
    assert any(f["code"] == "unread-sheets" for f in result["findings"])


def test_explicit_sheet_behavior_unchanged(workbook: Path, capsys) -> None:
    assert main(["check", str(workbook), "--sheet", "Hidden & Formula",
                 "--format", "json", "--fail-on", "none"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "excel-formulas" for f in result["findings"])
    assert any(f["code"] == "unread-sheets" for f in result["findings"])


def test_mutually_exclusive_sheet_selection(workbook: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["check", str(workbook), "--sheet", "Orders", "--all-sheets"])
    with pytest.raises(SystemExit, match="2"):
        main(["profile", str(workbook), "--sheet", "Orders", "--all-sheets"])


def test_profile_all_sheets(workbook: Path, capsys) -> None:
    assert main(["profile", str(workbook), "--all-sheets"]) == 0
    out = capsys.readouterr().out
    assert "[sheet: Orders]" in out
    assert "[sheet: Hidden & Formula]" in out
    assert "amount" in out
    assert "unread-sheets" not in out


def test_profile_all_sheets_rejects_non_excel(tmp_path: Path, capsys) -> None:
    path = tmp_path / "sample.csv"
    path.write_text("id,name\n1,A\n", encoding="utf-8")
    assert main(["profile", str(path), "--all-sheets"]) == 2
    assert "requires an .xlsx or .xlsm" in capsys.readouterr().err


def test_empty_sheet_is_reported_and_is_error(workbook: Path, capsys) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(workbook)
    wb.create_sheet("Empty")
    wb.save(workbook)
    wb.close()
    assert main(["check", str(workbook), "--all-sheets", "--format", "json",
                 "--fail-on", "none"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert len(report["files"]) == 2
    assert report["unreadable"][0]["sheet"] == "Empty"
    assert "No usable rows" in report["unreadable"][0]["error"]


def test_profile_failure_does_not_skip_sibling(workbook: Path, capsys) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(workbook)
    wb.create_sheet("Empty")
    wb.save(workbook)
    wb.close()
    assert main(["profile", str(workbook), "--all-sheets"]) == 2
    report = capsys.readouterr()
    assert "[sheet: Orders]" in report.out
    assert "[sheet: Hidden & Formula]" in report.out
    assert "sheet Empty:" in report.err


def test_max_rows_still_checks_formulas_in_remaining_rows(workbook: Path, capsys) -> None:
    assert main(["check", str(workbook), "--all-sheets", "--max-rows", "1",
                 "--format", "json", "--fail-on", "none"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [item["rows"] for item in report["files"]] == [1, 1]
    assert any(f["code"] == "excel-formulas" for f in report["files"][1]["findings"])


def test_mixed_directory_checks_other_formats(workbook: Path, capsys) -> None:
    (workbook.parent / "notes.csv").write_text("id,v\n1,2\n", encoding="utf-8")
    assert main(["check", str(workbook.parent), "--all-sheets", "--format", "json",
                 "--fail-on", "none"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["files"]) == 3
    csv_report = next(item for item in result["files"] if item["file"].endswith("notes.csv"))
    assert "sheet" not in csv_report


def test_html_escapes_sheet_names_and_file_output(workbook: Path, tmp_path: Path,
                                                    capsys) -> None:
    output = tmp_path / "reports" / "analysis.html"
    output.parent.mkdir()
    assert main(["check", str(workbook), "--all-sheets", "--format", "html",
                 "--fail-on", "none", "--output", str(output)]) == 0
    assert "Wrote" in capsys.readouterr().out
    data = output.read_text(encoding="utf-8")
    assert "Hidden &amp; Formula" in data
    assert "[sheet: Orders]" in data
    assert "<html" in data


def test_json_file_output(workbook: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "analysis.json"
    assert main(["check", str(workbook), "--all-sheets", "--format", "json",
                 "--fail-on", "none", "--output", str(output)]) == 0
    assert "Wrote" in capsys.readouterr().out
    assert len(json.loads(output.read_text())["files"]) == 2


def test_unreadable_workbook_continues_with_csv(tmp_path: Path, capsys) -> None:
    path = tmp_path / "wrong.xlsx"
    path.write_text("not a workbook", encoding="utf-8")
    csv_path = tmp_path / "clean.csv"
    csv_path.write_text("id,n\n1,5\n", encoding="utf-8")
    assert main(["check", str(path), str(csv_path), "--all-sheets",
                 "--format", "json", "--fail-on", "none"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert len(result["unreadable"]) == 1
    assert result["unreadable"][0]["file"] == str(path)
    assert len(result["files"]) == 1
    assert result["files"][0]["file"] == str(csv_path)


def test_output_does_not_overwrite_input(workbook: Path) -> None:
    before = workbook.read_bytes()
    assert main(["check", str(workbook), "--all-sheets", "--output", str(workbook)]) == 2
    assert workbook.read_bytes() == before
