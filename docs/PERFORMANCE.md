# Performance observations

These are bounded measurements of Sift's in-memory CSV analysis, not a guaranteed
throughput, maximum supported input size, or comprehensive benchmark. The
[controlled corruption benchmark](../BENCHMARK.md) measures detection on
known fixtures and is separate from runtime performance.

## Workload and environment

A synthetic, five-column CSV contained `id`, `region`, `qty`, `price` and
`status`. Measurements were collected on an ephemeral Linux x86-64 machine
with Python 3.13.5. The command was:

```bash
python -m sift_lint.cli check FILE --no-config --fail-on none --format json --output REPORT
```

Linux `/usr/bin/time -f 'seconds=%e rssKiB=%M'` measured wall-clock seconds
and peak resident set size (RSS), including interpreter startup and imports.
The measurements below were taken during an earlier development revision;
they have **not** been rerun against every subsequent release. Two runs were
recorded for each size.

| Data rows | CSV bytes | Elapsed time (run 1 / run 2) | Peak RSS (run 1 / run 2) |
| ---: | ---: | ---: | ---: |
| 10,000 | 270,670 | 1.05 / 1.00 s | 110,868 / 110,860 KiB |
| 50,000 | 1,397,795 | 2.32 / 2.26 s | 160,808 / 161,036 KiB |
| 150,000 | 4,265,615 | 6.03 / 5.83 s | 278,692 / 278,100 KiB |

These limited observations do not establish a statistically significant
speedup, a memory ceiling, or performance on wider, date-heavy or high-
cardinality files. Working-memory needs can be many times the input bytes
because Sift retains tables, profiles and analysis structures in memory.

## Reproduce the sample data

From the repository root, generate files in a temporary directory:

```bash
python - <<'PYGEN'
from pathlib import Path
from tempfile import gettempdir

root = Path(gettempdir()) / 'sift_perf_samples'
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
PYGEN
```

To compare with the historical measurements, run the same check command with
these generated files and record resource use under your own hardware,
filesystem and Python version. Do not treat cross-machine measurements as
controlled comparisons.

## Related correctness work

Historical inspection of duplicate headers exposed two defects that were
subsequently fixed: generated lookup keys could collide with literal header
names, and drift comparisons could misclassify the removal of one repeated
column as a column reorder. The current implementation reserves original
names before generating unique keys and compares duplicate occurrences by
position and count. Regression coverage is in `tests/test_reliability.py`
and `tests/test_final_hardening.py`.

For practical limits and safe data handling, see the
[Project Guide](PROJECT_GUIDE.md) and [security policy](../SECURITY.md).
