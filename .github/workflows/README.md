# Workflows

`ci.yml` runs on every push to `main` and every pull request.

**test** — installs the package and runs `ruff`, the test suite, and the recall
benchmark across Python 3.11, 3.12 and 3.13.

**data-gate** — runs Sift against this repository's own example files, the way
it is meant to be used in a pipeline: `check` on a folder, `diff` against a
baseline, `fix` as a dry run, `impact` on a known-bad column, and `init` on the
known-good file. These use `--fail-on none` because the example data is
deliberately broken; in a real pipeline you would drop that flag and let a bad
file fail the build.
