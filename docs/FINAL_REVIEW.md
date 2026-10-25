# Historical hardening review — Batch 5 source candidate

These are **historical findings from the Batch 5/6 audit**, not a certification
of the current branch.
For the current release process see [the release checklist](RELEASE_CHECKLIST.md).
The findings describe an **unreleased branch candidate**. Passing tests is
not proof that the software is defect-free, and nothing here authorizes a merge,
GitHub release, or PyPI publication.

## Additional correctness review

The final pass reproduced two linked bugs in the Batch 4 source:

- An input header of `v,v,v#1` assigned the generated `v#1` key to the second
  column as well as the literal `v#1` third column. A lookup of `v#1` returned
  the wrong one. The fix reserves every literal header name before generating
  distinct keys. CSV, Excel, and Parquet adapters now share the policy.
- When a baseline had headers `v,v` and its successor had `v`, drift comparison
  reported a column-order change instead of the removed occurrence. It now
  compares header **counts** and corresponding duplicate occurrences. A
  correctly reordered set of duplicate columns is still flagged as reordered.

Tests cover exact key resolution, repeated headers, genuine reorders, changes
only to the second duplicate, CSV and Excel; a small Arrow API stand-in checks
position-based schema lookup. Real Parquet integration still belongs to CI.

## Bounded runtime and memory observations

**Purpose:** characterize the current design, not set a supported maximum size.
Measurements ran on an ephemeral Linux x86-64 Python 3.13.5 environment using
plain generated five-column CSVs, without external services or optional format
readers. This workload is narrow; files with free text, dates, many distinct
categories, or many columns can behave differently.

For each input, the command was `python -m sift_lint.cli check FILE --no-config
--fail-on none --format json --output REPORT`; results were discarded after
each run. Linux `/usr/bin/time -f 'seconds=%e rssKiB=%M'` measured process
wall time and peak resident set size (RSS). Batch 4 was tested once and the
Batch 5 candidate twice. These measurements include Python startup and import
cost. Hardware resources may vary between executions.

| Data rows | CSV bytes | Batch 4 time | Batch 5 times | Batch 5 peak RSS |
|---:|---:|---:|---:|---:|
| 10,000 | 270,670 | 0.99 s | 1.05 / 1.00 s | 110,868 / 110,860 KiB |
| 50,000 | 1,397,795 | 2.28 s | 2.32 / 2.26 s | 160,808 / 161,036 KiB |
| 150,000 | 4,265,615 | 5.94 s | 6.03 / 5.83 s | 278,692 / 278,100 KiB |

No material slowdown is evident at these sizes; these are **not statistical
performance claims**. Memory grows considerably beyond input bytes due to
Python objects, profiling, source tables, and other process overhead. The
results are insufficient to promise that any particular 100 MB or 1 GB file
will work within a fixed memory budget. No streaming rewrite is proposed
without a user-specified target and representative benchmarks.

### Reproducing the synthetic input

From the repository root, generate fixtures **outside** the tracked source
(tree below uses a temporary directory). The same generator was used for the
measurements above; it has no third-party dependencies.

```bash
python - <<'PY'
from pathlib import Path
from tempfile import gettempdir
root = Path(gettempdir()) / 'sift_perf_review'
root.mkdir(exist_ok=True)
for n in (10_000, 50_000, 150_000):
    path = root / f'example_{n}.csv'
    with path.open('w', encoding='utf-8', newline='') as handle:
        handle.write('id,region,qty,price,status\n')
        for i in range(n):
            region = ('north', 'south', 'east', 'west')[i % 4]
            status = ('active', 'pending', 'closed')[i % 3]
            handle.write(f'{i+1},{region},{i%80+1},{i%199+1}.50,{status}\n')
    print(path, path.stat().st_size)
PY
```

Measure wall time and memory using your OS's preferred process-monitoring
tools; do not compare results across machines as if they were controlled trials.

## Verification boundaries / release gate

Local checks for this batch: Python compilation; all available tests; the
28-case synthetic corruption benchmark and clean control; targeted CSV/Excel
regressions. Optional PyArrow could not be installed locally. Ruff and
wheel/sdist builds could not be run locally because external package access
was unavailable. **Batch 5 later passed all 10 GitHub CI jobs at commit
`f1d394e5bdeb3271f0fcda34a0d94b68c594864f` (workflow `40175650994`).**

Before declaring the branch merge-ready, verify all 10 GitHub CI jobs (six
test/benchmark jobs, three package jobs, one data-gate), inspect any failures,
and confirm that the source matches the intended PR commit. Do not merge merely
because previous batch commits were green. Review documentation, package
metadata and change scope again after CI.

A future release additionally needs a deliberate version/tag choice, release
notes, installed-artifact verification, and explicit authorization to publish.
Published PyPI `1.0.0` artifacts do not change when the GitHub branch changes.
The original personal/private master archive remains outside this workflow.

Known design limits remain: formula-containing Excel sheets are not exported
by `fix`; cross-file output writes are individually atomic rather than a
single transaction; sensitive-value detection is heuristic; in-memory tables
constrain practical scale; and the controlled 28-case benchmark says nothing
about universal recall. New feature ideas belong on a separate roadmap.

## Follow-up independent audit and corrections (Batch 6)

An additional audit after the five original green batches reproduced sensitive
baseline category leakage, sensitive foreign-reference example leakage,
normalization of password-like values, duplicate sensitive-name findings,
wrong post-repair detection of custom separators, and an inaccurately labeled
TSV output. Batch 6 adds focused fixes and regressions for these cases, raises
the optional PyArrow minimum beyond its known vulnerable 14.0.0 release, and
suppresses median-shift statistics for obviously identifier-shaped columns.
These changes require their own GitHub CI verification on the precise new commit.
Existing findings about optional dependencies, in-memory scale, cross-file
transaction limitations, and heuristic privacy detection remain in force.
