"""Structured, privacy-first preview and rule explanations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift_lint.cli import main
from sift_lint.config import Config
from sift_lint.loading import load
from sift_lint.repair import plan_repairs, render_plan_json


def _source(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / "input.csv"
    path.write_text(contents, encoding="utf-8", newline="\n")
    return path


def _preview(source: Path, capsys, *options: str) -> dict:
    status = main(["fix", str(source), "--no-config", "--dry-run", "--format", "json",
                   *options])
    captured = capsys.readouterr()
    assert status == 0, captured.err
    assert not captured.err
    return json.loads(captured.out)


def test_json_preview_protects_all_values_by_default(tmp_path: Path, capsys) -> None:
    source = _source(tmp_path, "region,value\n North , 12 \nSouth, 13 \n")
    before = source.read_bytes()
    data = _preview(source, capsys)
    assert source.read_bytes() == before
    assert data["schema_version"] == 1
    assert data["command"] == "fix"
    assert data["mode"] == "preview"
    assert data["summary"]["cells_changed"] == len(data["changes"])
    assert data["summary"]["cells_changed"] > 0
    assert data["values_included"] is False
    assert data["output_selected"] is False
    assert data["write_permitted"] is True
    assert all("before" not in c and "after" not in c for c in data["changes"])
    assert all(c["explanation"] for c in data["changes"])
    assert not (tmp_path / "clean.csv").exists()


def test_show_values_explicit_opt_in_and_sensitive_redaction(tmp_path: Path, capsys) -> None:
    source = _source(tmp_path, 'region,access_token\n" North ","  TOPSECRET  "\n')
    data = _preview(source, capsys, "--show-values")
    serialized = json.dumps(data)
    assert data["values_included"] is True
    assert "TOPSECRET" not in serialized
    assert " North " in serialized
    assert all(change["column"] != "access_token" for change in data["changes"])
    assert any(r["rule"] == "sensitive-value-preserved" for r in data["refusals"])


def test_preview_refusal_message_is_redacted(tmp_path: Path, capsys) -> None:
    source = _source(tmp_path, "password,amount\n  secret  , 3 \n")
    data = _preview(source, capsys, "--show-values")
    assert "secret" not in json.dumps(data)
    assert any(r["rule"] == "sensitive-value-preserved" for r in data["refusals"])


def test_duplicate_dry_run_records_provenance_without_row_contents(
    tmp_path: Path, capsys
) -> None:
    source = _source(tmp_path, "region\n North \nNorth\n")
    data = _preview(source, capsys, "--drop-duplicates", "--show-values")
    assert data["summary"]["rows_dropped"] == 1
    assert data["summary"]["by_rule"]["drop-duplicate-row"] == 1
    dropped = data["dropped_rows"][0]
    assert dropped["line"] == 3
    assert dropped["duplicate_of_line"] == 2
    assert dropped["matched_after_normalization"] is True
    assert "original_values" not in json.dumps(data)


def test_partial_read_is_not_safe_for_export(tmp_path: Path, capsys) -> None:
    source = _source(tmp_path, "region\n North \nSouth\n")
    data = _preview(source, capsys, "--max-rows", "1")
    assert data["partial_read"] is True
    assert data["write_permitted"] is False
    assert data["write_blockers"] == ["partial-read"]
    assert data["rows_read"] == 1


def test_formula_header_blocker_reflected_in_json(tmp_path: Path, capsys) -> None:
    source = _source(tmp_path, '=SUM(A1:A2),region\n1, North \n')
    data = _preview(source, capsys)
    assert data["write_permitted"] is False
    assert "formula-injection-introduced" in data["write_blockers"]
    assert "formula-injection-unresolved" in data["write_blockers"]
    forced = _preview(source, capsys, "--force")
    assert forced["write_permitted"] is True
    assert not forced["write_blockers"]
    assert "formula-injection-header" in forced["accepted_risks"]
    assert "formula-injection-existing" in forced["accepted_risks"]


def test_explain_text_lists_rules_without_cell_values(tmp_path: Path, capsys) -> None:
    source = _source(tmp_path, "region,access_token\n North ,  SECRET  \n")
    assert main(["fix", str(source), "--dry-run", "--no-config", "--explain"]) == 0
    output = capsys.readouterr().out
    assert "Why these changes?" in output
    assert "trim-whitespace" in output
    assert "High-confidence" in output
    assert "SECRET" not in output


@pytest.mark.parametrize("flags", [
    ["--format", "json"],
    ["--show-values", "--dry-run"],
    ["--format", "json", "--show-values"],
    ["--dry-run", "--format", "json", "--explain"],
])
def test_invalid_preview_flags_do_not_write(tmp_path: Path, capsys, flags: list[str]) -> None:
    source = _source(tmp_path, "region\n North \n")
    dest = tmp_path / "clean.csv"
    rc = main(["fix", str(source), "--output", str(dest), "--no-config", *flags])
    assert rc == 2
    assert "sift-lint:" in capsys.readouterr().err
    assert not dest.exists()


def test_external_python_json_preview_is_stable_and_nonmutating(tmp_path: Path) -> None:
    source = _source(tmp_path, "region\n North \n")
    table = load(source)
    plan, rows = plan_repairs(table, Config())
    first = render_plan_json(table, plan, "clean.csv")
    second = render_plan_json(table, plan, "clean.csv")
    assert first == second
    assert rows == [["North"]]
    assert " North " not in first


def test_text_fix_output_unchanged_without_new_options(tmp_path: Path, capsys) -> None:
    source = _source(tmp_path, "region\n North \n")
    dest = tmp_path / "clean.csv"
    assert main(["fix", str(source), "--no-config", "--output", str(dest)]) == 0
    printed = capsys.readouterr().out
    assert "Why these changes?" not in printed
    assert "Findings:" in printed
    assert dest.read_text(encoding="utf-8") == "region\nNorth\n"
