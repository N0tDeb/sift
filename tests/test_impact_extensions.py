"""Numeric aggregate impact and signed GROUP BY exposure."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift_lint.cli import main
from sift_lint.config import Config
from sift_lint.impact import ImpactError, aggregate_impact, group_impact
from sift_lint.loading import load


def _table(tmp_path: Path, text: str):
    source = tmp_path / "input.csv"
    source.write_text(text, encoding="utf-8", newline="\n")
    return load(source)


def test_avg_min_max_compare_strict_and_repaired_rows(tmp_path: Path) -> None:
    table = _table(tmp_path, "id,amount\n1,100\n2,200\n3,$300\n")
    average = aggregate_impact(table, Config(), "amount", "avg")
    minimum = aggregate_impact(table, Config(), "amount", "min")
    maximum = aggregate_impact(table, Config(), "amount", "max")
    assert (average.coerced_value, average.repaired_value) == (150, 200)
    assert (minimum.coerced_value, minimum.repaired_value) == (100, 100)
    assert (maximum.coerced_value, maximum.repaired_value) == (200, 300)
    assert average.difference == 50
    assert minimum.difference == 0
    assert maximum.difference == 100
    assert not average.loads_as_number
    assert (average.coerced_rows, average.repaired_rows, average.total_rows) == (2, 3, 3)


def test_cli_multiple_metrics_and_old_sum_can_coexist(tmp_path: Path, capsys) -> None:
    table = _table(tmp_path, "id,amount\n1,100\n2,200\n3,$300\n")
    assert main([
        "impact", str(table.path), "--no-config", "--sum", "amount",
        "--avg", "amount", "--min", "amount", "--max", "amount",
    ]) == 0
    output = capsys.readouterr().out
    assert "SUM(amount)" in output
    for metric in ("AVG", "MIN", "MAX"):
        assert f"{metric}(amount)" in output
    assert "from 2 of 3 rows" in output
    assert "from 3 of 3 rows" in output


def test_mean_alias_and_group_counts_unchanged(tmp_path: Path, capsys) -> None:
    table = _table(tmp_path, "region,amount\nNorth,100\nnorth,200\n")
    assert main([
        "impact", str(table.path), "--no-config", "--mean", "amount",
        "--group-by", "region",
    ]) == 0
    output = capsys.readouterr().out
    assert "AVG(amount)" in output
    assert "GROUP BY region" in output
    assert "GROUP BY region, SUM" not in output


def test_empty_numeric_aggregates_report_unavailable(tmp_path: Path, capsys) -> None:
    table = _table(tmp_path, "id,amount\n1,banana\n2,apple\n")
    result = aggregate_impact(table, Config(), "amount", "avg")
    assert result.coerced_value is None
    assert result.repaired_value is None
    assert result.difference is None
    assert main(["impact", str(table.path), "--no-config", "--min", "amount"]) == 0
    assert "n/a (no numeric rows)" in capsys.readouterr().out


def test_ambiguous_locale_refuses_all_new_metrics(tmp_path: Path) -> None:
    table = _table(tmp_path, 'amount\n"1,200"\n"1,300"\n')
    for metric in ("avg", "min", "max"):
        with pytest.raises(ImpactError, match="undecidable"):
            aggregate_impact(table, Config(), "amount", metric)
        assert main(["impact", str(table.path), "--no-config", f"--{metric}", "amount"]) == 2


def test_missing_key_and_unknown_metric_fail_cleanly(tmp_path: Path, capsys) -> None:
    table = _table(tmp_path, "id,amount\n1,3\n")
    with pytest.raises(ImpactError, match="No column"):
        aggregate_impact(table, Config(), "missing", "avg")
    with pytest.raises(ImpactError, match="Unsupported aggregate"):
        aggregate_impact(table, Config(), "amount", "median")
    assert main(["impact", str(table.path), "--no-config", "--avg", "missing"]) == 2
    assert "No column" in capsys.readouterr().err


def test_numeric_aggregation_preserves_duplicate_column_selection(tmp_path: Path) -> None:
    table = _table(tmp_path, "v,v\n1,50\n3,70\n")
    assert aggregate_impact(table, Config(), "v", "avg").coerced_value == 2
    assert aggregate_impact(table, Config(), "v#1", "avg").coerced_value == 60


def test_group_negative_amounts_do_not_cancel_exposure(tmp_path: Path) -> None:
    table = _table(tmp_path, "region,amount\nNorth,100\nnorth,-100\nSouth,200\n")
    result = group_impact(table, Config(), "region", "amount")
    assert result.total_amount == 200  # signed API behavior is retained
    assert result.total_magnitude == 400
    assert result.misplaced_amount == 100
    assert result.misplaced_share == pytest.approx(0.25)
    assert result.merges[0][2] == 100


def test_group_zero_net_still_reports_misplaced_share(tmp_path: Path) -> None:
    table = _table(tmp_path, "region,amount\nNorth,100\nnorth,-100\n")
    result = group_impact(table, Config(), "region", "amount")
    assert result.total_amount == 0
    assert result.total_magnitude == 200
    assert result.misplaced_share == pytest.approx(0.5)


def test_group_counts_use_row_share_without_measure(tmp_path: Path) -> None:
    table = _table(tmp_path, "region\nNorth\nnorth\nSouth\n")
    result = group_impact(table, Config(), "region")
    assert result.total_amount == 3
    assert result.total_magnitude == 3
    assert result.misplaced_share == pytest.approx(1 / 3)


def test_new_metrics_reuse_single_repaired_snapshot(tmp_path: Path, monkeypatch) -> None:
    import sift_lint.impact as impact_module

    table = _table(tmp_path, "id,amount\n1,100\n2,$300\n")
    real = impact_module.plan_repairs
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(impact_module, "plan_repairs", counted)
    assert main([
        "impact", str(table.path), "--no-config", "--sum", "amount",
        "--avg", "amount", "--max", "amount",
    ]) == 0
    assert calls == 1


def test_large_finite_average_does_not_overflow(tmp_path: Path) -> None:
    # float's largest value has no representable successor, but its AVG with
    # itself is perfectly valid. A direct fsum of value/n can still overflow.
    huge = "17976931348623157" + "0" * 292
    table = _table(tmp_path, f"amount\n{huge}\n{huge}\n{huge}\n")
    result = aggregate_impact(table, Config(), "amount", "avg")
    assert result.coerced_value == pytest.approx(result.repaired_value)
    assert result.coerced_value is not None
    assert result.coerced_value > 1e307
