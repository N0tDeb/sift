"""Regression tests for the independent post-Batch-5 release audit."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from sift_lint import diff as public_diff
from sift_lint.checks import run_checks
from sift_lint.cli import main
from sift_lint.config import Config
from sift_lint.drift import compare
from sift_lint.loading import load
from sift_lint.references import Reference, check_reference
from sift_lint.repair import plan_repairs
from sift_lint.sources import load_any


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _private_baseline(tmp_path: Path) -> tuple[Path, Path]:
    old = _write(
        tmp_path / "baseline.csv",
        "category\nalpha@example.com\nbeta@example.com\ngamma@example.com\n",
    )
    new = _write(
        tmp_path / "today.csv", "category\nNorth\nSouth\nEast\n"
    )
    return old, new


@pytest.mark.parametrize("format_name", ["json", "text", "html"])
def test_diff_redacts_baseline_only_sensitive_data(tmp_path: Path, capsys, format_name: str):
    old, new = _private_baseline(tmp_path)
    assert main([
        "diff", str(old), str(new), "--no-config", "--format", format_name,
        "--fail-on", "none",
    ]) == 0
    result = capsys.readouterr().out
    assert "alpha@example.com" not in result
    assert "beta@example.com" not in result
    assert "gamma@example.com" not in result
    assert "missing-category" in result
    if format_name == "json":
        payload = json.loads(result)
        assert all(
            not finding["examples"]
            for finding in payload["findings"]
            if finding["code"] == "missing-category"
        )


def test_diff_public_api_and_comparator_redact_baseline(tmp_path: Path):
    old, new = _private_baseline(tmp_path)
    api = public_diff(old, new, Config())
    direct = compare(load(old), load(new), Config())
    for findings in (api, direct):
        leaked = json.dumps([f.to_dict() for f in findings])
        assert "alpha@example.com" not in leaked
        assert any(f.code == "missing-category" for f in findings)


@pytest.mark.parametrize("format_name", ["json", "text", "html"])
def test_reference_redacts_sensitive_foreign_values(tmp_path: Path, capsys, format_name):
    foreign = _write(tmp_path / "foreign.csv", "password\nTOPSECRET\nTOPSECRET\n")
    local = _write(tmp_path / "local.csv", "lookup\nTOPSECRET\n")
    assert main([
        "check", str(local), "--references", f"{foreign}:lookup=password",
        "--no-config", "--format", format_name,
    ]) == 1
    data = capsys.readouterr().out
    assert "TOPSECRET" not in data
    assert "reference-not-unique" in data
    if format_name == "json":
        assert next(f for f in json.loads(data)["findings"]
                    if f["code"] == "reference-not-unique")["examples"] == []


def test_reference_direct_python_redacts_foreign_values(tmp_path: Path):
    foreign = _write(tmp_path / "foreign.csv", "password\nOPAQUE\nOPAQUE\n")
    local = _write(tmp_path / "local.csv", "lookup\nOPAQUE\n")
    ref = Reference(local="lookup", path=foreign, foreign="password")
    result = check_reference(load(local), ref, Config(), load)
    assert "OPAQUE" not in json.dumps([f.to_dict() for f in result])


@pytest.mark.parametrize("confidence", ["high", "medium"])
def test_sensitive_field_bytes_are_not_normalized(
    tmp_path: Path, capsys, confidence: str
):
    src = _write(
        tmp_path / "secret.csv",
        'id,password,region\n1,"  SYNTHETIC-SECRET  "," North "\n2,valid,South\n',
    )
    output = tmp_path / "repaired.csv"
    audit = tmp_path / "audit.csv"
    assert main([
        "fix", str(src), "--no-config", "--min-confidence", confidence,
        "--output", str(output), "--log", str(audit),
    ]) == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC-SECRET" not in out
    assert "SYNTHETIC-SECRET" not in audit.read_text(encoding="utf-8")
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[1][1] == "  SYNTHETIC-SECRET  "
    assert rows[1][2] == "North"
    assert "sensitive-value-preserved" in out


def test_sensitive_field_plan_preserves_value(tmp_path: Path):
    path = _write(tmp_path / "opaque.csv", "password\n passphrase \n")
    table = load(path)
    plan, rows = plan_repairs(table, Config(), "medium")
    assert rows == [[" passphrase "]]
    assert not plan.changes
    assert any(r.rule == "sensitive-value-preserved" for r in plan.refusals)


@pytest.mark.parametrize("values", ["pw1\npw2\n", ""])
def test_sensitive_column_warning_not_duplicated(tmp_path: Path, values):
    src = _write(tmp_path / "named.csv", "password\n" + values)
    findings = run_checks(load(src), Config())
    assert sum(f.code == "sensitive-column-name" for f in findings) == 1


def test_fix_custom_delimiter_uses_correct_postcheck(tmp_path: Path, capsys):
    src = _write(tmp_path / "caret.csv", "id^region\n1^ North \n2^North\n")
    output = tmp_path / "out.csv"
    assert main([
        "fix", str(src), "--no-config", "--delimiter", "^", "--output", str(output),
    ]) == 0
    printed = capsys.readouterr().out
    after = run_checks(load_any(output, delimiter="^"), Config())
    match = re.search(r"Findings: (\d+) before, (\d+) after", printed)
    assert match is not None
    assert int(match.group(2)) == len(after)
    assert output.read_text(encoding="utf-8").startswith("id^region\n")


def test_fix_tsv_output_is_tab_delimited(tmp_path: Path):
    src = _write(tmp_path / "comma.csv", "id,region\n1, North \n2,South\n")
    output = tmp_path / "out.tsv"
    assert main(["fix", str(src), "--no-config", "--output", str(output)]) == 0
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    assert rows == [["id", "region"], ["1", "North"], ["2", "South"]]
    assert "\t" in output.read_text(encoding="utf-8")


def test_drift_skips_numeric_postcodes_but_not_measurements(tmp_path: Path):
    old = _write(tmp_path / "baseline.csv", "zip,amount,id\n10001,10,1\n10002,11,2\n")
    new = _write(tmp_path / "new.csv", "zip,amount,id\n90001,100,501\n90002,110,502\n")
    findings = compare(load(old), load(new), Config())
    columns = {f.column for f in findings if f.code == "distribution-shift"}
    assert "amount" in columns
    assert "zip" not in columns
    assert "id" not in columns


def test_parquet_dependency_minimum_is_patched():
    import tomllib

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    for extra in ("parquet", "all", "dev"):
        assert "pyarrow>=14.0.1" in project["project"]["optional-dependencies"][extra]

@pytest.mark.parametrize("name", ["api_key", "access_token", "client_secret", "email", "phone"])
def test_sensitive_opaque_names_protect_small_files(tmp_path: Path, name: str):
    # Name-based protection must work without three matching values.
    src = _write(tmp_path / "single.csv", f"{name}\n  OPAQUE  \n")
    table = load(src)
    plan, rows = plan_repairs(table, Config())
    assert rows == [["  OPAQUE  "]]
    assert not plan.changes
    assert sum(f.code == "sensitive-column-name" for f in run_checks(table, Config())) == 1
