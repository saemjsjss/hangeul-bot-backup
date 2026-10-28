"""The result code (APP_SPEC rev 1 section 1.3): 8 hex digits over the verdict lines and the check date, shown as
XXXX-XXXX. The same input, program and check date give the same code on any PC whenever the verdicts are the same.

What goes in: the check date, program, bundle/folder, overall verdict and every summary verdict line: each document
line (title, pages or file name, verdict, reasons as (level, rule id, pages)); the NAMES, FINANCIAL CHAIN and BUNDLE
lines; the MISSING lines; the ORDER / HINT / NOTE / STOP notices with their pages and whether they block.
What stays out: timings, paths, the case id, the reading line, versions, run timestamps and the reason WORDING (a
reason's message can carry a value read by OCR). A different code therefore always means a different decision.
Changing the algorithm means raising RC_SCHEMA (a pinned golden value in tests/test_resultcode.py). No personal data.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, List, Sequence, Tuple

from . import report as REPORT
from .model import ERROR, FAIL, FLAG, RANK, RunResult

RC_SCHEMA = "HDC-RC/1"
VERDICT_LEVELS = (FAIL, ERROR, FLAG)       # the levels that are reasons; PASS / NOTE / PRESENT / ORDER are not
CODE_RE = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")
_WS = re.compile(r"\s+")


def norm(s: Any) -> str:
    """Unicode NFC, whitespace runs -> one space, stripped, upper case."""
    return _WS.sub(" ", unicodedata.normalize("NFC", str(s if s is not None else ""))).strip().upper()


def reasons(fs: Iterable[Any]) -> List[list]:
    """The sorted set of [verdict, rule, sorted 1-based pages] of the findings whose verdict is a reason level,
    sorted by (-RANK[verdict], rule, pages)."""
    seen = {(f.verdict, str(f.rule or ""), tuple(sorted({int(p) for p in (f.pages or ())})))
            for f in fs if f.verdict in VERDICT_LEVELS}
    return [[v, rule, list(pages)] for v, rule, pages in sorted(seen, key=lambda x: (-RANK[x[0]], x[1], x[2]))]


def where_key(u: Any, mode: str) -> list:
    """bundle: [first, last] 1-based input pages of the unit; folder: [norm(source file)]."""
    if mode == "folder":
        return [norm(u.source_file)]
    ps = [int(i) for i in (u.pages or ())]
    return [min(ps) + 1, max(ps) + 1] if ps else []


def cross_groups(r: RunResult) -> List[Tuple[str, List[Any]]]:
    """(line name, findings) for NAMES, FINANCIAL CHAIN and BUNDLE, grouped as the summary groups them."""
    groups = {name: [] for name in REPORT.CROSS_LINES}
    for f in r.cross or ():
        groups[REPORT._cross_line_of(f)].append(f)
    return [(name, groups[name]) for name in REPORT.CROSS_LINES]


def canonical(r: RunResult) -> list:
    """The JSON-able list the code is the hash of (RC_SCHEMA)."""
    return [RC_SCHEMA, r.today, r.program, r.mode, r.overall,
            [[norm(REPORT.title_of(d.unit)), where_key(d.unit, r.mode), d.verdict, reasons(d.findings)]
             for d in REPORT.display_docs(r)],
            [[name, REPORT._line_label(fs), reasons(fs)] for name, fs in cross_groups(r) if fs],
            [[lv, norm(text)] for lv, text in (r.missing_lines or ())],
            sorted([n.kind, int(n.pages[0]), int(n.pages[1]), bool(n.blocks)] for n in (r.notices or ()))]


def code(r: RunResult) -> str:
    """XXXX-XXXX: the first 8 hex digits (upper case) of the sha256 of canonical(r) as compact ASCII JSON."""
    data = json.dumps(canonical(r), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    h = hashlib.sha256(data).hexdigest()
    return f"{h[:4]}-{h[4:8]}".upper()


def line(r: RunResult, rc: str) -> str:
    """The summary's third line: "RESULT CODE: XXXX-XXXX (check date YYYY-MM-DD)"."""
    return f"RESULT CODE: {rc} (check date {r.today})"


__all__: Sequence[str] = ("RC_SCHEMA", "VERDICT_LEVELS", "CODE_RE", "norm", "reasons", "where_key", "cross_groups",
                          "canonical", "code", "line")
