"""Tests.

Two things are being defended here. The obvious one: each check fires on data
that is genuinely broken. The one that matters more: none of them fire on data
that is fine. A linter with false positives gets muted, and a muted linter
catches nothing — so `test_clean_file_is_quiet` is the most important test in
this file.
"""

from __future__ import annotations

import textwrap

import pytest

from sift.checks import run_checks
from sift.config import Config
from sift.findings import Severity
from sift.inference import parse_dates, parse_number, whitespace_problem
from sift.loading import load
from sift.profiling import CATEGORICAL, DATE, NUMERIC, TEXT, profile_column

CLEAN = """\
order_id,customer,order_date,region,quantity,order_total
A-1,Acme Corp,2024-01-05,North,3,120.50
A-2,Bluefin Ltd,2024-01-06,South,7,340.00
A-3,Corvus Media,2024-01-07,East,2,88.25
A-4,Delta Foods,2024-01-08,West,5,210.75
A-5,Acme Corp,2024-01-09,North,1,45.00
"""


def write(tmp_path, text, name="data.csv"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def codes(findings):
    return {f.code for f in findings}


def lint(tmp_path, text, config=None, name="data.csv"):
    table = load(write(tmp_path, text, name))
    return run_checks(table, config or Config())


def test_parse_number():
    assert parse_number("1,234.50").value == 1234.5
    assert parse_number("$1,200").value == 1200.0
    assert parse_number("(45.00)").value == -45.0
    assert parse_number("twelve") is None


def test_ambiguous_date_has_two_readings():
    readings = parse_dates("04/03/2024")
    orders = {order for _, order in readings}
    assert {"dmy", "mdy"} <= orders


def test_whitespace_problem():
    assert whitespace_problem(" North") == "padded with spaces"
    assert whitespace_problem("North") is None


def test_profile_kinds():
    assert profile_column("n", 0, ["1", "2", "3"]).kind == NUMERIC
    assert profile_column("d", 0, ["2024-01-01", "2024-02-01"]).kind == DATE
    assert profile_column("c", 0, ["a", "b", "a", "b", "a", "b"]).kind == CATEGORICAL
    assert profile_column("t", 0, [f"free text note number {i} of many" for i in range(60)]).kind == TEXT


def test_clean_file_is_quiet(tmp_path):
    findings = lint(tmp_path, CLEAN)
    assert [f for f in findings if f.severity is not Severity.INFO] == []


def test_mixed_types(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,amount
        1,10
        2,20
        3,30
        4,40
        5,not recorded
        """,
    )
    assert "mixed-types" in codes(findings)


def test_ambiguous_dates(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,when
        1,04/03/2024
        2,05/06/2024
        3,25/12/2024
        """,
    )
    assert "ambiguous-dates" in codes(findings)


def test_leading_zeros_and_number_as_text(tmp_path):
    findings = lint(
        tmp_path,
        """\
        zip,total
        02134,"$1,200.00"
        07094,"$980.00"
        02139,"$1,450.00"
        """,
    )
    assert {"leading-zeros", "number-as-text"} <= codes(findings)


def test_label_variants_and_whitespace(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,region
        1,North
        2,north
        3,"North "
        4,South
        """,
    )
    assert {"label-variants", "whitespace"} <= codes(findings)


def test_duplicate_rows_and_key_uniqueness(tmp_path):
    text = """\
        id,value
        1,a
        2,b
        2,b
        """
    findings = lint(tmp_path, text, Config(key=["id"]))
    assert {"duplicate-rows", "key-not-unique"} <= codes(findings)


def test_missing_values_severity_follows_config(tmp_path):
    text = """\
        id,note
        1,
        2,
        3,x
        4,
        """
    strict = lint(tmp_path, text, Config(null_error=0.5))
    relaxed = lint(tmp_path, text, Config(null_warn=0.9, null_error=0.99))
    assert any(f.code == "missing-values" and f.severity is Severity.ERROR for f in strict)
    assert "missing-values" not in codes(relaxed)


def test_ignore_silences_a_code(tmp_path):
    text = """\
        id,region
        1,North
        2,north
        """
    assert "label-variants" in codes(lint(tmp_path, text))
    assert "label-variants" not in codes(lint(tmp_path, text, Config(ignore=["label-variants"])))
