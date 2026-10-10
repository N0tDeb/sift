# Changelog

Notable user-visible changes to Sift are recorded here. Releases use
[Semantic Versioning](https://semver.org/).

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
