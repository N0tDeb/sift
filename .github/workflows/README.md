# Workflows

`ci.yml` runs on every push to `main` and every pull request.

**test** — runs Ruff, the full test suite and the recall benchmark on Ubuntu
with Python 3.11–3.14, plus Windows and macOS with Python 3.14. The format
extras are installed for every test job. Python 3.15 will be added when
`actions/setup-python` provides it for the selected runner.

**package** — builds a wheel and source distribution on Linux, Windows and
macOS. Both archives undergo strict metadata checks and are installed
separately in clean virtual environments. Each installed package is tested
outside the repository for version consistency, imports, the CLI, a real CSV
check and `pip check`.

**data-gate** — runs Sift against this repository's own example files, the way
it is meant to be used in a pipeline: `check` on a folder, `diff` against a
baseline, `fix` as a dry run, and `impact` on a known-bad column. These use
`--fail-on none` because the example data is deliberately broken; in a real
pipeline you would drop that flag and let a bad file fail the build.

## Supply-chain controls

Workflow actions are pinned to full commit SHAs rather than mutable version
tags. Checkout does not persist the repository token because these jobs never
push back to the repository, and the workflow token is explicitly limited to
read-only repository contents.

## Release publishing

`release.yml` runs only when a GitHub Release is published. It ignores prereleases
and rejects tags that do not match the package's stable version or point outside
`main`. The unprivileged build job runs tests and the benchmark, then builds and
validates a wheel and source distribution. The separate publish job downloads
those artifacts and publishes through PyPI Trusted Publishing.

The publish job uses the `pypi` GitHub environment and has only the OIDC
permission needed for publishing. No long-lived PyPI token is stored. Creating
a branch, pushing a tag, or merging a pull request never triggers publishing.

Before using **Publish release**, configure the `pypi` environment and the
matching Trusted Publisher on PyPI, then complete the maintainer's release
checklist. Publishing a release is the deliberate production publishing action.

`.github/dependabot.yml` proposes weekly updates for GitHub Actions. Review the
updated commit SHA pins before merging those pull requests.
