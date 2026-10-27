"""The runner's three outputs (RUNNER_SPEC section 5.11, revision 2): summary.txt (chat-ready), report.xlsx
(sheets Summary, Documents, Pages, Findings; critique 20) and run.json (schema runner-run/2).

The outputs are the owner's private review copies: they may hold the student's data. This module holds
none. Every write goes through safety.assert_writable (looked up when it is needed, so the unit tests can
stand a fake in for it). openpyxl is the vendored copy (RUNNER_SPEC 2.2), imported only by write_excel.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import importlib
import json
import math
import re
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import (ERROR, FAIL, FLAG, MISSING, NEW_CHECK_RULES, NOT_JUDGED, NOTE, ORDER, PASS, PRESENT, STOP,
                    DocResult, DocUnit, Finding, Notice, RunResult)
from . import verdict as V

MAX_LINES = 80
MAX_REASON = 160
MAX_REASONS = 3
SHEETS = ("Summary", "Documents", "Pages", "Findings")
CROSS_LINES = ("NAMES", "FINANCIAL CHAIN", "BUNDLE")
ASSUMED_RULES = ("names.assumed_dots",)      # a default the owner has not confirmed decided this run (A2, Q13)
# The defaults in force until the owner answers (RUNNER_SPEC section 12); listed on the Excel Summary sheet.
ASSUMPTIONS = (
    "Q13 (A2, unstated case): the passport prints 'MD' and EVERY other document 'MD.' (the same dot everywhere "
    "except the passport) = OK, by symmetry with 'passport MD. + all others MD'",
    "Q16: leading titles (Mr, Mrs, Ms, Miss, Dr, Late) are not part of a name; a dot after the last letter is "
    "punctuation. MD, MST and every other word count letter for letter",
    "Q4: MISSING lines alone make the result FLAG; a FAIL anywhere outranks them",
    "Q14: the two new checks from test 1 (TIN printed from a non-.gov.bd address; tax circle/zone differs) are "
    "on, FLAG only",
    "Q18: a value read ONCE that adds a word other than an honorific to a name is page noise (the name inside "
    "it counts); two agreeing reads (two pages or the zoomed re-read) make it a difference. An added MD/MST is "
    "always a difference",
    "Q19: the birth certificate's parent value goes on after the words the other documents print and no second "
    "read (another page, the zoomed re-read) shows those words: the other documents are compared with the "
    "confirmed part, the birth certificate's line is FLAG 'check by eye' (names.<parent>_tail), and a document or "
    "holder adding those words, or other words in their place (no more words than the tail), is FLAG",
    "Q21: a word printed right after a name in another case than the name (a capitalised name, then a Title-case "
    "or small-letter word), a field label glued to a colon ('Program:'), or a sentence, academic or occupation "
    "word (successfully, Programme, Farmer, Freedom Fighter ...) ends the name: it is never an extra name word",
    "Q22: folder mode, the solvency certificate and the statement in separate bank files: the files are one bank "
    "document (the one-page FLAG becomes a NOTE; the two dates are compared across the files)",
)
CHAIN_PREFIXES = ("financial.", "bank.", "tin.")
FILL = {PASS: "C6EFCE", NOTE: "DDEBF7", PRESENT: "D9D9D9", FLAG: "FFEB9C", ERROR: "FFEB9C", FAIL: "FFC7CE",
        MISSING: "F4B084", NOT_JUDGED: "E4DFEC", ORDER: "F8CBAD", STOP: "FF9999", "HINT": "DDEBF7",
        "REREAD": "EDEDED"}


def _assert_writable(p: Path) -> None:
    importlib.import_module(__package__ + ".safety").assert_writable(p)


# ================================================================================= JSON helpers
def _is_array(x: Any) -> bool:
    return type(x).__name__ == "ndarray" and type(x).__module__.startswith("numpy")


def to_jsonable(x: Any) -> Any:
    """dataclasses -> dicts; Path -> str; date/datetime -> ISO; tuple/set -> list; numpy scalars -> Python;
    image arrays dropped from dicts (None in lists); bytes -> "<n bytes>"; NaN/inf -> None."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        x = dataclasses.asdict(x)
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if _is_array(v):
                continue
            key = k if isinstance(k, (str, int, float, bool)) or k is None else str(k)
            out[key] = to_jsonable(v)
        return out
    if isinstance(x, (list, tuple)):
        return [None if _is_array(v) else to_jsonable(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted((to_jsonable(v) for v in x), key=str)
    if isinstance(x, PurePath):
        return str(x)
    if isinstance(x, (dt.date, dt.datetime, dt.time)):
        return x.isoformat()
    if isinstance(x, (bytes, bytearray)):
        return f"<{len(x)} bytes>"
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)):
        return x
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if _is_array(x):
        return None
    if type(x).__module__.startswith("numpy") and hasattr(x, "item"):
        return to_jsonable(x.item())
    return str(x)


def write_json_data(data: Any, path: Path) -> None:
    """Write any JSON-able data (after to_jsonable) as UTF-8 to `path` (safety-checked)."""
    path = Path(path)
    _assert_writable(path)
    path.write_text(json.dumps(to_jsonable(data), ensure_ascii=False, indent=1), encoding="utf-8")


def write_json(r: RunResult, path: Path, extra: Optional[Mapping[str, Any]] = None) -> None:
    """dataclasses.asdict(RunResult) (schema runner-run/2; Path -> str, date -> ISO, tuple -> list), plus the
    extra keys "names_rereads" and "tin" (raw check_tin_certificate dicts, image arrays dropped)."""
    data = to_jsonable(dataclasses.asdict(r))
    for k, v in (extra or {}).items():
        data[k] = to_jsonable(v)
    write_json_data(data, path)


# ================================================================================= summary lines
def _clip(s: str, n: int = MAX_REASON) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _reason_text(reasons: Sequence[str]) -> str:
    shown = [_clip(x) for x in reasons[:MAX_REASONS]]
    more = len(reasons) - len(shown)
    return "; ".join(shown) + (f" (+{more} more)" if more > 0 else "")


def pages_label(pages: Iterable[int]) -> str:
    """0-based run pages -> "p5", "p6-7", "p2-3,5"."""
    ps = sorted(set(pages))
    if not ps:
        return "p-"
    runs, a, b = [], ps[0], ps[0]
    for p in ps[1:]:
        if p == b + 1:
            b = p
            continue
        runs.append((a, b))
        a = b = p
    runs.append((a, b))
    return "p" + ",".join(V.page_span(x + 1, y + 1) for x, y in runs)


def where(u: DocUnit, mode: str) -> str:
    """"p6-7" in a bundle; the input file name in a folder, with the pages inside it when the unit is one of
    several documents cut out of that file ("FINANCIAL DOCUMENTS.pdf p4-5", revision 2.3)."""
    if mode == "folder":
        name = u.source_file or (u.path.name if u.path else pages_label(u.pages))
        part = getattr(u, "part", "")
        return f"{name} {part}" if part and u.source_file else name
    return pages_label(u.pages)


def title_of(u: DocUnit) -> str:
    return u.title or u.key.replace("_", " ").upper()


# the owner's fixed order, used to list a FOLDER's documents (a bundle's are listed in page order, which is it)
FIXED_ORDER = ["passport", "photo", "father_nid", "father_death_cert", "mother_nid", "mother_death_cert",
               "student_nid", "death_cert", "birth_cert", "family_cert", "academic", "bank", "trade_license",
               "income_tax", "affidavit", "language_cert", "eca", "personal_statement", "recommendation",
               "id_card_cv", "nid_unassigned", "unrecognised"]
FINE_ORDER = {"trade_license": 0, "employment_certificate": 1, "tin_certificate": 2}
LEVEL_ORDER = {"degree": 0, "diploma": 1, "HSC": 2, "SSC": 3}


def display_docs(r: RunResult) -> List[DocResult]:
    """Bundle: the documents in page order (as merged). Folder: in the owner's fixed order (file names sort
    alphabetically, which is no order of his), academic levels newest first; ties keep the run order."""
    if r.mode != "folder":
        return list(r.docs)
    rank = {k: i for i, k in enumerate(FIXED_ORDER)}
    pos = {id(d): i for i, d in enumerate(r.docs)}
    return sorted(r.docs, key=lambda d: (rank.get(d.unit.key, len(rank)), FINE_ORDER.get(d.unit.fine, 9),
                                         LEVEL_ORDER.get(d.unit.level, 9), pos[id(d)]))


def _cross_line_of(f: Finding) -> str:
    if f.rule.startswith("names."):
        return "NAMES"
    if f.rule.startswith(CHAIN_PREFIXES):
        return "FINANCIAL CHAIN"
    return "BUNDLE"


def _line_label(fs: Sequence[Finding]) -> str:
    return V.worst(fs)


def _page_types_lines(r: RunResult, width: int = 200) -> List[str]:
    """"PAGE TYPES: p1 passport, p2-3 nid, ..." (consecutive pages of one type joined), wrapped."""
    items: List[str] = []
    types = sorted(r.types, key=lambda t: t.index)
    i = 0
    while i < len(types):
        j = i
        while j + 1 < len(types) and types[j + 1].best == types[i].best:
            j += 1
        items.append(f"{pages_label(range(types[i].index, types[j].index + 1))} {types[i].best}")
        i = j + 1
    lines, cur = [], ""
    for it in items:
        if cur and len(cur) + len(it) + 2 > width:
            lines.append("PAGE TYPES: " + cur)
            cur = it
        else:
            cur = f"{cur}, {it}" if cur else it
    if cur:
        lines.append("PAGE TYPES: " + cur)
    return lines


def reading_line(r: RunResult) -> str:
    by = (r.reading or {}).get("pages_by_engine") or {}
    line = (f"READING: GPU pre-read {int(by.get('gpu', 0))} pages, CPU light engine {int(by.get('cpu', 0))}, "
            f"text layer {int(by.get('layer', 0))}")
    note = str((r.reading or {}).get("note") or "").strip()
    return line + (f" — {note}" if note else "")


def new_checks_fired(r: RunResult) -> List[Tuple[str, Optional[DocUnit]]]:
    """(rule, unit or None) of every NEW_CHECK_RULES finding that fired (FLAG, FAIL or ERROR), once each."""
    out: List[Tuple[str, Optional[DocUnit]]] = []
    seen = set()
    for d in r.docs:
        for f in d.findings:
            if f.rule in NEW_CHECK_RULES and f.verdict in (FLAG, FAIL, ERROR) and (f.rule, d.unit.uid) not in seen:
                seen.add((f.rule, d.unit.uid))
                out.append((f.rule, d.unit))
    for f in r.cross:
        if f.rule in NEW_CHECK_RULES and f.verdict in (FLAG, FAIL, ERROR) and not any(x[0] == f.rule for x in out):
            out.append((f.rule, None))
    return out


def summary_lines(r: RunResult, out: Optional[Path] = None) -> List[Tuple[str, str]]:
    """(label, line) pairs of the chat-ready summary (RUNNER_SPEC 5.11.1); the label colours the Excel cell."""
    L: List[Tuple[str, str]] = []
    L.append((r.overall, f"{r.case_id} | {r.program} | {r.mode} | {len(r.pages)} pages | RESULT: {r.overall}"))
    L.append((r.overall, r.headline))
    L.append(("", reading_line(r)))
    notices = list(r.notices)
    orders = [n for n in notices if n.kind == ORDER]
    if r.mode == "bundle" and not orders:
        L.append((PASS, "ORDER: OK — the pages fit the fixed order"))
    for n in orders:
        L.append((ORDER, f"ORDER: fix {V.pages_word(*n.pages)} — {n.message}"))
    for n in notices:
        if n.kind == STOP:
            L.append((STOP, f"STOP: {n.message} ({V.pages_word(*n.pages)})"))
    for n in notices:
        if n.kind == "HINT":
            L.append(("HINT", f"HINT: {n.message}"))
    for n in notices:
        if n.kind == NOTE:
            L.append((NOTE, f"NOTE: {n.message}"))
    if r.overall == STOP:
        L.extend(("", s) for s in _page_types_lines(r))
        L.extend(not_checked_lines(r))
        L.append(("", _report_line(out)))
        return _fit(L)
    present_only = [d for d in r.docs if d.unit.key == "id_card_cv"]
    for d in display_docs(r):
        if d.unit.key == "id_card_cv":
            continue
        reasons = list(d.reasons)
        if d.verdict == PRESENT and not reasons:
            reasons = [f.message for f in d.findings if f.verdict == PRESENT][:1]
        line = f"{title_of(d.unit)} ({where(d.unit, r.mode)}): {d.verdict}"
        if reasons:
            line += " — " + _reason_text(reasons)
        L.append((d.verdict, line))
    for lv, text in r.missing_lines:
        L.append((lv, text))
    if r.overall == NOT_JUDGED:                      # --split-only
        L.extend(("", s) for s in _page_types_lines(r))
    groups: Dict[str, List[Finding]] = {k: [] for k in CROSS_LINES}
    for f in r.cross:
        groups[_cross_line_of(f)].append(f)
    for name in CROSS_LINES:
        fs = groups[name]
        if not fs:
            continue
        lab = _line_label(fs)
        if name == "BUNDLE" and lab == PASS:
            continue
        if r.mode == "folder" and name == "FINANCIAL CHAIN":
            fs = [_naming_its_file(f, r) for f in fs]
        reasons = V.reasons_of(fs)
        L.append((lab, f"{name}: {lab}" + (f" — {_reason_text(reasons)}" if reasons else "")))
    fired = new_checks_fired(r)
    if fired:
        parts = [f"{rule} on {title_of(u)} ({where(u, r.mode)})" if u is not None else rule for rule, u in fired]
        L.append((FLAG, "NEW CHECKS (owner to confirm): " + "; ".join(parts)))
    assumed = list(dict.fromkeys(f.message for f in r.cross if f.rule in ASSUMED_RULES))
    if assumed:
        L.append((NOTE, "ASSUMED (owner to confirm): " + "; ".join(assumed)))
    if present_only:
        L.append((PRESENT, "Not checked (present): " + "; ".join(
            f"{title_of(d.unit)} ({where(d.unit, r.mode)})" for d in present_only)))
    L.extend(not_checked_lines(r))
    L.append(("", _report_line(out)))
    return _fit(L)


def not_checked_lines(r: RunResult) -> List[Tuple[str, str]]:
    """Folder mode (revision 2.3): "Not checked (not a PDF or image): <file>; <file>" and "Not checked (could not
    be opened): <file>", one line per reason, for the files the runner could not read. Never a document line,
    never a slot, never a verdict."""
    by: Dict[str, List[str]] = {}
    for item in (getattr(r, "not_checked", None) or []):
        try:
            name, why = item
        except (TypeError, ValueError):
            continue
        by.setdefault(str(why), []).append(str(name))
    return [("", f"Not checked ({why}): " + "; ".join(names)) for why, names in by.items()]


def _report_line(out: Optional[Path]) -> str:
    if out is None:
        return "Report: report.xlsx | JSON: run.json"
    return f"Report: {Path(out) / 'report.xlsx'} | JSON: {Path(out) / 'run.json'}"


def _fit(L: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Keep the summary at MAX_LINES: first the PASS document lines are joined into "PASS: A; B; ...", then the
    middle is cut with one "... (+N more lines in report.xlsx)" line (the first three and the last line stay)."""
    if len(L) <= MAX_LINES:
        return L
    passes = [i for i, (lab, s) in enumerate(L) if i >= 2 and lab == PASS and ": PASS" in s   # never the RESULT line
              and not s.startswith("ORDER") and not s.startswith(CROSS_LINES)]
    if passes:
        names = [L[i][1].split(": PASS")[0] for i in passes]
        joined, cur = [], ""
        for n in names:
            if cur and len(cur) + len(n) + 2 > 200:
                joined.append((PASS, "PASS: " + cur))
                cur = n
            else:
                cur = f"{cur}; {n}" if cur else n
        joined.append((PASS, "PASS: " + cur))
        first, gone = passes[0], set(passes)
        L = [x for i, x in enumerate(L) if i not in gone]
        L[first:first] = joined
    if len(L) <= MAX_LINES:
        return L
    keep_head, keep_tail = MAX_LINES - 2, 1
    cut = len(L) - keep_head - keep_tail
    return L[:keep_head] + [("", f"... (+{cut} more lines in report.xlsx)")] + L[-keep_tail:]


def summary_text(r: RunResult, out: Optional[Path] = None) -> str:
    return "\n".join(s for _, s in summary_lines(r, out)) + "\n"


# ================================================================================= Excel
def _units_of(r: RunResult) -> List[DocUnit]:
    return [d.unit for d in r.docs] if r.docs else list(r.split.units if r.split else [])


def _evidence_run_pages(f: Finding, r: RunResult) -> List[int]:
    """The 0-based run pages a cross-file finding carries in its evidence ("run_pages"), else []."""
    rp = (f.evidence or {}).get("run_pages") if isinstance(f.evidence, dict) else None
    if not isinstance(rp, (list, tuple)):
        return []
    return sorted({int(p) for p in rp if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < len(r.pages)})


def file_pages_label(run_pages: Iterable[int], r: RunResult) -> str:
    """Folder mode: "TIN CERTIFICATE.pdf p1; BANK SOLVENCY.pdf p2" (each page with its own file, review 2);
    bundle mode: "1,2"."""
    ps = sorted(set(run_pages))
    if r.mode != "folder":
        return ",".join(str(p + 1) for p in ps)
    by: Dict[str, List[int]] = {}
    for p in ps:
        by.setdefault(r.pages[p].file, []).append(r.pages[p].file_page + 1)
    return "; ".join(f"{name} p" + ",".join(str(n) for n in sorted(set(nums))) for name, nums in by.items())


def _finding_run_pages(f: Finding, u: Optional[DocUnit], r: RunResult) -> List[int]:
    """0-based run pages a finding names. Its evidence "run_pages" when it carries them (a finding about pages of
    two files). Bundle: its 1-based input pages. Folder: pages of the unit's file."""
    rp = _evidence_run_pages(f, r)
    if rp:
        return rp
    if not f.pages:
        return []
    if r.mode == "folder" and u is not None:
        fname = u.source_file or (r.pages[u.pages[0]].file if u.pages else "")
        return [p.index for p in r.pages if p.file == fname and (p.file_page + 1) in f.pages]
    return [p - 1 for p in f.pages if isinstance(p, int) and 0 < p <= len(r.pages)]


def _short_json(x: Any, n: int = 500) -> str:
    if not x:
        return ""
    s = json.dumps(to_jsonable(x), ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def _sheet(ws, header: Sequence[str], rows: Sequence[Sequence[Any]], widths: Sequence[int],
           colour_cols: Sequence[int] = (), start_row: int = 1) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    hdr, hfill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="44546A")
    wrap = Alignment(wrap_text=True, vertical="top")
    for j, h in enumerate(header, 1):
        c = ws.cell(row=start_row, column=j, value=h)
        c.font, c.fill, c.alignment = hdr, hfill, wrap
    for i, row in enumerate(rows, start_row + 1):
        for j, val in enumerate(row, 1):
            _put(ws, i, j, val, wrap)
            if j in colour_cols and isinstance(val, str):
                f = _fill_for(val)
                if f:
                    ws.cell(row=i, column=j).fill = PatternFill("solid", fgColor=f)
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(header))}{start_row + len(rows)}"


def _fill_for(val: str) -> Optional[str]:
    v = val.strip().upper()
    for k in sorted(FILL, key=len, reverse=True):
        if v == k or v.startswith(k + " ") or v.startswith(k + ":"):
            return FILL[k]
    return None


def _put(ws, row: int, col: int, val: Any, align=None):
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    if isinstance(val, str):
        val = ILLEGAL_CHARACTERS_RE.sub("", val)
        if len(val) > 32000:
            val = val[:32000] + "…"
    elif val is not None and not isinstance(val, (int, float, bool, dt.date, dt.datetime)):
        val = str(val)
    c = ws.cell(row=row, column=col, value=val)
    if isinstance(val, str) and val.startswith("="):
        c.data_type = "s"                        # text, never a formula
    if align is not None:
        c.alignment = align
    return c


def _facts(r: RunResult, extra: Optional[Mapping[str, Any]]) -> List[Tuple[str, str]]:
    rd = r.reading or {}
    by = rd.get("pages_by_engine") or {}
    facts: List[Tuple[str, str]] = [
        ("runner version", f"{r.runner_version} (schema {r.schema})"),
        ("rules", f"bot {r.base_rules_version} + runner {r.runner_rules_version}"),
        ("case id", r.case_id), ("input", r.input), ("mode", r.mode), ("program", r.program), ("today", r.today),
        ("reading", f"gpu {by.get('gpu', 0)}, cpu {by.get('cpu', 0)}, layer {by.get('layer', 0)}"),
        ("reading note", str(rd.get("note") or "")), ("page texts file", str(rd.get("page_texts") or "")),
        ("timings (s)", ", ".join(f"{k} {v:.1f}" for k, v in (r.timings or {}).items())),
        ("engine stats", _short_json(rd.get("engine_stats"), 1000)),
    ]
    sf = r.safety or {}
    facts.append(("safety: blocked events", str(len(sf.get("blocked") or [])) if isinstance(sf.get("blocked"), list)
                  else str(sf.get("blocked", ""))))
    facts.append(("safety: opens under C:\\Hangeul\\BOT", str(sf.get("bot_opens", ""))))
    for k in ("input_unchanged", "unchanged", "snapshot_before", "snapshot_after", "spawned"):
        if k in sf:
            facts.append((f"safety: {k}", _short_json(sf[k], 1000) if not isinstance(sf[k], (str, bool, int))
                          else str(sf[k])))
    idn = r.identity
    if idn is not None:
        def ref(n) -> str:
            return (f"{n.source or 'not read'}; confirmed={n.confirmed}"
                    + ("; truncated MRZ" if n.truncated else "") + ("; dots from the print" if n.written else ""))
        facts += [("identity: student", ref(idn.student)), ("identity: father", ref(idn.father)),
                  ("identity: mother", ref(idn.mother)),
                  ("identity: father NID", ref(idn.father_nid)), ("identity: mother NID", ref(idn.mother_nid)),
                  ("identity: DOB", f"{idn.dob_source or 'not read'}; trusted={idn.dob_trusted}"),
                  ("identity: expiry", f"{idn.expiry_source or 'not read'}; trusted={idn.expiry_trusted}"),
                  ("identity: MRZ", f"line 2 ok={bool((idn.mrz or {}).get('line2_ok'))}; "
                                    f"name trusted={idn.name_trusted}")]
    fired = new_checks_fired(r)
    facts.append(("new checks fired", "; ".join(f"{rule} ({title_of(u) if u else 'cross'})" for rule, u in fired)
                  or "none"))
    if extra and extra.get("names_rereads") is not None:
        facts.append(("name re-reads", str(len(extra.get("names_rereads") or []))))
    return facts


def write_excel(r: RunResult, path: Path, extra: Optional[Mapping[str, Any]] = None) -> None:
    """Sheets in this order: Summary, Documents, Pages, Findings (RUNNER_SPEC 5.11.2)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    path = Path(path)
    _assert_writable(path)
    wb = Workbook()
    # ---- Summary
    ws = wb.active
    ws.title = SHEETS[0]
    lines = summary_lines(r, path.parent)
    _sheet(ws, ["verdict", "summary line"], [(lab, s) for lab, s in lines], [14, 160], colour_cols=(1,))
    row = len(lines) + 3
    ws.cell(row=row, column=1, value="run facts").font = Font(bold=True)
    for k, v in _facts(r, extra):
        row += 1
        _put(ws, row, 1, k).font = Font(bold=True)
        _put(ws, row, 2, v)
    row += 2
    ws.cell(row=row, column=1, value="assumptions (owner to confirm)").font = Font(bold=True)
    for a in ASSUMPTIONS:
        row += 1
        _put(ws, row, 2, a)
    # ---- Documents
    ws = wb.create_sheet(SHEETS[1])
    rows = []
    for d in display_docs(r):
        u = d.unit
        fails = [f.message for f in d.findings if f.verdict == FAIL]
        flags = [(V.ERROR_PREFIX + f.message if f.verdict == ERROR else f.message)
                 for f in d.findings if f.verdict in (FLAG, ERROR)]
        notes = [f"{f.verdict}: {f.message}" for f in d.findings if f.verdict in (NOTE, PRESENT)]
        if d.verdict == NOT_JUDGED:
            flags = list(d.reasons) + flags
        rows.append([u.uid, title_of(u), u.key, u.fine, u.level, where(u, r.mode) if r.mode == "folder"
                     else pages_label(u.pages), u.path.name if u.path else "", u.role, u.role_how, d.verdict,
                     "\n".join(fails), "\n".join(flags), "\n".join(notes),
                     sum(1 for f in d.findings if f.verdict == PASS)])
    _sheet(ws, ["uid", "title", "key", "fine", "level", "pages", "cut_file", "role", "role_how", "verdict",
                "FAIL reasons", "FLAG reasons", "notes", "pass_count"], rows,
           [6, 26, 16, 22, 8, 12, 32, 8, 12, 12, 70, 70, 60, 8], colour_cols=(10,))
    # ---- Pages
    ws = wb.create_sheet(SHEETS[2])
    units = _units_of(r)
    unit_of: Dict[int, DocUnit] = {}
    for u in units:
        for i in u.pages:
            unit_of.setdefault(i, u)
    verdict_of = {d.unit.uid: d for d in r.docs}
    types = {t.index: t for t in r.types}
    on_page: Dict[int, Tuple[List[str], List[str]]] = {}
    for d in r.docs:
        for f in d.findings:
            if f.verdict not in (FAIL, FLAG, ERROR):
                continue
            for i in _finding_run_pages(f, d.unit, r):
                fails, flags = on_page.setdefault(i, ([], []))
                (fails if f.verdict == FAIL else flags).append(f"{f.rule}: {f.message}")
    rows = []
    for p in sorted(r.pages, key=lambda p: p.index):
        t = types.get(p.index)
        u = unit_of.get(p.index)
        d = verdict_of.get(u.uid) if u is not None else None
        second = ""
        if t is not None and t.scores:
            ranked = sorted(t.scores.items(), key=lambda kv: -kv[1])
            if len(ranked) > 1:
                second = f"{ranked[1][0]}: {ranked[1][1]:.1f}"
        nts = [f"{n.kind}: {n.message}" for n in r.notices if n.pages[0] <= p.index + 1 <= n.pages[1]]
        fails, flags = on_page.get(p.index, ([], []))
        rows.append([p.index + 1, p.file, p.file_page + 1, p.kind, p.engine, p.chars, round(p.seconds, 2),
                     p.sideways, t.best if t else "", round(t.score, 2) if t else "", second,
                     t.translation if t else "", (t.level or "") if t else "", t.kind if t else "",
                     u.uid if u else "", title_of(u) if u else "", u.role if u else "", u.role_how if u else "",
                     u.misplaced if u else "", "\n".join(nts), d.verdict if d else "", "\n".join(fails),
                     "\n".join(flags), (p.text or "")[:160].replace("\r", "").replace("\n", " | ")])
    _sheet(ws, ["page", "file", "file_page", "read", "engine", "chars", "seconds", "sideways", "type", "score",
                "second", "translation", "academic_level", "kind", "unit", "document", "role", "role_how",
                "misplaced", "notices", "doc_verdict", "fails", "flags", "text_start"], rows,
           [6, 28, 8, 7, 7, 7, 8, 8, 20, 7, 20, 10, 10, 11, 6, 24, 8, 10, 9, 50, 12, 60, 60, 70],
           colour_cols=(21,))
    # ---- Findings
    ws = wb.create_sheet(SHEETS[3])
    rows = []
    for d in r.docs:
        for f in d.findings:
            rows.append([d.unit.uid, title_of(d.unit), _pages_of_finding(f, d.unit, r), f.verdict, f.rule, f.source,
                         f.message, _short_json(f.evidence)])
    for f in r.cross:
        rows.append(["", _cross_line_of(f), cross_pages(f, r), f.verdict, f.rule, f.source, f.message,
                     _short_json(f.evidence)])
    for n in r.notices:
        rows.append([",".join(n.units), "", V.page_span(*n.pages), n.kind, f"notice.{n.kind.lower()}",
                     "split" if n.kind != ORDER or not n.units else "verdict", n.message,
                     _short_json({"blocks": n.blocks}) if n.kind == ORDER else ""])
    for lv, text in r.missing_lines:
        rows.append(["", "", "", lv, "missing", "verdict", text, ""])
    for rr in (extra or {}).get("names_rereads") or []:
        rr = to_jsonable(rr)
        page = rr.get("page") if isinstance(rr, dict) else None
        rp = rr.get("run_page") if isinstance(rr, dict) else None
        if r.mode == "folder" and isinstance(rp, int) and 0 <= rp < len(r.pages):
            where_rr = file_pages_label([rp], r)                                # the page with its file
        else:
            where_rr = "" if page is None else str(page)                       # "page" is already 1-based
        rows.append(["", "NAMES", where_rr,
                     "REREAD", "names.reread", "runner:names",
                     str(rr.get("effect", "")) if isinstance(rr, dict) else str(rr), _short_json(rr)])
    _sheet(ws, ["uid", "title", "pages", "verdict", "rule", "source", "message", "evidence"], rows,
           [6, 26, 10, 11, 28, 24, 100, 80], colour_cols=(4,))
    wb.save(str(path))


def cross_pages(f: Finding, r: RunResult) -> str:
    """The pages cell of a cross line's Findings row: its run pages with their files; else, in folder mode, the
    file of the document line that carries the same finding (a FINANCIAL CHAIN row names its file, review 2)."""
    rp = _evidence_run_pages(f, r)
    if rp:
        return file_pages_label(rp, r)
    if r.mode == "folder" and f.pages:
        home = next((d.unit for d in (r.docs or []) if any(f == g for g in d.findings)), None)
        if home is not None:
            return _pages_of_finding(f, home, r)
    return ",".join(str(p) for p in f.pages)


_PAGE_REF = re.compile(r"\bpages? \d")


def _naming_its_file(f: Finding, r: RunResult) -> Finding:
    """Folder mode, the FINANCIAL CHAIN line (it belongs to no file): a message that cites a page of the document
    line carrying the finding ("check by eye, page 2") gets that file's name after it, as the document line shows it
    ("... page 2 (BANK SOLVENCY.pdf)"). A message that already names a file of the run ("page 2 of BANK
    SOLVENCY.pdf") or cites no page is kept (review 2, C1)."""
    m = f.message or ""
    if not _PAGE_REF.search(m) or any(p.file and p.file in m for p in r.pages):
        return f
    home = next((d.unit for d in (r.docs or []) if any(f == g for g in d.findings)), None)
    name = (home.source_file or (r.pages[home.pages[0]].file if home.pages else "")) if home is not None else ""
    if not name:
        return f
    return dataclasses.replace(f, message=f"{m} ({name})")


def _pages_of_finding(f: Finding, u: DocUnit, r: RunResult) -> str:
    rp = _evidence_run_pages(f, r)
    if rp:
        return file_pages_label(rp, r)
    if not f.pages:
        return ""
    if r.mode == "folder":
        return f"{u.source_file} p" + ",".join(str(p) for p in f.pages)
    return ",".join(str(p) for p in f.pages)


# ================================================================================= all three
def write_all(r: RunResult, out: Path, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Path]:
    """summary.txt, report.xlsx and run.json in `out` (each path checked by safety.assert_writable)."""
    out = Path(out)
    paths = {"summary": out / "summary.txt", "excel": out / "report.xlsx", "json": out / "run.json"}
    for p in paths.values():
        _assert_writable(p)
    paths["summary"].write_text(summary_text(r, out), encoding="utf-8")
    write_excel(r, paths["excel"], extra)
    write_json(r, paths["json"], extra)
    return paths
