# Changelog

Notable user-visible changes to Sift are recorded here. Releases use
[Semantic Versioning](https://semver.org/).

## Unreleased (post-1.0.0 source changes — not on PyPI yet)

- Batch 6 privacy/correctness: redact comparisons using both datasets and
  reference checks using both files' sensitivity, including common secret
  field names; preserve opaque
  sensitive values during automatic repair; stop duplicate sensitive-column
  warnings. Verify repairs using the actual output delimiter, write real TSV
  when the output suffix is `.tsv`, and require PyArrow >=14.0.1 for optional
  Parquet support. Suppress meaningless numeric distribution-shift warnings on
  recognized postal codes and identifier columns. Update security and
  architecture documentation.

- Batch 5 schema hardening: reserve real header names when generating keys
  for repeated columns (including names like `v#1`); apply the same naming
  policy across CSV, Excel and Parquet. Detect added/removed duplicate-header
  occurrences during drift comparisons, and compare corresponding occurrences
  instead of repeatedly comparing the first one. Document bounded runtime/memory
  measurements and release validation limits without claiming a size guarantee.

- Batch 4 reliability: resolve `impact` selectors using duplicate-column keys,
  reject non-finite TOML thresholds, preserve existing output file permissions,
  and preflight repaired-CSV and audit-log paths before starting either write.
  Invalid delimiter overrides exit cleanly. Multi-file writes remain separate
  atomic replacements rather than a single transaction.

- Batch 3 audit/privacy: record duplicate-row removals with source-row
  provenance and a source-file hash; keep the original six-column audit header.
  Redact sensitive values from repair refusal summaries, numeric profiles,
  sensitive group labels in impact reports, and unscoped duplicate examples.
  Escape formula-like values in audit logs without silently discarding them.

- Batch 2 data correctness: reject malformed CSV quoting and files without a
  usable header, including in `fix --force`; require populated drift baselines.
  Treat grouping-shaped numeric values as ambiguous unless the column contains
  decisive locale evidence, preventing speculative repairs, misleading numeric
  statistics and `impact` sums.

- Batch 1 formula safeguards: recognize risky formula-like cells regardless of
  inferred column type, including newline and non-breaking-space prefixes;
  preserve ordinary signed numbers. Refuse newly introduced formula-like CSV
  output and prohibit Excel formula-to-CSV repair even under `--force`.
  Read-only Excel inspection reports formulas in the selected worksheet.

- Reject row-limited `fix` writes to prevent truncating a data file; preserve
  limited reads for `fix --dry-run` previews.
- Guard every checked reference and loaded configuration file against report
  or audit output collisions; reject overlapping repaired output and audit log.
- Treat any unreadable member of a batch check as an operational failure
  (exit code 2). Honor text, JSON and HTML batch `--output` consistently.
- Stage text, CSV and audit-log files before replacing a destination, so an
  interrupted single-file write preserves the previous destination.
- Reject non-finite/overflowing numeric inputs in aggregate-impact calculations
  with a controlled diagnostic instead of an uncaught exception.
- Reject invalid delimiter overrides and record physical source line numbers
  for CSV repairs containing multiline quoted fields.
- Use absolute GitHub links in the package README so documentation and images
  resolve correctly on PyPI, and clarify that `--log` is optional.

These changes are prepared for review and cross-platform CI. The published
`v1.0.0` artifacts remain unchanged.

## 1.0.0

### Included in the first stable distribution

- `sift-lint` CLI with `check`, `diff`, `fix`, `impact`, `init`, and `profile`
  commands; a small Python API is available as `sift_lint`.
- Dependency-free CSV linting, with optional Excel and Parquet readers
  provided through the `excel`, `parquet`, and `all` extras.
- Stable finding codes with text, JSON, and standalone HTML reports;
  cross-file reference checks and configuration through `sift.toml`.
- Conservative repair with an audit log, baseline drift analysis, and
  quantified aggregate-impact reporting.
- Terminal-control escaping and sensitive-data redaction safeguards.

### Release readiness

- Cross-platform CI covers Python 3.11–3.14 on Linux and Python 3.14 on
  Windows/macOS; wheel and source-distribution smoke tests run on all three.
- The controlled corruption benchmark detects 28 of 28 planted defects
  with zero findings on its clean control. This is not a general recall claim.
- Secure release-only PyPI publishing uses GitHub Actions and short-lived
  Trusted Publishing credentials; GitHub Actions dependencies use pinned SHAs
  with Dependabot updates.
- Public installation instructions, package metadata, contribution guidance,
  and a security-reporting policy accompany the distribution.
