"""Render results as text, JSON, Markdown or a self-contained HTML page."""
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Callable

from .classify import (
    COVERED, MEASURED, NONEXEC, PARTIAL, UNCOVERED, UNKNOWN, UNKNOWN_HEADER,
    UNKNOWN_SOURCE, FileResult, Result, Tally, Verdict,
)
from .gitlines import ChangedLines

MAX_SNIPPET_LINES = 15
HTML_CONTEXT = 3


@dataclass
class Report:
    result: Result
    verdict: Verdict
    changed: ChangedLines
    metric: str
    source: Callable[[str], list[str]]  # path -> lines at rev


def ranges(nums: list[int]) -> str:
    """[1, 2, 3, 7, 9, 10] -> '1-3, 7, 9-10'"""
    out: list[str] = []
    start = prev = None
    for n in nums:
        if prev is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    if start is not None:
        out.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(out)


def _fmt(t: Tally) -> str:
    return "-" if t.pct is None else f"{t.hit}/{t.total} {t.pct:.1f}%"


def _columns(rep: Report) -> list[tuple[str, str]]:
    cols = [("Lines", "lines")]
    if rep.result.has_branches:
        cols.append(("Branches", "branches"))
    if rep.result.has_regions:
        cols.append(("Regions", "regions"))
    return cols


def _unknown_note(f: FileResult) -> str:
    what = "header" if f.status == UNKNOWN_HEADER else "source file"
    return f"{what} not in coverage data ({len(f.line_status)} changed lines)"


def _snippet(rep: Report, f: FileResult) -> list[tuple[int, str, str]]:
    src = rep.source(f.path)
    nums = f.with_status(UNCOVERED, PARTIAL)
    return [
        (n, f.line_status[n], src[n - 1] if n <= len(src) else "")
        for n in nums[:MAX_SNIPPET_LINES]
    ]


def _header(rep: Report) -> str:
    c = rep.changed
    s = f"rev {c.rev[:12]}, {len(c.commits)} commit(s), metric: {rep.metric}"
    return s


# --- text -------------------------------------------------------------------

def render_text(rep: Report) -> str:
    cols = _columns(rep)
    rows = [["File", *(name for name, _ in cols)]]
    notes: dict[int, list[str]] = {}
    for f in rep.result.files:
        if f.status != MEASURED:
            rows.append([f.path, _unknown_note(f)])
            continue
        rows.append([f.path, *(_fmt(getattr(f, attr)) for _, attr in cols)])
        extra = []
        unc = f.with_status(UNCOVERED)
        part = f.with_status(PARTIAL)
        if unc:
            extra.append(f"    uncovered  {ranges(unc)}")
        if part:
            extra.append(f"    partial    {ranges(part)}")
        for n, status, text in _snippet(rep, f):
            mark = "!" if status == UNCOVERED else "~"
            extra.append(f"      {mark} {n:>5} | {text}")
        notes[len(rows) - 1] = extra
    rows.append(["TOTAL", *(_fmt(getattr(rep.result, attr)) for _, attr in cols)])
    ncol = len(rows[0])
    widths = [max(len(r[i]) for r in rows if len(r) == ncol) for i in range(ncol)]
    widths[0] = max(len(r[0]) for r in rows)

    out = [f"diffcov: {_header(rep)}", ""]
    for i, r in enumerate(rows):
        if i == len(rows) - 1:
            out.append("-" * (sum(widths) + 3 * (len(widths) - 1)))
        if len(r) == len(widths):
            out.append("   ".join(cell.ljust(w) if j == 0 else cell.rjust(w) for j, (cell, w) in enumerate(zip(r, widths))).rstrip())
        else:
            out.append(f"{r[0].ljust(widths[0])}   {r[1]}")
        out += notes.get(i, [])
    out.append("")
    for w in rep.verdict.warnings:
        out.append(f"warning: {w}")
    if rep.verdict.passed:
        out.append("PASS")
    else:
        out += [f"FAIL: {msg}" for msg in rep.verdict.failures]
    return "\n".join(out) + "\n"


# --- json -------------------------------------------------------------------

def _tally(t: Tally) -> dict:
    return {"hit": t.hit, "total": t.total, "pct": None if t.pct is None else round(t.pct, 2)}


def render_json(rep: Report) -> str:
    files = []
    for f in rep.result.files:
        entry = {"path": f.path, "status": f.status}
        if f.status == MEASURED:
            entry |= {
                "lines": _tally(f.lines),
                "branches": _tally(f.branches),
                "regions": _tally(f.regions),
                "covered": f.with_status(COVERED),
                "partial": f.with_status(PARTIAL),
                "uncovered": f.with_status(UNCOVERED),
                "nonexec": f.with_status(NONEXEC),
                "uncovered_regions": [list(r) for r in f.uncovered_regions],
            }
        else:
            entry["changed"] = f.with_status(UNKNOWN)
        files.append(entry)
    doc = {
        "rev": rep.changed.rev,
        "commits": rep.changed.commits,
        "merges": rep.changed.merges,
        "not_ancestors": rep.changed.not_ancestors,
        "metric": rep.metric,
        "totals": {
            "lines": _tally(rep.result.lines),
            "branches": _tally(rep.result.branches),
            "regions": _tally(rep.result.regions),
        },
        "files": files,
        "passed": rep.verdict.passed,
        "failures": rep.verdict.failures,
        "warnings": rep.verdict.warnings,
    }
    return json.dumps(doc, indent=2) + "\n"


# --- markdown ---------------------------------------------------------------

def _md_cell(s: str) -> str:
    return s.replace("|", "\\|")


def render_markdown(rep: Report) -> str:
    cols = _columns(rep)
    icon = "✅" if rep.verdict.passed else "❌"
    out = [f"### {icon} diffcov: changed-line coverage", "", f"_{_header(rep)}_", ""]
    out.append("| File | " + " | ".join(n for n, _ in cols) + " |")
    out.append("|---|" + "---:|" * len(cols))
    for f in rep.result.files:
        if f.status == MEASURED:
            cells = [_fmt(getattr(f, a)) for _, a in cols]
        else:
            cells = [f"⚠️ {_unknown_note(f)}"] + [""] * (len(cols) - 1)
        out.append(f"| `{_md_cell(f.path)}` | " + " | ".join(cells) + " |")
    out.append("| **Total** | " + " | ".join(f"**{_fmt(getattr(rep.result, a))}**" for _, a in cols) + " |")
    out.append("")
    for msg in rep.verdict.failures:
        out.append(f"- ❌ {msg}")
    for msg in rep.verdict.warnings:
        out.append(f"- ⚠️ {msg}")
    for f in rep.result.files:
        snip = _snippet(rep, f) if f.status == MEASURED else []
        if not snip:
            continue
        unc, part = f.with_status(UNCOVERED), f.with_status(PARTIAL)
        summary = ", ".join(s for s in (unc and f"uncovered {ranges(unc)}", part and f"partial {ranges(part)}") if s)
        out += ["", f"<details><summary><code>{html.escape(f.path)}</code>: {summary}</summary>", "", "```"]
        out += [f"{'!' if s == UNCOVERED else '~'} {n:>5} | {t}" for n, s, t in snip]
        out += ["```", "</details>"]
    return "\n".join(out) + "\n"


# --- html -------------------------------------------------------------------

_CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--card:#fff;
--cov:#e3f4e6;--cov-b:#2f8f46;--unc:#fde4e2;--unc-b:#c9372c;--par:#fdf1d6;--par-b:#b7791f;
--unk:#ececf3;--unk-b:#6366a0;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#e8e8ea;--muted:#9a9aa2;--line:#2c2c30;
--card:#1e1e21;--cov:#173a22;--cov-b:#4cc26b;--unc:#45201d;--unc-b:#f07067;--par:#3d3115;--par-b:#e3a945;
--unk:#26263a;--unk-b:#9a9ce0;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0;font-family:var(--mono);font-weight:600}
.sub{color:var(--muted);margin:0 0 20px}
.verdict{padding:10px 14px;border-radius:8px;margin:0 0 20px;border-left:4px solid}
.verdict.pass{background:var(--cov);border-color:var(--cov-b)}
.verdict.fail{background:var(--unc);border-color:var(--unc-b)}
.verdict ul{margin:4px 0 0;padding-left:20px}
.scroll{overflow-x:auto}
table.sum{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px}
.sum th,.sum td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
.sum th:first-child,.sum td:first-child{text-align:left}
.sum td:first-child a{font-family:var(--mono);color:inherit}
.sum tr.total td{font-weight:600;border-bottom:0}
.bar{display:inline-block;width:60px;height:6px;border-radius:3px;background:var(--unc);margin-left:8px;vertical-align:middle;overflow:hidden}
.bar i{display:block;height:100%;background:var(--cov-b)}
.file{margin-top:28px;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.file header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;padding:10px 14px;border-bottom:1px solid var(--line)}
.file header span{color:var(--muted)}
pre{margin:0;font:12.5px/1.5 var(--mono)}
.l{display:flex;white-space:pre}
.l .n{flex:none;width:56px;padding-right:10px;text-align:right;color:var(--muted);user-select:none;border-right:3px solid transparent}
.l .t{padding-left:10px}
.l.ctx{opacity:.55}
.l.covered{background:var(--cov)}.l.covered .n{border-color:var(--cov-b)}
.l.uncovered{background:var(--unc)}.l.uncovered .n{border-color:var(--unc-b)}
.l.partial{background:var(--par)}.l.partial .n{border-color:var(--par-b)}
.l.unknown{background:var(--unk)}.l.unknown .n{border-color:var(--unk-b)}
.l.nonexec .n{border-color:var(--line)}
.gap{color:var(--muted);padding:2px 14px;font:12px var(--mono);border-block:1px dashed var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0 0;color:var(--muted);font-size:12px}
.legend b{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
"""


def _bar(t: Tally) -> str:
    if t.pct is None:
        return "-"
    return f'{t.hit}/{t.total} {t.pct:.1f}%<span class="bar"><i style="width:{t.pct:.1f}%"></i></span>'


def _html_file(rep: Report, idx: int, f: FileResult) -> str:
    e = html.escape
    src = rep.source(f.path)
    if f.status == MEASURED:
        unc, part = f.with_status(UNCOVERED), f.with_status(PARTIAL)
        meta = [f"lines {_fmt(f.lines)}"]
        if unc:
            meta.append(f"uncovered {ranges(unc)}")
        if part:
            meta.append(f"partial {ranges(part)}")
    else:
        meta = [_unknown_note(f)]
    shown: set[int] = set()
    for n in f.line_status:
        shown.update(range(max(1, n - HTML_CONTEXT), min(len(src), n + HTML_CONTEXT) + 1))
    rows, prev = [], 0
    for n in sorted(shown):
        if prev and n != prev + 1:
            rows.append(f'<div class="gap">⋯ {n - prev - 1} lines</div>')
        cls = f.line_status.get(n, "ctx")
        rows.append(f'<div class="l {cls}"><span class="n">{n}</span><span class="t">{e(src[n - 1])}</span></div>')
        prev = n
    return (
        f'<section class="file" id="f{idx}"><header><h2>{e(f.path)}</h2>'
        f'<span>{e(" · ".join(meta))}</span></header>'
        f'<div class="scroll"><pre>{"".join(rows)}</pre></div></section>'
    )


def render_html(rep: Report) -> str:
    e = html.escape
    cols = _columns(rep)
    v = rep.verdict
    if v.passed:
        verdict = '<div class="verdict pass"><strong>PASS</strong>'
    else:
        verdict = '<div class="verdict fail"><strong>FAIL</strong>'
    items = [f"<li>{e(m)}</li>" for m in v.failures] + [f"<li>warning: {e(m)}</li>" for m in v.warnings]
    if items:
        verdict += f"<ul>{''.join(items)}</ul>"
    verdict += "</div>"

    head = "<tr><th>File</th>" + "".join(f"<th>{n}</th>" for n, _ in cols) + "</tr>"
    body = []
    for i, f in enumerate(rep.result.files):
        if f.status == MEASURED:
            cells = "".join(f"<td>{_bar(getattr(f, a))}</td>" for _, a in cols)
        else:
            cells = f'<td colspan="{len(cols)}">{e(_unknown_note(f))}</td>'
        body.append(f'<tr><td><a href="#f{i}">{e(f.path)}</a></td>{cells}</tr>')
    body.append(
        '<tr class="total"><td>Total</td>'
        + "".join(f"<td>{_bar(getattr(rep.result, a))}</td>" for _, a in cols)
        + "</tr>"
    )
    legend = "".join(
        f'<span><b style="background:var(--{k}-b)"></b>{label}</span>'
        for k, label in (("cov", "covered"), ("par", "partial"), ("unc", "uncovered"), ("unk", "not in coverage data"))
    ) + "<span>unmarked: not executable · faded: context</span>"
    files = "".join(_html_file(rep, i, f) for i, f in enumerate(rep.result.files))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>diffcov {e(rep.changed.rev[:12])}</title><style>{_CSS}</style></head>
<body><main>
<h1>Changed-line coverage</h1>
<p class="sub">{e(_header(rep))}</p>
{verdict}
<div class="scroll"><table class="sum"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>
<div class="legend">{legend}</div>
{files}
</main></body></html>
"""


RENDERERS = {
    "text": render_text,
    "json": render_json,
    "markdown": render_markdown,
    "html": render_html,
}
