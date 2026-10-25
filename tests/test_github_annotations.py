"""Opt-in GitHub Actions formatting: compatible exit codes and no value leaks."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift_lint.cli import main
from sift_lint.findings import Finding, Severity
from sift_lint.github_annotations import _escape, render_annotations


def test_protocol_escapes_workflow_delimiters_and_control_bytes() -> None:
    finding = Finding(
        "odd-rule", Severity.WARNING,
        "line\n::error file=fake.csv::injected\rESC:\x1b[31m 100%",
        column="a,b:danger%",
        examples=["secret-example"],
    )
    report = render_annotations([(Path("data,x::y.csv"), None, [finding])])
    assert report.startswith("::warning file=data%2Cx%3A%3Ay.csv,title=sift%3A odd-rule::")
    assert "line%0A::error" in report
    assert "injected%0DESC:" in report
    assert "\\x1b" in report and "\x1b" not in report
    assert "100%25" in report
    assert "Column 'a,b:danger%25'" in report
    assert "secret-example" not in report
    assert len(report.splitlines()) == 1


def test_protocol_escapes_percent_before_other_properties() -> None:
    assert _escape("%2C,\n", property_value=True) == "%252C%2C%0A"


@pytest.mark.parametrize("severity, command", [
    (Severity.ERROR, "::error "),
    (Severity.WARNING, "::warning "),
    (Severity.INFO, "::notice "),
])
def test_severity_map(severity: Severity, command: str) -> None:
    output = render_annotations([(Path("orders.csv"), None, [
        Finding("test-code", severity, "explanation")
    ])])
    assert output.startswith(command)
    assert "title=sift%3A test-code" in output
    assert "line=" not in output


def test_unreadable_annotations_are_errors_even_without_findings() -> None:
    result = render_annotations([], [(Path("bad.csv"), "Private", "Cannot parse\ninput")])
    assert result.startswith("::error file=bad.csv,title=sift%3A unreadable::")
    assert "Worksheet 'Private':" in result
    assert "parse%0Ainput" in result


def test_single_file_github_format_and_threshold(tmp_path: Path, capsys) -> None:
    sample = tmp_path / "orders.csv"
    sample.write_text("id,quantity\n1,-3\n1,-5\n", encoding="utf-8")
    assert main(["check", str(sample), "--no-config", "--format", "github",
                 "--fail-on", "none", "--key", "id"]) == 0
    stdout = capsys.readouterr().out
    assert stdout.count("::warning ") + stdout.count("::error ") >= 1
    assert "::notice " in stdout or "::warning " in stdout or "::error " in stdout
    assert "\n::" not in stdout or all(line.startswith("::") for line in stdout.splitlines())


def test_single_file_write_output_without_annotations_on_stdout(tmp_path: Path, capsys) -> None:
    source = tmp_path / "orders.csv"
    report = tmp_path / "annotations.txt"
    source.write_text("id,qty\n1,\n", encoding="utf-8")
    assert main(["check", str(source), "--format", "github", "--no-config",
                 "--fail-on", "none", "--output", str(report)]) == 0
    assert report.is_file()
    assert "::" in report.read_text(encoding="utf-8")
    assert "::error" not in capsys.readouterr().out


def test_multi_file_unreadable_is_exit_two(tmp_path: Path, capsys) -> None:
    one = tmp_path / "valid.csv"
    one.write_text("id,qty\n1,-5\n", encoding="utf-8")
    missing = tmp_path / "missing.csv"
    assert main(["check", str(one), str(missing), "--no-config",
                 "--format", "github", "--fail-on", "none"]) == 2
    out = capsys.readouterr().out
    assert "::error file=" in out and "sift%3A unreadable" in out
    assert str(missing) in out


def test_sensitive_examples_never_reach_workflow_log(tmp_path: Path, capsys) -> None:
    source = tmp_path / "private.csv"
    secret = "top-secret-credential-ABC123"
    source.write_text("id,password\n1," + secret + "\n1," + secret + "\n", encoding="utf-8")
    assert main(["check", str(source), "--no-config", "--format", "github",
                 "--fail-on", "none"]) == 0
    output = capsys.readouterr().out
    assert "::" in output
    assert secret not in output


def test_all_sheets_annotations_preserve_context(tmp_path: Path, capsys) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "workbook.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Warehouse"
    sheet.append(["id", "qty"])
    sheet.append([1, -3])
    sheet.append([1, -5])
    hidden = workbook.create_sheet("Hidden Notes")
    hidden.sheet_state = "hidden"
    hidden.append(["id", "qty"])
    hidden.append([1, -5])
    hidden.append([1, -5])
    workbook.save(source)
    assert main(["check", str(source), "--all-sheets", "--no-config",
                 "--format", "github", "--fail-on", "none", "--key", "id"]) == 0
    output = capsys.readouterr().out
    assert "Warehouse" in output and "Hidden Notes" in output
    assert all(line.startswith("::") for line in output.splitlines())


def test_diff_does_not_claim_to_support_workflow_commands() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["diff", "a.csv", "b.csv", "--format", "github"])
    assert exc.value.code == 2


def test_no_findings_emit_no_annotations() -> None:
    assert render_annotations([(Path("empty.csv"), None, [])]) == ""
