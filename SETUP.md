# Publishing this repository

The GitHub account is already configured as `N0tDeb`. The demo is optional.

## 1. The CI badge

`README.md` has four badges at the top. The CI badge is already configured for:

```
https://github.com/N0tDeb/sift/actions/workflows/ci.yml/badge.svg
```

If you use a repository name other than `sift`, update that path segment in both
the badge image URL and its link. The other three badges are static and need no changes.

## 2. The demo GIF (optional)

The README has a commented-out image at the top. Record it and uncomment:

```bash
brew install vhs          # or: go install github.com/charmbracelet/vhs@latest
pip install -e .
vhs demo/demo.tape        # writes demo/sift.gif
```

See `demo/README.md` for what the recording shows and why. If you uncomment
the README image, commit `demo/sift.gif` so GitHub can render it, and re-record
the GIF whenever output formatting changes.

## Pushing

```bash
git init
git add .
git commit -m "Sift: a linter for tabular data"
git branch -M main
git remote add origin git@github.com:N0tDeb/sift.git  # adjust the repo name if needed
git push -u origin main
```

The workflow in `.github/workflows/ci.yml` runs on the first push: tests on
Python 3.11, 3.12 and 3.13, `ruff`, the recall benchmark, a wheel build/install
smoke test, and Sift against its own example files. The badge turns green when
it passes.

## CI security settings

The committed workflow uses read-only `GITHUB_TOKEN` permissions, disables
checkout credential persistence, and pins GitHub Actions to immutable commit
SHAs. These settings reduce unnecessary CI permissions and keep workflow
dependencies explicit.
