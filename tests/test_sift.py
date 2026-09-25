"""Tests.

Two things are being defended here. The obvious one: each check fires on data
that is genuinely broken. The one that matters more: none of them fire on data
that is fine. A linter with false positives gets muted, and a muted linter
catches nothing — so `test_clean_file_is_quiet` is the most important test in
this file.
"""

from __future__ import annotations

import json
import re
import textwrap
import time
from datetime import datetime
from pathlib import Path

import pytest

from sift.checks import run_checks
from sift.cli import main
from sift.config import Config, ConfigError, load_config
from sift.drift import compare
from sift.findings import Severity
from sift.impact import ImpactError, duplicate_impact, group_impact, sum_impact
from sift.inference import (
    DATE_FORMATS,
    is_null_token,
    number_evidence,
    parse_dates,
    parse_number,
    parse_number_any,
    whitespace_problem,
)
from sift.initialize import build as build_config
from sift.initialize import describe
from sift.loading import LoadError, load
from sift.profiling import (
    CATEGORICAL,
    DATE,
    NUMERIC,
    TEXT,
    infer_date_order,
    profile_column,
)
from sift.references import Reference, ReferenceError, check_reference
from sift.repair import HIGH, MEDIUM, plain_number, plan_repairs, write_csv
from sift.reporting import render_html
from sift.sources import discover, load_any

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


# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected,flag",
    [
        ("1234", 1234.0, None),
        ("1,234.50", 1234.5, "thousands"),
        ("$1,200", 1200.0, "currency"),
        ("(45.00)", -45.0, "parens"),
        ("12%", 12.0, "percent"),
        ("-3", -3.0, None),
    ],
)
def test_parse_number(raw, expected, flag):
    parsed = parse_number(raw)
    assert parsed is not None
    assert parsed.value == expected
    if flag:
        assert flag in parsed.flags


@pytest.mark.parametrize("raw", ["twelve", "", "1.2.3", "(45", "12-", "N/A"])
def test_parse_number_rejects(raw):
    assert parse_number(raw) is None


def test_ambiguous_date_has_two_readings():
    readings = parse_dates("04/03/2024")
    orders = {order for _, order in readings}
    assert {"dmy", "mdy"} <= orders

    unambiguous = parse_dates("25/03/2024")
    assert {order for _, order in unambiguous} == {"dmy"}


def test_whitespace_problem():
    assert whitespace_problem(" North") == "padded with spaces"
    assert whitespace_problem("New  York") == "contains repeated spaces"
    assert whitespace_problem("North") is None


# --- profiling -------------------------------------------------------------


def test_profile_kinds():
    assert profile_column("n", 0, ["1", "2", "3"]).kind == NUMERIC
    assert profile_column("d", 0, ["2024-01-01", "2024-02-01"]).kind == DATE
    assert profile_column("c", 0, ["a", "b", "a", "b"]).kind == CATEGORICAL
    assert profile_column("t", 0, [f"note {i}" for i in range(60)]).kind == TEXT


def test_nulls_are_not_counted_as_values():
    profile = profile_column("x", 0, ["1", "", "N/A", "3"])
    assert profile.n_null == 2
    assert profile.n_disguised_null == 1
    assert profile.kind == NUMERIC


def test_dominant_type_records_offenders():
    profile = profile_column("x", 0, ["1", "2", "3", "4", "5", "6", "7", "oops"])
    assert profile.kind == NUMERIC
    assert profile.offenders == [(7, "oops")]


# --- the point of the whole thing -----------------------------------------


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
    # No row settles the order: every value is valid read either way.
    findings = lint(
        tmp_path,
        """\
        id,when
        1,04/03/2024
        2,05/06/2024
        3,01/02/2024
        """,
    )
    assert "ambiguous-dates" in codes(findings)


def test_one_decisive_row_settles_the_whole_column(tmp_path):
    # "25/12/2024" can only be day-first, so the column is not ambiguous —
    # the other rows follow that reading. Reporting it as ambiguous anyway
    # was a false positive the graded inference removed.
    findings = lint(
        tmp_path,
        """\
        id,when
        1,04/03/2024
        2,05/06/2024
        3,25/12/2024
        """,
    )
    assert "ambiguous-dates" not in codes(findings)


def test_two_conflicting_date_formats_are_an_error(tmp_path):
    # One row can only be day-first, another only month-first. Every row is
    # individually valid, so nothing raises — and the dates are simply wrong.
    findings = lint(
        tmp_path,
        """\
        id,when
        1,25/12/2024
        2,12/25/2024
        3,04/03/2024
        """,
    )
    assert "conflicting-date-formats" in codes(findings)
    assert any(
        f.code == "conflicting-date-formats" and f.severity is Severity.ERROR
        for f in findings
    )


def test_iso_dates_are_never_ambiguous(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,when
        1,2024-03-04
        2,2024-06-05
        3,2024-12-25
        """,
    )
    assert "ambiguous-dates" not in codes(findings)


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


def test_ragged_rows(tmp_path):
    path = tmp_path / "ragged.csv"
    path.write_text("a,b,c\n1,2,3\n4,5,6,7\n", encoding="utf-8")
    findings = run_checks(load(path), Config())
    assert "ragged-rows" in codes(findings)


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


def test_ignore_columns_is_scoped(tmp_path):
    config = Config(ignore_columns={"label-variants": ["region"]})
    findings = lint(
        tmp_path,
        """\
        id,region,city
        1,North,Boston
        2,north,boston
        """,
        config,
    )
    columns = {f.column for f in findings if f.code == "label-variants"}
    assert columns == {"city"}


def test_empty_file_is_an_error_not_a_finding(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(LoadError):
        load(path)


# --- drift -----------------------------------------------------------------


def test_drift_detects_schema_and_distribution_changes(tmp_path):
    baseline = load(write(tmp_path, CLEAN, "baseline.csv"))
    current = load(
        write(
            tmp_path,
            """\
            order_id,customer,order_date,region,quantity
            A-6,Acme Corp,2024-02-05,Central,3
            A-7,Bluefin Ltd,2024-02-06,North,7
            """,
            "current.csv",
        )
    )
    found = codes(compare(baseline, current, Config()))
    assert "column-removed" in found  # order_total is gone
    assert "new-category" in found  # Central is new


def test_drift_is_quiet_on_an_identical_file(tmp_path):
    baseline = load(write(tmp_path, CLEAN, "a.csv"))
    current = load(write(tmp_path, CLEAN, "b.csv"))
    assert compare(baseline, current, Config()) == []


# --- config and cli --------------------------------------------------------


def test_config_round_trip(tmp_path):
    path = tmp_path / "sift.toml"
    path.write_text(
        textwrap.dedent(
            """\
            [sift]
            null_warn = 0.4
            key = ["order_id"]
            ignore = ["outliers"]

            [sift.ignore_columns]
            whitespace = ["notes"]
            """
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.null_warn == 0.4
    assert config.key == ["order_id"]
    assert config.silenced("outliers", None)
    assert config.silenced("whitespace", "notes")
    assert not config.silenced("whitespace", "region")


def test_cli_exit_codes(tmp_path, capsys):
    clean = write(tmp_path, CLEAN, "clean.csv")
    assert main(["check", str(clean), "--no-config"]) == 0

    dirty = write(
        tmp_path,
        """\
        id,amount
        1,10
        2,20
        3,30
        4,40
        5,oops
        """,
        "dirty.csv",
    )
    assert main(["check", str(dirty), "--no-config"]) == 1
    assert main(["check", str(dirty), "--no-config", "--fail-on", "none"]) == 0
    assert main(["check", str(tmp_path / "nope.csv"), "--no-config"]) == 2


def test_cli_json_is_parseable(tmp_path, capsys):
    path = write(tmp_path, CLEAN, "clean.csv")
    main(["check", str(path), "--no-config", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 5
    assert payload["summary"]["error"] == 0


def test_cli_html_report_is_written(tmp_path):
    path = write(tmp_path, CLEAN, "clean.csv")
    output = tmp_path / "report.html"
    main(["check", str(path), "--no-config", "--format", "html", "--output", str(output)])
    html = output.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    assert "order_total" in html


# --- inference confidence --------------------------------------------------


def _date_reading(values):
    return infer_date_order(profile_column("when", 0, values))


def test_date_order_is_certain_when_some_rows_prove_it():
    reading = _date_reading(["25/12/2024", "04/03/2024", "01/06/2024"])
    assert reading.order == "dmy"
    assert reading.label == "certain"
    assert reading.usable


def test_date_order_is_undecidable_when_nothing_proves_it():
    reading = _date_reading(["04/03/2024", "05/06/2024", "01/02/2024"])
    assert reading.order is None
    assert reading.label == "undecidable"
    assert not reading.usable


def test_date_order_is_conflicting_when_both_are_proven():
    reading = _date_reading(["25/12/2024", "12/25/2024"])
    assert reading.label == "conflicting"
    assert not reading.usable


def test_iso_dates_need_no_decision():
    reading = _date_reading(["2024-01-05", "2024-02-06"])
    assert reading.order == "ymd"
    assert reading.label == "certain"


# --- repair ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("$1,200", "1200"), ("(45.00)", "-45.00"), ("1,234.50", "1234.50"), ("980", None)],
)
def test_plain_number_edits_text_not_floats(raw, expected):
    assert plain_number(raw) == expected


def _fix(tmp_path, text, confidence=HIGH, **kwargs):
    table = load(write(tmp_path, text))
    return plan_repairs(table, Config(), confidence, **kwargs)


def test_high_confidence_fixes_only_the_mechanical_things(tmp_path):
    plan, rows = _fix(
        tmp_path,
        """\
        id,region,total
        1,"North ","$1,200"
        2,north,"$980"
        3,N/A,"$1,050"
        """,
    )
    rules = {change.rule for change in plan.changes}
    assert rules == {"trim-whitespace", "blank-null", "plain-number"}
    # 'north' is left alone: choosing a canonical spelling is a judgement call.
    assert rows[1][1] == "north"
    assert rows[0][2] == "1200"
    assert rows[2][1] == ""


def test_medium_confidence_folds_labels_toward_the_well_formed_spelling(tmp_path):
    plan, rows = _fix(
        tmp_path,
        """\
        id,region
        1,north
        2,north
        3,North
        """,
        MEDIUM,
    )
    # Frequency says 'north', capitalisation says 'North'. Casing is
    # presentation, so the well-formed spelling wins.
    assert [row[1] for row in rows] == ["North", "North", "North"]
    assert all(change.rule == "canonical-label" for change in plan.changes)


def test_confident_dates_become_iso(tmp_path):
    _, rows = _fix(
        tmp_path,
        """\
        id,when
        1,25/12/2024
        2,04/03/2024
        """,
    )
    assert [row[1] for row in rows] == ["2024-12-25", "2024-03-04"]


def test_undecidable_dates_are_refused_not_guessed(tmp_path):
    plan, rows = _fix(
        tmp_path,
        """\
        id,when
        1,04/03/2024
        2,05/06/2024
        """,
        MEDIUM,
    )
    assert [row[1] for row in rows] == ["04/03/2024", "05/06/2024"]
    assert any(refusal.rule == "iso-date" for refusal in plan.refusals)


def test_unrecoverable_damage_is_refused_with_a_reason(tmp_path):
    plan, _ = _fix(
        tmp_path,
        """\
        id,name,amount
        1,CafÃ© Rouge,10
        2,Bistro,20
        3,Diner,not sure
        """,
        MEDIUM,
    )
    refused = {refusal.rule for refusal in plan.refusals}
    assert {"mojibake", "mixed-types"} <= refused
    assert all(refusal.reason for refusal in plan.refusals)


def test_every_change_is_in_the_audit_log(tmp_path):
    text = """\
        id,region,total
        1,"North ","$1,200"
        2,N/A,"$980"
        """
    table = load(write(tmp_path, text))
    plan, rows = plan_repairs(table, Config(), MEDIUM)

    # Replaying the log against the original must reproduce the output exactly,
    # which is the only thing that makes the log worth having.
    replayed = [list(column.values) for column in table.columns]
    index = {column.name: i for i, column in enumerate(table.columns)}
    for change in plan.changes:
        replayed[index[change.column]][change.line - 2] = change.after
    assert [list(r) for r in zip(*replayed, strict=True)] == rows


def test_repair_refuses_formula_injection_column_without_rewriting_it(tmp_path):
    plan, rows = _fix(
        tmp_path,
        """\
        id,note,region
        1," =1+1 "," North "
        2,ok,South
        3,fine,West
        """,
    )

    assert [row[1] for row in rows] == [" =1+1 ", "ok", "fine"]
    assert rows[0][2] == "North"  # unrelated safe repairs still happen
    assert not any(change.column == "note" for change in plan.changes)
    assert any(
        refusal.column == "note" and refusal.rule == "formula-injection"
        for refusal in plan.refusals
    )


def test_cli_fix_refuses_to_write_unresolved_formula_content(tmp_path, capsys):
    path = write(
        tmp_path,
        """\
        id,note,region
        1," =1+1 "," North "
        2,ok,South
        3,fine,West
        """,
        "formula.csv",
    )
    output = tmp_path / "fixed.csv"

    assert main(["fix", str(path), "--output", str(output), "--no-config"]) == 2
    captured = capsys.readouterr()
    assert "formula-injection" in captured.out
    assert "refusing to write" in captured.err
    assert not output.exists()


def test_cli_fix_force_preserves_formula_column_and_repairs_other_columns(tmp_path):
    path = write(
        tmp_path,
        """\
        id,note,region
        1," =1+1 "," North "
        2,ok,South
        3,fine,West
        """,
        "formula.csv",
    )
    output = tmp_path / "fixed.csv"

    assert main(
        ["fix", str(path), "--output", str(output), "--no-config", "--force"]
    ) == 0
    repaired = load(output)
    assert repaired.by_key("note").values[0] == " =1+1 "
    assert repaired.by_key("region").values[0] == "North"


def test_fixing_twice_changes_nothing_the_second_time(tmp_path):
    text = """\
        id,region,total,when
        1,"North ","$1,200",25/12/2024
        2,north,"$980",04/03/2024
        3,N/A,"$1,050",01/06/2024
        """
    table = load(write(tmp_path, text))
    _, rows = plan_repairs(table, Config(), MEDIUM)
    once = tmp_path / "once.csv"
    write_csv(once, table.header, rows)

    plan_again, rows_again = plan_repairs(load(once), Config(), MEDIUM)
    assert plan_again.changes == []
    assert rows_again == rows


def test_cli_fix_refuses_a_file_that_did_not_parse(tmp_path, capsys):
    path = tmp_path / "ragged.csv"
    path.write_text("a,b\n1,2\n3,4,5\n", encoding="utf-8")
    assert main(["fix", str(path), "--output", str(tmp_path / "out.csv"), "--no-config"]) == 2
    assert not (tmp_path / "out.csv").exists()
    assert main(
        ["fix", str(path), "--output", str(tmp_path / "out.csv"), "--force", "--no-config"]
    ) == 0


def test_cli_fix_needs_a_destination(tmp_path):
    path = write(tmp_path, CLEAN, "clean.csv")
    assert main(["fix", str(path), "--no-config"]) == 2
    assert main(["fix", str(path), "--no-config", "--dry-run"]) == 0


# --- blast radius ----------------------------------------------------------

MONEY = """\
id,region,total
1,North,"$1,000.00"
2,north,"$500.00"
3,"North ","$250.00"
4,South,"$250.00"
5,South,-999
"""


def test_sum_impact_shows_what_a_loader_actually_gets(tmp_path):
    table = load(write(tmp_path, MONEY))
    impact = sum_impact(table, Config(), "total")

    # float() cannot read "$1,000.00", so a typed loader keeps only the -999.
    assert not impact.loads_as_number
    assert impact.coerced_total == -999
    assert impact.coerced_rows == 1
    # A lone -999 is left alone: one occurrence is not evidence of a sentinel,
    # so the repaired total still carries it.
    assert impact.repaired_total == 1001.0
    assert impact.repaired_rows == 5


def test_repeated_sentinels_are_removed_from_the_total(tmp_path):
    table = load(
        write(
            tmp_path,
            """\
            id,total
            1,100
            2,200
            3,-999
            4,-999
            """,
        )
    )
    impact = sum_impact(table, Config(), "total")
    assert impact.coerced_total == pytest.approx(-1698.0)
    assert impact.repaired_total == pytest.approx(300.0)


def test_causes_add_up_to_the_difference(tmp_path):
    table = load(write(tmp_path, MONEY))
    impact = sum_impact(table, Config(), "total")
    assert sum(cause.amount for cause in impact.causes) == pytest.approx(impact.difference)


def test_sum_impact_is_quiet_on_a_clean_column(tmp_path):
    table = load(write(tmp_path, CLEAN))
    impact = sum_impact(table, Config(), "order_total")
    assert impact.loads_as_number
    assert impact.difference == pytest.approx(0.0)
    assert impact.causes == []


def test_group_impact_counts_the_groups_that_should_not_exist(tmp_path):
    table = load(write(tmp_path, MONEY))
    impact = group_impact(table, Config(), "region", "total")
    assert impact.groups_now == 4  # North, north, "North ", South
    assert impact.groups_after == 2
    canonical, sources, amount = impact.merges[0]
    assert canonical == "North"
    assert sources == ["North ", "north"]
    assert amount == pytest.approx(750.0)


def test_duplicate_impact_measures_the_inflation(tmp_path):
    table = load(
        write(
            tmp_path,
            """\
            id,total
            1,100
            2,250
            2,250
            """,
        )
    )
    impact = duplicate_impact(table, Config(), "total")
    assert impact.extra_rows == 1
    assert impact.inflated_amount == pytest.approx(250.0)


def test_impact_on_an_unknown_column_is_an_error(tmp_path):
    table = load(write(tmp_path, MONEY))
    with pytest.raises(ImpactError):
        sum_impact(table, Config(), "nope")


def test_cli_impact_needs_something_to_measure(tmp_path):
    path = write(tmp_path, MONEY)
    assert main(["impact", str(path), "--no-config"]) == 2
    assert main(["impact", str(path), "--no-config", "--sum", "total"]) == 0
    assert main(["impact", str(path), "--no-config", "--sum", "nope"]) == 2


# --- regressions found by running against real public datasets -------------


def test_long_labels_that_differ_by_a_word_are_not_typos(tmp_path):
    # Chipotle's menu: these share 35 characters and score 0.93 on a
    # similarity ratio, but they are two products, not a misspelling.
    findings = lint(
        tmp_path,
        """\
        id,item
        1,Chips and Tomatillo Green Chili Salsa
        2,Chips and Tomatillo Red Chili Salsa
        3,Chicken Bowl
        """,
    )
    assert "near-duplicate-labels" not in codes(findings)


def test_short_transpositions_are_still_typos(tmp_path):
    # "Acme Crop" needs to look like a typo, not a second, evenly-used
    # spelling — so it appears once against many "Acme Corp"s, the pattern
    # that actually distinguishes a typo from a legitimate category.
    rows = "\n".join(f"{i},Acme Corp" for i in range(1, 10))
    findings = lint(
        tmp_path,
        f"""\
        id,customer
        {rows}
        10,Acme Crop
        11,Bluefin Ltd
        """,
    )
    assert "near-duplicate-labels" in codes(findings)


def test_evenly_used_categories_are_not_typos_of_each_other(tmp_path):
    # Real data caught this: diamond clarity grades ('vvs1'/'vvs2'), a sex
    # column ('male'/'female'), and academic majors ('biology'/'ecology')
    # are all one or two characters apart and used about equally often.
    # None of them are typos.
    rows = "\n".join(
        f"{i},{'vvs1' if i % 2 else 'vvs2'}" for i in range(1, 21)
    )
    findings = lint(tmp_path, f"id,clarity\n{rows}\n")
    assert "near-duplicate-labels" not in codes(findings)


def test_no_statistics_over_a_column_with_no_consistent_type(tmp_path):
    # Titanic's Ticket column: 74% numeric, 26% strings like "STON/O2. 3101282".
    findings = lint(
        tmp_path,
        """\
        id,ticket
        1,3101295
        2,3101296
        3,3101298
        4,17599
        5,21171
        6,113803
        7,373450
        8,STON/O2. 3101282
        9,A/5 21171
        """,
    )
    assert "mixed-types" in codes(findings)
    assert "outliers" not in codes(findings)


def test_a_skewed_column_is_not_an_outlier_report(tmp_path):
    # GDP: exponentially distributed, so a quarter of the column sits far from
    # the median. That is a property of the data, not a defect in it.
    values = [str(10 ** (i % 12)) for i in range(200)]
    rows = "\n".join(f"{i},{v}" for i, v in enumerate(values))
    findings = lint(tmp_path, f"id,value\n{rows}\n")
    assert "outliers" not in codes(findings)


def test_a_genuine_outlier_still_reports(tmp_path):
    values = [str(100 + i % 5) for i in range(60)] + ["999999"]
    rows = "\n".join(f"{i},{v}" for i, v in enumerate(values))
    findings = lint(tmp_path, f"id,measurement\n{rows}\n")
    assert "outliers" in codes(findings)


def test_sequential_ids_are_not_flagged_as_typos_of_each_other(tmp_path):
    # A duplicated order_id ("A-1033" twice) used to fire near-duplicate-
    # labels against its numeric neighbour "A-1032" — IDs are near each
    # other by construction, not by accident.
    rows = "\n".join(f"A-{1000 + i},x" for i in range(1, 30))
    findings = lint(tmp_path, f"order_id,note\n{rows}\nA-1033,x\n")
    assert "near-duplicate-labels" not in codes(findings)


def test_jan_1_2000_without_a_comma_still_parses_as_a_date(tmp_path):
    # stocks.csv writes dates as "Jan 1 2000", no comma. Missing that format
    # meant the column fell through to TEXT and fired near-duplicate-labels
    # on "apr 1 2000" / "apr 1 2001".
    reading = parse_dates("Jan 1 2000")
    assert reading and reading[0][0].year == 2000


def test_large_numbers_are_not_dates(tmp_path):
    # Population figures: 11,620,823 "parses" as the year 1162, which turned
    # three real datasets into false mixed-type errors.
    findings = lint(
        tmp_path,
        """\
        country,pop
        A,11620823
        B,13491107
        C,14871208
        D,9876543
        """,
    )
    assert "mixed-types" not in codes(findings)


def test_compact_dates_resolve_to_dates_when_the_column_says_so(tmp_path):
    # Mixed with an unambiguous ISO date, the 8-digit values are dates.
    profile = profile_column("dt", 0, ["20240115", "2024-01-16", "20240117"])
    assert profile.kind == DATE
    assert profile.unresolved_compact == 0


def test_an_all_compact_column_is_reported_as_undecidable(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,dt
        1,20240115
        2,20240116
        3,20240117
        """,
    )
    assert "compact-date-or-number" in codes(findings)
    assert "mixed-types" not in codes(findings)


def test_nan_the_name_is_not_a_missing_value():
    # congress-terms.csv has a member whose first name is Nan.
    assert not is_null_token("Nan")
    assert is_null_token("nan")
    assert is_null_token("NaN")


def test_a_small_count_never_renders_as_zero_percent(tmp_path):
    rows = "\n".join(f"{i},{i}" for i in range(2000))
    findings = lint(tmp_path, f"id,amount\n{rows}\n1,oops\n")
    message = next(f.message for f in findings if f.code == "mixed-types")
    assert "0.0%" not in message


def test_date_shape_prefilter_agrees_with_strptime():
    # The regex prefilter is an optimisation, so it must never change an
    # answer — only reach it faster.
    samples = [
        "2024-01-05", "25/12/2024", "04/03/2024", "Jan 1 2000", "Jan 1, 2000",
        "01 Jan 2024", "20240115", "11620823", "1610612737", "ATL", "0.5",
        "", "not a date", "2024-01-05T10:30:00", "12/25/24", "$1,200",
    ]
    for text in samples:
        expected = []
        for fmt, order in DATE_FORMATS:
            try:
                parsed = datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
            if fmt == "%Y%m%d" and not (1900 <= parsed.year <= 2100):
                continue
            expected.append((parsed, order))
        assert parse_dates(text) == expected, text


def test_parsing_a_large_file_stays_fast():
    # A 126,000-row file once took four minutes, because every value was
    # tried against all 18 date formats and strptime rebuilds its regex on
    # each call. This is a guard against that regressing.
    values = [f"team-{i % 30}" for i in range(20000)]
    start = time.perf_counter()
    for value in values:
        parse_dates(value)
    assert time.perf_counter() - start < 1.0


# --- other formats ---------------------------------------------------------


def _write_xlsx(path, rows, sheets=("Orders",)):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheets[0]
    for row in rows:
        ws.append(row)
    for extra in sheets[1:]:
        wb.create_sheet(extra)
    wb.save(path)
    return path


def test_excel_preamble_rows_are_skipped_and_reported(tmp_path):
    path = _write_xlsx(
        tmp_path / "book.xlsx",
        [
            ["Quarterly Export"],
            [],
            ["id", "region", "total"],
            ["A-1", "North", 1200.0],
            ["A-2", "South", 980.0],
        ],
    )
    table = load_any(path)
    assert table.header == ["id", "region", "total"]
    assert table.n_rows == 2
    assert "preamble-rows" in {f.code for f in table.file_findings}


def test_excel_whole_numbers_do_not_gain_decimals(tmp_path):
    # str(1200.0) is "1200.0", which would make every whole number in a
    # spreadsheet look like a measurement to one decimal place.
    path = _write_xlsx(
        tmp_path / "n.xlsx", [["id", "total"], ["A-1", 1200.0], ["A-2", 980.5]]
    )
    table = load_any(path)
    assert table.by_key("total").values == ["1200", "980.5"]


def test_excel_partial_dates_are_an_error(tmp_path):
    # The classic spreadsheet failure: some cells are real dates, the rest are
    # text that renders identically.
    path = _write_xlsx(
        tmp_path / "d.xlsx",
        [
            ["id", "when"],
            ["A-1", datetime(2024, 1, 5)],
            ["A-2", datetime(2024, 1, 6)],
            ["A-3", "2024-01-07"],
        ],
    )
    table = load_any(path)
    assert "partial-excel-dates" in {f.code for f in table.file_findings}


def test_excel_formula_errors_are_reported(tmp_path):
    path = _write_xlsx(
        tmp_path / "f.xlsx",
        [["id", "total"], ["A-1", 100.0], ["A-2", "#REF!"], ["A-3", 300.0]],
    )
    table = load_any(path)
    assert "formula-error" in {f.code for f in table.file_findings}


def test_unread_sheets_are_mentioned(tmp_path):
    path = _write_xlsx(
        tmp_path / "s.xlsx",
        [["id", "total"], ["A-1", 100.0]],
        sheets=("Orders", "Notes"),
    )
    table = load_any(path)
    assert "unread-sheets" in {f.code for f in table.file_findings}


def test_parquet_reports_a_schema_that_stopped_matching(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    path = tmp_path / "t.parquet"
    pq.write_table(
        pa.table({"id": ["A-1", "A-2"], "amount": ["1200", "980"]}), path
    )
    table = load_any(path)
    assert "loose-schema" in {f.code for f in table.file_findings}


def test_old_xls_gets_a_useful_error(tmp_path):
    path = tmp_path / "legacy.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(LoadError, match="xlsx"):
        load_any(path)


def test_discover_finds_supported_files_in_a_directory(tmp_path):
    (tmp_path / "a.csv").write_text(CLEAN, encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.csv").write_text(CLEAN, encoding="utf-8")
    (tmp_path / "notes.md").write_text("ignore me", encoding="utf-8")
    found = discover([tmp_path])
    assert [p.name for p in found] == ["a.csv", "b.csv"]


def test_cli_checks_a_whole_directory(tmp_path, capsys):
    (tmp_path / "a.csv").write_text(CLEAN, encoding="utf-8")
    (tmp_path / "b.csv").write_text(
        "id,amount\n1,10\n2,20\n3,30\n4,40\n5,oops\n", encoding="utf-8"
    )
    assert main(["check", str(tmp_path), "--no-config"]) == 1
    out = capsys.readouterr().out
    assert "a.csv" in out and "b.csv" in out


def test_cli_directory_json_lists_every_file(tmp_path, capsys):
    (tmp_path / "a.csv").write_text(CLEAN, encoding="utf-8")
    (tmp_path / "b.csv").write_text(CLEAN, encoding="utf-8")
    main(["check", str(tmp_path), "--no-config", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["files"]) == 2


# --- decimal conventions ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234", "neutral"),        # identical either way
        ("1.234,56", "eu"),         # only European can read it
        ("1,234.56", "en"),         # only English can read it
        ("1,23", "eu"),             # comma with two digits is a decimal
        ("1.234.567", "eu"),        # repeated dots are grouping
        ("1,200", "group-en"),      # weaker: looks like comma grouping
        ("1.234", "group-eu"),      # weaker: looks like dot grouping
        ("abc", None),
    ],
)
def test_number_evidence(raw, expected):
    assert number_evidence(raw) == expected


def test_european_numbers_are_read_correctly(tmp_path):
    # 12.500,00 is twelve and a half thousand. Read the English way it is
    # 12.5 — a thousandfold error with no exception raised.
    profile = profile_column("betrag", 0, ["1.234,56", "980,00", "12.500,00"])
    assert profile.kind == NUMERIC
    assert profile.number_convention == "eu"
    assert profile.numbers == [1234.56, 980.0, 12500.0]


def test_european_numbers_are_reported(tmp_path):
    path = tmp_path / "de.csv"
    path.write_text(
        "id;betrag\n1;1.234,56\n2;980,00\n3;12.500,00\n", encoding="utf-8"
    )
    findings = run_checks(load(path), Config())
    assert "european-numbers" in codes(findings)


def test_english_currency_is_not_mistaken_for_european(tmp_path):
    # "$1,200" is 1.2 under European convention, so it is ambiguous in
    # isolation — but three digits after a separator is what grouping looks
    # like, and this must not become a false error on ordinary money.
    findings = lint(
        tmp_path,
        """\
        id,total
        1,"$1,200"
        2,"$980"
        3,"$1,050"
        """,
    )
    assert "ambiguous-decimal-separator" not in codes(findings)
    assert "european-numbers" not in codes(findings)


def test_a_column_mixing_both_conventions_is_an_error(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,amount
        1,"1.234,56"
        2,"1,234.56"
        3,"980,00"
        """,
    )
    assert "conflicting-number-formats" in codes(findings)


def test_european_numbers_are_normalised_by_fix(tmp_path):
    path = tmp_path / "de.csv"
    path.write_text(
        "id;betrag\n1;1.234,56\n2;980,00\n3;12.500,00\n", encoding="utf-8"
    )
    plan, rows = plan_repairs(load(path), Config(), HIGH)
    assert [row[1] for row in rows] == ["1234.56", "980.00", "12500.00"]
    assert all(c.rule == "plain-number" for c in plan.changes)


def test_plain_number_respects_the_convention():
    assert plain_number("1.234,56", "eu") == "1234.56"
    assert plain_number("1,234.56", "en") == "1234.56"


# --- cross-file references -------------------------------------------------


def _pair(tmp_path):
    (tmp_path / "customers.csv").write_text(
        "id,name\nC-1,Acme Corp\nC-2,Bluefin Ltd\nC-3,Corvus Media\n", encoding="utf-8"
    )
    return tmp_path / "customers.csv"


def test_reference_spec_parsing():
    ref = Reference.parse("customers.csv:customer_id=id")
    assert ref.local == "customer_id"
    assert ref.foreign == "id"
    assert ref.path.name == "customers.csv"


@pytest.mark.parametrize("spec", ["customers.csv", "customers.csv:", "customers.csv:x"])
def test_malformed_reference_is_rejected(spec):
    with pytest.raises(ReferenceError):
        Reference.parse(spec)


def test_orphaned_references_are_found(tmp_path):
    _pair(tmp_path)
    orders = write(
        tmp_path,
        """\
        order_id,customer_id
        O-1,C-1
        O-2,C-9
        """,
        "orders.csv",
    )
    ref = Reference.parse(f"{tmp_path / 'customers.csv'}:customer_id=id")
    findings = check_reference(load(orders), ref, Config(), load)
    assert "orphaned-reference" in codes(findings)


def test_a_valid_relationship_is_quiet(tmp_path):
    _pair(tmp_path)
    orders = write(
        tmp_path,
        """\
        order_id,customer_id
        O-1,C-1
        O-2,C-2
        """,
        "orders.csv",
    )
    ref = Reference.parse(f"{tmp_path / 'customers.csv'}:customer_id=id")
    assert check_reference(load(orders), ref, Config(), load) == []


def test_a_dirty_key_is_distinguished_from_a_missing_row(tmp_path):
    # "c-1 " is not a missing customer, it is a formatting problem — and the
    # fix is completely different, so the two must not look the same.
    _pair(tmp_path)
    orders = write(
        tmp_path,
        """\
        order_id,customer_id
        O-1,"c-1 "
        O-2,C-2
        """,
        "orders.csv",
    )
    ref = Reference.parse(f"{tmp_path / 'customers.csv'}:customer_id=id")
    found = codes(check_reference(load(orders), ref, Config(), load))
    assert "reference-matches-after-cleaning" in found


def test_a_repeated_foreign_key_is_an_error(tmp_path):
    (tmp_path / "customers.csv").write_text(
        "id,name\nC-1,Acme\nC-1,Acme Again\n", encoding="utf-8"
    )
    orders = write(tmp_path, "order_id,customer_id\nO-1,C-1\n", "orders.csv")
    ref = Reference.parse(f"{tmp_path / 'customers.csv'}:customer_id=id")
    assert "reference-not-unique" in codes(check_reference(load(orders), ref, Config(), load))


def test_unknown_columns_in_a_reference_are_reported(tmp_path):
    _pair(tmp_path)
    orders = write(tmp_path, "order_id,customer_id\nO-1,C-1\n", "orders.csv")
    bad = Reference.parse(f"{tmp_path / 'customers.csv'}:nope=id")
    with pytest.raises(ReferenceError):
        check_reference(load(orders), bad, Config(), load)


# --- init ------------------------------------------------------------------


def test_init_produces_a_config_that_silences_the_baseline(tmp_path):
    # The property that matters: what init writes must make check quiet about
    # the very file it learned from.
    path = write(
        tmp_path,
        """\
        order_id,zip,status
        A-1,02134,complete
        A-2,07094,complete
        A-3,10001,complete
        """,
        "base.csv",
    )
    table = load(path)
    findings = run_checks(table, Config())
    assert findings  # leading-zeros and constant-column fire here

    (tmp_path / "sift.toml").write_text(build_config(table, findings), encoding="utf-8")
    learned = load_config(tmp_path / "sift.toml")
    assert run_checks(load(path), learned) == []


def test_init_refuses_to_learn_from_a_damaged_file(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b\n1,2\n3,4,5\n", encoding="utf-8")  # ragged
    _, refusable = describe(run_checks(load(path), Config()))
    assert any(f.code == "ragged-rows" for f in refusable)


def test_init_does_not_accept_damage_as_normal(tmp_path):
    path = write(
        tmp_path,
        """\
        id,amount
        1,10
        2,20
        3,30
        4,40
        5,oops
        """,
        "base.csv",
    )
    table = load(path)
    body = build_config(table, run_checks(table, Config()))
    # A real defect must never be written into the definition of normal.
    assert "mixed-types" not in body


def test_init_records_the_key_it_found(tmp_path):
    path = write(tmp_path, CLEAN, "base.csv")
    table = load(path)
    body = build_config(table, run_checks(table, Config()))
    assert 'key = ["order_id"]' in body


def test_cli_init_writes_a_file(tmp_path):
    path = write(tmp_path, CLEAN, "base.csv")
    out = tmp_path / "sift.toml"
    assert main(["init", str(path), "--output", str(out)]) == 0
    assert out.exists()
    # Refuses to clobber without --force.
    assert main(["init", str(path), "--output", str(out)]) == 2
    assert main(["init", str(path), "--output", str(out), "--force"]) == 0


# --- documentation cannot drift -------------------------------------------


def _emitted_codes() -> set[str]:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path(__file__).resolve().parent.parent / "sift").glob("*.py")
    )
    return set(re.findall(r'Finding\(\s*\n?\s*"([a-z0-9-]+)"', source))


def test_every_finding_code_is_documented():
    # The codes are a public interface — they appear in JSON output and are
    # what users name in sift.toml to silence a check. A code with no entry
    # in CODES.md is a string nobody can look up.
    documented = set(
        re.findall(
            r"^\| `([a-z0-9-]+)` \|",
            (Path(__file__).resolve().parent.parent / "CODES.md").read_text(
                encoding="utf-8"
            ),
            re.MULTILINE,
        )
    )
    missing = _emitted_codes() - documented
    assert not missing, f"undocumented finding codes: {sorted(missing)}"


def test_codes_documentation_has_no_phantom_entries():
    # The reverse drift: a documented code that no longer exists sends people
    # looking for a check that was renamed or removed.
    documented = set(
        re.findall(
            r"^\| `([a-z0-9-]+)` \|",
            (Path(__file__).resolve().parent.parent / "CODES.md").read_text(
                encoding="utf-8"
            ),
            re.MULTILINE,
        )
    )
    phantom = documented - _emitted_codes()
    assert not phantom, f"documented but never emitted: {sorted(phantom)}"


def test_every_subcommand_is_in_the_readme():
    from sift.cli import build_parser

    parser = build_parser()
    commands = {
        name
        for action in parser._actions
        if getattr(action, "dest", None) == "command"
        for name in action.choices
    }
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    missing = {c for c in commands if f"`sift {c}" not in readme}
    assert not missing, f"subcommands missing from README: {sorted(missing)}"


# --- audit regressions: adversarial and degenerate input -------------------


def test_a_field_larger_than_the_csv_default_limit_does_not_crash(tmp_path):
    # Python's csv module refuses fields over 128 KB; real files embed JSON
    # blobs and base64 in a single cell, and this used to be a raw _csv.Error.
    path = tmp_path / "long.csv"
    path.write_text("a,b\n" + "x" * 200_000 + ",2\n", encoding="utf-8")
    table = load(path)
    assert table.n_rows == 1
    assert len(table.by_key("a").values[0]) == 200_000


def test_nul_bytes_are_reported(tmp_path):
    path = tmp_path / "nul.csv"
    path.write_bytes(b"a,b\n1,\x00binary\n")
    assert "null-bytes" in codes(run_checks(load(path), Config()))


def test_a_header_with_no_rows_is_one_finding_not_one_per_column(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("a,b,c,d\n", encoding="utf-8")
    found = codes(run_checks(load(path), Config()))
    assert "no-data-rows" in found
    assert "empty-column" not in found


def test_distinct_is_exact_beyond_the_frequency_cap():
    # The cap bounds how many frequencies are tracked. Letting it bound the
    # distinct count silently switched off candidate-key on large files.
    profile = profile_column("id", 0, [f"v{i}" for i in range(6000)])
    assert profile.distinct == 6000
    assert profile.unique_ratio == 1.0


def test_candidate_key_still_fires_on_a_large_file(tmp_path):
    rows = "\n".join(f"A-{i},{i % 7}" for i in range(6000))
    findings = lint(tmp_path, f"order_id,v\n{rows}\n")
    assert "candidate-key" in codes(findings)


def test_html_report_escapes_hostile_values(tmp_path):
    path = write(
        tmp_path,
        """\
        id,note
        1,"<script>alert(1)</script> "
        2,ok
        """,
    )
    table = load(path)
    report = render_html(table, run_checks(table, Config()))
    assert "<script>alert(1)" not in report
    assert "&lt;script&gt;" in report


# --- audit regressions: configuration --------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "this is not toml [[[",
        '[sift]\nnull_warn = "not a number"\n',
        "[sift]\nnull_warn = -5\n",
        "[sift]\nnull_warn = 0.9\nnull_error = 0.2\n",
        "[sift]\nkey = 42\n",
        '[sift]\nfail_on = "banana"\n',
        "[sift]\noutlier_z = 0\n",
        "[sift]\nmax_examples = -1\n",
    ],
)
def test_bad_config_is_rejected_with_a_message(tmp_path, body):
    # A config is a statement of intent. Falling back to defaults on a typo
    # leaves someone believing a rule is in force when it is not.
    path = tmp_path / "sift.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_a_valid_config_still_loads(tmp_path):
    path = tmp_path / "sift.toml"
    path.write_text(
        '[sift]\nnull_warn = 0.2\nnull_error = 0.6\nfail_on = "warning"\n'
        'key = "id"\nignore = ["outliers"]\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.null_warn == 0.2
    assert config.fail_on == "warning"
    assert config.key == ["id"]


def test_cli_reports_a_bad_config_rather_than_crashing(tmp_path):
    data = write(tmp_path, CLEAN, "data.csv")
    bad = tmp_path / "bad.toml"
    bad.write_text('[sift]\nfail_on = "banana"\n', encoding="utf-8")
    assert main(["check", str(data), "--config", str(bad)]) == 2


# --- audit regressions: destructive output ---------------------------------


def test_output_may_not_overwrite_its_own_input(tmp_path):
    path = write(tmp_path, CLEAN, "data.csv")
    original = path.read_text(encoding="utf-8")

    assert main(["check", str(path), "--no-config", "--format", "html",
                 "--output", str(path)]) == 2
    assert main(["fix", str(path), "--no-config", "--output", str(path)]) == 2
    assert main(["init", str(path), "--output", str(path)]) == 2
    assert main(["diff", str(path), str(path), "--no-config",
                 "--output", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == original


def test_spreadsheet_formula_injection_is_flagged(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,note
        1,"=cmd|'/c calc'!A0"
        2,ok
        3,fine
        """,
    )
    assert "formula-injection" in codes(findings)


def test_ordinary_negative_numbers_are_not_formula_injection(tmp_path):
    findings = lint(
        tmp_path,
        """\
        id,amount
        1,-5
        2,-10
        3,+15
        """,
    )
    assert "formula-injection" not in codes(findings)


def test_number_parsing_cache_is_not_mutated_by_callers():
    # parse_number_any returns a cached dict; a caller mutating it would
    # poison every later lookup of the same value.
    first = parse_number_any("1.234")
    assert set(first) == {"en", "eu"}
    second = parse_number_any("1.234")
    assert second == first


# --- calibration: noise control on wide files ------------------------------


def test_a_finding_repeated_across_many_columns_is_collapsed(tmp_path):
    # A 1,093-column survey export produced 97 constant-column notes and 107
    # outlier notes. Nobody reads the 204th line of a report.
    header = ",".join(f"c{i}" for i in range(30))
    row = ",".join("same" for _ in range(30))
    findings = lint(tmp_path, f"{header}\n{row}\n{row}\n{row}\n")
    constant = [f for f in findings if f.code == "constant-column"]
    assert len(constant) == 1
    assert constant[0].detail["collapsed"] == 30
    # The full list survives for anything reading the JSON.
    assert len(constant[0].detail["columns"]) == 30


def test_errors_are_never_collapsed(tmp_path):
    # A column that will break something must appear by name, however many
    # of them there are.
    columns = 12
    header = ",".join(f"c{i}" for i in range(columns))
    good = ",".join("1" for _ in range(columns))
    bad = ",".join("oops" for _ in range(columns))
    rows = "\n".join([good] * 10 + [bad])
    findings = lint(tmp_path, f"{header}\n{rows}\n")
    mixed = [f for f in findings if f.code == "mixed-types"]
    assert len(mixed) == columns
    assert all(f.column for f in mixed)


def test_a_sub_one_decimal_is_not_a_european_number():
    # 0.247 was read as "247 grouped the European way", which flagged every
    # ordinary fraction in a survey export as a locale problem. Thousands
    # grouping never starts with a lone zero.
    assert number_evidence("0.247") == "en"
    assert number_evidence("0.445") == "en"
    # Genuine grouping is still recognised.
    assert number_evidence("1.234") == "group-eu"
    assert number_evidence("12.500,00") == "eu"


def test_a_survey_of_fractions_is_not_flagged_as_european(tmp_path):
    rows = "\n".join(f"{i},0.{i:03d}" for i in range(1, 40))
    findings = lint(tmp_path, f"id,grams\n{rows}\n")
    assert "european-numbers" not in codes(findings)


# --- coverage: codes that had no test and no benchmark case ----------------
# A measured 30% of finding codes were reachable only in production. These
# close that gap; each one asserts the code fires on data that should produce
# it, so a refactor cannot silently remove a check.


def test_invalid_utf8_is_reported(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_bytes(b"id,name\n1,Caf\xe9 Rouge\n")
    assert "encoding" in codes(run_checks(load(path), Config()))


def test_missing_trailing_newline_is_reported(tmp_path):
    path = tmp_path / "n.csv"
    path.write_text("id,v\n1,2", encoding="utf-8")
    assert "no-trailing-newline" in codes(run_checks(load(path), Config()))


def test_a_padded_column_name_is_reported(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("id, name \n1,x\n2,y\n", encoding="utf-8")
    assert "padded-column-name" in codes(run_checks(load(path), Config()))


def test_high_cardinality_text_is_reported(tmp_path):
    rows = "\n".join(f"{i},unique free text number {i}" for i in range(25))
    findings = lint(tmp_path, f"id,comment\n{rows}\n")
    assert "high-cardinality" in codes(findings)


def test_dates_before_1900_are_reported(tmp_path):
    findings = lint(tmp_path, "id,when\n1,1850-04-02\n2,2024-01-05\n")
    assert "implausible-dates" in codes(findings)


def test_mixed_iso_and_slash_dates_are_reported(tmp_path):
    # Decisive enough to settle the order, but the layouts still differ.
    findings = lint(
        tmp_path,
        "id,when\n1,25/12/2024\n2,26/12/2024\n3,2024-12-27\n4,28/12/2024\n",
    )
    assert "mixed-date-formats" in codes(findings)


def test_thin_date_evidence_is_reported(tmp_path):
    # One row proves day-first; the rest could go either way. That is an
    # inference worth making but worth flagging as thin.
    rows = "\n".join(f"{i},0{i}/0{i + 1}/2024" for i in range(1, 7))
    findings = lint(tmp_path, f"id,when\n{rows}\n7,25/12/2024\n")
    assert "weak-date-evidence" in codes(findings)


def test_negative_quantities_are_reported(tmp_path):
    rows = "\n".join(f"{i},{i}" for i in range(1, 12))
    findings = lint(tmp_path, f"id,quantity\n{rows}\n12,-4\n")
    assert "unexpected-negative" in codes(findings)


def test_a_declared_key_that_is_empty_is_reported(tmp_path):
    findings = lint(tmp_path, "id,v\n1,a\n,b\n3,c\n", Config(key=["id"]))
    assert "key-null" in codes(findings)


def test_a_declared_key_that_is_absent_is_reported(tmp_path):
    findings = lint(tmp_path, "id,v\n1,a\n2,b\n", Config(key=["nope"]))
    assert "missing-key-column" in codes(findings)


def test_a_null_foreign_key_is_reported(tmp_path):
    (tmp_path / "customers.csv").write_text("id,name\nC-1,Acme\n", encoding="utf-8")
    orders = write(tmp_path, "order_id,customer_id\nO-1,C-1\nO-2,\n", "orders.csv")
    ref = Reference.parse(f"{tmp_path / 'customers.csv'}:customer_id=id")
    assert "reference-null" in codes(check_reference(load(orders), ref, Config(), load))


# --- coverage: drift codes -------------------------------------------------


def _drift(tmp_path, baseline_text, current_text):
    baseline = load(write(tmp_path, baseline_text, "baseline.csv"))
    current = load(write(tmp_path, current_text, "current.csv"))
    return codes(compare(baseline, current, Config()))


def test_drift_reports_an_added_column(tmp_path):
    found = _drift(tmp_path, "a,b\n1,x\n2,y\n", "a,b,c\n1,x,9\n2,y,8\n")
    assert "column-added" in found


def test_drift_reports_reordered_columns(tmp_path):
    found = _drift(tmp_path, "a,b\n1,x\n2,y\n", "b,a\nx,1\ny,2\n")
    assert "column-order-changed" in found


def test_drift_reports_a_changed_type(tmp_path):
    found = _drift(tmp_path, "a\n1\n2\n3\n", "a\nred\ngreen\nblue\n")
    assert "type-changed" in found


def test_drift_reports_a_null_rate_jump(tmp_path):
    # Two columns, so a blank value is a blank field rather than a blank line
    # that the reader would skip entirely.
    baseline = "id,a\n" + "\n".join(f"{i},1" for i in range(10)) + "\n"
    current = "id,a\n" + "\n".join(
        f"{i},1" if i < 5 else f"{i}," for i in range(10)
    ) + "\n"
    assert "null-rate-jump" in _drift(tmp_path, baseline, current)


def test_drift_reports_a_distribution_shift(tmp_path):
    baseline = "a\n" + "\n".join(str(100 + i) for i in range(10)) + "\n"
    current = "a\n" + "\n".join(str(1000 + i) for i in range(10)) + "\n"
    assert "distribution-shift" in _drift(tmp_path, baseline, current)


def test_drift_reports_a_vanished_category(tmp_path):
    baseline = "a\nred\ngreen\nblue\nred\n"
    current = "a\nred\nred\nred\nred\n"
    assert "missing-category" in _drift(tmp_path, baseline, current)


def test_drift_reports_a_large_row_count_change(tmp_path):
    baseline = "a\n" + "\n".join("1" for _ in range(20)) + "\n"
    current = "a\n1\n2\n"
    assert "row-count-shift" in _drift(tmp_path, baseline, current)


def test_every_finding_code_has_a_test_or_a_benchmark_case():
    # Coverage was measured at 70% once; 18 checks existed only in production.
    # This keeps a new check from being added without something exercising it.
    root = Path(__file__).resolve().parent.parent
    source = "\n".join(p.read_text(encoding="utf-8") for p in (root / "sift").glob("*.py"))
    emitted = set(re.findall(r'Finding\(\s*\n?\s*"([a-z0-9-]+)"', source))

    tests = Path(__file__).read_text(encoding="utf-8")
    bench = "\n".join(
        p.read_text(encoding="utf-8") for p in (root / "bench").glob("*.py")
    )
    uncovered = {
        code
        for code in emitted
        if f'"{code}"' not in tests and f"'{code}'" not in tests and f'"{code}"' not in bench
    }
    assert not uncovered, f"finding codes with no test or benchmark case: {sorted(uncovered)}"


# --- sensitive data --------------------------------------------------------
# The first version of this check flagged 17 of 30 real datasets: dates,
# latitudes and Elo ratings all matched a loose phone pattern. The second
# failed 5 of 7 adversarial files. Those cases are kept here permanently.


def test_email_and_card_columns_are_detected(tmp_path):
    cards = ["4111111111111111", "5555555555554444", "378282246310005"]
    rows = "\n".join(
        f"{i},user{i}@example.com,{cards[i % 3]}" for i in range(30)
    )
    findings = lint(tmp_path, f"id,contact,payment\n{rows}\n")
    sensitive = {f.column for f in findings if f.code == "sensitive-data"}
    assert {"contact", "payment"} <= sensitive


def test_sensitive_findings_never_include_example_values(tmp_path):
    # The whole point: a card number must not reach a CI log.
    rows = "\n".join(f"{i},4111111111111111" for i in range(30))
    findings = lint(tmp_path, f"id,payment\n{rows}\n")
    for finding in findings:
        if finding.code == "sensitive-data":
            assert finding.examples == []
            assert "4111" not in finding.message


def test_sensitive_columns_redact_values_from_other_findings(tmp_path):
    findings = lint(
        tmp_path,
        """\
        customer
         alice@example.com 
         bob@example.com 
         carol@example.com 
        """,
    )

    whitespace = next(f for f in findings if f.code == "whitespace")
    assert whitespace.examples == []
    assert "alice@example.com" not in whitespace.message
    assert "alice@example.com" not in json.dumps(whitespace.to_dict())
    assert "redacted" in whitespace.message.lower()


def test_silencing_sensitive_warning_does_not_reenable_value_output(tmp_path):
    findings = lint(
        tmp_path,
        """\
        customer
         alice@example.com 
         bob@example.com 
         carol@example.com 
        """,
        Config(ignore=["sensitive-data"]),
    )

    assert "sensitive-data" not in codes(findings)
    whitespace = next(f for f in findings if f.code == "whitespace")
    assert whitespace.examples == []
    assert "alice@example.com" not in json.dumps(whitespace.to_dict())


def test_fix_audit_log_redacts_sensitive_values_but_keeps_other_values(tmp_path):
    source = write(
        tmp_path,
        """\
        id,customer,region
        1," alice@example.com "," North "
        2," bob@example.com ",South
        3," carol@example.com ",West
        """,
    )
    output = tmp_path / "fixed.csv"
    audit = tmp_path / "audit.csv"

    assert main(["fix", str(source), "--output", str(output), "--log", str(audit)]) == 0

    log = audit.read_text(encoding="utf-8")
    assert "alice@example.com" not in log
    assert "bob@example.com" not in log
    assert "carol@example.com" not in log
    assert "[redacted sensitive value]" in log
    assert " North " in log  # non-sensitive audit values remain exact


def test_public_diff_redacts_sensitive_category_examples(tmp_path):
    from sift import diff as public_diff

    baseline = write(
        tmp_path,
        "contact\n"
        + "\n".join(["alice@example.com"] * 5 + ["bob@example.com"] * 5)
        + "\n",
        name="baseline.csv",
    )
    current = write(
        tmp_path,
        "contact\n"
        + "\n".join(["alice@example.com"] * 4 + ["bob@example.com"] * 3 + ["carol@example.com"] * 3)
        + "\n",
        name="current.csv",
    )

    findings = public_diff(baseline, current)
    new_category = next(f for f in findings if f.code == "new-category")
    assert new_category.examples == []
    assert "carol@example.com" not in json.dumps(new_category.to_dict())


def test_a_column_named_like_a_sensitive_field_is_noted(tmp_path):
    rows = "\n".join(f"{i},REDACTED" for i in range(10))
    findings = lint(tmp_path, f"id,ssn\n{rows}\n")
    assert "sensitive-column-name" in codes(findings)


def test_international_phone_numbers_are_detected(tmp_path):
    rows = "\n".join(f"{i},+44 20 7946 {1000 + i:04d}" for i in range(20))
    findings = lint(tmp_path, f"id,contact\n{rows}\n")
    assert "sensitive-data" in codes(findings)


@pytest.mark.parametrize(
    "name,value",
    [
        ("zip", "10001-4321"),        # ZIP+4
        ("sku", "100-200-0300"),      # three-three-four product code
        ("isbn", "978-0-300-10000-0"),
        ("period", "1900-1905"),      # year range
        ("build", "12.4.0.20240101"), # version string
    ],
)
def test_digit_strings_that_are_not_phone_numbers(tmp_path, name, value):
    rows = "\n".join(f"{i},{value}" for i in range(30))
    findings = lint(tmp_path, f"id,{name}\n{rows}\n")
    assert "sensitive-data" not in codes(findings)


def test_numeric_columns_are_never_called_phone_numbers(tmp_path):
    # Latitudes, dates and Elo ratings were all reported as phone numbers by
    # the first version, because their punctuation fit the pattern.
    rows = "\n".join(f"{i},-122.33{i:02d},2024-01-{(i % 28) + 1:02d}" for i in range(30))
    findings = lint(tmp_path, f"id,latitude,when\n{rows}\n")
    assert "sensitive-data" not in codes(findings)


def test_a_random_sixteen_digit_reference_is_not_a_card(tmp_path):
    # Passing Luhn is not enough; an issuer prefix and length are required.
    rows = "\n".join(f"{i},1234567890123456" for i in range(30))
    findings = lint(tmp_path, f"id,ref\n{rows}\n")
    assert "sensitive-data" not in codes(findings)
