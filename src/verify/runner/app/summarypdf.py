"""summary.pdf (APP_SPEC rev 1 section 3.2; rev 2: "GPU" in the footer's version).

The worker writes it after the runner, into the case folder, next to summary.txt. A4 pages made with PyMuPDF's
`page.insert_htmlbox` from the HTML-escaped summary lines (report.summary_lines without paths), the verdict words
in colour; the result code, the check date and the program at the top; on every page a footer with the result
code, check date, program, the app version ("App 1.0.0 GPU, build ...") and the rules and runner versions.

Pages are filled greedily: as many whole lines as fit (binary search on a scratch document, no scaling); a line
that does not fit a page on its own is shrunk to fit. The file is written by Python (`open`), after the guard's
assert_writable, so the runner's audit hook sees the write (a MuPDF save would not raise an audit event).

The PDF holds the student's data (it is the case's report, like summary.txt); this module holds none.
"""
from __future__ import annotations

import html
import importlib
import re
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

A4 = (595.0, 842.0)                 # points
MARGIN = 40.0
FOOTER_H = 34.0
GAP = 8.0
DOC_TITLE = "Hangeul Document Checker — check result"
FONT_PT = 9.5
CSS = ("* {font-family: sans-serif; font-size: %.1fpt; line-height: 1.25;} "
       "p {margin: 0 0 3pt 0;} h1 {font-size: 14pt; font-weight: bold; margin: 0 0 4pt 0;} "
       "p.head {font-size: 10.5pt; margin: 0 0 8pt 0;} b {font-weight: bold;}") % FONT_PT
FOOTER_CSS = "* {font-family: sans-serif; font-size: 7pt; color: #404040;} p {margin: 0;}"
# text colours of the verdict words (darker than the report's cell fills, readable on white)
COLOURS = {"PASS": "#1e7b34", "NOTE": "#2f5597", "PRESENT": "#595959", "FLAG": "#9c6500", "ERROR": "#9c6500",
           "FAIL": "#c00000", "MISSING": "#c55a11", "NOT JUDGED": "#7030a0", "ORDER": "#c55a11",
           "STOP": "#c00000", "HINT": "#2f5597"}
_WORD = {k: re.compile(r"(?<![A-Za-z])" + re.escape(k) + r"(?![A-Za-z])") for k in COLOURS}


def esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def coloured(label: str, text: str) -> str:
    """The HTML-escaped line with the first whole-word occurrence of its label (when the label is a verdict word)
    in bold and the verdict's colour."""
    t = esc(text)
    lab = str(label or "").strip().upper()
    if lab not in COLOURS:
        return t
    return _WORD[lab].sub(lambda m: f'<b style="color:{COLOURS[lab]}">{m.group(0)}</b>', t, count=1)


def line_html(label: str, text: str) -> str:
    return f"<p>{coloured(label, text)}</p>"


def head_html(code: str, check_date: str, program: str, overall: str) -> str:
    lab = str(overall or "").upper()
    res = (f'<b style="color:{COLOURS[lab]}">{esc(lab)}</b>' if lab in COLOURS else esc(overall))
    return (f"<h1>{esc(DOC_TITLE)}</h1>"
            f'<p class="head">Result {res} &#183; Result code <b>{esc(code)}</b> &#183; check date '
            f"{esc(check_date)} &#183; program {esc(program)}</p>")


def version_text(app_version: str, build_id: str = "", runner_version: str = "", rules_bot: str = "",
                 rules_runner: str = "", flavour: str = "GPU") -> str:
    """'App 1.0.0 GPU (CUDA 12.8), build <id> · rules bot <x> + runner <y> · runner <z>' (always says GPU)."""
    fl = str(flavour or "").strip()
    fl = fl if "GPU" in fl else ("GPU " + fl).strip()
    s = f"App {app_version or '?'} {fl}" + (f", build {build_id}" if build_id else "")
    if rules_bot or rules_runner:
        s += f" · rules bot {rules_bot or '?'} + runner {rules_runner or '?'}"
    if runner_version:
        s += f" · runner {runner_version}"
    return s


def footer_html(code: str, check_date: str, program: str, version: str, page: int, pages: int) -> str:
    return (f"<p>Result code {esc(code)} (check date {esc(check_date)}) &#183; program {esc(program)} &#183; "
            f"page {page} of {pages}</p><p>{esc(version)}</p>")


def plan_pages(n: int, fits: Callable[[int, int, bool], bool]) -> List[Tuple[int, int, bool]]:
    """Split n lines into pages: [(start, end, shrink)]. fits(start, end, first_page) tells whether lines
    [start, end) fit one page unscaled. Each page takes the most whole lines that fit (binary search; the fit
    shrinks as lines are added); a page that cannot hold even its first line takes that line alone, shrunk."""
    out: List[Tuple[int, int, bool]] = []
    i = 0
    while i < n:
        first = not out
        if not fits(i, i + 1, first):
            out.append((i, i + 1, True))
            i += 1
            continue
        lo, hi = i + 1, n                      # lo fits; find the largest end that fits
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fits(i, mid, first):
                lo = mid
            else:
                hi = mid - 1
        out.append((i, lo, False))
        i = lo
    if not out:                                # no lines at all: one page with the head only
        out.append((0, 0, False))
    return out


def render(lines: Sequence[Tuple[str, str]], *, code: str, check_date: str, program: str, overall: str,
           version: str, pymupdf: Any = None) -> bytes:
    """The PDF bytes of the summary: A4 pages, the head on page 1, a footer on every page."""
    if pymupdf is None:
        import pymupdf  # noqa: F811
    w, h = A4
    body = pymupdf.Rect(MARGIN, MARGIN, w - MARGIN, h - MARGIN - FOOTER_H - GAP)
    foot = pymupdf.Rect(MARGIN, h - MARGIN - FOOTER_H, w - MARGIN, h - MARGIN)
    head = head_html(code, check_date, program, overall)
    paras = [line_html(lab, text) for lab, text in lines]

    def chunk(a: int, b: int, first: bool) -> str:
        return (head if first else "") + "".join(paras[a:b])

    scratch = pymupdf.open()

    def fits(a: int, b: int, first: bool) -> bool:
        page = scratch.new_page(width=w, height=h)
        try:
            spare, _scale = page.insert_htmlbox(body, chunk(a, b, first), css=CSS, scale_low=1)
        finally:
            scratch.delete_page(-1)
        return spare >= 0

    try:
        plan = plan_pages(len(paras), fits)
    finally:
        scratch.close()
    doc = pymupdf.open()
    try:
        for k, (a, b, shrink) in enumerate(plan, 1):
            page = doc.new_page(width=w, height=h)
            page.insert_htmlbox(body, chunk(a, b, k == 1), css=CSS, scale_low=0 if shrink else 1)
            page = doc[-1]                     # a page object may be renewed by insert_htmlbox
            page.insert_htmlbox(foot, footer_html(code, check_date, program, version, k, len(plan)),
                                css=FOOTER_CSS, scale_low=0)
        doc.set_metadata({"title": "Hangeul Document Checker summary", "creator": version,
                          "producer": "Hangeul Document Checker"})
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def summary_pairs(r: Any) -> List[Tuple[str, str]]:
    """report.summary_lines(r) without the output folder (its last line then names no path)."""
    report = importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.report")
    return [(str(lab or ""), str(text)) for lab, text in report.summary_lines(r, None)]


def write(r: Any, path: Path, *, code: str, app_version: str = "", build_id: str = "", flavour: str = "GPU",
          assert_writable: Optional[Callable[[Path], None]] = None,
          lines: Optional[Sequence[Tuple[str, str]]] = None, pymupdf: Any = None) -> Path:
    """Writes summary.pdf of the RunResult `r` to `path`. `lines` defaults to summary_pairs(r).
    `assert_writable` (the guard's check) runs before the write; by default safety.assert_writable."""
    path = Path(path)
    version = version_text(app_version, build_id, getattr(r, "runner_version", ""),
                           getattr(r, "base_rules_version", ""), getattr(r, "runner_rules_version", ""), flavour)
    data = render(summary_pairs(r) if lines is None else list(lines), code=code,
                  check_date=str(getattr(r, "today", "") or ""), program=str(getattr(r, "program", "") or ""),
                  overall=str(getattr(r, "overall", "") or ""), version=version, pymupdf=pymupdf)
    if assert_writable is None:
        assert_writable = importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.safety").assert_writable
    assert_writable(path)
    with open(path, "wb") as f:
        f.write(data)
    return path
