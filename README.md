# Sift

[![CI](https://github.com/N0tDeb/sift/actions/workflows/ci.yml/badge.svg)](https://github.com/N0tDeb/sift/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/N0tDeb/sift/blob/main/LICENSE)
[![Controlled benchmark: 28/28](https://img.shields.io/badge/controlled%20benchmark-28%2F28-brightgreen.svg)](https://github.com/N0tDeb/sift/blob/main/BENCHMARK.md)

**Find the data errors that don't raise errors.**

Sift is a command-line linter for CSV, Excel and Parquet files. It checks for problems that a successful import won't reveal: ambiguous dates, numbers stored as text, inconsistent labels, missing references, duplicate keys and changes from a known-good baseline. It explains the potential consequences and offers conservative, auditable repairs when the data provides enough evidence.

![Sift CLI demonstration](https://raw.githubusercontent.com/N0tDeb/sift/main/demo/sift.gif)

## Install and try it

Python 3.11 or later is required. For an isolated command-line installation:

```bash
uv tool install sift-lint
sift-lint check orders.csv
```

Alternatively, install into a Python environment using `python -m pip install sift-lint`. CSV requires no extra runtime dependencies. For Excel (`.xlsx`/`.xlsm`) and Parquet:

```bash
uv tool install 'sift-lint[all]'
# Or install the specific extra with pip:
python -m pip install 'sift-lint[excel]'
python -m pip install 'sift-lint[parquet]'
```

To try the included example from a source checkout:

```bash
python -m pip install -e '.[all]'
sift-lint check examples/orders_messy.csv --key order_id
```

The example contains intentional defects, so a nonzero finding exit status is expected. A sample finding looks like this:

```text
error   conflicting-date-formats  order_date
        'order_date' holds two date formats mixed together.
```

The full example currently reports:

```text
23 findings: 6 error, 14 warning, 3 info
```

Sift uses stable finding codes; [CODES.md](https://github.com/N0tDeb/sift/blob/main/CODES.md) lists all 70 of them. See the [example HTML report](https://github.com/N0tDeb/sift/blob/main/sample-report.html) for a shareable output format.

![HTML report preview](https://raw.githubusercontent.com/N0tDeb/sift/main/docs/html-report-preview.png)

## Commands

| Command | Purpose |
| --- | --- |
| `sift-lint check FILE...` | Check one file, multiple files or a directory for data-quality issues. |
| `sift-lint profile FILE` | Inspect inferred types, missingness, cardinality and ranges. |
| `sift-lint diff BASELINE CURRENT` | Compare schema, missingness, categories and distributions against a reference file. |
| `sift-lint fix FILE --output clean.csv` | Produce a repaired CSV copy and report what could not safely be changed. |
| `sift-lint impact FILE --sum amount --group-by region` | Estimate how defects change aggregates and groupings. |
| `sift-lint init FILE` | Generate a reviewable `sift.toml` from a trusted file. |
| `sift-lint baseline save NAME FILE` | Store a local, integrity-checked source snapshot for later comparisons. |

For help with any command, run `sift-lint COMMAND --help`. Useful checking options include `--key`, `--ignore`, `--min-confidence`, `--fail-on` and `--format text|json|html`; GitHub Actions workflows can also use `--format github` for escaped error, warning and notice annotations.

### Check files and references

```bash
sift-lint check examples/orders_messy.csv --key order_id
sift-lint check data/ --format json --output report.json
sift-lint check orders.csv --references customers.csv:customer_id=id
```

The last command verifies that nonempty `orders.customer_id` values exist in `customers.id`. In general, use `--references FILE:LOCAL=FOREIGN` for explicit cross-file checks. A multi-file check keeps the available results even when one source cannot be read; unreadable files cause exit code 2.

### Inspect every Excel sheet

By default, Sift checks the first worksheet, or the one selected using `--sheet NAME`. To inspect all sheets, including hidden sheets:

```bash
sift-lint check workbook.xlsx --all-sheets
sift-lint profile workbook.xlsx --all-sheets
sift-lint check workbook.xlsx --all-sheets --format json --output report.json
```

Each sheet receives separate findings. Empty or unreadable sheets are reported explicitly. Workbook-wide analysis is read-only; `fix`, `diff`, `init` and `impact` still use one selected worksheet at a time. Formula-containing Excel sheets are not exported by `fix`, since a CSV export could discard the formulas. Excel support requires the `excel` or `all` extra.

### Preview repairs before writing

```bash
sift-lint fix examples/orders_messy.csv --dry-run --format json
sift-lint fix examples/orders_messy.csv --dry-run --explain
sift-lint fix examples/orders_messy.csv --output clean.csv --log changes.csv
```

Repairs are conservative: ambiguous dates or numeric conventions, unsafe CSV formula-like values, and formula-bearing Excel sheets can block edits. The JSON preview lists proposed changes, rules, row references and refusals without raw cell values by default. `--show-values` is an explicit opt-in for non-sensitive fields and should be used only on trusted data. Real repairs write a separate output, and `--log` is optional. For repair rules and safety constraints, see the [Project Guide](https://github.com/N0tDeb/sift/blob/main/docs/PROJECT_GUIDE.md).

### Compare against named baselines

```bash
sift-lint baseline save daily-reference examples/orders_baseline.csv
sift-lint baseline list
sift-lint baseline verify daily-reference
sift-lint diff daily-reference examples/orders_messy.csv --saved
```

Named baselines are optional; ordinary `diff BASELINE CURRENT` is unchanged. **Saved baselines contain full, unencrypted copies of the original source**, potentially including sensitive values. They are stored under `.sift/baselines/` by default, ignored by Git and protected by a SHA-256 integrity check, but the hash does not provide encryption or authenticity. Restrict access and never commit or share baseline snapshots. See the [Project Guide](https://github.com/N0tDeb/sift/blob/main/docs/PROJECT_GUIDE.md) for storage overrides and deletion instructions.

### Quantify impact

```bash
sift-lint impact examples/orders_messy.csv --sum order_total --group-by region
sift-lint impact examples/orders_messy.csv --avg order_total --min order_total --max order_total
```

Impact compares strict numeric coercion with the supported repair plan, making missing numeric observations visible. Aggregate differences are estimates conditioned on the input's evidence; Sift refuses to calculate them when numeric conventions cannot be resolved. `--mean` is an alias for `--avg`.

## Findings and limits

The controlled benchmark detects **28 of 28 planted corruptions**, with **zero findings in its clean control**. It is not a general estimate of recall or an assurance that every real-world defect will be detected. The [benchmark](https://github.com/N0tDeb/sift/blob/main/BENCHMARK.md), [public-dataset evaluation](https://github.com/N0tDeb/sift/blob/main/REAL-DATA.md) and [bounded performance observations](https://github.com/N0tDeb/sift/blob/main/docs/PERFORMANCE.md) describe the evidence and limitations.

Sift reads and profiles data in memory, so memory use can substantially exceed file size. Sensitive-field detection is heuristic; review files and reports before sharing them. Multi-output operations are not a single filesystem transaction. Running Sift is not a substitute for domain-specific validation or a trusted source of truth. See [SECURITY.md](https://github.com/N0tDeb/sift/blob/main/SECURITY.md) for security considerations.

## Version and documentation

Package managers install the latest version published to [PyPI](https://pypi.org/project/sift-lint/), which may differ from the current GitHub source. Run `sift-lint --version` to check your installed version. Sift 1.1.0 adds workbook-wide checks, column rules, named baselines, richer repair previews, impact aggregates and CI annotations. Some malformed inputs and unsafe repairs are now rejected rather than accepted; see the [1.1.0 upgrade notes](https://github.com/N0tDeb/sift/blob/main/CHANGELOG.md) before updating existing automation.

- [Project Guide](https://github.com/N0tDeb/sift/blob/main/docs/PROJECT_GUIDE.md) — architecture, format handling, CLI options, repair behavior and limitations
- [SETUP.md](https://github.com/N0tDeb/sift/blob/main/SETUP.md) — local development and CI
- [CONTRIBUTING.md](https://github.com/N0tDeb/sift/blob/main/CONTRIBUTING.md) — contributing and testing
- [SECURITY.md](https://github.com/N0tDeb/sift/blob/main/SECURITY.md) — security policy
- [CHANGELOG.md](https://github.com/N0tDeb/sift/blob/main/CHANGELOG.md) — releases and compatibility notes

Sift is released under the [MIT License](https://github.com/N0tDeb/sift/blob/main/LICENSE).
