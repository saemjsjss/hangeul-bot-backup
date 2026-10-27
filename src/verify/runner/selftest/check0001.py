"""T1 — CHECK_0001, the A6 regression (RUNNER_SPEC section 10, Task 6). Program KLP (A8).

    <runner venv python> -I -B -m src.verify.runner.selftest.check0001 [--text-only] [--gpu] [--folder]
                                                [--today YYYY-MM-DD] [--refresh] [--source <CHECK_0001.pdf>]

Steps (each prints ok / MISS, rule ids, page numbers and counts only; never a name, number or text):
  1. before   the owner's PDF: sha256 (must equal test1\\source_sha256_before.txt), size, mtime
  2. texts    test1\\pages.json (the existing GPU read) -> runs\\CHECK_0001\\page_texts.json via
              gpuread.py --from-legacy (no card)
  3. split    text level, no OCR: pagetype + split (+ identity without the engine for the roles) against the
              truth table
  4. run      the runner on the bundle with --page-texts; the expected KLP outcome (section 10, T1 step 4)
  5. gpu      (--gpu) run_case.py on the bundle -> runs\\CHECK_0001_gpu; its document and MISSING lines must
              equal step 4's (differences listed by rule id)
  6. after    the owner's PDF unchanged; the run's audit: 0 blocked events, 0 opens under C:\\Hangeul\\BOT
  7. folder   (--folder) test1\\docs copied with bot-style names into selftest\\check0001\\folder, the runner in
              folder mode; per-slot verdicts compared with the bundle run's (differences listed)

Without the owner's PDF (moved; --source names its new place) steps 1, 2, 4, 5 and 6 report MISS or are
skipped, step 3 reads test1\\pages.json directly, and step 7 still runs on test1\\docs.
The owner's file is only read. Every write is under C:\\Hangeul\\DOCCHECK\\lab\\private-runner. Each runner run is
a separate process (the runner's own audit hook). No personal data in this file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
SOURCE = Path(r"C:\Users\User\Downloads\CHECK_0001.pdf")
TEST1 = PRIVATE / "test1"
LEGACY = TEST1 / "pages.json"
SHA_BEFORE = TEST1 / "source_sha256_before.txt"
RUNS = PRIVATE / "runs"
OUT = RUNS / "CHECK_0001"
OUT_GPU = RUNS / "CHECK_0001_gpu"
OUT_FOLDER = RUNS / "CHECK_0001_folder"
SELF = PRIVATE / "selftest" / "check0001"
FOLDER = SELF / "folder"
PAGE_TEXTS = OUT / "page_texts.json"
PY = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe")
GPUREAD = Path(r"C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\gpuread.py")
RUN_CASE = PRIVATE / "run_case.py"
PROGRAM = "KLP"
N_PAGES = 14
# the owner's truth (T1 step 3): (key, level or fine type, first page, last page, role), pages 1-based
TRUTH = [("passport", "", 1, 1, ""), ("father_nid", "", 2, 3, "father"), ("mother_nid", "", 4, 5, "mother"),
         ("academic", "HSC", 6, 7, ""), ("academic", "SSC", 8, 9, ""),
         ("trade_license", "employment_certificate", 10, 10, ""), ("trade_license", "tin_certificate", 11, 11, ""),
         ("income_tax", "", 12, 14, "")]
MISSING_EXPECTED = {"PHOTO MISSING", "BIRTH CERTIFICATE MISSING", "FAMILY CERTIFICATE MISSING",
                    "BANK SOLVENCY / STATEMENT MISSING"}
SHEETS = ["Summary", "Documents", "Pages", "Findings"]
# the test-1 split files -> bot-style names (the p14 file is left out)
FOLDER_NAMES = {"01_passport.pdf": "PASSPORT.pdf", "02_nid_father.pdf": "FATHER NID.pdf",
                "03_nid_mother.pdf": "MOTHER NID.pdf", "04_academic_hsc.pdf": "ACADEMIC CERTIFICATE HSC.pdf",
                "05_academic_ssc.pdf": "ACADEMIC CERTIFICATE SSC.pdf",
                "06_employment_certificate.pdf": "EMPLOYMENT CERTIFICATE.pdf",
                "07_tin_certificate.pdf": "TIN CERTIFICATE.pdf", "08_income_tax.pdf": "INCOME TAX.pdf"}
LETTERS_NOTE = re.compile(r"\b\d+\s+letters?\b", re.I)


class Tally:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        print(f"{'ok  ' if ok else 'MISS'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)
        return bool(ok)

    def note(self, text: str) -> None:
        print(f"     {text}", flush=True)

    @property
    def misses(self) -> int:
        return sum(1 for _, ok, _ in self.rows if not ok)


# ------------------------------------------------------------------------------------------ helpers
def file_facts(p: Path) -> Tuple[str, int, int]:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    st = p.stat()
    return h.hexdigest(), st.st_size, st.st_mtime_ns


def expected_sha() -> str:
    line = SHA_BEFORE.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    return line.split()[0].lower()


def run_proc(args: Sequence[str], log: Path, timeout: int = 3600) -> Tuple[int, float]:
    """A child process; its stdout/stderr go to `log` (private) and are not printed."""
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        rc = subprocess.call(list(map(str, args)), stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    return rc, time.perf_counter() - t0


def runner_args(inp: Path, out: Path, page_texts: Optional[Path], today: Optional[str]) -> List[str]:
    a = [str(PY), "-I", "-B", "-m", "src.verify.runner", str(inp), "--program", PROGRAM, "--out", str(out)]
    if page_texts is not None:
        a += ["--page-texts", str(page_texts)]
    if today:
        a += ["--today", today]
    return a


def load_run(out: Path) -> Dict[str, Any]:
    return json.loads((out / "run.json").read_text(encoding="utf-8"))


def span(u: Dict[str, Any]) -> Tuple[int, int]:
    ps = u.get("pages") or []
    return (min(ps) + 1, max(ps) + 1) if ps else (0, 0)


def sub_of(u: Dict[str, Any]) -> str:
    return u.get("level") or (u.get("fine") if u.get("key") == "trade_license" else "") or ""


def find_doc(d: Dict[str, Any], key: str, a: int, b: int, sub: str = "") -> Optional[Dict[str, Any]]:
    for x in d.get("docs") or []:
        u = x["unit"]
        if u["key"] == key and span(u) == (a, b) and (not sub or sub_of(u) == sub):
            return x
    return None


def rules(x: Optional[Dict[str, Any]], *verdicts: str) -> List[str]:
    if not x:
        return []
    return [f["rule"] for f in x["findings"] if not verdicts or f["verdict"] in verdicts]


def lines_of(d: Dict[str, Any]) -> List[Tuple[str, str, Tuple[int, int], str]]:
    """(key, sub, pages, verdict) per document line, in page order."""
    return [(x["unit"]["key"], sub_of(x["unit"]), span(x["unit"]), x["verdict"]) for x in d.get("docs") or []]


# ------------------------------------------------------------------------------------------ steps
def step_before(t: Tally) -> Optional[Tuple[str, int, int]]:
    print("== 1. before")
    if not t.check("owner's PDF present (--source)", SOURCE.is_file()):
        return None
    facts = file_facts(SOURCE)
    t.check("sha256 equals test1 record", facts[0] == expected_sha())
    return facts


def step_texts(t: Tally, refresh: bool) -> bool:
    print("== 2. page texts (gpuread --from-legacy, no card)")
    if PAGE_TEXTS.is_file() and not refresh:
        t.note("page_texts.json exists (reused; --refresh rewrites it)")
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        rc, s = run_proc([str(PY), "-I", "-B", str(GPUREAD), "--from-legacy", str(LEGACY), "--source", str(SOURCE),
                          "--out", str(PAGE_TEXTS)], SELF / "gpuread_legacy.log", timeout=600)
        t.check("gpuread --from-legacy exit 0", rc == 0, f"exit {rc}")
    if not t.check("page_texts.json written", PAGE_TEXTS.is_file()):
        return False
    d = json.loads(PAGE_TEXTS.read_text(encoding="utf-8"))
    n = sum(len(f.get("pages") or []) for f in d.get("files") or [])
    t.check("page texts: schema runner-page-texts/1, complete, 14 pages",
            d.get("schema") == "runner-page-texts/1" and d.get("complete") is True and n == N_PAGES,
            f"schema {d.get('schema')}, complete {d.get('complete')}, {n} pages")
    return True


def _texts() -> List[Tuple[str, int, Dict[str, Any]]]:
    """(file name, 1-based page, page dict) from page_texts.json, else from the legacy test1 pages.json."""
    if PAGE_TEXTS.is_file():
        d = json.loads(PAGE_TEXTS.read_text(encoding="utf-8"))
        return [(f["name"], int(p["page"]), p) for f in d["files"] for p in f["pages"]]
    d = json.loads(LEGACY.read_text(encoding="utf-8"))
    out = []
    for k in sorted(d["pages"], key=int):
        p = dict(d["pages"][k])
        p.setdefault("kind", "layer" if str(p.get("method", "")).startswith("layer") else "ocr")
        out.append((SOURCE.name, int(k), p))
    return out


def step_split(t: Tally) -> None:
    print("== 3. text level (pagetype + split, no OCR)")
    if not PAGE_TEXTS.is_file():
        t.note("page_texts.json absent: the legacy test1 pages.json texts are used")
    from src.verify.runner import botimport, pagetype, split
    from src.verify.runner.model import PageRead
    try:
        from src.verify.runner import identity
    except ImportError:                                  # identity.py not landed yet: roles by position only
        identity = None
        t.note("identity.py not importable: the split gets empty reference names (roles by position)")
    botimport.load(botimport.DEFAULT_CACHE_ROOT)
    pages: List[PageRead] = []
    for name, page_no, p in _texts():
        text = p.get("text") or ""
        pages.append(PageRead(index=len(pages), file=name, file_page=page_no - 1, text=text,
                              kind=p.get("kind") or "ocr", chars=len(text), sideways=bool(p.get("sideways")),
                              seconds=float(p.get("seconds") or 0.0), image_only=True,
                              engine="layer" if p.get("kind") == "layer" else "gpu"))
    t.check("text level: 14 pages", len(pages) == N_PAGES, f"{len(pages)} pages")
    types = pagetype.type_pages(pages)
    seg = split.segment(types, PROGRAM)
    prov = split.provisional_units(seg, pages)
    work = SELF / "work"
    work.mkdir(parents=True, exist_ok=True)
    if identity is not None:
        ident = identity.build_identity(prov, pages, {}, None, work, lambda i, v: "unclear")
        refs = identity.refs(ident)
    else:
        refs = split.Refs()
    res = split.finish(seg, types, pages, refs, PROGRAM)
    got = {(u.key, (u.level or (u.fine if u.key == "trade_license" else "")), min(u.pages) + 1, max(u.pages) + 1): u
           for u in res.units if u.pages}
    for key, sub, a, b, role in TRUTH:
        u = got.get((key, sub, a, b))
        t.check(f"split {key}{' ' + sub if sub else ''} p{a}-{b}", u is not None)
        if u is not None and role:
            ok = u.role == role and (u.role_how == "name" or (u.role_how == "position" and any(
                f.rule == "split.role_position" for f in u.notes)))
            t.check(f"split role {role} p{a}-{b} ({u.role_how or 'none'})", ok, f"role {u.role or '-'}")
    extra = [k for k in got if (k[0], k[1], k[2], k[3]) not in {(x[0], x[1], x[2], x[3]) for x in TRUTH}]
    t.check("split: no other units", not extra, "; ".join(f"{k[0]} p{k[2]}-{k[3]}" for k in extra))
    t.check("split: no STOP", not res.stop)
    t.check("split: no ORDER notice naming page 14",
            not [n for n in res.notices if n.kind == "ORDER" and n.pages[0] <= N_PAGES <= n.pages[1]])


def step_run(t: Tally, today: Optional[str]) -> Optional[Dict[str, Any]]:
    print("== 4. full run (runner, --page-texts)")
    rc, s = run_proc(runner_args(SOURCE, OUT, PAGE_TEXTS, today), SELF / "run_bundle.log")
    t.note(f"runner exit {rc} in {s:.0f} s")
    t.check("exit 0 (no STOP)", rc == 0, f"exit {rc}")
    have = {n: (OUT / n).is_file() for n in ("summary.txt", "report.xlsx", "run.json")}
    if not t.check("three outputs", all(have.values()), ", ".join(k for k, v in have.items() if not v)):
        return None
    d = load_run(OUT)
    if "error" in d:
        t.check("run.json without error", False, f"stage {d.get('stage')}")
        return None
    check_expectations(t, d, (OUT / "summary.txt").read_text(encoding="utf-8").splitlines(), OUT / "report.xlsx")
    return d


def check_expectations(t: Tally, d: Dict[str, Any], summary: List[str], xlsx: Path) -> None:
    by = (d.get("reading") or {}).get("pages_by_engine") or {}
    t.check("READING: 14 pages from the GPU read (layer counted as layer)",
            by.get("gpu", 0) + by.get("layer", 0) == N_PAGES and by.get("cpu", 0) == 0,
            f"gpu {by.get('gpu')}, cpu {by.get('cpu')}, layer {by.get('layer')}")
    t.check("overall FAIL", d.get("overall") == "FAIL", str(d.get("overall")))
    for level, a, b in (("HSC", 6, 7), ("SSC", 8, 9)):
        x = find_doc(d, "academic", a, b, level)
        fails = rules(x, "FAIL")
        apostille = any(f["verdict"] == "FAIL" and f["rule"] == "bot.check_academic"
                        and "apostille" in f["message"].lower() for f in (x or {}).get("findings", []))
        t.check(f"ACADEMIC {level} (p{a}-{b}) FAIL", x is not None and x["verdict"] == "FAIL",
                x["verdict"] if x else "no unit")
        t.check(f"ACADEMIC {level}: FAIL no e-Apostille (bot.check_academic)", apostille, ",".join(fails))
        t.check(f"ACADEMIC {level}: FAIL names.mother (confirmed difference)", "names.mother" in fails,
                ",".join(fails))
        if x is not None:
            kinds = [str((f.get("evidence") or {}).get("kind", "")) for f in x["findings"]
                     if f["rule"] == "names.mother"]
            t.note(f"ACADEMIC {level} names.mother kind(s): {', '.join(k for k in kinds if k) or '-'}")
    ssc = find_doc(d, "academic", 8, 9, "SSC")
    student_fail = [r for r in rules(ssc, "FAIL") if r.startswith("names.student")]
    t.check("no false SSC student-name FAIL (A6 b)", not student_fail, ",".join(student_fail))
    dots = [f["verdict"] for f in (ssc or {}).get("findings", []) if f["rule"] == "names.student_dots"]
    t.note(f"SSC names.student_dots: {', '.join(dots) or 'none'}")
    missing = [txt for lv, txt in d.get("missing_lines") or [] if lv == "MISSING"]
    cond = [txt for lv, txt in d.get("missing_lines") or [] if lv != "MISSING"]
    t.check("MISSING lines exactly the four", set(missing) == MISSING_EXPECTED and len(missing) == 4 and not cond,
            f"{len(missing)} MISSING, {len(cond)} conditional; extra: "
            f"{', '.join(sorted(set(missing) - MISSING_EXPECTED))}; absent: "
            f"{', '.join(sorted(MISSING_EXPECTED - set(missing)))}")
    tin = find_doc(d, "trade_license", 11, 11, "tin_certificate")
    t.check("TIN CERTIFICATE (p11) FLAG", tin is not None and tin["verdict"] == "FLAG", tin["verdict"] if tin else "-")
    t.check("tin.web_address FLAG", "tin.web_address" in rules(tin, "FLAG"), ",".join(rules(tin, "FLAG")))
    t.check("tin.qr_tin PASS", "tin.qr_tin" in rules(tin, "PASS"), ",".join(rules(tin)))
    t.check("NEW CHECKS line lists tin.web_address",
            any(s.startswith("NEW CHECKS") and "tin.web_address" in s for s in summary))
    pp = find_doc(d, "passport", 1, 1)
    idn = d.get("identity") or {}
    t.check("PASSPORT (p1) PASS", pp is not None and pp["verdict"] == "PASS", pp["verdict"] if pp else "-")
    t.check("MRZ trusted (line2_ok; DOB and expiry from the MRZ)",
            bool((idn.get("mrz") or {}).get("line2_ok")) and idn.get("dob_source") in ("MRZ", "MRZ re-read")
            and idn.get("expiry_source") in ("MRZ", "MRZ re-read"),
            f"line2_ok {(idn.get('mrz') or {}).get('line2_ok')}, dob {idn.get('dob_source')}, "
            f"expiry {idn.get('expiry_source')}")
    mrz_rows = [f["rule"] for f in (pp or {}).get("findings", []) if "mrz" in f["rule"].lower()
                and f["verdict"] in ("NOTE", "FLAG", "FAIL", "ERROR")]
    t.check("no MRZ FLAG or NOTE on the passport", not mrz_rows, ",".join(mrz_rows))
    p14 = [n for n in d.get("notices") or [] if n["kind"] == "ORDER" and n["pages"][0] <= 14 <= n["pages"][1]]
    t.check("no ORDER notice naming page 14 (A6 f)", not p14)
    itax = find_doc(d, "income_tax", 12, 14)
    t.check("INCOME TAX one line p12-14", itax is not None)
    t.check("INCOME TAX split.attached_trailing NOTE", "split.attached_trailing" in rules(itax, "NOTE"),
            ",".join(rules(itax)))
    other_flags = [r for r in rules(itax, "FLAG", "FAIL", "ERROR") if r != "income_tax.tax_office"]
    t.check("INCOME TAX PRESENT (A6 h)", itax is not None and (itax["verdict"] == "PRESENT" or (
        itax["verdict"] == "FLAG" and not other_flags)), (itax or {}).get("verdict", "-"))
    bot_flags = [f for f in (itax or {}).get("findings", []) if f["source"] == "bot:check_income_tax"
                 and f["verdict"] in ("FLAG", "FAIL")]
    t.check("INCOME TAX: the bot's rows are NOTEs, not FLAGs", not bot_flags, f"{len(bot_flags)} FLAG/FAIL")
    if "income_tax.tax_office" in rules(itax, "FLAG"):
        t.note("income_tax.tax_office FLAG fired: confirm by eye (both circles read), then it becomes the expectation")
    mn = find_doc(d, "mother_nid", 4, 5)
    t.check("MOTHER NID (p4-5): nid.number PASS (A6 d)", "nid.number" in rules(mn, "PASS"), ",".join(rules(mn)))
    nc = [f["rule"] for f in (mn or {}).get("findings", []) if "not compared" in f["message"].lower()]
    t.check("MOTHER NID: no 'not compared' NOTE", not nc, ",".join(nc))
    msgs = [(x["unit"]["uid"], f["rule"]) for x in d.get("docs") or [] for f in x["findings"]
            if LETTERS_NOTE.search(f["message"] or "")]
    msgs += [("", f["rule"]) for f in d.get("cross") or [] if LETTERS_NOTE.search(f["message"] or "")]
    t.check("no 'N letters' name-length message (A6 e)", not msgs, "; ".join(f"{u} {r}" for u, r in msgs))
    emp = find_doc(d, "trade_license", 10, 10, "employment_certificate")
    t.check("EMPLOYMENT (p10) and TIN (p11) two lines (A6 g)", emp is not None and tin is not None)
    t.check("EMPLOYMENT CERTIFICATE PASS or FLAG, never FAIL", emp is not None and emp["verdict"] in ("PASS", "FLAG"),
            (emp or {}).get("verdict", "-"))
    t.check("academic.board_qr PASS on SSC (A6 c)", "academic.board_qr" in rules(ssc, "PASS"), ",".join(rules(ssc)))
    for key, a, b, role in (("father_nid", 2, 3, "father"), ("mother_nid", 4, 5, "mother")):
        x = find_doc(d, key, a, b)
        u = (x or {}).get("unit") or {}
        ok = x is not None and u.get("role") == role and (
            u.get("role_how") == "name" or (u.get("role_how") == "position" and "split.role_position" in rules(x)))
        t.check(f"role p{a}-{b} {role} ({u.get('role_how') or '-'})", ok)
    t.check("run.json schema runner-run/2", d.get("schema") == "runner-run/2", str(d.get("schema")))
    try:
        from openpyxl import load_workbook
        wb = load_workbook(xlsx, read_only=True)
        t.check("report.xlsx sheets Summary, Documents, Pages, Findings", wb.sheetnames == SHEETS,
                ",".join(wb.sheetnames))
        t.check("report.xlsx Pages rows = 14", sum(1 for _ in wb["Pages"].iter_rows(min_row=2)) == N_PAGES)
        wb.close()
    except Exception as e:                                # a broken workbook is a MISS, not a crash
        t.check("report.xlsx opens", False, type(e).__name__)


def step_gpu(t: Tally, base: Optional[Dict[str, Any]], today: Optional[str]) -> None:
    print("== 5. GPU variant (run_case.py)")
    args = [str(PY), "-I", "-B", str(RUN_CASE), str(SOURCE), "--program", PROGRAM, "--out", str(OUT_GPU)]
    if today:
        args += ["--today", today]
    rc, s = run_proc(args, SELF / "run_case_gpu.log")
    t.note(f"run_case exit {rc} in {s:.0f} s")
    if not t.check("run_case exit 0", rc == 0, f"exit {rc}") or base is None:
        return
    d = load_run(OUT_GPU)
    note = (d.get("reading") or {}).get("note") or ""
    if note:
        t.note("reading note present (the GPU slot may not have been free): see the summary's READING line")
    compare_runs(t, "gpu", base, d)


def compare_runs(t: Tally, label: str, a: Dict[str, Any], b: Dict[str, Any], by_slot: bool = False) -> None:
    def keyed(d: Dict[str, Any]) -> Dict[Tuple[str, str, Any], Dict[str, Any]]:
        out = {}
        for x in d.get("docs") or []:
            u = x["unit"]
            k = (u["key"], sub_of(u), u.get("role") or "") if by_slot else (u["key"], sub_of(u), span(u))
            out[k] = x
        return out
    ka, kb = keyed(a), keyed(b)
    diffs = []
    for k in sorted(set(ka) | set(kb), key=str):
        xa, xb = ka.get(k), kb.get(k)
        if xa is None or xb is None:
            diffs.append(f"{k[0]} {k[1]}: only in {'bundle' if xb is None else label}")
            continue
        if xa["verdict"] != xb["verdict"]:
            ra = {(f["rule"], f["verdict"]) for f in xa["findings"]}
            rb = {(f["rule"], f["verdict"]) for f in xb["findings"]}
            ch = sorted({r for r, _ in ra ^ rb})
            diffs.append(f"{k[0]} {k[1]}: {xa['verdict']} vs {xb['verdict']} ({', '.join(ch)})")
    ma = sorted(txt for _, txt in a.get("missing_lines") or [])
    mb = sorted(txt for _, txt in b.get("missing_lines") or [])
    if ma != mb:
        diffs.append(f"MISSING lines differ: {len(ma)} vs {len(mb)}")
    t.check(f"{label}: document and MISSING lines equal the bundle run's", not diffs)
    for x in diffs:
        t.note(f"{label} difference: {x}")


def step_after(t: Tally, before: Optional[Tuple[str, int, int]], d: Optional[Dict[str, Any]]) -> None:
    print("== 6. after")
    if before is not None:
        t.check("owner's PDF unchanged (sha256, size, mtime)", file_facts(SOURCE) == before)
    if d is not None:
        sf = d.get("safety") or {}
        blocked = sf.get("blocked_count", len(sf.get("blocked") or []))
        t.check("audit: 0 blocked events", blocked == 0, f"{blocked} blocked")
        t.check("audit: 0 opens under C:\\Hangeul\\BOT", sf.get("bot_opens") == 0, str(sf.get("bot_opens")))
        if "input_unchanged" in sf:
            t.check("audit: input snapshot unchanged", sf.get("input_unchanged") is True)


def step_folder(t: Tally, base: Optional[Dict[str, Any]], today: Optional[str]) -> None:
    print("== 7. folder twin")
    FOLDER.mkdir(parents=True, exist_ok=True)
    for src_name, dst_name in FOLDER_NAMES.items():
        src, dst = TEST1 / "docs" / src_name, FOLDER / dst_name
        if not src.is_file():
            t.check(f"test1 split file {src_name}", False)
            return
        if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
    rc, s = run_proc(runner_args(FOLDER, OUT_FOLDER, None, today), SELF / "run_folder.log")
    t.note(f"folder run exit {rc} in {s:.0f} s")
    if not t.check("folder run exit 0", rc == 0, f"exit {rc}") or base is None:
        return
    compare_runs(t, "folder", base, load_run(OUT_FOLDER), by_slot=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -I -B -m src.verify.runner.selftest.check0001")
    ap.add_argument("--text-only", action="store_true", help="steps 1-3 only (no runner run)")
    ap.add_argument("--gpu", action="store_true", help="also step 5 (run_case.py, the GPU pre-read)")
    ap.add_argument("--folder", action="store_true", help="also step 7 (the folder twin; CPU, minutes)")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD passed to the runner")
    ap.add_argument("--refresh", action="store_true", help="rewrite page_texts.json from test1\\pages.json")
    ap.add_argument("--source", default=str(SOURCE), help="where the owner's CHECK_0001.pdf is now (read-only)")
    a = ap.parse_args(argv)
    globals()["SOURCE"] = Path(a.source)
    SELF.mkdir(parents=True, exist_ok=True)
    t = Tally()
    before = step_before(t)
    if before is not None:
        step_texts(t, a.refresh)
    step_split(t)
    d = None
    if not a.text_only and before is not None and PAGE_TEXTS.is_file():
        d = step_run(t, a.today)
        if a.gpu:
            step_gpu(t, d, a.today)
    if a.folder and not a.text_only:
        step_folder(t, d, a.today)
    if before is not None:
        step_after(t, before, d)
    result = {"checks": len(t.rows), "misses": t.misses,
              "missed": [n for n, ok, _ in t.rows if not ok]}
    (SELF / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"== {len(t.rows) - t.misses}/{len(t.rows)} ok, {t.misses} MISS")
    return 0 if t.misses == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
