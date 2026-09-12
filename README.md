# Sift

Sift is a Python project for finding silent data-quality problems in tabular files.

This first milestone establishes the package structure and the core `Finding` / `Severity` model that every later check will return.

## Development setup

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

The command-line interface and file loading are intentionally not part of this first commit.
