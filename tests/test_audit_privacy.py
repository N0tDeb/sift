"""Trace every deletion without exposing private values in diagnostics."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from sift_lint.cli import main
from sift_lint.config import Config
from sift_lint.loading import load
from sift_lint.repair import Change, DroppedRow, RepairPlan, plan_repairs, write_log


def read_audit(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_duplicate_deletions_logged_with_source_lines_and_fingerprint(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_bytes(
        b'id,note,region\n1,"hello\nworld", North \n1,"hello\nworld",North\n2,ok,West\n'
    )
    output = tmp_path / "out.csv"
    audit = tmp_path / "audit.csv"
    assert main([
        "fix", str(source), "--no-config", "--drop-duplicates", "--output",
        str(output), "--log", str(audit),
    ]) == 0

    with output.open(newline="", encoding="utf-8") as handle:
        remaining = list(csv.reader(handle))
    assert remaining == [["id", "note", "region"], ["1", "hello\nworld", "North"],
                         ["2", "ok", "West"]]

    records = read_audit(audit)
    assert list(records[0]) == ["line", "column", "rule", "confidence", "before", "after"]
    assert records[0]["rule"] == "source-sha256"
    assert records[0]["before"] == hashlib.sha256(source.read_bytes()).hexdigest()
    deletions = [entry for entry in records if entry["rule"] == "drop-duplicate-row"]
    assert len(deletions) == 1
    entry = deletions[0]
    assert entry["line"] == "4"
    assert entry["column"] == "[row]"
    assert entry["after"] == ""
    payload = json.loads(entry["before"])
    assert payload == {
        "values": ["1", "hello\nworld", "North"],
        "duplicate_of_line": 2,
        "matched_after_normalization": True,
    }
    assert any(e["rule"] == "trim-whitespace" for e in records)


def test_source_duplicate_not_misclassified_as_normalized(tmp_path: Path) -> None:
    src = tmp_path / "rows.csv"
    src.write_text("id,name\n1,Bob\n1,Bob\n", encoding="utf-8", newline="\n")
    log = tmp_path / "log.csv"
    assert main([
        "fix", str(src), "--no-config", "--drop-duplicates",
        "--output", str(tmp_path / "fixed.csv"), "--log", str(log),
    ]) == 0
    dropped = next(r for r in read_audit(log) if r["rule"] == "drop-duplicate-row")
    assert dropped["line"] == "3"
    assert json.loads(dropped["before"])["matched_after_normalization"] is False


def test_deleted_row_values_are_redacted_but_not_output(tmp_path: Path) -> None:
    secret = "SYNTHETIC-PRIVATE-STRING"
    src = tmp_path / "private.csv"
    src.write_text(
        f"id,password,region\n1, {secret} ,West\n1, {secret} ,West\n",
        encoding="utf-8", newline="\n",
    )
    audit = tmp_path / "log.csv"
    out = tmp_path / "out.csv"
    assert main([
        "fix", str(src), "--no-config", "--drop-duplicates", "--output", str(out),
        "--log", str(audit),
    ]) == 0
    assert secret in out.read_text(encoding="utf-8")
    assert secret not in audit.read_text(encoding="utf-8")
    deletions = [r for r in read_audit(audit) if r["rule"] == "drop-duplicate-row"]
    assert len(deletions) == 1
    assert json.loads(deletions[0]["before"])["values"][1] == "[redacted sensitive value]"


def test_sensitive_refusal_never_prints_example(tmp_path: Path, capsys) -> None:
    secret = "SYNTHETIC-NONNUMERIC-PASSWORD"
    src = tmp_path / "secret.csv"
    src.write_text(
        "password\n12\n13\n14\n15\n" + secret + "\n", encoding="utf-8", newline="\n"
    )
    assert main(["fix", str(src), "--no-config", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert secret not in printed
    assert "redacted" in printed.lower() or "withheld" in printed.lower()


def test_duplicate_finding_examples_do_not_expose_sensitive_values(
    tmp_path: Path, capsys
) -> None:
    secret = "SYNTHETIC-CREDENTIAL"
    src = tmp_path / "data.csv"
    src.write_text(
        f"id,password\n1,{secret}\n1,{secret}\n", encoding="utf-8", newline="\n"
    )
    assert main(["check", str(src), "--no-config", "--format", "json"]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    payload = json.loads(output)
    entry = next(f for f in payload["findings"] if f["code"] == "duplicate-rows")
    assert entry["examples"] == []


def test_profile_omits_sensitive_numeric_extrema(tmp_path: Path, capsys) -> None:
    src = tmp_path / "numeric.csv"
    src.write_text("password\n147203\n147204\n147205\n", encoding="utf-8", newline="\n")
    assert main(["profile", str(src)]) == 0
    output = capsys.readouterr().out
    assert "147203" not in output
    assert "147205" not in output
    assert "password" in output


def test_impact_redacts_sensitive_group_labels(tmp_path: Path, capsys) -> None:
    secret = "SYNTHETIC-HIDDEN-KEY"
    src = tmp_path / "groups.csv"
    src.write_text(
        f"id,password\n1, {secret} \n2,{secret}\n", encoding="utf-8", newline="\n"
    )
    assert main(["impact", str(src), "--no-config", "--group-by", "password"]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    # Opaque values are no longer automatically normalized; there are no
    # group merges to display, so the secret must simply stay undisclosed.
    assert "GROUP BY password" in output


def test_formula_values_in_log_use_reversible_nonformula_encoding(tmp_path: Path) -> None:
    plan = RepairPlan(changes=[
        Change(2, "=DANGEROUS", "+HYPERLINK(\"https://example.invalid\")", "ok",
               "trim-whitespace", "high"),
        Change(3, "key", "json:literal", "safe", "trim-whitespace", "high"),
    ])
    log = tmp_path / "log.csv"
    write_log(log, plan)
    entries = read_audit(log)
    for field in ("column", "before", "after"):
        assert not entries[0][field].startswith(("=", "+", "-", "@"))
    assert json.loads(entries[0]["before"][len("json:"):]) == (
        '+HYPERLINK("https://example.invalid")'
    )
    assert json.loads(entries[0]["column"][len("json:"):]) == "=DANGEROUS"
    assert json.loads(entries[1]["before"][len("json:"):]) == "json:literal"


def test_direct_log_without_header_fails_closed_for_sensitive_rows(tmp_path: Path) -> None:
    plan = RepairPlan(dropped_rows=[
        DroppedRow(3, 2, ("1", "SYNTHETIC-SECRET"), False),
    ])
    log = tmp_path / "direct.csv"
    write_log(log, plan, {"password"})
    output = log.read_text(encoding="utf-8")
    assert "SYNTHETIC-SECRET" not in output
    deletion = read_audit(log)[0]
    assert json.loads(deletion["before"])["values"][1] == "[redacted sensitive value]"


def test_cell_audit_disambiguates_duplicate_column_names(tmp_path: Path) -> None:
    src = tmp_path / "duplicate_headers.csv"
    src.write_text("code,code\n A , B \n", encoding="utf-8", newline="\n")
    table = load(src)
    plan, _ = plan_repairs(table, Config())
    log = tmp_path / "log.csv"
    write_log(log, plan, source_path=src, header=table.header)
    columns = [entry["column"] for entry in read_audit(log) if entry["rule"] == "trim-whitespace"]
    assert columns == ["code", "code#1"]


def test_init_dry_run_does_not_copy_secret_to_config_comment(
    tmp_path: Path, capsys
) -> None:
    secret = "SYNTHETIC-PRIVATE-PASSWORD"
    source = tmp_path / "baseline.csv"
    source.write_text(
        f"id,password\n1,{secret}\n2,{secret}\n3,{secret}\n",
        encoding="utf-8", newline="\n",
    )
    assert main(["init", str(source), "--dry-run", "--force"]) == 0
    printed = capsys.readouterr().out
    assert secret not in printed
    assert "password" in printed


def test_drift_with_empty_sensitive_column_redacts_baseline_examples(
    tmp_path: Path, capsys
) -> None:
    secret = "SYNTHETIC-BASELINE-PASSWORD"
    baseline = tmp_path / "baseline.csv"
    current = tmp_path / "current.csv"
    baseline.write_text(
        f"password\n{secret}\n{secret}\n{secret}\n",
        encoding="utf-8", newline="\n",
    )
    current.write_text("password\n", encoding="utf-8", newline="\n")
    main(["diff", str(baseline), str(current), "--no-config", "--format", "json"])
    assert secret not in capsys.readouterr().out


def test_duplicate_sensitive_examples_redacted_in_all_report_formats(
    tmp_path: Path, capsys
) -> None:
    secret = "SYNTHETIC-PRIVATE-AUDIT-KEY"
    source = tmp_path / "source.csv"
    source.write_text(
        f"id,password\n1,{secret}\n1,{secret}\n",
        encoding="utf-8", newline="\n",
    )
    for format_name in ("text", "json", "html"):
        main(["check", str(source), "--format", format_name, "--no-config"])
        assert secret not in capsys.readouterr().out
