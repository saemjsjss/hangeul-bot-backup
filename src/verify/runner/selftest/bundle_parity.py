"""T4 — bundle / folder parity on real PDFs (RUNNER_SPEC section 10, Task 6).

    <runner venv python> -I -B -m src.verify.runner.selftest.bundle_parity [--n 5] [--sids S001,S002]
                                                                           [--build-only] [--today YYYY-MM-DD]

For N held-out students (corpus.halves, seed 20261027; the students with the most document slots first):
  1. concatenate their typed folder files into selftest\\bundle_parity\\<sid>.pdf in the owner's fixed order
     (images become pages; NID files keep their own page order; academic files newest level first), with a
     truth file <sid>.truth.json (keys and page spans only, no file names);
  2. seed the runner's case cache for the folder from the bot's text/image cache (read-only copies into
     runner-data\\verification, keyed by the runner's folder case id), so the folder run makes no fresh OCR
     apart from the targeted re-reads;
  3. run the runner on the bundle (CPU light engine, minutes per student) and on the real folder (read-only);
  4. per slot, where the bundle split was exact, compare the verdicts; every difference is listed by rule id.

Reads the student folders and the bot's caches READ-ONLY in this harness process, before any runner process
starts. Writes only under C:\\Hangeul\\DOCCHECK\\lab\\private-runner. Prints sids, keys, pages, verdicts and rule
ids only — never a name, a number, a file name or text. No personal data in this file.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
SELF = PRIVATE / "selftest" / "bundle_parity"
RUNS = PRIVATE / "runs"
CACHE_ROOT = PRIVATE / "runner-data"
PY = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe")
# the owner's fixed order (order_split.ORDER_KEYS): the bundle is built in this key order
ORDER_KEYS = ["passport", "photo", "father_nid", "father_death_cert", "mother_nid", "mother_death_cert",
              "student_nid", "birth_cert", "death_cert", "family_cert", "affidavit", "academic", "language_cert",
              "bank", "trade_license", "income_tax", "eca", "personal_statement", "recommendation", "id_card_cv"]
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


def pick(n: int, sids: Sequence[str]) -> List[Any]:
    from src.verify.runner.selftest import corpus
    students = corpus.load_corpus()
    _, held = corpus.halves(students)
    if sids:
        want = set(sids)
        return [s for s in students if s.sid in want]

    def slots(s) -> int:
        return len({f.key for f in s.files if f.key in ORDER_KEYS})
    return sorted(held, key=lambda s: (-slots(s), s.sid))[:n]


def _level_rank(f) -> int:
    from src.verify.runner import pagetype
    got = pagetype.level_of(" ".join(f.pages[:2]) if f.pages else "")
    return got[1] if got else 0


def build_bundle(stu, out_pdf: Path) -> List[Dict[str, Any]]:
    """The bundle PDF in the fixed order; returns the truth spans [{key, first, last, file}] (0-based pages;
    `file` is kept in memory only and never written or printed)."""
    import pymupdf
    files = [f for f in stu.files if f.key in ORDER_KEYS and f.path.suffix.lower() in (".pdf",) + IMAGE_SUFFIXES]
    files.sort(key=lambda f: (ORDER_KEYS.index(f.key), -_level_rank(f) if f.key == "academic" else 0, f.name))
    doc = pymupdf.open()
    spans: List[Dict[str, Any]] = []
    for f in files:
        start = doc.page_count
        if f.path.suffix.lower() in IMAGE_SUFFIXES:
            with pymupdf.open(f.path) as img:
                pdf_bytes = img.convert_to_pdf()
            with pymupdf.open("pdf", pdf_bytes) as one:
                doc.insert_pdf(one)
        else:
            with pymupdf.open(f.path) as src:
                doc.insert_pdf(src)
        if doc.page_count > start:
            spans.append({"key": f.key, "first": start, "last": doc.page_count - 1, "file": f.name})
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_pdf))
    doc.close()
    return spans


def seed_cache(stu) -> str:
    """Copies the bot's text/image cache of this student to runner-data\\verification\\{text,images}\\<case>.json
    (the runner's folder case id); returns the case id."""
    from src.verify.runner import ingest
    case = ingest.open_input(stu.folder).case_id
    for src, sub in ((stu.cache_json, "text"), (stu.images_json, "images")):
        if src is None or not Path(src).is_file():
            continue
        dst = CACHE_ROOT / "verification" / sub / f"{case}.json"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    return case


def run_runner(inp: Path, out: Path, program: str, today: Optional[str], log: Path) -> Tuple[int, float]:
    args = [str(PY), "-I", "-B", "-m", "src.verify.runner", str(inp), "--program", program, "--out", str(out)]
    if today:
        args += ["--today", today]
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.call(args, stdout=fh, stderr=subprocess.STDOUT, timeout=3 * 3600)
    return rc, time.perf_counter() - t0


def compare(sid: str, spans: List[Dict[str, Any]], bundle: Dict[str, Any], folder: Dict[str, Any]) -> Dict[str, int]:
    """Per truth span: exact split? then bundle verdict vs folder verdict (the folder unit of that file)."""
    counts = {"slots": 0, "exact": 0, "equal": 0, "different": 0, "not_exact": 0, "no_folder_unit": 0}
    bdocs = bundle.get("docs") or []
    fdocs = {x["unit"].get("source_file"): x for x in folder.get("docs") or []}
    for sp in spans:
        counts["slots"] += 1
        pages = list(range(sp["first"], sp["last"] + 1))
        same = [x for x in bdocs if x["unit"]["pages"] == pages]
        exact = [x for x in same if x["unit"]["key"] == sp["key"] or (
            sp["key"].endswith("_nid") and x["unit"]["key"].endswith("_nid"))]
        label = f"{sid} {sp['key']} p{sp['first'] + 1}-{sp['last'] + 1}"
        if not exact:
            counts["not_exact"] += 1
            got = sorted({(x['unit']['key'], len(x['unit']['pages'])) for x in bdocs
                          if set(x['unit']['pages']) & set(pages)})
            print(f"     {label}: split not exact (bundle units here: "
                  f"{', '.join(f'{k}/{n}p' for k, n in got) or 'none'})")
            continue
        counts["exact"] += 1
        fx = fdocs.get(sp["file"])
        if fx is None:
            counts["no_folder_unit"] += 1
            print(f"     {label}: no folder unit for this file")
            continue
        bx = exact[0]
        if bx["verdict"] == fx["verdict"]:
            counts["equal"] += 1
            print(f"ok   {label}: {bx['verdict']}")
            continue
        counts["different"] += 1
        ra = {(f["rule"], f["verdict"]) for f in bx["findings"]}
        rb = {(f["rule"], f["verdict"]) for f in fx["findings"]}
        only_b = sorted(f"{r}={v}" for r, v in ra - rb)
        only_f = sorted(f"{r}={v}" for r, v in rb - ra)
        print(f"DIFF {label}: bundle {bx['verdict']} vs folder {fx['verdict']} | bundle only: "
              f"{', '.join(only_b) or '-'} | folder only: {', '.join(only_f) or '-'}")
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -I -B -m src.verify.runner.selftest.bundle_parity")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--sids", default="", help="comma-separated sids instead of the automatic pick")
    ap.add_argument("--build-only", action="store_true", help="build the bundles and truth files only")
    ap.add_argument("--today", default=None)
    a = ap.parse_args(argv)
    SELF.mkdir(parents=True, exist_ok=True)
    students = pick(a.n, [s for s in a.sids.split(",") if s])
    print(f"== {len(students)} student(s): {', '.join(s.sid for s in students)}")
    total: Dict[str, int] = {}
    for stu in students:
        pdf = SELF / f"{stu.sid}.pdf"
        spans = build_bundle(stu, pdf)
        (SELF / f"{stu.sid}.truth.json").write_text(json.dumps(
            {"sid": stu.sid, "program": stu.program,
             "spans": [{k: v for k, v in sp.items() if k != "file"} for sp in spans]}, indent=1), encoding="utf-8")
        print(f"== {stu.sid} ({stu.program}): {len(spans)} file(s), "
              f"{(spans[-1]['last'] + 1) if spans else 0} page(s)")
        if a.build_only:
            continue
        seed_cache(stu)
        out_b, out_f = RUNS / f"T4_{stu.sid}_bundle", RUNS / f"T4_{stu.sid}_folder"
        rc_b, s_b = run_runner(pdf, out_b, stu.program, a.today, SELF / f"{stu.sid}_bundle.log")
        rc_f, s_f = run_runner(stu.folder, out_f, stu.program, a.today, SELF / f"{stu.sid}_folder.log")
        print(f"     bundle exit {rc_b} ({s_b:.0f} s), folder exit {rc_f} ({s_f:.0f} s)")
        if rc_b not in (0, 3) or rc_f not in (0, 3):
            print(f"MISS {stu.sid}: a run did not finish (see the logs under selftest\\bundle_parity)")
            total["crashed"] = total.get("crashed", 0) + 1
            continue
        c = compare(stu.sid, spans, json.loads((out_b / "run.json").read_text(encoding="utf-8")),
                    json.loads((out_f / "run.json").read_text(encoding="utf-8")))
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    (SELF / "result.json").write_text(json.dumps(total, indent=1), encoding="utf-8")
    print("== totals: " + ", ".join(f"{k} {v}" for k, v in total.items()))
    return 0 if not total.get("crashed") else 1


if __name__ == "__main__":
    sys.exit(main())
