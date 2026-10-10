# Contributing to Sift

Bug reports, reproducible false positives, documentation corrections and
focused pull requests are welcome. For security vulnerabilities, follow
[SECURITY.md](SECURITY.md) rather than filing a public issue.

## Before proposing a change

Check existing [issues](https://github.com/N0tDeb/sift/issues). A useful data
quality bug report includes a small, non-sensitive input file, the command
used, the expected result and the actual finding or error. Do not attach
real customer data or secrets.

Sift's priority is accurate findings with few false positives. Before adding
a rule, explain what downstream damage it detects and when correct input
could produce a false alarm. Repairs must not guess when evidence is unclear.

## Development

Sift needs Python 3.11 or newer. After cloning, install development extras
in a suitable environment:

```bash
python -m pip install -e '.[dev]'
ruff check .
pytest -q
python -m bench.run
```

The `dev` extra supplies pytest, Ruff and both optional file readers. No
special local release tooling is required; distribution checks run in GitHub
Actions. See [SETUP.md](SETUP.md) for the CI and maintainer workflow.

## Pull requests

- Branch from the latest `main` and keep changes focused.
- Add a regression test for behavioral fixes, including cases where Sift
  should produce **no** finding.
- For new checks, extend the known-corruption benchmark where applicable and
  update [CODES.md](CODES.md) when finding codes change.
- Update README examples and [Project Guide](docs/PROJECT_GUIDE.md) when
  public commands, supported formats or expectations change.
- Make sure CI passes on Windows, macOS and Linux before requesting a merge.

The public command is `sift-lint`; the Python package is `sift_lint`.
See the [MIT license](LICENSE) for contribution licensing.
