# Workflows

`ci.yml` runs on every push to `main` and every pull request.

**test** — installs the package in editable mode and runs `ruff`, the test suite,
and the recall benchmark across Python 3.11, 3.12 and 3.13.

**package** — builds the wheel declared by `pyproject.toml`, installs that wheel
without dependencies into a fresh virtual environment, then smoke-tests the
installed import, console script, a real CSV check, and `pip check`. The smoke
commands run outside the repository so the source checkout cannot shadow the
installed wheel.

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
