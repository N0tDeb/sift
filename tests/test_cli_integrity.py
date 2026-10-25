"""Safety regressions found during the post-1.0.0 audit.

Use deliberately small fixture files so destructive-path mistakes are caught
without risking real data.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift_lint.cli import main
from sift_lint.repair import RepairPlan, write_csv, write_log


def sample(tmp_path: Path, name: str = "source.csv") -> Path:
    src = tmp_path / name
    src.write_text("id,region\n1, North \n2,South\n3,West\n4,East\n", encoding="utf-8")
    return src


def test_fix_bounded_read_cannot_produce_truncated_file(tmp_path):
    src = sample(tmp_path)
    result = tmp_path / "result.csv"
    result.write_text("existing data must remain\n")
    assert main(["fix", str(src), "--max-rows", "1", "--output", str(result), "--no-config"]) == 2
    assert result.read_text() == "existing data must remain\n"


def test_bounded_fix_is_allowed_as_dry_run(tmp_path):
    src = sample(tmp_path)
    assert main(["fix", str(src), "--max-rows", "1", "--dry-run", "--no-config"]) == 0


def test_fix_rejects_output_log_collision(tmp_path):
    src = sample(tmp_path)
    same = tmp_path / "out.csv"
    same.write_text("old\n")
    assert main(["fix", str(src), "--output", str(same), "--log", str(same), "--no-config"]) == 2
    assert same.read_text() == "old\n"


def test_fix_rejects_output_log_symlink_collision(tmp_path):
    src = sample(tmp_path)
    target = tmp_path / "out.csv"
    target.write_text("old\n")
    alias = tmp_path / "alias.csv"
    try:
        alias.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert main(["fix", str(src), "--output", str(target), "--log", str(alias), "--no-config"]) == 2
    assert target.read_text() == "old\n"


def test_check_protects_reference_file_when_writing_report(tmp_path):
    src = sample(tmp_path)
    ref = tmp_path / "ref.csv"
    ref.write_text("id\n1\n2\n3\n4\n")
    original = ref.read_bytes()
    assert main([
        "check", str(src), "--references", f"{ref}:id=id", "--output", str(ref),
        "--format", "json", "--no-config",
    ]) == 2
    assert ref.read_bytes() == original


def test_multi_file_missing_input_is_operational_failure(tmp_path, capsys):
    src = sample(tmp_path)
    missing = tmp_path / "not-there.csv"
    assert main([
        "check", str(src), str(missing), "--format", "json",
        "--fail-on", "none", "--no-config",
    ]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] and len(payload["unreadable"]) == 1


def test_multi_file_text_output_is_written(tmp_path):
    src = sample(tmp_path)
    other = sample(tmp_path, "second.csv")
    output = tmp_path / "report.txt"
    assert main([
        "check", str(src), str(other), "--output", str(output),
        "--fail-on", "none", "--no-config",
    ]) == 0
    text = output.read_text(encoding="utf-8")
    assert "source.csv" in text and "second.csv" in text


def test_multi_file_html_is_real_html_and_escaped(tmp_path):
    src = sample(tmp_path)
    other = sample(tmp_path, "second.csv")
    output = tmp_path / "report.html"
    assert main([
        "check", str(src), str(other), "--format", "html", "--output",
        str(output), "--fail-on", "none", "--no-config",
    ]) == 0
    body = output.read_text(encoding="utf-8")
    assert "<!doctype html>" in body.lower()
    assert "source.csv" in body and "second.csv" in body


def test_multi_file_html_escapes_hostile_filename(tmp_path):
    import os

    src = sample(tmp_path, "<script>alert(1)<script>.csv") if os.name != "nt" else sample(tmp_path)
    other = sample(tmp_path, "second.csv")
    output = tmp_path / "report.html"
    assert main([
        "check", str(src), str(other), "--format", "html", "--output",
        str(output), "--fail-on", "none", "--no-config",
    ]) == 0
    body = output.read_text(encoding="utf-8")
    assert "<script>alert(1)<script>" not in body


def test_atomic_csv_write_preserves_existing_on_writer_failure(tmp_path, monkeypatch):
    dest = tmp_path / "fixed.csv"
    dest.write_text("old good file\n", encoding="utf-8")
    import sift_lint.repair as repair
    original = repair.csv.writer
    class Writer:
        def __init__(self, stream, **kwargs):
            self.inner = original(stream, **kwargs)
        def writerow(self, row):
            return self.inner.writerow(row)
        def writerows(self, rows):
            raise OSError("simulated interrupted write")
    monkeypatch.setattr(repair.csv, "writer", Writer)
    with pytest.raises(OSError, match="interrupted"):
        write_csv(dest, ["id"], [["1"]])
    assert dest.read_text() == "old good file\n"


def test_atomic_audit_write_preserves_existing_on_replace_failure(tmp_path, monkeypatch):
    dest = tmp_path / "audit.csv"
    dest.write_text("old log\n")
    import sift_lint.output as outmod
    def fail_replace(src, dst):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(outmod.os, "replace", fail_replace)
    with pytest.raises(OSError, match="rename failure"):
        write_log(dest, RepairPlan())
    assert dest.read_text() == "old log\n"
    assert not list(tmp_path.glob(".audit.csv.*.tmp"))


def test_invalid_delimiter_fails_cleanly(tmp_path, capsys):
    src = sample(tmp_path)
    assert main(["check", str(src), "--delimiter", "||", "--no-config"]) == 2
    assert "delimiter" in capsys.readouterr().err.lower()


def test_fix_cannot_overwrite_automatically_discovered_config_via_log(tmp_path):
    src = sample(tmp_path)
    config = tmp_path / "sift.toml"
    config.write_text('[sift]\n', encoding='utf-8')
    assert main([
        "fix", str(src), "--output", str(tmp_path / "fixed.csv"), "--log", str(config),
    ]) == 2
    assert config.read_text() == '[sift]\n'


def test_check_cannot_overwrite_automatically_discovered_config(tmp_path):
    src = sample(tmp_path)
    config = tmp_path / "sift.toml"
    config.write_text('[sift]\n', encoding='utf-8')
    assert main(["check", str(src), "--output", str(config), "--format", "json"]) == 2
    assert config.read_text() == '[sift]\n'


def test_diff_cannot_overwrite_explicit_config(tmp_path):
    original = sample(tmp_path)
    changed = sample(tmp_path, "current.csv")
    config = tmp_path / "settings.toml"
    config.write_text('[sift]\n', encoding='utf-8')
    assert main(["diff", str(original), str(changed), "--config", str(config),
                 "--output", str(config)]) == 2
    assert config.read_text() == '[sift]\n'


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '1e999999'])
def test_impact_fails_cleanly_on_nonfinite_raw_numbers(tmp_path, capsys, bad):
    src = tmp_path / 'nonfinite.csv'
    src.write_text('id,amount\n1,10\n2,'+bad+'\n3,20\n')
    assert main(['impact',str(src),'--sum','amount','--no-config']) == 2
    assert 'finite' in capsys.readouterr().err.lower()


def test_numeric_inference_rejects_overflow_instead_of_infinity():
    from sift_lint.inference import parse_number
    assert parse_number('9'*420) is None


def test_formatter_handles_non_finite_values_safely():
    from sift_lint.text import fmt_number
    assert fmt_number(float('inf')) == 'infinity'
    assert fmt_number(float('nan')) == 'NaN'


def test_multiline_csv_repair_log_uses_physical_source_line(tmp_path):
    from sift_lint.config import Config
    from sift_lint.loading import load
    from sift_lint.repair import plan_repairs
    src = tmp_path / 'multiline.csv'
    src.write_text('id,note\n1,"hello\nworld"\n2," West "\n', encoding='utf-8')
    plan, _ = plan_repairs(load(src), Config())
    changes = [c for c in plan.changes if c.before == ' West ']
    assert len(changes) == 1
    assert changes[0].line == 4  # the quoted first record occupies lines 2 and 3
