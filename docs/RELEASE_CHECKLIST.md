# Sift release-readiness checklist

This checklist applies to the **unreleased source candidate**. It is not a
release announcement or authorization to merge, tag, or publish. The current
public PyPI release remains independent of the draft PR until deliberately
published. Green CI is necessary, but it does not prove the absence of bugs.

## Review the cumulative pull request

- [ ] Confirm that every intended commit is in the draft PR and the target is
  the expected `main` commit; review the cumulative code diff, not just the
  final feature batch.
- [ ] Reconcile known issues and limitations in `docs/FINAL_REVIEW.md`, the
  changelog, `SECURITY.md`, and the public README. Do not claim that Sift is
  fully streaming, free of false positives, or guaranteed to redact all secrets.
- [ ] Review changes to the Python library API, finding-code semantics,
  command flags, configuration keys, and report formats for compatibility.
- [ ] Review any third-party dependencies and the supported Python-version
  matrix. Reassess vulnerability advisories and supported minimum versions.
- [ ] Decide which new features warrant the next public version. The version
  is **not** bumped by the feature batches automatically.
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

- [ ] Consider branch protection or a ruleset on `main` requiring the
  appropriate checks. GitHub currently must be configured by the maintainer;
  documenting this does not enable protection.
- [ ] After review and explicit approval, merge the intended PR, verify the
  new `main` commit and its CI results, and choose the next version using the
  compatibility and changelog review.
- [ ] Update `sift_lint/__init__.py` and finalize `CHANGELOG.md` release notes
  in a reviewed commit. Confirm the stable tag `vX.Y.Z` matches the package
  version using `.github/scripts/check_release.py`.
- [ ] Check GitHub's `pypi` environment, reviewers/approvals, and PyPI Trusted
  Publisher configuration. **Publishing a stable GitHub Release can publish
  directly to PyPI**; do not use that operation as a test.
- [ ] Only after explicit publishing approval, create the matching tag and
  publish a stable GitHub Release, then verify the published artifacts,
  installation, CLI version and package links.

The original personal/private master archive is out of scope for the public
repository review and must be discussed separately.
