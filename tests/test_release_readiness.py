"""Release metadata and documentation safety checks, without publishing anything."""

from __future__ import annotations

import re
import runpy
from pathlib import Path

import pytest

from sift_lint import __version__
from sift_lint.findings import Finding, Severity
from sift_lint.loading import load
from sift_lint.reporting import render_batch_html, render_html

ROOT = Path(__file__).resolve().parents[1]


def _check_tag():
    return runpy.run_path(str(ROOT / ".github/scripts/check_release.py"))["check_tag"]


@pytest.mark.parametrize("version", ["1.0.0", "1.7.12", "2.0.0", "10.20.30"])
def test_stable_release_tags_match_exact_package_versions(version: str) -> None:
    _check_tag()(f"v{version}", version)


@pytest.mark.parametrize("tag, version", [
    ("v1.2.1", "1.2.0"), ("1.2.0", "1.2.0"), ("v0.9.0", "0.9.0"),
    ("v01.2.3", "01.2.3"), ("v1.2.3rc1", "1.2.3rc1"),
    ("v1.2.3\n::warning::hello", "1.2.3"),
])
def test_invalid_or_mismatched_tags_cannot_pass(tag: str, version: str) -> None:
    with pytest.raises(ValueError):
        _check_tag()(tag, version)


def test_current_source_version_has_valid_stable_tag() -> None:
    _check_tag()(f"v{__version__}", __version__)


def test_documented_module_count_matches_package() -> None:
    count = len(list((ROOT / "sift_lint").glob("*.py")))
    guide = (ROOT / "docs/PROJECT_GUIDE.md").read_text(encoding="utf-8")
    assert f"| Python source modules | {count} |" in guide


def test_github_annotations_are_opt_in_and_documented() -> None:
    for file in (ROOT / "README.md", ROOT / "docs/PROJECT_GUIDE.md"):
        assert "--format github" in file.read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '--output "$RUNNER_TEMP/sift-annotations.txt"' in ci
    assert "python -m pip check" in ci


def test_release_workflow_separates_build_and_publish() -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "needs: build" in release
    assert "id-token: write" in release
    assert "PYTHONPATH=. python .github/scripts/check_release.py" in release
    assert "pypa/gh-action-pypi-publish@" in release
    assert "types: [published]" in release


def test_release_checklist_mentions_approval_and_snapshot_privacy() -> None:
    content = (ROOT / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
    assert "explicit approval" in content
    assert "private baseline snapshots" in content
    assert "10" in content or "six OS/Python" in content


def test_html_preview_asset_matches_its_png_extension() -> None:
    asset = ROOT / "docs/html-report-preview.png"
    assert asset.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

def test_documented_repository_tree_stays_current() -> None:
    guide = (ROOT / "docs/PROJECT_GUIDE.md").read_text(encoding="utf-8")
    count = len(list((ROOT / "sift_lint").glob("*.py")))
    regression_count = len(list((ROOT / "tests").glob("test_*.py")))
    assert f"...{count} Python modules..." in guide
    assert f"test_*.py ({regression_count} regression modules)" in guide
    for name in ("PERFORMANCE.md", "RELEASE_CHECKLIST.md", "html-report-preview.png",
                 "vhs-demo.yml"):
        assert name in guide


def test_release_version_matches_public_guide_and_changelog() -> None:
    guide = (ROOT / "docs/PROJECT_GUIDE.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert f"> **Project version:** `{__version__}`" in guide
    assert f"| Project version | `{__version__}` |" in guide
    assert f"## {__version__}\n" in changelog
    assert '["version"]' in project
    assert 'path = "sift_lint/__init__.py"' in project
    _check_tag()(f"v{__version__}", __version__)


def test_documentation_relative_links_resolve() -> None:
    """Keep repository documentation links valid when files are reorganized."""
    pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    failures = []
    for doc in ROOT.rglob("*.md"):
        # Generated build directories are not part of the repository docs.
        if any(part in {"dist", "build", ".venv"} for part in doc.parts):
            continue
        in_fence = False
        for line in doc.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for match in pattern.finditer(line):
                link = match.group(1).split("#", 1)[0].strip("<>")
                if not link or ":" in link or link.startswith("#"):
                    continue
                if not (doc.parent / link).resolve().exists():
                    failures.append(f"{doc.relative_to(ROOT)}: {link}")
    assert not failures, "Broken relative links: " + ", ".join(failures)


# Report counts are prose in HTML: check zero, singular, and plural for each level.

def _html_count_table(tmp_path: Path, rows: int, columns: int):
    path = tmp_path / "counts.csv"
    path.write_text(
        ",".join(f"column_{index}" for index in range(columns))
        + "\n"
        + (",".join("value" for _ in range(columns)) + "\n") * rows,
        encoding="utf-8",
    )
    return load(path)


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("severity,word", [
    (Severity.ERROR, "error"),
    (Severity.WARNING, "warning"),
    (Severity.INFO, "note"),
])
def test_single_report_html_count_grammar(tmp_path, count, severity, word):
    table = _html_count_table(tmp_path, rows=1, columns=1)
    findings = [
        Finding(code=f"test-{i}", severity=severity, message="sample")
        for i in range(count)
    ]
    output = render_html(table, findings)
    expected = word if count == 1 else word + "s"
    assert f"<b>{count}</b> {expected}" in output
    assert "across 1 row and 1 column." in output


@pytest.mark.parametrize("rows,columns", [(0, 1), (1, 1), (2, 2)])
def test_html_row_column_counts(tmp_path, rows, columns):
    table = _html_count_table(tmp_path, rows=rows, columns=columns)
    output = render_html(table, [])
    r = "row" if rows == 1 else "rows"
    c = "column" if columns == 1 else "columns"
    assert f"across {rows} {r} and {columns} {c}." in output


@pytest.mark.parametrize("count", [0, 1, 2])
@pytest.mark.parametrize("severity,word", [
    (Severity.ERROR, "error"),
    (Severity.WARNING, "warning"),
    (Severity.INFO, "note"),
])
def test_batch_report_html_count_grammar(tmp_path, count, severity, word):
    findings = [
        Finding(code=f"test-{i}", severity=severity, message="sample")
        for i in range(count)
    ]
    output = render_batch_html([(tmp_path / "input.csv", findings)], failures=[])
    expected = word if count == 1 else word + "s"
    assert f"{count} {expected}" in output
    assert "<b>" not in output
