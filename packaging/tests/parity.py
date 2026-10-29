"""P1 parity with r8 (APP_SPEC rev 2, R2.6): the frozen app against the r8 runs, on THIS PC's graphics card.

    <runner python> -I -B packaging\\tests\\parity.py --build-id <id> [--cases owner_f3_r8,test1_r8] [--no-auto]
                    [--app-dir <app folder>] [--timeout 3600] [--input LABEL=PATH ...]

For each r8 case (owner folder 3, owner folder 9, the EAP folder, CHECK_0001 = test 1) it reads the input, program
and ``today`` from ``private-runner\\runs\\<case>\\run.json``, runs the frozen app ``--cli`` with a FRESH
``--data-dir`` through ``cardrun`` (NEEDS ORCHESTRATOR APPROVAL Q-O1), and compares:
  (a) page texts: the app's ``<DATA>\\cache\\pagetexts\\<case_id>.json`` against r8's ``page_texts.json`` (file
      names, sha256, page counts, every page's text, kind and sideways flag). Differing pages are listed by number
      with character counts. A difference here does not fail the build but must be explained before hand-over.
  (b) summary.txt lines, except the READING line, the RESULT CODE line and the output-path ("Report:") line;
  (c) run.json: ``overall``; ``docs[*]`` unit key and pages, verdict, ``findings[*].(verdict, rule, pages)``; the
      cross findings (verdict, rule, pages); ``missing_lines``; ``notices``;
  (d) every closer look (run.json ``names_rereads``): decision, best candidate, dot (and its page and candidates);
  (e) the result code per case; CHECK_0001's is THIS_PC_CODE. One extra ``--auto`` run of CHECK_0001 (the window ->
      worker path) must give the same code (and, when its case folder is found, the same (b)-(d)).
The build passes only when (b)-(d) hold for all four cases. Also reported: calls on the processor and waits.

Prints case ids, counts, rule ids, page numbers and codes only: never text, names or paths. Everything the app
writes goes under ``private-runner\\apptest\\<build-id>\\p1-<stamp>\\`` (a new folder every time; nothing is ever
deleted); the report (counts and codes only) is ``parity.json`` there, copied to ``apptest\\<build-id>\\
p1-latest.json`` and to ``builds\\<build-id>\\reports\\parity.json``. Exit 0 PASS, 1 FAIL, 2 could not run.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))      # -I drops the script folder from sys.path

import argparse  # noqa: E402
import difflib  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import cardrun  # noqa: E402

RUNS = cardrun.PRIVATE / "runs"
CASES: Tuple[Tuple[str, str], ...] = (
    ("owner_f3_r8", "owner folder 3"),
    ("owner_f9_r8", "owner folder 9"),
    ("owner_eap1_r8", "the EAP folder"),
    ("test1_r8", "CHECK_0001 (test 1)"),
)
CHECK_CASE = "test1_r8"
DEFAULT_TIMEOUT_S = 3600.0                  # one check incl. the app's own waits for the bot
SKIP_PREFIXES = ("READING:", "RESULT CODE:", "Report:")
DONE_EXITS = (0, 3)                         # rev 1 §2: 0 done, 3 done with STOP
_VERDICT_RE = re.compile(r":\s+(NOT JUDGED|PASS|FLAG|FAIL|ERROR|MISSING|PRESENT|NOTE|STOP|OK)\b")
_KIND_RE = re.compile(r"^(ORDER|NOTE|HINT|STOP|NAMES|FINANCIAL CHAIN|BUNDLE|NEW CHECKS|ASSUMED)\b")
_LABEL_RE = re.compile(r"^([A-Z][A-Z ]{1,40}?)\s*(?:\(|:)")


@dataclass
class R8Case:
    label: str
    desc: str
    dir: Path
    input: str
    program: str
    today: Optional[str]
    case_id: str
    mode: str


def load_r8(label: str, runs: Path = RUNS, desc: str = "") -> R8Case:
    d = Path(runs) / label
    r = cardrun.read_json(d / "run.json")
    return R8Case(label=label, desc=desc or label, dir=d, input=str(r["input"]), program=str(r["program"]),
                  today=r.get("today") or None, case_id=str(r.get("case_id") or ""), mode=str(r.get("mode") or ""))


def parse_inputs(specs: Sequence[str], known: Sequence[str]) -> Dict[str, str]:
    """``--input LABEL=PATH`` (repeatable): check PATH instead of the input r8's run.json names, for a case whose
    original input is gone (the owner removed Desktop folders 3 and 9; G2 read them from the owner's zips into a
    private test folder). The stand-in must be the SAME input: (a) compares the file names, sha256 values and page
    counts, and (c) fails when the app's case id (names, sizes and modification times) differs from r8's. The path
    is never printed."""
    out: Dict[str, str] = {}
    for spec in specs or ():
        label, sep, path = str(spec).partition("=")
        label, path = label.strip(), path.strip()
        if not sep or not label or not path:
            raise ValueError("--input takes LABEL=PATH")
        if label not in known:
            raise ValueError("--input: unknown case; known: " + ", ".join(known))
        if not Path(path).is_absolute() or not Path(path).exists():
            raise ValueError(f"--input {label}: the path must be absolute and exist")
        out[label] = path
    return out


def apply_inputs(cases: Sequence[R8Case], inputs: Dict[str, str]) -> List[str]:
    """Replaces the inputs of the named cases; returns the labels whose input was replaced."""
    done = []
    for c in cases:
        if c.label in inputs:
            c.input = inputs[c.label]
            done.append(c.label)
    return done


# ------------------------------------------------------------------------------------------------ (a) page texts

def _pages(store: Dict[str, Any]) -> List[Tuple[int, int, Dict[str, Any]]]:
    out = []
    for fi, f in enumerate(store.get("files") or []):
        for pi, p in enumerate(f.get("pages") or []):
            out.append((fi, pi, p))
    return out


def _diff_chars(a: str, b: str) -> int:
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")


def compare_page_texts(r8: Dict[str, Any], app: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts and page numbers only; never text or file names."""
    rep: Dict[str, Any] = {"equal": False, "pages_r8": len(_pages(r8)), "pages_app": 0, "equal_pages": 0,
                           "problems": [], "differing": []}
    if app is None:
        rep["problems"].append("the app's page-texts file was not found")
        return rep
    rep["pages_app"] = len(_pages(app))
    if not app.get("complete", False):
        rep["problems"].append("the app's page-texts file is not complete")
    fr, fa = r8.get("files") or [], app.get("files") or []
    if len(fr) != len(fa):
        rep["problems"].append(f"files: {len(fr)} in r8, {len(fa)} in the app")
    for i, (a, b) in enumerate(zip(fr, fa), start=1):
        for k, what in (("name", "name"), ("sha256", "sha256"), ("n_pages", "page count")):
            if a.get(k) != b.get(k):
                rep["problems"].append(f"file {i}: {what} differs")
    app_pages = {(fi, pi): p for fi, pi, p in _pages(app)}
    for n, (fi, pi, p) in enumerate(_pages(r8), start=1):
        q = app_pages.get((fi, pi))
        if q is None:
            rep["differing"].append({"page": n, "file": fi + 1, "file_page": pi + 1, "missing_in_app": True})
            continue
        ta, tb = str(p.get("text") or ""), str(q.get("text") or "")
        same_text = ta == tb
        same_kind = p.get("kind") == q.get("kind")
        same_side = bool(p.get("sideways")) == bool(q.get("sideways"))
        if same_text and same_kind and same_side:
            rep["equal_pages"] += 1
            continue
        d: Dict[str, Any] = {"page": n, "file": fi + 1, "file_page": pi + 1, "r8_chars": len(ta),
                             "app_chars": len(tb)}
        if not same_text:
            d["diff_chars"] = _diff_chars(ta, tb)
        if not same_kind:
            d["kind"] = [p.get("kind"), q.get("kind")]
        if not same_side:
            d["sideways"] = [bool(p.get("sideways")), bool(q.get("sideways"))]
        rep["differing"].append(d)
    extra = len(app_pages) - len(_pages(r8))
    if extra > 0:
        rep["problems"].append(f"{extra} page(s) more in the app")
    rep["equal"] = not rep["problems"] and not rep["differing"] and rep["pages_r8"] == rep["pages_app"]
    return rep


def find_store(data_dir: Path, case_id: str) -> Optional[Path]:
    """<DATA>\\cache\\pagetexts\\<case_id>.json (R2.1.2), else the one pagetexts file under DATA."""
    p = Path(data_dir) / "cache" / "pagetexts" / f"{case_id}.json"
    if p.is_file():
        return p
    hits = sorted(Path(data_dir).rglob(f"pagetexts/{case_id}.json")) if case_id else []
    if hits:
        return hits[0]
    hits = sorted(h for h in Path(data_dir).rglob("pagetexts/*.json") if not h.name.endswith(".tmp"))
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------------------------------------ (b) summary.txt

def summary_core(text: str) -> List[str]:
    """summary.txt lines without the READING, RESULT CODE and output-path lines (and blank ends)."""
    return [ln.rstrip() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith(SKIP_PREFIXES)]


def verdict_word(line: str) -> str:
    m = _VERDICT_RE.search(line)
    if m:
        return m.group(1)
    m = _KIND_RE.match(line)
    return m.group(1) if m else ""


def safe_label(line: str) -> str:
    """The document title in front of a summary line ("ACADEMIC HSC"), or "" (never names or values)."""
    m = _LABEL_RE.match(line)
    return m.group(1).strip() if m else ""


def compare_summary(r8_text: str, app_text: Optional[str]) -> Dict[str, Any]:
    if app_text is None:
        return {"equal": False, "lines_r8": len(summary_core(r8_text)), "lines_app": 0,
                "diffs": ["the app's summary.txt was not found"]}
    a, b = summary_core(r8_text), summary_core(app_text)
    diffs: List[str] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            ia, jb = i1 + k, j1 + k
            la = a[ia] if ia < i2 else None
            lb = b[jb] if jb < j2 else None
            label = safe_label(la or lb or "")
            where = f"line {ia + 1 if la is not None else '-'} / {jb + 1 if lb is not None else '-'}"
            va = verdict_word(la) if la is not None else "(none)"
            vb = verdict_word(lb) if lb is not None else "(none)"
            diffs.append(f"summary {where}{' ' + label if label else ''}: r8 {va or '?'}, app {vb or '?'}"
                         + ("" if va != vb else " (wording)"))
    return {"equal": not diffs, "lines_r8": len(a), "lines_app": len(b), "diffs": diffs}


# ------------------------------------------------------------------------------------------------ (c) run.json

def _pages_of(f: Dict[str, Any]) -> Tuple[int, ...]:
    return tuple(sorted(int(x) for x in (f.get("pages") or [])))


def finding_key(f: Dict[str, Any]) -> Tuple[str, str, Tuple[int, ...]]:
    return (str(f.get("verdict")), str(f.get("rule")), _pages_of(f))


def _multiset_diff(a: Sequence[Any], b: Sequence[Any]) -> Tuple[List[Any], List[Any]]:
    ra, rb = list(a), list(b)
    for x in list(a):
        if x in rb:
            rb.remove(x)
            ra.remove(x)
    return ra, rb


def _fmt_keys(keys: Sequence[Tuple[str, str, Tuple[int, ...]]]) -> str:
    return "; ".join(f"{v} {r} p{','.join(map(str, p)) if p else '-'}" for v, r, p in keys)


def compare_run(r8: Dict[str, Any], app: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if app is None:
        return {"equal": False, "diffs": ["the app's run.json was not found"]}
    diffs: List[str] = []
    if app.get("error"):
        diffs.append("the app's run.json holds an error")
    if r8.get("overall") != app.get("overall"):
        diffs.append(f"overall: r8 {r8.get('overall')}, app {app.get('overall')}")
    da, db = r8.get("docs") or [], app.get("docs") or []
    if len(da) != len(db):
        diffs.append(f"documents: {len(da)} in r8, {len(db)} in the app")
    for i, (x, y) in enumerate(zip(da, db), start=1):
        ux, uy = x.get("unit") or {}, y.get("unit") or {}
        key = str(ux.get("key") or "?")
        if ux.get("key") != uy.get("key"):
            diffs.append(f"doc {i}: document type {ux.get('key')} vs {uy.get('key')}")
        if list(ux.get("pages") or []) != list(uy.get("pages") or []):
            diffs.append(f"doc {i} ({key}): pages {list(ux.get('pages') or [])} vs {list(uy.get('pages') or [])}")
        if x.get("verdict") != y.get("verdict"):
            diffs.append(f"doc {i} ({key}): verdict r8 {x.get('verdict')}, app {y.get('verdict')}")
        miss, extra = _multiset_diff(sorted(map(finding_key, x.get("findings") or [])),
                                     sorted(map(finding_key, y.get("findings") or [])))
        if miss or extra:
            diffs.append(f"doc {i} ({key}): findings only in r8 [{_fmt_keys(miss)}], only in the app "
                         f"[{_fmt_keys(extra)}]")
    miss, extra = _multiset_diff(sorted(map(finding_key, r8.get("cross") or [])),
                                 sorted(map(finding_key, app.get("cross") or [])))
    if miss or extra:
        diffs.append(f"cross findings only in r8 [{_fmt_keys(miss)}], only in the app [{_fmt_keys(extra)}]")
    ma, mb = r8.get("missing_lines") or [], app.get("missing_lines") or []
    if len(ma) != len(mb):
        diffs.append(f"missing lines: {len(ma)} in r8, {len(mb)} in the app")
    for i, (x, y) in enumerate(zip(ma, mb), start=1):
        if list(x) != list(y):
            lx = x[0] if x else "?"
            ly = y[0] if y else "?"
            diffs.append(f"missing line {i}: r8 {lx}, app {ly}" + (" (text differs)" if lx == ly else ""))
    na, nb = r8.get("notices") or [], app.get("notices") or []
    if len(na) != len(nb):
        diffs.append(f"notices: {len(na)} in r8, {len(nb)} in the app")
    for i, (x, y) in enumerate(zip(na, nb), start=1):
        for k in ("kind", "pages", "blocks", "units"):
            if x.get(k) != y.get(k):
                diffs.append(f"notice {i} ({x.get('kind')} p{x.get('pages')}): {k} differs")
        if x.get("message") != y.get("message"):
            diffs.append(f"notice {i} ({x.get('kind')} p{x.get('pages')}): message differs")
    return {"equal": not diffs, "diffs": diffs}


# ------------------------------------------------------------------------------------------------ (d) closer looks

def compare_looks(r8: Dict[str, Any], app: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    la = list((r8 or {}).get("names_rereads") or [])
    lb = list((app or {}).get("names_rereads") or []) if app is not None else []
    rep: Dict[str, Any] = {"equal": False, "looks_r8": len(la), "looks_app": len(lb), "equal_looks": 0,
                           "diffs": []}
    if app is None:
        rep["diffs"].append("the app's run.json was not found")
        return rep
    if len(la) != len(lb):
        rep["diffs"].append(f"looks: {len(la)} in r8, {len(lb)} in the app")
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        tag = f"look {i} (p{x.get('page')}, {x.get('kind') or 're-read'})"
        bad = []
        if x.get("run_page") != y.get("run_page") or x.get("page") != y.get("page"):
            bad.append(f"page p{x.get('page')} vs p{y.get('page')}")
        if x.get("kind") != y.get("kind") or x.get("how") != y.get("how"):
            bad.append("kind differs")
        if x.get("result") != y.get("result"):
            bad.append(f"decision {x.get('result')} vs {y.get('result')}")
        if x.get("best") != y.get("best"):
            bad.append("best candidate differs")
        if x.get("dot") != y.get("dot"):
            bad.append(f"dot {x.get('dot')} vs {y.get('dot')}")
        if x.get("candidates") != y.get("candidates") or x.get("variant") != y.get("variant"):
            bad.append("candidates differ")
        if bad:
            rep["diffs"].append(f"{tag}: " + "; ".join(bad))
        else:
            rep["equal_looks"] += 1
    rep["equal"] = not rep["diffs"]
    return rep


# ------------------------------------------------------------------------------------------------ one case

def _read_text(p: Path) -> Optional[str]:
    try:
        return Path(p).read_text(encoding="utf-8")
    except OSError:
        return None


def _read_json_or_none(p: Optional[Path]) -> Optional[Any]:
    if p is None:
        return None
    try:
        return cardrun.read_json(p)
    except (OSError, ValueError):
        return None


def gpu_facts(events: Sequence[Tuple[float, Dict[str, Any]]]) -> Dict[str, Any]:
    done = cardrun.done_event(events) or {}
    g = done.get("gpu") if isinstance(done.get("gpu"), dict) else {}
    waits = [e for _, e in events if e.get("ev") == "gpu_wait" and e.get("state") == "start"]
    return {"cpu_calls": g.get("cpu_calls"), "waits": g.get("waits", len(waits)), "waited_s": g.get("waited_s"),
            "peak_alloc_gb": g.get("peak_alloc_gb"), "peak_reserved_gb": g.get("peak_reserved_gb"),
            "bot_waits": sum(1 for e in waits if e.get("lock") == "bot")}


def judge_outputs(case: R8Case, out_dir: Optional[Path], data_dir: Optional[Path]) -> Dict[str, Any]:
    """(a)-(d) and the code for one finished app run (its out folder and DATA folder)."""
    r8_run = cardrun.read_json(case.dir / "run.json")
    r8_store = cardrun.read_json(case.dir / "page_texts.json")
    r8_summary = _read_text(case.dir / "summary.txt") or ""
    app_run = _read_json_or_none(Path(out_dir) / "run.json") if out_dir else None
    app_summary = _read_text(Path(out_dir) / "summary.txt") if out_dir else None
    store_path = find_store(data_dir, case.case_id) if data_dir else None
    app_store = _read_json_or_none(store_path)
    rep = {"page_texts": compare_page_texts(r8_store, app_store),
           "summary": compare_summary(r8_summary, app_summary),
           "run": compare_run(r8_run, app_run),
           "looks": compare_looks(r8_run, app_run),
           "code": (app_run or {}).get("result_code") if isinstance(app_run, dict) else None}
    if app_run is not None and app_run.get("case_id") not in (None, case.case_id):
        rep["run"]["diffs"].insert(0, "case id differs")
        rep["run"]["equal"] = False
    rep["verdicts_equal"] = rep["summary"]["equal"] and rep["run"]["equal"] and rep["looks"]["equal"]
    return rep


def check_case(case: R8Case, res: Any, out_dir: Path, data_dir: Path) -> Dict[str, Any]:
    events = cardrun.events_of(res.lines)
    done = cardrun.done_event(events) or {}
    rep: Dict[str, Any] = {"label": case.label, "case_id": case.case_id, "program": case.program,
                           "today": case.today, "exit": res.returncode, "timed_out": res.timed_out,
                           "errors": cardrun.error_codes(events), "cardrun": res.stats()}
    rep.update(judge_outputs(case, out_dir, data_dir))
    if not rep.get("code"):
        rep["code"] = done.get("code")
    elif done.get("code") and done.get("code") != rep["code"]:
        rep["code_mismatch"] = True
    rep["gpu"] = gpu_facts(events)
    rep["finished"] = (res.returncode in DONE_EXITS) and not res.timed_out
    rep["ok"] = rep["finished"] and rep["verdicts_equal"]
    return rep


def case_line(rep: Dict[str, Any]) -> str:
    pt, sm, rl, lk, g = rep["page_texts"], rep["summary"], rep["run"], rep["looks"], rep.get("gpu") or {}
    waited = g.get("waited_s")
    return (f"case {rep['label']} {rep.get('case_id', '')}: exit {rep.get('exit')}"
            f"{' TIMEOUT' if rep.get('timed_out') else ''}"
            f"{' errors ' + ','.join(rep['errors']) if rep.get('errors') else ''}"
            f" | page texts {pt['equal_pages']}/{pt['pages_r8']} equal"
            f" | summary {'equal' if sm['equal'] else 'DIFFERS'} ({sm['lines_r8']} lines)"
            f" | run.json {'equal' if rl['equal'] else 'DIFFERS'}"
            f" | looks {lk['equal_looks']}/{lk['looks_r8']} equal"
            f" | code {rep.get('code') or '-'}"
            f" | processor calls {g.get('cpu_calls', '-')}"
            f" | waits {g.get('waits', '-')}" + (f" ({waited} s)" if waited is not None else "")
            + f" | {'OK' if rep.get('ok') else 'MISS'}")


def detail_lines(rep: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    pt = rep["page_texts"]
    for p in pt["problems"]:
        out.append(f"  page texts: {p}")
    for d in pt["differing"]:
        if d.get("missing_in_app"):
            out.append(f"  page texts: p{d['page']} (file {d['file']} page {d['file_page']}) missing in the app")
            continue
        extra = []
        if "diff_chars" in d:
            extra.append(f"{d['diff_chars']} chars differ")
        if "kind" in d:
            extra.append(f"kind {d['kind'][0]} vs {d['kind'][1]}")
        if "sideways" in d:
            extra.append(f"sideways {d['sideways'][0]} vs {d['sideways'][1]}")
        out.append(f"  page texts: p{d['page']} (file {d['file']} page {d['file_page']}) "
                   f"{d['r8_chars']} vs {d['app_chars']} chars, " + ", ".join(extra))
    for key in ("summary", "run", "looks"):
        for d in rep[key]["diffs"]:
            out.append(f"  MISS {d}")
    return out


# ------------------------------------------------------------------------------------------------ the run

RunFn = Callable[..., Any]


def run_cli_case(case: R8Case, exe: Path, root: Path, timeout: float, run_fn: RunFn) -> Dict[str, Any]:
    d = root / case.label
    data, out = d / "data", d / "out"
    data.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    cmd = cardrun.cli_cmd(exe, case.input, case.program, out, case.today, data)
    res = run_fn(cmd, timeout, what=f"hdc P1 {case.label}", env=cardrun.app_env(data), log_dir=d)
    return check_case(case, res, out, data)


def _find_case_folder(report_root: Path) -> Optional[Path]:
    hits = sorted(p.parent for p in Path(report_root).rglob("run.json"))
    return hits[0] if len(hits) == 1 else None


def run_auto(case: R8Case, exe: Path, root: Path, timeout: float, run_fn: RunFn) -> Dict[str, Any]:
    d = root / "auto"
    data, reports = d / "data", d / "reports"
    data.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    auto_json = d / "auto.json"
    cmd = cardrun.auto_cmd(exe, case.input, case.program, case.today, reports, auto_json, data)
    res = run_fn(cmd, timeout, what="hdc P1 auto CHECK_0001", env=cardrun.app_env(data), log_dir=d)
    auto = _read_json_or_none(auto_json) or {}
    out = auto.get("out") or auto.get("case_folder")
    out_dir = Path(out) if out and Path(out).is_dir() else _find_case_folder(reports)
    rep: Dict[str, Any] = {"label": "auto " + case.label, "case_id": case.case_id, "exit": res.returncode,
                           "timed_out": res.timed_out, "cardrun": res.stats(), "auto_json": bool(auto),
                           "auto_code": auto.get("code") or auto.get("result_code"),
                           "case_folder_found": out_dir is not None}
    rep.update(judge_outputs(case, out_dir, data))
    rep["code"] = rep["auto_code"] or rep.get("code")
    rep["gpu"] = {}
    rep["finished"] = res.returncode == 0 and not res.timed_out and bool(auto)
    rep["ok"] = rep["finished"] and bool(rep["code"])
    return rep


def main(argv: Optional[Sequence[str]] = None, run_fn: Optional[RunFn] = None, runs: Path = RUNS) -> int:
    ap = argparse.ArgumentParser(description="P1: the frozen app against the r8 runs (needs Q-O1)")
    ap.add_argument("--build-id", default="")
    ap.add_argument("--app-dir", default="", help="the frozen app folder (default: the build's dist)")
    ap.add_argument("--cases", default=",".join(c for c, _ in CASES))
    ap.add_argument("--no-auto", action="store_true", help="skip the --auto run of CHECK_0001")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--input", action="append", default=[], metavar="LABEL=PATH",
                    help="check PATH instead of r8's input for that case (r8's input is gone; same files)")
    a = ap.parse_args(argv)
    try:
        app = cardrun.app_dir_for(a.build_id or None, a.app_dir or None)
        wanted = [c.strip() for c in a.cases.split(",") if c.strip()]
        known = dict(CASES)
        if any(c not in known for c in wanted):
            raise ValueError("unknown case; known: " + ", ".join(known))
        cases = [load_r8(c, runs, known[c]) for c in wanted]
        replaced = apply_inputs(cases, parse_inputs(a.input, list(known)))
    except (ValueError, OSError, KeyError) as exc:
        print(f"P1: cannot start: {type(exc).__name__}: {exc}")
        return 2
    exe = app / cardrun.APP_EXE
    if not exe.is_file():
        print("P1: cannot start: the app exe was not found in the app folder")
        return 2
    run_fn = run_fn or cardrun.run
    apptest = cardrun.apptest_dir(a.build_id or None)
    root = cardrun.new_run_dir(apptest, "p1")
    print(f"P1 parity with r8 | build {a.build_id or '-'} | run folder {root.name}"
          + (f" | stand-in input for {', '.join(replaced)}" if replaced else ""))
    reports: List[Dict[str, Any]] = []
    for case in cases:
        try:
            rep = run_cli_case(case, exe, root, a.timeout, run_fn)
        except cardrun.STOP_ERRORS as exc:
            print(f"P1: stopped: {type(exc).__name__}: {exc}")
            return 2
        rep["stand_in_input"] = case.label in replaced
        reports.append(rep)
        print(case_line(rep))
        for line in detail_lines(rep):
            print(line)
    auto_rep = None
    by_label = {r["label"]: r for r in reports}
    this_pc = (by_label.get(CHECK_CASE) or {}).get("code")
    if not a.no_auto and any(c.label == CHECK_CASE for c in cases):
        case = next(c for c in cases if c.label == CHECK_CASE)
        try:
            auto_rep = run_auto(case, exe, root, a.timeout, run_fn)
        except cardrun.STOP_ERRORS as exc:
            print(f"P1: stopped before the --auto run: {type(exc).__name__}: {exc}")
            return 2
        same = bool(this_pc) and auto_rep.get("code") == this_pc
        auto_rep["same_code"] = same
        auto_rep["ok"] = auto_rep["ok"] and same and (not auto_rep["case_folder_found"]
                                                      or auto_rep["verdicts_equal"])
        print(f"auto {CHECK_CASE}: exit {auto_rep['exit']} | code {auto_rep.get('code') or '-'} "
              f"{'= the --cli code' if same else 'DIFFERS from the --cli code'}"
              f" | case folder {'checked' if auto_rep['case_folder_found'] else 'not found'}"
              f" | {'OK' if auto_rep['ok'] else 'MISS'}")
        if auto_rep["case_folder_found"]:
            for line in detail_lines(auto_rep):
                print(line)
    all_four = {c for c, _ in CASES} <= {r["label"] for r in reports}
    passed = all(r["ok"] for r in reports) and (auto_rep is None or auto_rep["ok"])
    text_pages = sum(len(r["page_texts"]["differing"]) for r in reports)
    text_cases = sum(1 for r in reports if not r["page_texts"]["equal"])
    if this_pc:
        print(f"THIS_PC_CODE {this_pc}")
    if text_pages or text_cases:
        print(f"page-text differences: {text_pages} page(s) in {text_cases} case(s): explain them in the report "
              "before hand-over")
    if passed and all_four:
        verdict = "PASS: (b)-(d) hold for all four cases"
    elif passed:
        verdict = "PASS for the cases run (not all four were run: the build needs all four)"
    else:
        verdict = "FAIL: see the MISS lines"
    print(f"P1 {verdict}")
    report = {"schema": "hdc-p1/1", "build_id": a.build_id or None, "run_folder": root.name,
              "this_pc_code": this_pc, "passed": passed, "all_four": all_four, "cases": reports, "auto": auto_rep,
              "page_text_differences": text_pages, "stand_in_inputs": replaced}
    cardrun.write_json(root / "parity.json", report)
    cardrun.write_json(apptest / "p1-latest.json", report)
    rdir = cardrun.reports_dir_for(a.build_id or None)
    if rdir is not None and rdir.is_dir():
        cardrun.write_json(rdir / "parity.json", report)
    return 0 if passed and all_four else 1


if __name__ == "__main__":
    sys.exit(main())
