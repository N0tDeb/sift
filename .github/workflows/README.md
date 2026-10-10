# Workflows

`ci.yml` runs on every push to `main` and every pull request.

**test** — runs Ruff, the full test suite and the recall benchmark on Ubuntu
with Python 3.11–3.14, plus Windows and macOS with Python 3.14. An exploratory
Python 3.15 Ubuntu job runs without blocking the established compatibility
checks. The format extras are installed for every test job; new Python
versions are not advertised as supported until verification is complete.

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
