# Sift release-readiness checklist

Use this checklist for each candidate release. Passing the checks does not
authorize a merge, GitHub Release, or PyPI publication; those require separate
maintainer decisions. A version change on `main` does not update PyPI. Green
CI is necessary, but it does not prove the absence of bugs.

## Review the release changes

- [ ] Review the full release diff against the previously published tag,
  including files changed by earlier pull requests. Verify the intended
  candidate and target branch before merging.
- [ ] Reconcile known issues and limitations in `docs/PERFORMANCE.md`, the
  changelog, `SECURITY.md`, and the public README. Do not claim that Sift is
  fully streaming, free of false positives, or guaranteed to redact all secrets.
- [ ] Review changes to the Python library API, finding-code semantics,
  command flags, configuration keys, and report formats for compatibility.
- [ ] Review any third-party dependencies and the supported Python-version
  matrix. Reassess vulnerability advisories and supported minimum versions.
- [ ] Choose the next public version based on compatibility and the changelog.
  Version updates are deliberate and do not publish the package.
- [ ] Review security-sensitive operations: `fix` refusal rules, formula
  safety, privacy and audit-log redaction, baseline storage, and multi-output
  partial-write limitations. Do not commit private baseline snapshots.

## Verify source and distributions

- [ ] Run `ruff check .`, `pytest -q`, and `python -m bench.run` on the exact
  candidate commit. The known 28-case recall benchmark must catch all planted
  corruptions and the clean control must have zero findings.
- [ ] Confirm the latest GitHub Actions `ci` run for that commit is green:
  six OS/Python test jobs, three package build-and-install jobs, and one
  data-gate. Ensure none were skipped or running when making the decision.
- [ ] Check the opt-in `check --format github` annotations on a private test
  run. Use `--fail-on warning` (or your chosen threshold) for real CI gates.
  Existing JSON/HTML/text formats must retain their documented behavior.
- [ ] Validate Excel and Parquet integrations and both wheel and source
  distribution installations **outside** the repository. CI smoke checks do
  this and verifies installed metadata and `pip check`.
- [ ] Re-run the main CLI examples and check that README and project-guide
  options, finding-code counts, and implementation module count are current.
- [ ] Check memory usage against representative data. Existing benchmarks do
  not establish a validated maximum supported input size.

## Merge and release only with maintainer approval

- [ ] Confirm `main` branch protection or a ruleset requires the appropriate
  CI checks and restricts force pushes.
- [ ] After review and explicit approval, merge the intended PR and verify the
  resulting `main` commit, its tracked files and its CI results.
- [ ] Update `sift_lint/__init__.py` and finalize `CHANGELOG.md` release notes
  in a reviewed commit. Confirm the stable tag `vX.Y.Z` matches the package
  version using `.github/scripts/check_release.py`.
- [ ] Check GitHub's `pypi` environment, reviewers/approvals, and PyPI Trusted
  Publisher configuration. **Publishing a stable GitHub Release can publish
  directly to PyPI**; do not use that operation as a test.
- [ ] Only after explicit publishing approval, create the matching tag and
  publish a stable GitHub Release, then verify the published artifacts,
  installation, CLI version and package links.
