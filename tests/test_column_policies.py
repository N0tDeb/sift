"""Column roles and validation: CLI, API, privacy and safe repair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift_lint import diff as public_diff
from sift_lint import lint
from sift_lint.checks import run_checks, sensitive_columns
from sift_lint.cli import main
from sift_lint.config import ColumnPolicy, Config, ConfigError, load_config
from sift_lint.drift import compare
from sift_lint.loading import load
from sift_lint.repair import plan_repairs


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _config(tmp_path: Path, text: str) -> Config:
    return load_config(_write(tmp_path / "custom.toml", text))


def test_roles_and_policy_parse(tmp_path: Path) -> None:
    config = _config(tmp_path, '''
[sift.columns."postal code"]
role = "identifier"
required = true

[sift.columns.amount]
role = "measure"
min_value = 0
max_value = 100

[sift.columns.status]
allowed_values = ["new", "done"]
''')
    assert config.policy("postal code") == ColumnPolicy(role="identifier", required=True)
    assert config.policy("amount").min_value == 0
    assert config.policy("amount").max_value == 100
    assert config.policy("status").allowed_values == ("new", "done")
    assert config.policy("other") == ColumnPolicy()


@pytest.mark.parametrize("option", [
    'role = "secret"',
    'role = 42',
    'required = "yes"',
    'allowed_values = [1, 2]',
    'allowed_values = []',
    'allowed_values = ["x", "x"]',
    'min_value = nan',
    'max_value = inf',
    'min_value = 9\nmax_value = 8',
    'role = "identifier"\nmin_value = 0',
    'unrecognized_option = true',
])
def test_invalid_policy_rejected(tmp_path: Path, option: str) -> None:
    with pytest.raises(ConfigError):
        _config(tmp_path, f"[sift.columns.id]\n{option}\n")


def test_explicit_required_allowed_and_numeric_bounds(tmp_path: Path) -> None:
    data = _write(tmp_path / "data.csv", "status,amount\nopen,10\ninvalid,110\n,wrong\n")
    config = _config(tmp_path, '''
[sift.columns.status]
required = true
allowed_values = ["open", "closed"]

[sift.columns.amount]
role = "measure"
min_value = 0
max_value = 100

[sift.columns.customer_id]
role = "identifier"
required = true
''')
    report = run_checks(load(data), config)
    codes = {finding.code for finding in report}
    assert {"required-column-missing", "required-value-missing", "value-not-allowed",
            "range-nonnumeric", "value-out-of-range"} <= codes
    assert next(f for f in report if f.code == "value-not-allowed").examples == ["invalid"]
    assert next(f for f in report if f.code == "value-out-of-range").detail["count"] == 1


def test_ambiguous_numbers_fail_closed(tmp_path: Path) -> None:
    data = _write(tmp_path / "data.csv", "amount\n1,200\n")
    data = _write(data, 'amount\n"1,200"\n"1,300"\n')
    config = Config(columns={"amount": ColumnPolicy(min_value=0, max_value=5000)})
    report = run_checks(load(data), config)
    assert "range-unverifiable" in {f.code for f in report}
    assert "value-out-of-range" not in {f.code for f in report}


def test_no_config_same_results_as_old_behavior(tmp_path: Path) -> None:
    data = _write(tmp_path / "data.csv", "id,status\n001,open\n002,bad\n")
    settings = Config(columns={"status": ColumnPolicy(allowed_values=("open",))})
    assert any(f.code == "value-not-allowed" for f in lint(data, settings))
    assert all(f.code != "value-not-allowed" for f in lint(data, Config()))


def test_identifier_role_prevents_repair_and_cleans_no_data(tmp_path: Path) -> None:
    path = _write(tmp_path / "data.csv", 'opaque,value\n" 001 "," $1,200.00 "\n')
    table = load(path)
    settings = Config(columns={"opaque": ColumnPolicy(role="identifier")})
    plan, rows = plan_repairs(table, settings)
    assert rows[0][0] == " 001 "
    assert any(r.rule == "sensitive-value-preserved" and r.column == "opaque"
               for r in plan.refusals)
    assert all(c.column != "opaque" for c in plan.changes)


def test_sensitive_role_prevents_repair_and_redacts_output(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "data.csv", 'opaque\n"  new-secret  "\nother\n')
    cfg = _write(tmp_path / "custom.toml", '[sift.columns.opaque]\nrole = "sensitive"\n')
    settings = load_config(cfg)
    table = load(path)
    assert "opaque" in sensitive_columns(table, settings)
    plan, rows = plan_repairs(table, settings)
    assert rows[0][0] == "  new-secret  "
    assert all(c.column != "opaque" for c in plan.changes)
    assert main(["check", str(path), "--config", str(cfg), "--format", "json",
                 "--fail-on", "none"]) == 0
    assert "new-secret" not in capsys.readouterr().out
    assert main(["profile", str(path), "--config", str(cfg)]) == 0
    assert "new-secret" not in capsys.readouterr().out
    assert main(["fix", str(path), "--config", str(cfg), "--dry-run"]) == 0
    assert "new-secret" not in capsys.readouterr().out


def test_cli_check_config_and_no_config_override(tmp_path: Path, capsys) -> None:
    data = _write(tmp_path / "orders.csv", "status\nwrong\n")
    config = _write(tmp_path / "custom.toml", '''
[sift.columns.status]
allowed_values = ["open", "closed"]
''')
    assert main(["check", str(data), "--config", str(config), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "value-not-allowed" for f in payload["findings"])
    assert main(["check", str(data), "--no-config", "--format", "json",
                 "--fail-on", "none"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert all(f["code"] != "value-not-allowed" for f in payload["findings"])


def test_diff_respects_explicit_column_roles(tmp_path: Path) -> None:
    old = _write(tmp_path / "before.csv", "tracking\n100\n200\n300\n")
    new = _write(tmp_path / "after.csv", "tracking\n1000\n2000\n3000\n")
    baseline, current = load(old), load(new)
    ordinary = compare(baseline, current, Config())
    assert any(f.code == "distribution-shift" for f in ordinary)
    as_id = Config(columns={"tracking": ColumnPolicy(role="identifier")})
    assert all(f.code != "distribution-shift" for f in compare(baseline, current, as_id))
    measured = Config(columns={"tracking": ColumnPolicy(role="measure")})
    assert any(f.code == "distribution-shift" for f in compare(baseline, current, measured))


def test_explicit_sensitive_drift_redacts_baseline(tmp_path: Path) -> None:
    old = _write(tmp_path / "old.csv", "opaque\nalpha\nbeta\ngamma\n")
    new = _write(tmp_path / "new.csv", "opaque\nx\ny\nz\n")
    settings = Config(columns={"opaque": ColumnPolicy(role="sensitive")})
    serialized = json.dumps([f.to_dict() for f in public_diff(old, new, settings)])
    assert "alpha" not in serialized
    assert "beta" not in serialized
    assert "gamma" not in serialized


def test_explicit_role_does_not_reinterpret_input_or_change_values(tmp_path: Path) -> None:
    path = _write(tmp_path / "data.csv", "id,status\n0001,OPEN\n0002,CLOSED\n")
    cfg = Config(columns={
        "id": ColumnPolicy(role="identifier"),
        "status": ColumnPolicy(role="category", allowed_values=("OPEN", "CLOSED")),
    })
    plan, rows = plan_repairs(load(path), cfg)
    assert [r[0] for r in rows] == ["0001", "0002"]
    assert {f.code for f in run_checks(load(path), cfg)}.isdisjoint({"value-not-allowed"})
    assert all(c.column != "id" for c in plan.changes)


def test_percent_bounds_do_not_assume_implicit_scale(tmp_path: Path) -> None:
    path = _write(tmp_path / "pct.csv", "share\n25%\n50%\n")
    cfg = Config(columns={"share": ColumnPolicy(min_value=0, max_value=1)})
    findings = run_checks(load(path), cfg)
    assert "range-unverifiable" in {f.code for f in findings}
    assert "value-out-of-range" not in {f.code for f in findings}


def test_configured_sensitive_enum_examples_are_redacted(tmp_path: Path, capsys) -> None:
    path = _write(tmp_path / "secrets.csv", "access_code\nsecret-opaque-value\n")
    cfg = _write(tmp_path / "custom.toml", '''
[sift.columns.access_code]
role = "sensitive"
allowed_values = ["expected"]
''')
    assert main(["check", str(path), "--config", str(cfg), "--format", "json"]) == 1
    response = capsys.readouterr().out
    assert "secret-opaque-value" not in response
    assert any(f["code"] == "value-not-allowed" for f in json.loads(response)["findings"])


def test_measure_role_opt_in_for_numeric_outlier_check(tmp_path: Path) -> None:
    numbers = [99] * 10 + [100] * 10 + [101] * 20 + [999]
    path = _write(tmp_path / "id.csv", "id\n" + "\n".join(map(str, numbers)) + "\n")
    generic = [f.code for f in run_checks(load(path), Config())]
    measured = [f.code for f in run_checks(
        load(path), Config(columns={"id": ColumnPolicy(role="measure")})
    )]
    assert "outliers" not in generic
    assert "outliers" in measured


def test_standalone_repair_formatters_keep_declared_secrets_private(tmp_path: Path) -> None:
    from sift_lint.repair import render_plan, write_log

    path = _write(tmp_path / "input.csv", 'opaque,amount\n"  top-secret  ",9\n')
    settings = Config(columns={"opaque": ColumnPolicy(role="sensitive")})
    table = load(path)
    plan, _ = plan_repairs(table, settings)
    assert "top-secret" not in render_plan(table, plan, "out.csv")
    log = tmp_path / "changes.csv"
    write_log(log, plan, header=table.header, source_path=path)
    assert "top-secret" not in log.read_text(encoding="utf-8")
    assert "opaque" in plan.private_columns


def test_policy_on_generated_duplicate_header_key_is_conservative(tmp_path: Path) -> None:
    path = _write(tmp_path / "repeated.csv", 'v,v\npublic,"  private-obscure  "\n')
    config = Config(columns={"v#1": ColumnPolicy(role="sensitive")})
    table = load(path)
    assert "v" in sensitive_columns(table, config)
    result = json.dumps([f.to_dict() for f in run_checks(table, config)])
    assert "private-obscure" not in result
    plan, rows = plan_repairs(table, config)
    assert rows[0][1] == "  private-obscure  "
    assert "v" in plan.private_columns


def test_identifier_role_targeting_duplicate_header_key(tmp_path: Path) -> None:
    path = _write(tmp_path / "repeated.csv", 'v,v\n"  first  ","  002  "\n')
    config = Config(columns={"v#1": ColumnPolicy(role="identifier")})
    table = load(path)
    _, rows = plan_repairs(table, config)
    assert rows[0][1] == "  002  "


def test_column_policy_public_python_export() -> None:
    from sift_lint import ColumnPolicy as ExportedPolicy

    assert ExportedPolicy is ColumnPolicy


def test_duplicate_header_policy_examples_redacted_by_both_identities(tmp_path: Path) -> None:
    path = _write(tmp_path / "repeated.csv", 'v,v\nordinary,secret-value-123\n')
    config = Config(columns={
        "v#1": ColumnPolicy(role="sensitive", allowed_values=("expected",)),
    })
    table = load(path)
    assert {"v", "v#1"} <= sensitive_columns(table, config)
    dumped = json.dumps([f.to_dict() for f in run_checks(table, config)])
    assert "secret-value-123" not in dumped
    assert "value-not-allowed" in dumped
