# Publishing this repository

Two things need your details before the first push.

## 1. The CI badge

`README.md` has five badges at the top. The first points at a GitHub Actions
run and contains a placeholder:

```
https://github.com/USERNAME/sift/actions/workflows/ci.yml/badge.svg
```

Replace `USERNAME` with your GitHub username in both the image URL and the link
beside it. The other four badges are static and need no changes.

## 2. The demo GIF

The README has a commented-out image at the top. Record it and uncomment:

```bash
brew install vhs          # or: go install github.com/charmbracelet/vhs@latest
pip install -e .
vhs demo/demo.tape        # writes demo/sift.gif
```

See `demo/README.md` for what the recording shows and why. The GIF is not
committed to the repository, because it is generated — re-record it whenever
output formatting changes.

## Pushing

```bash
git init
git add .
git commit -m "Sift: a linter for tabular data"
git branch -M main
git remote add origin git@github.com:USERNAME/sift.git
git push -u origin main
```

The workflow in `.github/workflows/ci.yml` runs on the first push: tests on
Python 3.11, 3.12 and 3.13, `ruff`, the recall benchmark, and Sift against its
own example files. The badge turns green when it passes.

## Optional: publishing to PyPI

`pyproject.toml` is already complete. The package name `sift-csv` may be taken
by the time you read this — check on pypi.org and change `name` if so.

```bash
pip install build twine
python -m build
twine upload dist/*
```
