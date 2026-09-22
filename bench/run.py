"""Measure what Sift catches, and what it invents.

Three rounds of testing against real files fixed false positives, because
those are the ones you can see. Nothing in that process could reveal a *false
negative* — a problem sitting in a file that Sift walked straight past. You
cannot eyeball a miss.

So: generate a clean file, verify Sift is silent on it, corrupt it in one
known way, and check that the matching finding appears. Recall is the share of
corruptions caught. The noise figure is the number of findings raised on the
clean file, where the correct answer is zero.

    python -m bench.run
    python -m bench.run --markdown BENCHMARK.md
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.corruptions import CASES, Case  # noqa: E402
from sift.checks import run_checks  # noqa: E402
from sift.config import Config  # noqa: E402
from sift.loading import load  # noqa: E402

HEADER = ["id", "when", "region", "customer", "amount", "postcode", "note"]
REGIONS = ["North", "South", "East", "West"]
CUSTOMERS = ["Northwind Traders", "Contoso Ltd", "Fabrikam Inc", "Tailspin Toys"]
NOTES = ["delivered", "collected", "held at depot", "signature required"]


def clean_rows(rng: random.Random, count: int = 60) -> list[list[str]]:
    """A file with nothing wrong with it. If Sift complains about this, the
    complaint is the bug."""
    rows = []
    for i in range(count):
        rows.append(
            [
                f"A-{1000 + i}",
                f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                REGIONS[i % len(REGIONS)],
                CUSTOMERS[i % len(CUSTOMERS)],
                f"{100 + (i * 7) % 400}.{i % 100:02d}",
                str(10000 + (i * 137) % 80000),
                NOTES[i % len(NOTES)],
            ]
        )
    return rows


def write(path: Path, case: Case) -> None:
    lines = [",".join(case.header)]
    for row in case.rows:
        lines.append(
            ",".join(
                f'"{c.replace(chr(34), chr(34) * 2)}"' if ("," in c or '"' in c) else c
                for c in row
            )
        )
    if case.raw_suffix == b"CRLF":
        body = "\r\n".join(lines[: len(lines) // 2]) + "\n" + "\n".join(lines[len(lines) // 2 :])
    else:
        body = "\n".join(lines)
    data = (body + "\n").encode("utf-8")
    if case.raw_suffix == b"BOM":
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


def run(seed: int = 11) -> tuple[list[dict], list[str]]:
    rng = random.Random(seed)
    header = list(HEADER)
    base = clean_rows(rng)
    config = Config(key=["id"])

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)

        # The control: Sift must be silent on a file with nothing wrong.
        control = Case("clean baseline", "", None, header=header, rows=base)
        control_path = directory / "clean.csv"
        write(control_path, control)
        noise = [
            f.code
            for f in run_checks(load(control_path), config)
            if f.severity.value != "info"
        ]

        results = []
        for index, corrupt in enumerate(CASES):
            case = corrupt(header, base, random.Random(seed + index))
            path = directory / f"case_{index}.csv"
            write(path, case)
            findings = run_checks(load(path), config)

            hit = any(
                f.code == case.expect and (case.column is None or f.column == case.column)
                for f in findings
            )
            raised = {f.code for f in findings if f.severity.value != "info"}
            extra = sorted(raised - {case.expect} - set(noise))
            results.append(
                {
                    "name": case.name,
                    "expect": case.expect,
                    "caught": hit,
                    "extra": extra,
                    "codes": sorted({f.code for f in findings}),
                }
            )
    return results, noise


def render(results: list[dict], noise: list[str]) -> str:
    caught = sum(1 for r in results if r["caught"])
    total = len(results)
    width = max(len(r["name"]) for r in results) + 2

    lines = ["", f"{'corruption':<{width}} {'expected finding':<26} result", ""]
    for result in sorted(results, key=lambda r: (r["caught"], r["name"])):
        mark = "caught" if result["caught"] else "MISSED"
        lines.append(f"{result['name']:<{width}} {result['expect']:<26} {mark}")

    lines.append("")
    lines.append(f"Recall: {caught}/{total} ({caught / total:.0%}) of known corruptions detected.")
    if noise:
        counts = Counter(noise)
        detail = ", ".join(f"{code} x{n}" if n > 1 else code for code, n in counts.items())
        lines.append(f"Noise:  {len(noise)} finding(s) on the clean control file: {detail}")
    else:
        lines.append("Noise:  0 findings on the clean control file.")

    collateral = sorted({code for r in results for code in r["extra"]})
    if collateral:
        lines.append(
            "Also raised on corrupted files (often correct — one corruption can "
            "genuinely cause several): " + ", ".join(collateral)
        )
    return "\n".join(lines)


def markdown(results: list[dict], noise: list[str]) -> str:
    caught = sum(1 for r in results if r["caught"])
    total = len(results)
    lines = [
        "# Benchmark",
        "",
        "Generated by `python -m bench.run`. A clean file is generated, verified",
        "silent, then corrupted one way at a time; each corruption declares the",
        "finding code that should catch it.",
        "",
        f"**Recall: {caught}/{total} ({caught / total:.0%})**  ",
        f"**Noise on the clean control file: {len(noise)}**",
        "",
        "| Corruption | Expected finding | Result |",
        "| --- | --- | --- |",
    ]
    for result in sorted(results, key=lambda r: (r["caught"], r["name"])):
        mark = "caught" if result["caught"] else "**missed**"
        lines.append(f"| {result['name']} | `{result['expect']}` | {mark} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure Sift's recall against known corruptions.")
    parser.add_argument("--markdown", type=Path, help="Also write a Markdown report here.")
    parser.add_argument(
        "--min-recall",
        type=float,
        default=0.9,
        help="Exit non-zero below this recall (default: 0.9).",
    )
    args = parser.parse_args(argv)

    results, noise = run()
    print(render(results, noise))

    if args.markdown:
        args.markdown.write_text(markdown(results, noise), encoding="utf-8")
        print(f"\nWrote {args.markdown}")

    recall = sum(1 for r in results if r["caught"]) / len(results)
    if recall < args.min_recall or noise:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
