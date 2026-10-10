# Development and release setup

This repository is already published on GitHub at
[github.com/N0tDeb/sift](https://github.com/N0tDeb/sift). These instructions
are for working with the existing project, not creating a new repository.

## Develop from source

Python 3.11 or newer is required. The same source tree works on Windows,
macOS and Linux. From a clone of the repository:

```bash
python -m pip install -e '.[dev]'
ruff check .
pytest -q
python -m bench.run
```

The `dev` extra installs optional Excel and Parquet readers as well as the
test and lint dependencies. For CSV-only use, `python -m pip install -e .`
is sufficient. Installing `sift-lint` from PyPI is not possible until the
first release has been published.

## Contribute changes

Create a topic branch from an up-to-date `main`. Open a pull request rather
than committing directly to the default branch; use **Create a merge commit**
for project-maintained PRs so their individual commits remain in history.
The required verification is documented in [CONTRIBUTING.md](CONTRIBUTING.md).

## Continuous integration

[CI](.github/workflows/ci.yml) runs on pushes to `main` and on pull requests:

- Ruff, tests and the known-corruption benchmark on Linux with Python
  3.11–3.14, and Windows/macOS with Python 3.14;
- wheel/sdist build and clean-install smoke tests on Linux, Windows and macOS;
- a data-quality gate against the intentionally damaged example files.

GitHub Actions are pinned to full commit SHAs. The workflows use read-only
repository access except for the narrowly scoped OIDC publishing job.
Dependabot proposes updates to pinned Actions, which must be reviewed before
merge. See [.github/workflows/README.md](.github/workflows/README.md).

The optional terminal demo is in [demo/](demo/). It is not required for a
release.

## Release preparation (maintainers)

1. Ensure `main` CI is green and all release documentation and metadata are
   complete. Check the [changelog](CHANGELOG.md),
   [security policy](SECURITY.md), and examples.
2. Prepare and validate the release version in `sift_lint/__init__.py` on a
   reviewed PR; the version is read dynamically by Hatchling.
3. Verify the built wheel and source distribution on all CI platforms.
4. Configure the protected GitHub environment `pypi` and the matching PyPI
   Trusted Publisher for `.github/workflows/release.yml`. Confirm any required
   environment reviewers and publishing permissions before proceeding.
5. **Only with deliberate release approval**, create the matching Git tag and
   publish a GitHub Release. The release workflow validates the tag, tests,
   builds, and then publishes to PyPI using short-lived OIDC credentials.

**Warning:** Publishing a stable GitHub Release after Trusted Publishing is
configured can trigger a real PyPI upload. A branch push, pull request merge,
or tag push alone does not publish a package. Do not publish a GitHub Release
as a test. See [.github/workflows/README.md](.github/workflows/README.md)
for workflow behavior and [SECURITY.md](SECURITY.md) for reporting vulnerabilities.
