# Sift

[![CI](https://github.com/USERNAME/sift/actions/workflows/ci.yml/badge.svg)](https://github.com/USERNAME/sift/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-172-brightgreen.svg)](tests/test_sift.py)
[![Benchmark](https://img.shields.io/badge/recall-28%2F28-brightgreen.svg)](BENCHMARK.md)

<!-- Record with `vhs demo/demo.tape`, then uncomment. See demo/README.md. -->
<!-- ![Sift finding problems in a CSV](demo/sift.gif) -->

**Finds the errors that don't raise errors.**

A linter for CSV, Excel and Parquet files. It looks for the problems that survive a successful load — the ones that don't raise an exception, don't show up in `df.head()`, and quietly change the number at the bottom of the report.

```
pip install -e .
sift check orders.csv
```

Run against 22 public datasets, it found a price column that sums to zero, a menu with three products split in two by a stray hyphen, and two Titanic passengers whose names have a trailing space. Details, including the three false positives that run exposed, are in [REAL-DATA.md](REAL-DATA.md).

## Why

A CSV has no types. Types appear when something reads it, and the reader guesses. Most of the time the guess is fine. When it isn't, nothing breaks:

- `03/04/2024` is the 3rd of April or the 4th of March. Both parse. Half a year of revenue moves one quarter over.
- `$1,200` loads as text, so the column is a string, so the sum is `"$1,200$980..."` or zero.
- `02134` loads as the number 2134, and the zip code is gone.
- `-999` was a placeholder for "unknown" upstream. It's now in your average.
- `North`, `north`, and `North ` are three regions in the group-by.

None of these raise an error. That's the whole problem: a file that loads is assumed to be fine. Sift's job is to say what a careful person would say after an hour of squinting at the data, in about a second.

## What it looks like

```
$ sift check examples/orders_messy.csv --key order_id
examples/orders_messy.csv  40 rows, 10 columns

error   key-not-unique  order_id
        Key column 'order_id' repeats 2 values. Joining on it will multiply rows.
        e.g. A-1033, A-1036

error   ambiguous-dates  order_date
        'order_date' can be read as either day-first or month-first: 12 rows valid
        both ways, and others that are not. The file does not say which was meant,
        so half your dates may land in the wrong month.
        e.g. 07/05/2024, 07/01/2024, 04/04/2024

error   mixed-types  quantity
        'quantity' is 2.5% non-numeric (1 row). A typed loader will fall back to
        text for the whole column, or drop these rows.
        e.g. twelve

warning number-as-text  order_total
        'order_total' holds numbers written with currency symbols and thousands
        separators. It will load as text and silently break any arithmetic.
        e.g. $465.46, $1,065.11, $2,443.74

warning leading-zeros  zip
        'zip' is all digits and 14 values start with a zero. Reading it as a
        number destroys them — keep it text.
        e.g. 02134, 07094, 02139

24 findings: 6 error, 14 warning, 4 info
```

`--format html` writes a standalone report you can send to whoever owns the data — [sample-report.html](sample-report.html) is one, generated from the example file. `--format json` is for everything else.

Every finding has a stable code. [CODES.md](CODES.md) lists all 61 of them with severity and meaning; a test keeps that list from drifting out of date.

## Commands

| | |
|---|---|
| `sift check FILE...` | Lint one file, several, or a whole directory. |
| `sift diff BASELINE FILE` | Compare against a known-good file: dropped columns, type changes, new categories, null-rate jumps, distribution shifts. |
| `sift fix FILE --output clean.csv` | Write a repaired copy plus an audit log of every cell changed — sensitive before/after values are redacted — and a list of what it refused to touch. |
| `sift impact FILE --sum COL --group-by COL` | Quantify how wrong one specific aggregate is, in the units of the aggregate. |
| `sift init FILE` | Learn a `sift.toml` from a file you already trust, with the reason for every exemption written down. |
| `sift profile FILE` | Describe every column — type, null rate, cardinality, range. No judgement. |

Useful flags: `--min-confidence high|medium`, `--dry-run`, `--key ORDER_ID` (must be unique and present), `--format text|json|html`, `--output PATH`, `--fail-on error|warning|info|none`, `--ignore CODE`.

## Other formats, and whole folders

CSV needs nothing installed. Excel and Parquet are optional extras, so the common path stays dependency-free:

```bash
pip install 'sift-csv[excel]'     # .xlsx, .xlsm
pip install 'sift-csv[parquet]'   # .parquet
```

There is a tension worth naming here. Sift's method is to read every value as text and infer the type from evidence, because a CSV has no types and the reader's guess is what goes wrong. Excel and Parquet *do* carry types — so should Sift just trust them?

For Parquet, yes. Its schema was written deliberately by a program, so Sift reports what it says and checks the values against it rather than re-deriving types from strings. The interesting question becomes whether the declaration still fits:

```
warning loose-schema  amount
        'amount' is declared as string but every value in it is a number.
        Anything reading this by schema will sort it alphabetically, so 10
        comes before 9.
```

For Excel, no. Excel's types were assigned by a spreadsheet, mostly by accident, to data a person pasted in. They are not a declaration — they are a record of what a spreadsheet decided to do, which makes them evidence about the damage rather than a reason to skip looking for it. So Sift reports on the typing itself:

```
error   partial-excel-dates  when
        'when' holds real dates in 4 of 5 cells; the rest are text that looks
        the same on screen. Sorting or filtering this column will not do what
        it appears to do.

error   formula-error  total
        'total' contains 1 broken formula result (#REF!, #N/A and friends).
        These export as literal text and will never parse as anything.

warning preamble-rows  file
        Skipped 2 rows above the header — a title or spacer written for a
        human reader. Any tool that does not skip them will read them as data.

info    unread-sheets  file
        Read sheet 'Orders'. This workbook has 1 other sheet that nothing
        checked: 'Notes'
```

### A folder at a time

`sift check` takes several paths, or a directory it will search:

```
$ sift check data/
data/day1.csv        5 error, 14 warning, 3 info
data/day2.csv        clean
data/orders.parquet  3 warning, 1 info
data/orders.xlsx     3 error, 3 warning, 2 info
data/titanic.csv     2 error, 2 warning, 2 info

Across files:
  missing-values             4 of 5 files
  mixed-types                3 of 5 files
  label-variants             3 of 5 files
```

The last section is the point. A pipeline that writes a file a day puts the same defect in every one of them, and *"number-as-text in 11 of 12 files"* points at the export that produced them rather than at twelve separate cleanup jobs.

## Fixing, and refusing to fix

Every other tool in this space validates and stops. Sift will repair the damage it can *derive from the file itself*, and refuses the rest out loud.

```
$ sift fix orders.csv --min-confidence medium --output clean.csv --log changes.csv
orders.csv to clean.csv

76 cells changed
  plain-number         38  high    order_total
  canonical-label      20  medium  customer, region
  trim-whitespace       9  high    customer, notes, region
  blank-null            6  high    region
  sentinel-null         2  medium  order_total
  collapse-spaces       1  medium  notes

Left alone (3):
  order_date [iso-date]
    12 rows can only be day-first and 9 can only be month-first, so no single
    reading fits. The column holds two formats mixed together and cannot be
    repaired without knowing which rows came from where.
  quantity [mixed-types]
    1 value does not parse as numeric ('twelve'). Whether those rows are typos,
    notes, or a second unit is a question about the world, not about the file.
  notes [mojibake]
    Characters were mangled by a bad encoding round-trip. The original bytes are
    gone, so any repair here is invention. Re-export the source as UTF-8.

Findings: 24 before, 13 after. 3 left for a human.
```

Stripping `$` from `$1,200` is derivable: the number was always 1200 and the formatting was decoration. Choosing between day-first and month-first when nothing in the column proves either is not derivable, and no confidence setting will make Sift do it.

**High confidence** is mechanical and meaning-preserving: trim whitespace, blank out `N/A`, strip currency and thousands separators, normalise dates *when the column proves its own format*. **Medium** adds judgement calls: folding `north`/`North ` to one spelling, turning repeated `-999` placeholders into nulls. A rule applies to a whole column or not at all — half-applying one leaves the column in a state that is neither the original nor the repair.

Three design decisions worth the words:

- **Numbers are edited as text, never round-tripped through `float()`.** Reformatting would change how many decimals the file had. The parse is used only to confirm the edit didn't change the value.
- **`--log` writes every changed cell** with its line number, before, after, and the rule responsible. Replaying that log against the original reproduces the output exactly — there's a test for it. A cleaned file you can't diff against the original is just a different unverified file.
- **`fix` refuses unsafe rewrites by default.** Ragged rows get padded or truncated on read, so rewriting would make that loss permanent and invisible. Columns containing spreadsheet-formula-risk values (`=`, `+` or `@`) are also left unchanged, and `fix` will not write an output while that risk remains. `--dry-run` previews the refusal; `--force` explicitly accepts the unresolved risk.

Running `fix` twice changes nothing the second time. Also a test.

## Blast radius

A finding is a claim about a file. `sift impact` answers the question the reader actually has: *how wrong is my number?*

```
$ sift impact orders.csv --sum order_total --group-by region
orders.csv  40 rows

SUM(order_total)
  as it loads         not a number — the column reads as text, so a loader drops
                      every value it cannot parse: 2 of 40 rows survive.
  coerced                    -1,998   what errors='coerce' would give you
  after sift fix       1,078,019.98   from 38 of 40 rows
  difference           1,080,017.98 (+100.2%)

      38 rows  formatted numbers recovered that a loader drops  +1,078,019.98
       2 rows  placeholder values removed that a loader counts as real  +1,998.00

GROUP BY region, SUM(order_total)
  7 groups today, 4 after repair.
  1,019,307.31 sits under a label that is a spelling of another (94.6% of the total):
    'NORTH ', 'north' belongs to 'North'  1,019,307.31

DUPLICATE ROWS
  1 row repeated exactly, inflating SUM(order_total) by 2,171.02.
```

The headline number there is real and worth staring at: a `SUM` over this file returns **-1,998** — the only two values `float()` can read are the `-999` placeholders. Everything else is `$1,065.11` and gets dropped. No exception is raised. The dashboard just shows a negative total.

Nothing in this command is a heuristic. It computes the same aggregate three ways over the same file — as a typed loader reads it, with unparseable values coerced away, and over the repaired copy — and the causes it lists are a decomposition of the difference, not a gesture at it. They add up exactly, and a test enforces that they do.

## The dot and the comma

Half the world writes `1,234.56` and half writes `1.234,56`. Read a German file with English convention and `12.500,00` — twelve and a half thousand — becomes `12.5`. **A thousandfold error, no exception raised.** It is the most expensive mistake in this tool's remit and the easiest to miss, because nothing about the output looks wrong.

Sift reads every number under both conventions and lets the column decide, the same way it settles date order:

```
warning european-numbers  betrag
        'betrag' uses a comma for the decimal point and a dot for thousands.
        Any reader assuming English convention will misread these by a factor
        of a thousand, without erroring.
```

The evidence comes in two strengths, and the distinction matters. `1.234,56` can *only* be read the European way — one such value settles the whole column. `1,200` is weaker: it is grouped thousands in English and `1.2` in European, and neither is impossible. Treating weak evidence as decisive would flag every ordinary `$1,200` as a locale error, so grouping only counts when nothing stronger exists. A column with strong evidence in both directions is reported as an error rather than resolved — it holds two formats mixed together, and one of them is off by a thousand.

`sift fix` normalises European numbers to `1234.56` when the convention is settled, and refuses when it is not:

```
  v [plain-number]
    2 values like "1.234" mean one thing under English convention and a
    thousand times more under European convention, and nothing else in the
    column settles which was meant.
```

## Confidence, not booleans

"Ambiguous or not" is the wrong output for a date column. A column where 30 rows can only be day-first and 10 could go either way isn't ambiguous — it's day-first, and the 10 will follow. A column with one day-first row and one month-first row isn't ambiguous either; it's corrupt, and no single reading saves it. Only a column with no decisive rows at all is genuinely undecidable.

Sift reports those as three different findings with three different remedies, and `infer_date_order` returns the evidence behind the call:

```python
>>> reading = infer_date_order(profile)
>>> reading.order, reading.label, round(reading.confidence, 2)
('dmy', 'certain', 0.57)
>>> reading.reason
'12 of 21 dates can only be read as day-first; the remaining 9 are valid both
 ways and would follow that reading.'
```

## In CI

Exit codes are the contract:

| code | meaning |
|---|---|
| 0 | nothing at or above the failure threshold |
| 1 | findings at or above it |
| 2 | the file could not be read at all |

A broken pipeline should never look like a clean file, which is why those last two are different numbers.

```yaml
- name: Check the daily export
  run: |
    sift check data/orders.csv --key order_id
    sift diff data/orders.baseline.csv data/orders.csv --fail-on warning
```

Committing a baseline file next to the data turns `diff` into a regression test for the shape of your data, not just its contents.

## Checks

**Structure** — ragged rows (unescaped delimiters, unclosed quotes), duplicate and unnamed column names, names differing only by case, BOM, mixed line endings, missing trailing newline.

**Numbers** — European decimal conventions (`1.234,56`), columns mixing both conventions, and values no evidence can settle.

**Types** — mixed types in one column, 8-digit values that are equally a date or a number, numbers written as text (`$`, `,`, `%`, accounting parentheses), leading zeros that a numeric read would destroy.

**Dates** — day-first/month-first ambiguity, graded by how much of the column proves its own format, mixed layouts within a column, future dates, dates before 1900, placeholder dates (`1970-01-01`, `9999-12-31`).

**Missing data** — null rates against configurable thresholds, missing values written as text (`N/A`, `unknown`, `-`), numeric sentinels (`-999`) that will be averaged as real values, empty and constant columns.

**Sensitive data** — columns holding email addresses, phone numbers, payment card numbers or IBANs, and columns *named* like sensitive fields. Once a column is classified as sensitive, other findings for that column also suppress source-value details, and repair audit logs redact its before/after values.

**Safety** — values beginning `=`, `+` or `@`, which a spreadsheet executes as a formula rather than displaying; NUL bytes; files with a header and no rows.

**Values** — case and whitespace variants of the same label, near-duplicate labels (`Acme Corp` / `Acme Crop`, gated on frequency skew — a rare misspelling of a spelling most rows agree on — so evenly-used categories like `vvs1`/`vvs2` or `male`/`female` are left alone, and identifier-shaped columns are excluded entirely), stray and non-breaking whitespace, mojibake from a bad encoding round-trip, outliers by modified z-score, negative values in columns named like quantities.

**Keys** — declared keys must be unique and complete; unique complete ID columns are suggested as candidates.

**Drift** (`sift diff`) — removed, added and reordered columns, type changes, null-rate jumps, median shifts, new and vanished categories, large row-count changes.

## Configuration

Checks are silenced by code — see [CODES.md](CODES.md) for the full list. Sift reads the nearest `sift.toml`, walking up from the file being checked.

```toml
[sift]
null_warn = 0.10
null_error = 0.50
key = ["order_id"]
fail_on = "error"
ignore = ["outliers"]

[sift.ignore_columns]
whitespace = ["notes"]
high-cardinality = ["email"]
```

Per-column silencing exists because that is what people actually need. A `notes` column is *supposed* to be free text; silencing `high-cardinality` everywhere to quiet it would hide the same signal in a column where it means an ID leaked into a category field.

## As a library

```python
from sift import lint, diff

for finding in lint("orders.csv"):
    print(finding.severity.value, finding.code, finding.column, finding.message)

changes = diff("baseline.csv", "today.csv")
```

## Design notes

**No dependencies.** Sift reads every value as text on purpose. A typed loader has already made its guesses by the time you can inspect the data, and those guesses are exactly what Sift is checking. Reading the raw strings is also what keeps it stdlib-only, which means it installs in a CI container in a second and never conflicts with the pandas version the project already pins.

**Median and MAD, not mean and standard deviation.** One bad row should not move the yardstick used to find bad rows. A single `999999` inflates a standard deviation enough to hide itself. Statistics are skipped entirely on columns that failed the type check, and the outlier report goes quiet when more than 5% of a column would be flagged — at that point the column is skewed, not dirty.

**Every threshold is configurable and every check is silenceable.** A 20% null rate is alarming in a payments table and expected in a survey export. A linter that can't be tuned gets muted, and a muted linter catches nothing.

**Findings are worded as consequences.** Not "column contains mixed types" but "a typed loader will fall back to text for the whole column, or drop these rows." The reader has to decide whether to care, and they can only do that if they know what happens next.

## Limitations

Performance is adequate, not exceptional: roughly 29 seconds and 340 MB of memory for an 18 MB, 126,000-row file on one core, down from four minutes before the parsers were profiled. Holding every value as a Python string costs roughly 20x the file size in memory, which is the price of reading everything as text. Files in the tens of megabytes are comfortable; gigabytes are not what this is for.

Heuristics, not proofs. Sift can tell you a date column is undecidable; it cannot tell you which reading is right when the file contains no evidence either way — that lives in a system it can't see, which is exactly why `fix` refuses the case rather than guessing. It reads the whole file into memory, so it is built for the megabyte-to-hundreds-of-megabytes range that these files actually live in, not for warehouse-scale data. Column-name heuristics (`quantity` implies non-negative) are English-only, and number parsing covers the comma and dot conventions but not space-grouped forms like `1 234,56`. Excel support reads one sheet at a time and names the others rather than checking them, and the old binary `.xls` format is not supported at all. And while `check` will take a whole folder, cross-file referential integrity — a key in one file resolving against another — is not implemented.

## Measuring accuracy

Three rounds of testing against real files fixed false positives, because those are the ones you can see in the output. Nothing in that process could reveal a *false negative* — a problem sitting in a file that Sift walked straight past. You cannot eyeball a miss.

`bench/` generates a clean file, verifies Sift is silent on it, then corrupts it one known way at a time. Each corruption declares the finding code that should catch it.

```
$ python -m bench.run

Recall: 28/28 (100%) of known corruptions detected.
Noise:  0 findings on the clean control file.
```

Both halves matter. Recall is the share of planted problems found; noise is findings raised against a file with nothing wrong, where the only acceptable answer is zero. It runs in CI, so a change that starts missing problems — or starts inventing them — fails the build rather than being discovered months later.

Writing it immediately paid for itself. It found a real gap: a column holding two *incompatible* date formats (some rows only day-first, others only month-first) is the one date problem that cannot be repaired at all, and `sift fix` was correctly refusing to touch it — while `sift check` reported it as a mild "mixed layouts" warning. The checker and the fixer disagreed about how bad it was. `check` now uses the same graded inference the repair engine does, and reports `conflicting-date-formats` as an error.

Wiring that up also removed a false positive nobody had noticed: the old check flagged a column as ambiguous whenever *any* row could be read two ways, even when another row in the same column settled the question. `04/03/2024` alongside `25/12/2024` is not ambiguous — the second row proves day-first, and the first follows.

## Tests

```
pip install -e ".[dev]"
pytest -q
```

172 tests. The important ones are `test_clean_file_is_quiet`, `test_every_change_is_in_the_audit_log`, and `test_fixing_twice_changes_nothing_the_second_time`, and `test_causes_add_up_to_the_difference`. Every check is easy to fire on broken data; the hard part is not firing on data that's fine, because a linter with false positives gets switched off. And a fixer you can't audit or re-run safely is worse than no fixer.

## Contributing

Issues and pull requests are welcome. Before opening a PR:

```bash
pip install -e ".[dev]"
pytest -q            # 172 tests
ruff check .
python -m bench.run  # recall against known corruptions
```

Three of those tests exist to stop documentation drifting: a new finding code fails the build unless it is listed in [CODES.md](CODES.md) *and* covered by a test or a benchmark case, and a new subcommand fails the build unless the README mentions it. If one of them fails, it is telling you something real.

New checks are judged on one standard above all others: not firing on data that is fine. `bench/` exists to measure that — add a corruption case for anything you add, and run the check against real files before trusting it. Every false positive erodes confidence in every other finding.

## License

MIT — see [LICENSE](LICENSE).
