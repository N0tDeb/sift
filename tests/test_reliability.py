"""Regressions for selectors, configuration, and output safety."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sift_lint.cli import main
from sift_lint.config import Config, ConfigError, load_config
from sift_lint.impact import ImpactError, group_impact, sum_impact
from sift_lint.loading import load
from sift_lint.output import atomic_write_text
from sift_lint.repair import RepairPlan, write_csv, write_log


def _source(tmp_path: Path, contents: str = "id,region\n1, North \n2,West\n") -> Path:
    path = tmp_path / "source.csv"
    path.write_bytes(contents.encode("utf-8"))
    return path


def test_impact_duplicate_columns_accept_explicit_keys(tmp_path: Path, capsys) -> None:
    src = _source(tmp_path, "id,v,v\n1,2,12\n2,3,13\n")
    table = load(src)
    assert [c.key for c in table.columns] == ["id", "v", "v#1"]
    assert sum_impact(table, Config(), "v#1").coerced_total == 25
    assert group_impact(table, Config(), "v#1").groups_now == 2
    assert group_impact(table, Config(), "v", "v#1").total_amount == 25
    assert main(["impact", str(src), "--sum", "v#1", "--no-config"]) == 0
    assert "SUM(v#1)" in capsys.readouterr().out
    assert main(["impact", str(src), "--group-by", "v#1", "--no-config"]) == 0
    assert "GROUP BY v#1" in capsys.readouterr().out


def test_impact_bad_key_is_a_domain_error(tmp_path: Path) -> None:
    table = load(_source(tmp_path))
    with pytest.raises(ImpactError, match="No column"):
        sum_impact(table, Config(), "nonexistent")


@pytest.mark.parametrize("name", ["null_warn", "null_error", "outlier_share", "outlier_z"])
@pytest.mark.parametrize("literal", ["nan", "inf", "-inf", "1e9999"])
def test_config_rejects_nonfinite_numbers(
    tmp_path: Path, name: str, literal: str, capsys
) -> None:
    path = tmp_path / "sift.toml"
    path.write_text(f"[sift]\n{name} = {literal}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="finite"):
        load_config(path)
    src = _source(tmp_path)
    assert main(["check", str(src), "--config", str(path)]) == 2
    assert "finite" in capsys.readouterr().err


def test_valid_numeric_config_still_accepted(tmp_path: Path) -> None:
    path = tmp_path / "sift.toml"
    path.write_text("[sift]\noutlier_z = 7.5\nnull_warn = 0.2\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.outlier_z == 7.5
    assert cfg.null_warn == 0.2


@pytest.mark.parametrize("delimiter", ['"', "\n", "\r", "\x00", "||"])
def test_bad_delimiters_exit_without_traceback(
    tmp_path: Path, delimiter: str, capsys
) -> None:
    src = _source(tmp_path)
    assert main(["check", str(src), "--delimiter", delimiter, "--no-config"]) == 2
    err = capsys.readouterr().err
    assert "delimiter" in err
    assert "Traceback" not in err


def test_bad_audit_path_does_not_write_output(tmp_path: Path, capsys) -> None:
    src = _source(tmp_path)
    dest = tmp_path / "fixed.csv"
    dest.write_bytes(b"unchanged\n")
    bad_log = tmp_path / "no-parent" / "audit.csv"
    assert main([
        "fix", str(src), "--output", str(dest), "--log", str(bad_log),
        "--no-config",
    ]) == 2
    assert dest.read_bytes() == b"unchanged\n"
    assert "sift-lint:" in capsys.readouterr().err


def test_directory_audit_path_does_not_write_output(tmp_path: Path) -> None:
    src = _source(tmp_path)
    dest = tmp_path / "fixed.csv"
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    assert main([
        "fix", str(src), "--output", str(dest), "--log", str(audit_dir),
        "--no-config",
    ]) == 2
    assert not dest.exists()


def test_valid_output_and_audit_still_work(tmp_path: Path) -> None:
    src = _source(tmp_path)
    dest = tmp_path / "fixed.csv"
    audit = tmp_path / "audit.csv"
    assert main([
        "fix", str(src), "--output", str(dest), "--log", str(audit),
        "--no-config",
    ]) == 0
    assert dest.exists() and audit.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable to Windows")
@pytest.mark.parametrize("writer", ["text", "csv", "log"])
def test_atomic_write_preserves_existing_mode(tmp_path: Path, writer: str) -> None:
    dest = tmp_path / "existing.csv"
    dest.write_text("previous\n", encoding="utf-8")
    dest.chmod(0o640)
    if writer == "text":
        atomic_write_text(dest, "new\n")
    elif writer == "csv":
        write_csv(dest, ["id"], [["1"]])
    else:
        write_log(dest, RepairPlan())
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640


def test_new_outputs_remain_private_on_posix(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX mode bits not available")
    dest = tmp_path / "new.csv"
    atomic_write_text(dest, "hello")
    assert stat.S_IMODE(dest.stat().st_mode) & 0o077 == 0
