# Sift against real data

Sift was run against four public datasets other people published and other
people use: Titanic, a Chipotle orders export, world GDP, and a CFPB consumer
complaints file.

## Chipotle orders

Every price is written `$2.39 ` — a currency symbol and a trailing space — so
a naive sum over the column returns zero, from zero surviving rows.

## Titanic

Two passenger names carry a trailing space, so a join on name treats those
two people as different from themselves. `Ticket` is 26% non-numeric.

## What Sift got wrong

Running against real files found a false positive: near-duplicate-labels
flagged `chips and tomatillo green chili salsa` and the red variant as a
likely typo, because they share 35 characters and score 0.93 on a similarity
ratio. They are two different products.

Fix underway.

## Fixed

Character similarity was the wrong signal. A typo is one or two characters
wrong regardless of label length; a similarity *ratio* rewards length instead,
which is exactly backwards. `near-duplicate-labels` now counts the characters
in the differing spans and requires two or fewer. Long labels that differ by
a whole word are left alone; `Acme Corp` / `Acme Crop` still fires.

Two more real bugs found in this round: statistics computed over Titanic's
`Ticket` column (74% numeric, 26% strings) reported ticket numbers as
outliers, and world GDP data — exponentially distributed — had 3,798 of
13,979 rows flagged as "extreme". Both fixed: statistics now skip any column
that already failed the type check, and the outlier report goes quiet above
a configurable share of the column.

## Round 2: a wider batch found the real signal was missing

Diamond clarity grades (`vvs1`/`vvs2`), a sex column (`male`/`female`), and
academic majors (`biology`/`ecology`) all fired the same check — one or two
characters apart, so "typo" by the edit-distance rule alone. None of them
are. Character distance answers "do these look alike"; what actually
separates a typo from a category is frequency skew, so the check now
requires a minority spelling that is both rare and clearly the exception
against a spelling with real support.

## Round 3: the worst bug so far, and a four-minute file

Three datasets — COVID case counts, world population, Gapminder — reported
`mixed-types` errors on plainly numeric columns. A population of 11,620,823
parses as the date 1162-08-23, because `%Y%m%d` matches any 8-digit run with
a plausible-looking middle. Fixed with a year-range guard and column-level
resolution: an all-8-digit column is genuinely undecidable and reads as a
number.

Profiling the same file — an 18 MB, 126,000-row NBA dataset — found it took
**239 seconds**. 143 of the first 149 were inside `parse_dates`: every value
tried against 18 formats, and `strptime` rebuilds its own regex on every
call. A cheap pre-filter plus a compiled shape-regex per format plus
memoisation: **239s to 28s**, an 8.5x speedup, byte-identical output.
