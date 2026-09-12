from sift.findings import Finding, Severity, sort_findings


def test_finding_serializes_stable_fields():
    finding = Finding(
        code="demo-problem",
        severity=Severity.WARNING,
        message="Example problem",
        column="amount",
        examples=["bad"],
    )
    assert finding.to_dict()["code"] == "demo-problem"
    assert finding.to_dict()["severity"] == "warning"


def test_errors_sort_before_warnings():
    findings = [
        Finding("warn", Severity.WARNING, "warning"),
        Finding("err", Severity.ERROR, "error"),
    ]
    assert [item.code for item in sort_findings(findings)] == ["err", "warn"]
