"""Release metadata and documentation safety checks, without publishing anything."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

from sift_lint import __version__

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
    for name in ("FINAL_REVIEW.md", "RELEASE_CHECKLIST.md", "html-report-preview.png",
                 "vhs-demo.yml"):
        assert name in guide
