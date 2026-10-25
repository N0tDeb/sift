# CLI demonstration

The animated demonstration in the [project README](../README.md) is generated
from [`demo.tape`](demo.tape) using [VHS](https://github.com/charmbracelet/vhs).
It shows three operations on the ten-row [`orders.csv`](orders.csv) fixture:

1. **Check** detects seven findings, including ambiguous dates, inconsistent
   region labels, text-formatted amounts and sentinel values.
2. **Impact** shows that direct numeric coercion yields `-1,998` because eight
   currency-formatted values are skipped. Sift estimates `10,149.75` after
   supported repairs. The estimated change is `12,147.75`; group labels also
   need normalization.
3. **Fix** writes `clean.csv`, applies 13 cell changes and leaves the ambiguous
   date column unchanged rather than guessing its interpretation.

## Regenerate in GitHub Actions (recommended)

No local tools need to be installed. From the repository's **Actions** tab:

1. Select [Generate Sift VHS Demo](../.github/workflows/vhs-demo.yml).
2. Choose **Run workflow** on the intended source branch.
3. When `generate-gif` succeeds, download the `sift-vhs-demo` artifact from
   the workflow-run page and extract `sift.gif`.
4. Inspect the GIF, compare its output with the current CLI and check its size.
5. On a new documentation branch, replace `demo/sift.gif`, commit the change,
   and open a pull request. Confirm normal CI passes before merging.

The workflow only generates and uploads an artifact; it does **not** commit the
GIF, update `main`, or publish a release. The recording is about 30 seconds
long and should remain comfortably below 3 MB for README readability.

## Local generation (optional)

If you already have [VHS](https://github.com/charmbracelet/vhs) installed, run
these commands from the repository root in a disposable environment:

```bash
python -m pip install -e .
vhs demo/demo.tape
```

VHS saves the result as `demo/sift.gif`. The CLI executable is `sift-lint`,
not `sift`. The recording may also create `demo/clean.csv`, which is ignored
by Git. You do not need this local setup to use the GitHub Actions workflow.

## Keeping the demonstration accurate

The counts and totals above describe the bundled fixture, not general product
performance. Re-record whenever the CLI's shown output or the fixture changes.
Update the script and this document alongside the GIF if the commands or their
results change. The GIF is a demonstration, not a substitute for tests.
