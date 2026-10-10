"""Rendering findings for three different readers.

Terminal output is for the person who just ran it and wants the worst thing
first. JSON is for the pipeline. HTML is for the person you send the file to,
who did not run anything and needs the verdict before the detail.

HTML design notes: the report reads as an inspection document, so prose is set
in a serif and every value from the data is set in mono — you can always tell
Sift's words from your file's contents. The one bold element is the column
strip under the verdict: one block per column, colored by its worst finding, so
the shape of the damage is visible before a single word is read. Everything
else is hairlines and space.
"""

from __future__ import annotations

import html
import json
import shutil
import sys
import textwrap
from collections import Counter, defaultdict
from pathlib import Path

from .findings import Finding, Severity, sort_findings
from .loading import Table
from .text import plural, terminal_safe

ANSI = {
    Severity.ERROR: "\033[31m",
    Severity.WARNING: "\033[33m",
    Severity.INFO: "\033[36m",
}
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

HEX = {
    Severity.ERROR: "#9E2B25",
    Severity.WARNING: "#A9701C",
    Severity.INFO: "#3F6D8C",
}


def summarize(findings: list[Finding]) -> dict[str, int]:
    counts = Counter(f.severity.value for f in findings)
    return {
        "error": counts.get("error", 0),
        "warning": counts.get("warning", 0),
        "info": counts.get("info", 0),
        "total": len(findings),
    }


def _verdict(summary: dict[str, int]) -> str:
    if summary["error"]:
        count = summary["error"]
        return (
            f"{plural(count, 'problem')} here will change your numbers, "
            f"not just annoy you."
        )
    if summary["warning"]:
        return "Nothing fatal, but this file will not load the way you expect."
    if summary["info"]:
        return "Clean. A few things worth a glance before you build on it."
    return "Clean. Nothing to report."


def render_text(table: Table, findings: list[Finding], color: bool | None = None) -> str:
    if color is None:
        color = sys.stdout.isatty()
    width = min(shutil.get_terminal_size((88, 24)).columns, 100)

    def paint(text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if color else text

    lines = [
        paint(terminal_safe(table.path), BOLD)
        + paint(
            f"  {plural(table.n_rows, 'row')}, "
            f"{plural(len(table.columns), 'column')}",
            DIM,
        ),
        "",
    ]

    if not findings:
        lines.append(paint("No findings.", ANSI[Severity.INFO]))
        return "\n".join(lines)

    for finding in sort_findings(findings):
        label = paint(f"{finding.severity.value:<7}", ANSI[finding.severity])
        where = terminal_safe(finding.column or "file")
        lines.append(f"{label} {paint(terminal_safe(finding.code), BOLD)}  {paint(where, DIM)}")
        for line in textwrap.wrap(terminal_safe(finding.message), width=width - 8):
            lines.append(f"        {line}")
        if finding.examples:
            shown = ", ".join(terminal_safe(e) for e in finding.examples)
            for i, line in enumerate(textwrap.wrap(shown, width=width - 16)):
                prefix = "        e.g. " if i == 0 else "             "
                lines.append(paint(prefix + line, DIM))
        lines.append("")

    summary = summarize(findings)
    tally = ", ".join(
        f"{summary[k]} {k}" for k in ("error", "warning", "info") if summary[k]
    )
    lines.append(paint(f"{plural(summary['total'], 'finding')}: {tally}", BOLD))
    return "\n".join(lines)


def render_json(table: Table, findings: list[Finding]) -> str:
    payload = {
        "file": str(table.path),
        "rows": table.n_rows,
        "columns": [c.name for c in table.columns],
        "summary": summarize(findings),
        "findings": [f.to_dict() for f in sort_findings(findings)],
    }
    return json.dumps(payload, indent=2, default=str)


CSS = """
:root {
  --paper: #EFF1EE;
  --panel: #F7F8F6;
  --ink: #1B2321;
  --muted: #66716D;
  --rule: #D3D8D4;
  --error: #9E2B25;
  --warning: #A9701C;
  --info: #3F6D8C;
  --clean: #6E8B72;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: Charter, "Bitstream Charter", "Iowan Old Style", Georgia, serif;
  font-size: 17px;
  line-height: 1.55;
}
.wrap { max-width: 46rem; margin: 0 auto; padding: 4rem 1.5rem 6rem; }
.file {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.8rem;
  color: var(--muted);
  letter-spacing: 0.01em;
  margin-bottom: 1.75rem;
}
h1 {
  font-size: clamp(1.6rem, 4.5vw, 2.3rem);
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -0.01em;
  margin: 0 0 1.5rem;
  max-width: 22ch;
}
.tally { color: var(--muted); font-size: 0.95rem; margin: 0 0 2.5rem; }
.tally b { color: var(--ink); font-weight: 600; }
.strip {
  display: flex;
  flex-wrap: wrap;
  margin: 0 -4px 0.9rem 0;
}
.strip > * {
  flex: 1 1 64px;
  min-width: 64px;
  display: block;
  padding: 0 4px 4px 0;
  text-decoration: none;
  color: inherit;
}
.strip .bar {
  height: 30px;
  border-radius: 1px;
  background: var(--clean);
  opacity: 0.3;
}
.strip .name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.65rem;
  color: var(--muted);
  margin-top: 0.3rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.strip [data-sev] .bar { opacity: 1; }
.strip [data-sev="error"] .bar { background: var(--error); }
.strip [data-sev="warning"] .bar { background: var(--warning); }
.strip [data-sev="info"] .bar { background: var(--info); opacity: 0.7; }
.strip a:hover .name, .strip a:focus-visible .name { color: var(--ink); }
.strip a:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; }
.legend {
  font-size: 0.8rem;
  color: var(--muted);
  margin: 0 0 3.5rem;
  padding-bottom: 2rem;
  border-bottom: 1px solid var(--rule);
}
.group { margin-bottom: 2.75rem; }
.group > h2 {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
  font-weight: 600;
  margin: 0 0 0.9rem;
  padding-bottom: 0.4rem;
  border-bottom: 1px solid var(--rule);
}
.finding {
  border-left: 3px solid var(--rule);
  padding: 0.1rem 0 0.1rem 1rem;
  margin-bottom: 1.4rem;
}
.finding[data-sev="error"] { border-left-color: var(--error); }
.finding[data-sev="warning"] { border-left-color: var(--warning); }
.finding[data-sev="info"] { border-left-color: var(--info); }
.code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  color: var(--muted);
  margin-bottom: 0.15rem;
}
.finding[data-sev="error"] .code { color: var(--error); }
.finding p { margin: 0; }
.examples {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.78rem;
  color: var(--muted);
  background: var(--panel);
  border: 1px solid var(--rule);
  padding: 0.5rem 0.65rem;
  margin-top: 0.6rem;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
footer {
  margin-top: 4rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--rule);
  font-size: 0.8rem;
  color: var(--muted);
}
@media print {
  body { background: #fff; }
  .wrap { padding: 0; }
}
"""


def render_html(table: Table, findings: list[Finding]) -> str:
    summary = summarize(findings)
    esc = html.escape

    worst_by_column: dict[str, Severity] = {}
    for finding in findings:
        if finding.column:
            current = worst_by_column.get(finding.column)
            if current is None or finding.severity.rank > current.rank:
                worst_by_column[finding.column] = finding.severity

    def anchor(name: str) -> str:
        return "col-" + "".join(ch if ch.isalnum() else "-" for ch in name.lower())

    strip = []
    for column in table.columns:
        severity = worst_by_column.get(column.name)
        attr = f' data-sev="{severity.value}"' if severity else ""
        title = f"{column.name}: {severity.value if severity else 'nothing found'}"
        inner = (
            f'<div class="bar"></div><div class="name">{esc(column.name)}</div>'
        )
        if severity:
            strip.append(
                f'<a href="#{anchor(column.name)}"{attr} title="{esc(title)}">{inner}</a>'
            )
        else:
            strip.append(f'<div title="{esc(title)}">{inner}</div>')

    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in sort_findings(findings):
        grouped[finding.column or "whole file"].append(finding)

    blocks = []
    for column, items in grouped.items():
        rows = []
        for finding in items:
            example_html = ""
            if finding.examples:
                example_html = (
                    f'<div class="examples">{esc(", ".join(finding.examples))}</div>'
                )
            rows.append(
                f'<div class="finding" data-sev="{finding.severity.value}">'
                f'<div class="code">{esc(finding.code)}</div>'
                f"<p>{esc(finding.message)}</p>{example_html}</div>"
            )
        blocks.append(
            f'<section class="group" id="{anchor(column)}">'
            f"<h2>{esc(column)}</h2>{''.join(rows)}</section>"
        )

    tally = (
        f"<b>{summary['error']}</b> errors, <b>{summary['warning']}</b> warnings, "
        f"<b>{summary['info']}</b> notes across {table.n_rows:,} rows and "
        f"{len(table.columns)} columns."
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sift report — {esc(table.path.name)}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<div class="file">{esc(str(table.path))}</div>
<h1>{esc(_verdict(summary))}</h1>
<p class="tally">{tally}</p>
<div class="strip">{"".join(strip)}</div>
<p class="legend">Columns in file order. Colour marks the worst finding in each.</p>
{"".join(blocks) or "<p>Nothing to report.</p>"}
<footer>Generated by Sift. Findings are heuristics, not verdicts — read the file
before you change it.</footer>
</div></body></html>"""


def write_report(
    table: Table, findings: list[Finding], fmt: str, output: Path | None
) -> str:
    renderers = {"text": render_text, "json": render_json, "html": render_html}
    if fmt not in renderers:
        raise ValueError(f"Unknown format: {fmt}")
    body = (
        render_text(table, findings, color=False)
        if fmt == "text" and output
        else renderers[fmt](table, findings)
    )
    if output:
        output.write_text(body, encoding="utf-8")
    return body
