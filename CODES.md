# Finding codes

Every code Sift can emit. These strings are the stable interface — they appear in `--format json`, and they are what you name to silence a check:

```toml
[sift]
ignore = ["outliers"]

[sift.ignore_columns]
high-cardinality = ["email"]
```

Silencing per column is almost always better than silencing globally: a `notes` column is *supposed* to be free text, but the same finding on `customer_id` means an identifier leaked into a category field.

`sift init` writes these exemptions for you, with the reason attached.

A test asserts this file lists exactly the codes the source can emit, so it cannot drift out of date.

## Structure

| Code | Severity | Meaning |
| --- | --- | --- |
| `ragged-rows` | error | Rows without the expected number of fields — usually an unescaped delimiter or an unclosed quote. |
| `no-data-rows` | error | A header and nothing under it. Readers get an empty result rather than an error. |
| `null-bytes` | error | NUL bytes in a text file — binary content with the wrong extension, or a truncated write. |
| `encoding` | error | The file is not valid UTF-8. Read as Latin-1 to continue. |
| `duplicate-column-name` | error | The same column name appears twice; most readers silently keep one. |
| `bom` | warning | A UTF-8 byte order mark that some readers fold into the first column name. |
| `mixed-line-endings` | warning | CRLF and LF in the same file, which makes diffs and checksums noisy. |
| `unnamed-column` | warning | A column with no name in the header. |
| `padded-column-name` | warning | A header with surrounding whitespace, so lookups by name miss it. |
| `similar-column-names` | warning | Two headers differing only by case or spacing. |
| `no-trailing-newline` | info | Concatenating this file with another would join two rows into one. |

## Types and numbers

| Code | Severity | Meaning |
| --- | --- | --- |
| `mixed-types` | error | A column that is mostly numbers or dates, with values that are neither. |
| `conflicting-number-formats` | error | The column mixes English and European decimal conventions; one of them is wrong by a thousand. |
| `ambiguous-decimal-separator` | error | Values like `1.234` differ by 1000x between conventions and nothing settles which was meant. |
| `european-numbers` | warning | Comma decimal, dot thousands. An English-locale reader misreads these silently. |
| `number-as-text` | warning | Numbers written with currency symbols, separators, percent signs or accounting parentheses. |
| `leading-zeros` | warning | An all-digit column whose zeros a numeric read would destroy. |
| `numeric-sentinel` | warning | A repeated placeholder like `-999` that will be averaged as a real value. |
| `outliers` | info | Values far from the median by modified z-score. Silent on skewed columns. |
| `unexpected-negative` | info | Negative values in a column named like a quantity. |
| `compact-date-or-number` | info | Every value is 8 digits, equally readable as `YYYYMMDD` or a plain number. |

## Dates

| Code | Severity | Meaning |
| --- | --- | --- |
| `conflicting-date-formats` | error | Some rows can only be day-first and others only month-first. Unrepairable. |
| `ambiguous-dates` | error | Every value reads both ways and nothing in the column settles the order. |
| `weak-date-evidence` | warning | An order was inferred, but only a small share of the column proves it. |
| `mixed-date-formats` | warning | ISO dates mixed with slash-separated ones. |
| `future-dates` | warning | Dates after today. |
| `implausible-dates` | warning | Dates before 1900. |
| `sentinel-dates` | warning | Placeholders such as `1970-01-01` or `9999-12-31` treated as real dates by range filters. |

## Missing data

| Code | Severity | Meaning |
| --- | --- | --- |
| `missing-values` | error / warning | Null rate above the configured threshold. Error above `null_error`, warning above `null_warn`. |
| `disguised-null` | warning | Missing values written as text (`N/A`, `unknown`, `-`) so they load as strings. |
| `empty-column` | warning | A column with no values at all. |
| `constant-column` | info | One value in every row — check whether a filter upstream collapsed it. |

## Values

| Code | Severity | Meaning |
| --- | --- | --- |
| `formula-injection` | warning | Values starting with `=`, `+` or `@`, which a spreadsheet executes as a formula rather than showing as text. |
| `mojibake` | error | Text mangled by a bad encoding round-trip. Not repairable; re-export the source. |
| `label-variants` | warning | The same category spelled several ways by case or spacing. |
| `whitespace` | warning | Stray, doubled or non-breaking whitespace inside values. |
| `near-duplicate-labels` | info | A rare misspelling of a spelling the rest of the column agrees on. |
| `high-cardinality` | info | A text column that is almost entirely unique — free text or an identifier, not a category. |

## Keys and references

| Code | Severity | Meaning |
| --- | --- | --- |
| `key-not-unique` | error | A declared key repeats, so joining on it multiplies rows. |
| `key-null` | error | A declared key is empty in some rows. |
| `missing-key-column` | error | A declared key column is not in the file. |
| `orphaned-reference` | error | Values pointing at rows that do not exist in the referenced file. |
| `reference-not-unique` | error | The far side of the relationship repeats, so it cannot identify a row. |
| `reference-matches-after-cleaning` | warning | Orphans that would match if case and spacing were normalised — dirty keys, not missing records. |
| `reference-null` | warning | The referencing column is empty, so those rows join to nothing. |
| `candidate-key` | info | A unique, complete column that could be enforced with `--key`. |

## Sensitive data

Nothing here is a defect — the file is fine. These say *this file contains personal or financial data*, which matters when it is about to be emailed, committed, or fed into a model. **No finding in this group ever includes example values**: a tool that prints someone's card number into a CI log has made the problem worse.

| Code | Severity | Meaning |
| --- | --- | --- |
| `sensitive-data` | warning | A column holds email addresses, phone numbers, payment card numbers or IBANs. Cards require an issuer prefix, a valid length and a Luhn check; phone numbers require an international prefix, or a local shape in a column named like a phone field. |
| `sensitive-column-name` | info | A column is *named* like a sensitive field (`ssn`, `date_of_birth`, `iban`). The values were not inspected. |

## Excel and Parquet

| Code | Severity | Meaning |
| --- | --- | --- |
| `formula-error` | error | `#REF!`, `#N/A` and similar exported as literal text. |
| `partial-excel-dates` | error | Some cells are real dates and the rest are text that looks identical on screen. |
| `preamble-rows` | warning | Title or spacer rows above the header, written for a human reader. |
| `loose-schema` | warning | A Parquet column declared as string whose values are all numbers. |
| `unread-sheets` | info | Other worksheets in the workbook that nothing checked. |

## Drift, from `sift diff`

| Code | Severity | Meaning |
| --- | --- | --- |
| `column-removed` | error | A baseline column is gone. |
| `type-changed` | error | A column's inferred type changed between the two files. |
| `new-category` | warning | Categories not present in the baseline. |
| `null-rate-jump` | warning | A column became substantially emptier. |
| `distribution-shift` | warning | The median moved enough to suggest a unit change. |
| `column-order-changed` | warning | Harmless for named access, fatal for anything reading by position. |
| `row-count-shift` | warning | The file grew or shrank dramatically. |
| `column-added` | info | A column not present in the baseline. |
| `missing-category` | info | Baseline categories that no longer appear. |
| `duplicate-rows` | warning | Exact repeated rows, which inflate any total. |
