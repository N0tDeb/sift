"""Batch 2 regressions: reject invalid input and unproven numeric conversions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift_lint import diff as public_diff, lint
from sift_lint.checks import run_checks
from sift_lint.cli import main
from sift_lint.config import Config
from sift_lint.drift import compare
from sift_lint.impact import ImpactError, group_impact, sum_impact
from sift_lint.loading import LoadError, load
from sift_lint.profiling import infer_number_convention
from sift_lint.repair import HIGH, plan_repairs


@pytest.mark.parametrize(
    "text",
    [
        'name,value\n"unclosed,1\n',
        'name,value\nword"break,1\n',
        'name,value\n"closed"junk,1\n',
        '"broken,header\n1,2\n',
    ],
)
def test_malformed_quoting_refused_at_load(tmp_path: Path, text: str) -> None:
    path = tmp_path / "malformed.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LoadError, match="(?i)(quot|csv)"):
        load(path)


def test_malformed_quoting_never_overwrites_output_even_with_force(tmp_path: Path) -> None:
    source = tmp_path / "broken.csv"
    source.write_text('id,notes\n1,"forgot to close\n', encoding="utf-8")
    output = tmp_path / "clean.csv"
    output.write_bytes(b"important original output\n")
    assert main([
        "fix", str(source), "--output", str(output), "--force", "--no-config"
    ]) == 2
    assert output.read_bytes() == b"important original output\n"


def test_valid_multiline_and_doubled_quotes_still_load(tmp_path: Path) -> None:
    source = tmp_path / "valid.csv"
    source.write_text('id,comment\n1,"hello\n""there"""\n2,ok\n', encoding="utf-8")
    table = load(source)
    assert table.n_rows == 2
    assert table.columns[1].values == ['hello\n"there"', "ok"]
    assert table.source_lines == [2, 4]


@pytest.mark.parametrize("text", ["\n", "\r\n", ",\n", ",,\n1,2,3\n", "  , \n1,2\n"])
def test_missing_usable_header_rejected(tmp_path: Path, text: str) -> None:
    source = tmp_path / "blank-header.csv"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(LoadError, match="header"):
        load(source)
    assert main(["check", str(source), "--no-config"]) == 2


def test_header_only_with_real_column_names_is_valid_for_check(tmp_path: Path) -> None:
    source = tmp_path / "header_only.csv"
    source.write_text("id,total\n", encoding="utf-8")
    assert load(source).n_rows == 0
    assert isinstance(lint(source), list)


def test_empty_baseline_raises_library_error_and_cli_exit_2(tmp_path: Path) -> None:
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    old.write_text("id,total\n", encoding="utf-8")
    new.write_text("id,total\n1,2\n", encoding="utf-8")
    with pytest.raises(LoadError, match="baseline"):
        compare(load(old), load(new), Config())
    with pytest.raises(LoadError, match="baseline"):
        public_diff(old, new)
    result = tmp_path / "previous-report.txt"
    result.write_bytes(b"old result stays\n")
    assert main(["diff", str(old), str(new), "--no-config", "--output", str(result)]) == 2
    assert result.read_bytes() == b"old result stays\n"


@pytest.mark.parametrize("text", ["1.234", "1,234", "$1,200", "1.234\n2.345"])
def test_grouping_only_cannot_set_certain_locale(tmp_path: Path, text: str) -> None:
    source = tmp_path / "amount.csv"
    if "\n" in text:
        source.write_text("amount\n" + text + "\n", encoding="utf-8")
    else:
        source.write_text("amount\n" + json.dumps(text) + "\n", encoding="utf-8")
    table = load(source)
    reading = infer_number_convention(table.columns[0].profile)
    assert reading.label == "undecidable"
    assert reading.convention is None
    assert table.columns[0].profile.stats() == {}
    findings = run_checks(table, Config())
    assert "ambiguous-decimal-separator" in {finding.code for finding in findings}
    plan, rows = plan_repairs(table, Config(), HIGH)
    assert rows == [[item] for item in table.columns[0].values]
    assert not any(change.rule == "plain-number" for change in plan.changes)
    assert any(refusal.rule == "plain-number" for refusal in plan.refusals)


def test_ambiguous_amount_blocks_impact_sums(tmp_path: Path) -> None:
    source = tmp_path / "amount.csv"
    source.write_text('id,amount\n1,"1,234"\n2,900\n', encoding="utf-8")
    table = load(source)
    with pytest.raises(ImpactError, match="convention"):
        sum_impact(table, Config(), "amount")
    with pytest.raises(ImpactError, match="convention"):
        group_impact(table, Config(), "id", "amount")
    assert main(["impact", str(source), "--sum", "amount", "--no-config"]) == 2


def test_force_can_write_other_fixes_without_guessing_ambiguous_numbers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ambiguous.csv"
    target = tmp_path / "repaired.csv"
    source.write_text(
        'id,amount,note\n1,1.234," Alpha "\n2,2.345," Beta "\n',
        encoding="utf-8",
    )
    assert main([
        "fix", str(source), "--output", str(target), "--force", "--no-config"
    ]) == 0
    output = load(target)
    assert output.columns[1].values == ["1.234", "2.345"]
    assert output.columns[2].values == ["Alpha", "Beta"]


def test_mixed_column_cannot_hide_ambiguous_impact(tmp_path: Path) -> None:
    source = tmp_path / "amount.csv"
    source.write_text('amount\n"1,234"\nunknown text\n', encoding="utf-8")
    table = load(source)
    with pytest.raises(ImpactError, match="convention"):
        sum_impact(table, Config(), "amount")


def test_decisive_locale_keeps_automatic_safe_repairs(tmp_path: Path) -> None:
    source = tmp_path / "amount.csv"
    source.write_text('amount\n"1.234,56"\n"2.345"\n', encoding="utf-8")
    table = load(source)
    reading = infer_number_convention(table.columns[0].profile)
    assert reading.label == "certain" and reading.convention == "eu"
    plan, rows = plan_repairs(table, Config(), HIGH)
    assert [row[0] for row in rows] == ["1234.56", "2345"]
    assert any(change.rule == "plain-number" for change in plan.changes)
