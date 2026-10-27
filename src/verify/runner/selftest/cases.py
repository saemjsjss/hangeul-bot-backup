"""T5 — the owner's 7 cases (RUNNER_SPEC section 10, Task 6).

    <runner venv python> -I -B -m src.verify.runner.selftest.cases --manifest <cases.json> [--case CASE01] [--cpu]
    <runner venv python> -I -B -m src.verify.runner.selftest.cases --dry-run [--cpu]
    <runner venv python> -I -B -m src.verify.runner.selftest.cases --review <runs\\CASE0N>     (review.txt only)

The manifest (private, under C:\\Hangeul\\DOCCHECK\\lab\\private-runner) lists the cases with the program the owner
names for each: [{"id": "CASE01", "input": "<bundle.pdf | folder>", "program": "KLP", "today": "YYYY-MM-DD"?}].
--dry-run runs the CHECK_0001 bundle (DRY01) and its folder twin from selftest\\check0001\\folder (DRY02).

Each case runs read-only through the wrapper run_case.py (GPU pre-read, CPU fallback after 120 s; --cpu skips
the card) into runs\\<id>. Acceptance, printed as ok / MISS per case: no crash; the three outputs; the input
snapshot (size, mtime, sha256 of every input file) unchanged; 0 blocked events and 0 opens under
C:\\Hangeul\\BOT; the READING line says which engine read the pages. A by-eye review checklist is written to
runs\\<id>\\review.txt (private: it quotes the report). Nothing personal is printed. No personal data in this file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
RUNS = PRIVATE / "runs"
PY = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe")
RUN_CASE = PRIVATE / "run_case.py"
PROGRAMS = ("KLP", "EAP", "BACHELOR", "MASTER")
DRY = [("DRY01", Path(r"C:\Users\User\Downloads\CHECK_0001.pdf"), "KLP"),
       ("DRY02", PRIVATE / "selftest" / "check0001" / "folder", "KLP")]
READING = re.compile(r"^READING: GPU pre-read \d+ pages, CPU light engine \d+, text layer \d+")
CHECK_BY_EYE = re.compile(r"check by eye|probably OCR", re.I)


# ------------------------------------------------------------------------------------------ snapshots
def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(inp: Path) -> Dict[str, Tuple[int, int, str]]:
    files = [inp] if inp.is_file() else sorted(p for p in inp.iterdir() if p.is_file())
    out = {}
    for p in files:
        st = p.stat()
        out[str(p)] = (st.st_size, st.st_mtime_ns, _sha(p))
    return out


# ------------------------------------------------------------------------------------------ review
def _pages(ps: Sequence[int]) -> str:
    ps = sorted(set(int(p) for p in ps))
    if not ps:
        return "-"
    return f"p{ps[0]}" if len(ps) == 1 else (f"p{ps[0]}-{ps[-1]}" if ps[-1] - ps[0] + 1 == len(ps)
                                             else "p" + ",".join(map(str, ps)))


def _unit_where(u: Dict[str, Any], mode: str) -> str:
    if mode == "folder":
        return u.get("source_file") or "-"
    return _pages([p + 1 for p in u.get("pages") or []])


def review_text(d: Dict[str, Any], summary: Sequence[str] = ()) -> str:
    """The by-eye checklist of one run (run.json as a dict): every FAIL with its pages, every ORDER notice,
    every MISSING line, every name FLAG "check by eye" with its page, the new checks that fired, the identity
    sources and the reading line. Private: it quotes the report's messages."""
    mode = d.get("mode", "")
    L: List[str] = [f"REVIEW — {d.get('case_id')} | {d.get('program')} | {mode} | RESULT: {d.get('overall')}",
                    str(d.get("headline") or ""), ""]
    if "error" in d:
        L += [f"[ ] the run CRASHED in stage {d.get('stage')}: {d.get('error')} (see log.txt)", ""]
    reading = [s for s in summary if s.startswith("READING:")]
    L += ["[ ] reading: " + (reading[0] if reading else "(no READING line)"), ""]
    docs = d.get("docs") or []
    fails = [(x["unit"], f) for x in docs for f in x.get("findings") or [] if f.get("verdict") == "FAIL"]
    fails += [(None, f) for f in d.get("cross") or [] if f.get("verdict") == "FAIL"]
    L.append(f"[ ] every FAIL ({len(fails)}):")
    for u, f in fails:
        where = (f"{u.get('title')} ({_unit_where(u, mode)})" if u else "cross line")
        pg = f" pages {_pages(f.get('pages') or [])}" if f.get("pages") else ""
        L.append(f"    - {where}{pg}: {f.get('rule')} — {f.get('message')}")
    orders = [n for n in d.get("notices") or [] if n.get("kind") in ("ORDER", "STOP")]
    L += ["", f"[ ] every ORDER / STOP notice ({len(orders)}):"]
    for n in orders:
        a, b = (n.get("pages") or [0, 0])[:2]
        where = f"page {a}" if a == b else f"pages {a}-{b}"
        L.append(f"    - {n.get('kind')}{' (blocking)' if n.get('blocks') else ''} {where}: {n.get('message')}")
    miss = d.get("missing_lines") or []
    L += ["", f"[ ] every MISSING line ({len(miss)}):"] + [f"    - {lv}: {txt}" for lv, txt in miss]
    eye = [(x["unit"], f) for x in docs for f in x.get("findings") or []
           if str(f.get("rule", "")).startswith("names.") and f.get("verdict") == "FLAG"]
    eye += [(x["unit"], f) for x in docs for f in x.get("findings") or []
            if not str(f.get("rule", "")).startswith("names.") and f.get("verdict") == "FLAG"
            and CHECK_BY_EYE.search(str(f.get("message") or ""))]
    L += ["", f"[ ] every 'check by eye' FLAG ({len(eye)}):"]
    for u, f in eye:
        L.append(f"    - {u.get('title')} ({_unit_where(u, mode)}) pages {_pages(f.get('pages') or [])}: "
                 f"{f.get('rule')} — {f.get('message')}")
    rereads = d.get("names_rereads") or []
    if rereads:
        L += ["", f"[ ] name re-reads to look at ({len(rereads)}):"]
        for r in rereads:
            if isinstance(r, dict):
                L.append(f"    - page {r.get('page', '?') if not isinstance(r.get('page'), int) else r['page'] + 1}: "
                         f"{r.get('effect', '')} | crop {r.get('crop', '')}")
    new = [(x["unit"], f) for x in docs for f in x.get("findings") or []
           if f.get("rule") in ("tin.web_address", "income_tax.tax_office") and f.get("verdict") in ("FLAG", "FAIL")]
    L += ["", f"[ ] NEW checks that fired ({len(new)}) — owner to confirm keeping them:"]
    for u, f in new:
        L.append(f"    - {f.get('rule')} on {u.get('title')} ({_unit_where(u, mode)}): {f.get('message')}")
    idn = d.get("identity") or {}
    L += ["", "[ ] identity sources:"]
    for who in ("student", "father", "mother", "father_nid", "mother_nid"):
        ref = idn.get(who) or {}
        L.append(f"    - {who}: {ref.get('source') or 'not read'}; confirmed={ref.get('confirmed')}"
                 + ("; MRZ name may be cut" if ref.get("truncated") else ""))
    L.append(f"    - DOB: {idn.get('dob_source') or 'not read'}; trusted={idn.get('dob_trusted')}")
    L.append(f"    - expiry: {idn.get('expiry_source') or 'not read'}; trusted={idn.get('expiry_trusted')}")
    L.append(f"    - MRZ line 2 ok: {bool((idn.get('mrz') or {}).get('line2_ok'))}")
    sf = d.get("safety") or {}
    L += ["", f"[ ] safety: blocked {sf.get('blocked_count', len(sf.get('blocked') or []))}, "
              f"opens under C:\\Hangeul\\BOT {sf.get('bot_opens')}, input unchanged {sf.get('input_unchanged')}"]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------ one case
def run_case(cid: str, inp: Path, program: str, cpu: bool, today: Optional[str]) -> Dict[str, Any]:
    out = RUNS / cid
    res: Dict[str, Any] = {"id": cid, "checks": [], "misses": 0}

    def check(name: str, ok: bool, detail: str = "") -> None:
        res["checks"].append((name, bool(ok)))
        res["misses"] += 0 if ok else 1
        print(f"{'ok  ' if ok else 'MISS'} {cid} {name}" + (f"  [{detail}]" if detail and not ok else ""),
              flush=True)

    if not inp.exists():
        check("input present", False)
        return res
    before = snapshot(inp)
    args = [str(PY), "-I", "-B", str(RUN_CASE), str(inp), "--program", program, "--out", str(out)]
    if cpu:
        args.append("--cpu")
    if today:
        args += ["--today", today]
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with open(out / "cases_stdout.log", "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.call(args, stdout=fh, stderr=subprocess.STDOUT, timeout=4 * 3600)
    print(f"     {cid}: exit {rc} in {time.perf_counter() - t0:.0f} s", flush=True)
    check("no crash (exit 0 or 3)", rc in (0, 3), f"exit {rc}")
    have = {n: (out / n).is_file() for n in ("summary.txt", "report.xlsx", "run.json")}
    check("three outputs", all(have.values()), ", ".join(k for k, v in have.items() if not v))
    check("input unchanged (size, mtime, sha256)", snapshot(inp) == before)
    if not have["run.json"]:
        return res
    d = json.loads((out / "run.json").read_text(encoding="utf-8"))
    summary = (out / "summary.txt").read_text(encoding="utf-8").splitlines() if have["summary.txt"] else []
    sf = d.get("safety") or {}
    blocked = sf.get("blocked_count", len(sf.get("blocked") or []))
    check("0 blocked events", blocked == 0, f"{blocked}")
    check("0 opens under C:\\Hangeul\\BOT", sf.get("bot_opens") == 0, str(sf.get("bot_opens")))
    check("READING line names the engines", any(READING.match(s) for s in summary))
    check("summary at most 80 lines", 0 < len(summary) <= 80, f"{len(summary)} lines")
    (out / "review.txt").write_text(review_text(d, summary), encoding="utf-8")
    res["overall"] = d.get("overall")
    print(f"     {cid}: RESULT {d.get('overall')} — review.txt written", flush=True)
    return res


def load_manifest(path: Path) -> List[Tuple[str, Path, str, Optional[str]]]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for r in rows:
        prog = str(r["program"]).upper()
        if prog not in PROGRAMS:
            raise SystemExit(f"case {r.get('id')}: program must be one of {', '.join(PROGRAMS)}")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(r["id"])):
            raise SystemExit("a case id is letters, digits, '_', '.' and '-' only")
        out.append((str(r["id"]), Path(r["input"]), prog, r.get("today")))
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -I -B -m src.verify.runner.selftest.cases")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--manifest", help="the cases JSON (private, under private-runner)")
    g.add_argument("--dry-run", action="store_true", help="the CHECK_0001 bundle and its folder twin")
    g.add_argument("--review", help="write review.txt for an existing run folder only")
    ap.add_argument("--case", default="", help="run only this case id")
    ap.add_argument("--cpu", action="store_true", help="no GPU pre-read (run_case.py --cpu)")
    ap.add_argument("--today", default=None)
    a = ap.parse_args(argv)
    if a.review:
        out = Path(a.review)
        if not out.resolve().is_relative_to(PRIVATE):
            print("MISS --review must be a run folder under private-runner")
            return 2
        d = json.loads((out / "run.json").read_text(encoding="utf-8"))
        summ = (out / "summary.txt").read_text(encoding="utf-8").splitlines() if (out / "summary.txt").is_file() else []
        (out / "review.txt").write_text(review_text(d, summ), encoding="utf-8")
        print("ok   review.txt written")
        return 0
    cases = [(cid, inp, prog, a.today) for cid, inp, prog in DRY] if a.dry_run else load_manifest(Path(a.manifest))
    if a.case:
        cases = [c for c in cases if c[0] == a.case]
    misses = 0
    for cid, inp, prog, today in cases:
        r = run_case(cid, inp, prog, a.cpu, today or a.today)
        misses += r["misses"]
    print(f"== {len(cases)} case(s), {misses} MISS")
    return 0 if misses == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
