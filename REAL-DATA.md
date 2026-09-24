# Sift against real data

Sift was run against four public datasets that other people published and other people use. Nothing here is synthetic, and nothing was tuned to make the output look good — the fixes described at the bottom went the other way, and made Sift quieter.

Reproduce any of it:

```bash
curl -O https://raw.githubusercontent.com/justmarkham/DAT8/master/data/chipotle.tsv
sift check chipotle.tsv
sift impact chipotle.tsv --sum item_price --group-by item_name
```

## Chipotle orders (4,622 rows)

`justmarkham/DAT8` — a dataset used in a widely-taught Python course.

```
SUM(item_price)
  as it loads         not a number — the column reads as text, so a loader drops
                      every value it cannot parse: 0 of 4,622 rows survive.
  coerced                         0   what errors='coerce' would give you
  after sift fix          34,500.16   from 4,622 of 4,622 rows

GROUP BY item_name, SUM(item_price)
  50 groups today, 47 after repair.
    'Chips and Tomatillo-Green Chili Salsa' belongs to 'Chips and Tomatillo Green Chili Salsa'
    'Chips and Tomatillo-Red Chili Salsa'   belongs to 'Chips and Tomatillo Red Chili Salsa'
    'Chips and Roasted Chili-Corn Salsa'    belongs to 'Chips and Roasted Chili Corn Salsa'

DUPLICATE ROWS
  59 rows repeated exactly, inflating SUM(item_price) by 322.91.
```

Every price is written `$2.39 ` — a currency symbol and a trailing space — so a naive `sum()` over the column returns **zero**, from zero surviving rows. The menu has 50 items in it and 47 real ones: three products are split in two by a hyphen that is sometimes a space. And 59 rows are exact duplicates.

## Titanic (891 rows)

`datasciencedojo/datasets` — probably the most-used teaching CSV in existence.

- `Cabin` is 77.1% empty.
- `Age` is 19.9% empty.
- `Ticket` is 25.8% non-numeric (`A/5 21171`, `STON/O2. 3101282`), so the column loads as text or drops a quarter of its rows depending on the reader.
- Two passenger names carry a trailing space: `'Hewlett, Mrs. (Mary D Kingcome) '`, `'Daly, Mr. Peter Denis '`. Any join or group-by on name silently treats those two as different people from themselves.

## Consumer complaints (28,156 rows)

`plotly/datasets` — a CFPB export.

- Column 1 has no name at all.
- `Consumer disputed?` is 78.7% empty; `Sub-issue` is 53.1% empty.
- 38 company names contain doubled internal spaces (`'NetSpend Corporation,  a TSYS Company'`), which splits those companies across two groups in any aggregate.

## World GDP (13,979 rows)

`datasets/gdp` — a curated Frictionless Data package.

One finding: no newline at the end of the file. It is a genuinely clean dataset, and Sift saying almost nothing about it is the result that matters most here. A linter that finds something wrong with everything is a linter nobody runs twice.

## Round two: a wider, more diverse batch

The first round used four datasets and found three bugs. A second, more deliberately varied batch — diamonds, restaurant tips, weather, stock prices, college majors, salaries, shampoo sales, daily temperatures — was run specifically to hunt for more, on the theory that variety finds bugs that more of the same data won't.

It found four more, all in the same check: `near-duplicate-labels`.

- **Diamond clarity grades.** `vvs1` and `vvs2` are one character apart and were reported as a likely typo. They are a defined, ordinal grading scale — evenly used, not a mistake.
- **Sex.** `male` / `female` share a four-letter suffix and were flagged the same way. Two categories, not a typo.
- **College majors.** `biology`, `ecology`, `zoology` — each one row in a 173-row list of majors — were cross-flagged against each other.
- **A missing date format.** Stock prices used `Jan 1 2000` — no comma. That format wasn't in the parser, so the whole column fell through to plain text, which is what let `apr 1 2000` get compared against `apr 1 2001` in the first place. Fixing the date parser fixed the symptom at its root; the near-duplicate rule was innocent here.

Once those were fixed, a second look at the first four datasets found one more: Titanic's `Cabin` column (`B101` / `E101`, each held by one or two passengers) was still catching false positives, because the first fix didn't go far enough.

## What Sift got wrong

Running against real files found three false positives. All three are fixed, and each fix made the tool quieter rather than louder.

**Typos that were not typos.** On the Chipotle menu, `chips and tomatillo green chili salsa` and `chips and tomatillo red chili salsa` were reported as probable misspellings of each other. They score 0.93 on a similarity ratio because they share 35 characters — but they are two different products. Similarity ratios reward length, which is exactly backwards for typo detection. Sift now counts the characters in the differing spans instead, and flags a pair only when two or fewer characters actually differ. `acme corp` / `acme crop` still fires; the salsas no longer do.

**Statistics over a column that has no consistent type.** Titanic's `Ticket` is 74% numeric and 26% strings. Sift computed a median over the numeric part and reported 16 "outliers" — which were just ticket numbers. Statistical checks now skip any column that already failed the type check, because a summary of a subset the author never intended is not a summary of anything.

**"Outliers" that were a skewed distribution.** On the GDP data, Sift flagged 3,798 of 13,979 values as far from the median. Twenty-seven percent of a column is not an outlier report; it means the column is exponentially distributed and the median is not a useful reference point. Sift now stays silent when more than 5% of a column would be flagged, configurable through `outlier_share`.

The general lesson is the one in the README: it is easy to make a check fire on broken data, and the whole difficulty is not firing on data that is fine.


## Round three: the batch that found a real bug and a real bottleneck

A third batch — airline safety, traffic fatalities, congressional terms, COVID case counts, world population, Gapminder, Boston housing, wind speed, air passengers, daily weather — was run the same way. Two of those files are clean enough that Sift says nothing at all about them, which is the right answer.

### The worst bug found so far

Three separate datasets — COVID case counts, world population, and Gapminder — were reported as having `mixed-types` **errors** in columns that are entirely, obviously numeric:

```
error   mixed-types  Confirmed
        'Confirmed' is 0.0% non-numeric (70 rows).
        e.g. 11620823, 13491107, 14871208
```

Those are numbers. The message even renders the contradiction: *0.0% non-numeric, 70 rows*.

The cause: `%Y%m%d` matches any 8-digit run whose middle digits look like a month and a day. A population of **11,620,823** parses cleanly as the date **1162-08-23**. Those rows were classified as dates inside an otherwise-numeric column, which made them type offenders, which produced a false error on real published data.

Two guards fixed it. A bare 8-digit run now only reads as a date if the year lands between 1900 and 2100, and even then the column decides: 8-digit values become dates only if something *else* in that column is unambiguously a date. A column that is entirely 8-digit values is genuinely undecidable — `20240115` is equally a date and a number — so Sift reads it as a number and says so, rather than guessing silently:

```
info    compact-date-or-number  dt
        Every value in 'dt' is 8 digits, which reads equally well as a YYYYMMDD
        date or as a plain number. Sift treated it as a number; nothing in the
        file settles it either way.
```

### Two smaller ones

**A congressman named Nan.** `congress-terms.csv` has a member whose first name is Nan, which Sift reported as a missing value written as text — because `nan` is in the null-token list. Now only the spellings a machine produces (`nan`, `NaN`, `NAN`) count as null; `Nan` is a person.

**"0.0% non-numeric (70 rows)".** A share that rounds to zero against a non-zero count reads like a bug in the tool rather than a fact about the data. Small shares now render as `<0.1%`.

### And a four-minute file

The largest file in the corpus — an 18 MB, 126,000-row NBA dataset — took **239 seconds**. Profiling put 143 of the first 149 seconds inside `parse_dates`: every value in the file was being tried against all 18 date formats, and `strptime` rebuilds and recompiles its own regex on every call, so 9 million of them dominated the run.

Three changes, none of which alter a single result:

1. **A cheap gate before the expensive part.** A team name has no digits, a score is too short, and a long run of digits could only ever match the compact format. Rejecting those before any parsing removes most of the work.
2. **A shape regex per format.** A compiled pattern decides whether a format could match, so `strptime` is called once or twice per value instead of failing 18 times.
3. **Memoisation.** Columns repeat themselves; distinct values are parsed once.

**239s to 28s, an 8.5x speedup, with byte-identical output.** Two tests hold the line: one asserts the prefilter returns exactly what unfiltered `strptime` would have returned across a set of awkward inputs, and one fails if parsing slows back down.

The lesson generalises past this project: the bug and the bottleneck were the same function. `parse_dates` was doing too much work *and* being too credulous about what counts as a date, and fixing the second is what exposed the first.
