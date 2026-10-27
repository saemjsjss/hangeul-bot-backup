"""T3 — real folders, cache-seeded (RUNNER_SPEC section 10; Task 5).

20 held-out students of mixed programs (selftest.corpus, seed 20261027). For the rules the owner did not
change, the runner's verdicts must equal the live bot's rows in results.json (read-only), document by
document, on >= 99% of the compared rows. Every difference is listed by rule and sid; differences the
spec makes on purpose are counted apart.

Two modes:
  --mode direct (the default)  doccheck.check_documents() on the real folder files, in a child process per
                               student, with the student's bot caches COPIED under private-runner (0 OCR:
                               cache-only, a file with no saved text is "not cached" and left out). Units come
                               from dv.classify(), as the bot's own folder loop, so every bot row has its unit.
                               Needs only doccheck.py, language.py and the bot code.
  --mode full                  the runner itself (python -m src.verify.runner <folder> --program P --today D),
                               after seeding runner-data\\verification\\{text,images}\\<case id>.json; findings
                               are read from run.json; the re-read audit list comes from run.json's
                               names_rereads. Needs the whole runner (Tasks 1-8).

Harness steps read the bot's caches and results.json READ-ONLY, in this (parent) process, before any child
starts. Children are guarded: writes only under private-runner, no open under C:\\Hangeul\\BOT, no network.
Everything written goes to C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\folder_parity\\ (private: the
difference list holds messages). It prints counts, rates, sids and rule ids only.

    python -I -B -m src.verify.runner.selftest.folder_parity [--mode direct|full] [--n 20] [--sids S001,S002]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import botimport

PRIVATE = botimport.PRIVATE
OUT = PRIVATE / "selftest" / "folder_parity"
BOT_RESULTS = Path(r"C:\Hangeul\BOT\data\verification\results.json")
BASE_RULES = "2026-10-21.3"
SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png")
FLOOR = 0.99

# Rules the owner did not change, by the bot's message: (rule id, keys it applies to or None, regex).
# The first matching rule wins; a message no rule matches is not compared (changed on purpose, a name or
# portal row, or a presence line). Birth certificates (A3), NID numbers (A6 d), bank USD / seal / dates /
# account age (A7, critique 15) and non-Master's income tax (A6 h) are left out on purpose.
_NID = ("father_nid", "mother_nid", "student_nid")
PARITY: List[Tuple[str, Optional[Tuple[str, ...]], str]] = [
    ("income_tax", ("income_tax",),
     r"^(net wealth figure|no net wealth|no amount labelled as tax paid|tax paid |income tax papers should be "
     r"notarised|notarised \(wording)"),
    ("page.colour", None,
     r"^(typed document|produced on a computer|could not render the pages to judge colour|could not judge whether "
     r"the scan is in colour|colour scan \(|only [\d.]+% of the page is coloured|looks like a black-and-white scan)"),
    ("page.qr", None,
     r"^(QR code scans cleanly|a QR code is on the page but could not be read|no QR code and nothing showing|no QR "
     r"code on this document|could not render the pages to read a QR code)"),
    ("page.bangla", None, r"^(Bangla document with translation wording|document appears to be in Bangla)"),
    ("notarisation", None,
     r"^(notarised \(wording or seal found\)|notarised through an advocate|no notarisation found|trade licence is "
     r"(?:not )?notarised)"),
    ("nid.lawyer_pad", _NID, r"^(on an advocate/lawyer pad|could not confirm the translation is on a lawyer pad)"),
    ("bank.balance", ("bank",),
     r"^(balance [\d,]+ BDT meets|the balance reads|the balance is|the statement's closing balance reads|no figure "
     r"is labelled as the balance|no balance amount could be read)"),
    ("bank.opening", ("bank",),
     r"^(no line is labelled as the opening balance|the statement opens at|opening balance [\d,]+ BDT is within)"),
    ("bank.one_page", ("bank",), r"^the bank file has only one page"),
    ("family.date", ("family_cert",),
     r"^(issued \d|no Date/Ref line|could not read the issue date|no issue date line could be read)"),
    ("family.issuer", ("family_cert",), r"^(issued by Union Parishad|issuer \(Union Parishad)"),
    ("family.numbers", ("family_cert",), r"(NID/birth-registration numbers listed|few or no NID/birth numbers)"),
    ("academic.apostille", ("academic",),
     r"(?i)apostille|qualification|^provisional certificate|scanned sideways"),
    ("financial.fiscal_year", ("trade_license",),
     r"^(licence fiscal year|the current fiscal year|the licence shows|no fiscal year found)"),
    ("financial.kind", ("trade_license",), r"^(contains: |could not tell which financial document)"),
    ("financial.business", ("trade_license",), r"^business started"),
    ("photo", ("photo",),
     r"^(\d+x\d+px|low resolution|background is|image looks flat|photo is |photo file could not|could not open the "
     r"image)"),
]
_PARITY_RX = [(rule, keys, re.compile(rx)) for rule, keys, rx in PARITY]
_SPLIT_BANK = "one page in this file: the solvency certificate and the statement are separate bank files"
LEFT_OUT_KEYS = ("birth_cert",)                     # A3: birth.py, not the bot
_QR_SKIP_WORDS = re.compile(r"taxpayer|tin certificate|etin")   # the bot's qr_check skip (PC 233-234)


def rule_of(key: str, message: str, program: str) -> Optional[str]:
    """The parity rule a finding belongs to, or None (not compared)."""
    if key in LEFT_OUT_KEYS:
        return None
    if key == "income_tax" and program != "MASTER":
        return None
    for rule, keys, rx in _PARITY_RX:
        if keys is not None and key not in keys:
            continue
        if rx.search(message or ""):
            if rule == "page.qr" and key == "academic":
                return None                             # board QR (A6 c) replaces it there: not compared
            return rule
    return None


def parse_detail(detail: str) -> List[Tuple[str, str]]:
    """A bot row's detail 'PASS: msg | FLAG: msg' -> [(verdict, msg)]."""
    out = []
    for part in re.split(r" \| (?=(?:PASS|NOTE|FLAG|FAIL|ERROR|MISSING): )", detail or ""):
        m = re.match(r"(PASS|NOTE|FLAG|FAIL|ERROR|MISSING): (.*)$", part.strip(), re.S)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def by_rule(key: str, findings: Sequence[Tuple[str, str]], program: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for v, m in findings:
        r = rule_of(key, m, program)
        if r:
            out.setdefault(r, []).append(v)
    return {r: sorted(vs) for r, vs in out.items()}


# --- the guard -----------------------------------------------------------------------------------------------
def _guard(block_bot_opens: bool) -> None:
    """Writes only under private-runner, no network; with block_bot_opens no open under C:\\Hangeul\\BOT."""
    try:
        from .. import safety
        safety.install_write_guard(block_bot_opens=block_bot_opens,     # the harness READS the bot's caches
                                   bot_read_ok=None if block_bot_opens else [safety.BOT])
        return
    except ImportError:
        pass
    priv = os.path.normcase(str(PRIVATE))
    bot = os.path.normcase(r"C:\Hangeul\BOT")

    def under(p: Any, root: str) -> bool:
        try:
            s = os.path.normcase(os.path.abspath(os.fspath(p)))
        except TypeError:
            return False
        return s == root or s.startswith(root + os.sep)

    def hook(event: str, args: tuple) -> None:
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else 0
            if block_bot_opens and under(args[0], bot):
                raise PermissionError("open under C:\\Hangeul\\BOT refused")
            write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT))
            if write and not under(args[0], priv):
                raise PermissionError("write outside private-runner refused")
        elif event in ("os.remove", "os.rename", "os.replace", "os.mkdir", "os.rmdir", "shutil.rmtree"):
            if args and not under(args[0], priv):
                raise PermissionError(f"{event} outside private-runner refused")
        elif event in ("socket.connect", "socket.getaddrinfo"):
            raise PermissionError("network refused")
    sys.addaudithook(hook)


# --- the child (direct mode) ---------------------------------------------------------------------------------
def _shim(name: str, **attrs) -> None:
    """A stand-in for another task's module when it is not importable yet (only the language and birth
    adapters use them; their rows are not parity rows)."""
    import importlib
    import types
    full = f"src.verify.runner.{name}"
    try:
        importlib.import_module(full)
        return
    except Exception:
        pass
    mod = types.ModuleType(full)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[full] = mod


def child(sid: str, folder: Path, program: str, today: dt.date, out: Path, board_qr: bool) -> int:
    _guard(block_bot_opens=True)
    from types import SimpleNamespace

    from .. import doccheck
    from ..model import AffidavitInfo, DerivedRecord, DocUnit, Identity, NameRef, PageRead
    ns = botimport.load(OUT / "data")
    dv, R, TC = ns.dv, ns.R, ns.TC
    dv.TODAY = today
    import types
    if not board_qr:                               # the board-QR ladder is slow and its rows are not parity rows
        sys.modules["src.verify.runner.tin"] = types.ModuleType("src.verify.runner.tin")
        sys.modules["src.verify.runner.tin"].read_qr = lambda path, deep=True: []
    _shim("names", covered=lambda aff, a, b: False, printed_student_name=lambda u, p, f: ("", False))
    _shim("identity", passport_for_language=lambda ident: {"surname": "", "given_names": "", "passport_no": "",
                                                           "dob": None, "name_verified": False,
                                                           "name_trusted": False})

    def no_image(*a, **k):
        raise RuntimeError("no page images in direct mode (cache only)")
    eng = SimpleNamespace(bot=ns, page_image_of=no_image, read_boxes=no_image, page_image=no_image)
    handle = TC.install(sid, cache_only=True, text_dir=OUT / "data" / "verification" / "text",
                        images_dir=OUT / "data" / "verification" / "images")
    report: Dict[str, Any] = {"sid": sid, "program": program, "today": today.isoformat(), "units": [],
                              "skipped_suffix": 0, "left_out": {}}
    try:
        wanted = list(dict.fromkeys(list(R.REQUIRED[program]) + list(R.OPTIONAL)))
        files = [f for f in sorted(Path(folder).iterdir()) if f.is_file() and not f.name.startswith(".")]
        units: List[DocUnit] = []
        pages: List[PageRead] = []
        for f in files:
            key = dv.classify(f.name)
            if key is None or key not in wanted:
                continue
            if f.suffix.lower() not in SUFFIXES:
                report["skipped_suffix"] += 1
                continue
            if key in LEFT_OUT_KEYS:
                report["left_out"][key] = report["left_out"].get(key, 0) + 1
                continue
            got = TC.page_texts(handle.cache, f, TC.MAX_PAGES)
            texts = list(got[0]) if got else []
            if not texts:
                whole = TC.whole_text(handle.cache, f, TC.MAX_PAGES)
                texts = [whole[0]] if whole else [""]
            idx = list(range(len(pages), len(pages) + len(texts)))
            for k, t in enumerate(texts):
                pages.append(PageRead(index=len(pages), file=f.name, file_page=k, text=t or "", kind="ocr",
                                      chars=len(t or ""), sideways=False, seconds=0.0, image_only=False))
            units.append(DocUnit(uid=f"D{len(units) + 1:02d}", key=key, fine=key, title=key.upper(), pages=idx,
                                 path=f, source_file=f.name, role_how="file-name"))
        ident = Identity(student=NameRef(), father=NameRef(), mother=NameRef())
        rec = DerivedRecord(program, today, ident)
        t0 = time.perf_counter()
        dc = doccheck.check_documents(units, pages, rec, eng, "folder", AffidavitInfo(), {},
                                      lambda i, v: "unclear", work_dir=out / "work")
        report["seconds"] = round(time.perf_counter() - t0, 2)
        for u in units:
            try:
                text = dv.read_document(Path(u.path))[0]
            except Exception:
                text = ""
            report["units"].append({
                "file": u.source_file, "key": u.key,
                "bot_qr_skip": bool(_QR_SKIP_WORDS.search((text or "").lower())),
                "findings": [{"verdict": f.verdict, "rule": f.rule, "message": f.message, "source": f.source}
                             for f in dc.per_unit.get(u.uid, [])]})
        report["not_cached"] = len(handle.misses)
        report["fresh"] = len(handle.fresh)
    finally:
        TC.uninstall(handle)
    out.mkdir(parents=True, exist_ok=True)
    (out / "findings.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


# --- the parent ----------------------------------------------------------------------------------------------
def pick(students, n: int, sids: Optional[Sequence[str]] = None):
    """n held-out students, programs mixed: round robin over the programs, each in sid order."""
    from . import corpus
    _, held = corpus.halves(students)
    if sids:
        return [s for s in held if s.sid in set(sids)]
    groups: Dict[str, List[Any]] = {}
    for s in sorted(held, key=lambda s: s.sid):
        groups.setdefault(s.program, []).append(s)
    out: List[Any] = []
    while len(out) < n and any(groups.values()):
        for prog in sorted(groups):
            if groups[prog] and len(out) < n:
                out.append(groups[prog].pop(0))
    return out


def _copy(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return True


def _runner_units_full(run_json: Path) -> List[Dict[str, Any]]:
    data = json.loads(run_json.read_text(encoding="utf-8"))
    out = []
    for d in data.get("docs") or []:
        u = d.get("unit") or {}
        out.append({"file": u.get("source_file") or "", "key": u.get("key") or "", "bot_qr_skip": None,
                    "findings": d.get("findings") or []})
    return out


def run_student(s, rec: Dict[str, Any], mode: str, board_qr: bool, py: str) -> Dict[str, Any]:
    """Seed the caches, run the child (direct) or the runner (full); -> the child's report."""
    sd = OUT / s.sid
    program = str(rec.get("program") or s.program).upper()
    when = str(rec.get("evaluated_at") or rec.get("checked") or "")[:10]
    today = dt.date.fromisoformat(when) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else dt.date.today()
    if mode == "direct":
        _copy(s.cache_json, OUT / "data" / "verification" / "text" / f"{s.sid}.json")
        _copy(s.images_json, OUT / "data" / "verification" / "images" / f"{s.sid}.json")
        cmd = [py, "-I", "-B", "-m", "src.verify.runner.selftest.folder_parity", "--child", s.sid,
               "--folder", str(s.folder), "--program", program, "--today", today.isoformat(), "--out", str(sd)]
        if board_qr:
            cmd.append("--board-qr")
        r = subprocess.run(cmd, capture_output=True, timeout=3600)
        (sd).mkdir(parents=True, exist_ok=True)
        (sd / "child.log").write_bytes(r.stdout + b"\n--- stderr ---\n" + r.stderr)
        if r.returncode != 0 or not (sd / "findings.json").exists():
            return {"sid": s.sid, "error": f"child exit {r.returncode}"}
        return json.loads((sd / "findings.json").read_text(encoding="utf-8"))
    from .. import ingest
    case = ingest.open_input(Path(s.folder)).case_id
    root = PRIVATE / "runner-data" / "verification"
    _copy(s.cache_json, root / "text" / f"{case}.json")
    _copy(s.images_json, root / "images" / f"{case}.json")
    cmd = [py, "-I", "-B", "-m", "src.verify.runner", str(s.folder), "--program", program,
           "--today", today.isoformat(), "--out", str(sd / "run")]
    r = subprocess.run(cmd, capture_output=True, timeout=7200)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "runner.log").write_bytes(r.stdout + b"\n--- stderr ---\n" + r.stderr)
    rj = sd / "run" / "run.json"
    if r.returncode not in (0, 3) or not rj.exists():
        return {"sid": s.sid, "error": f"runner exit {r.returncode}"}
    rep = {"sid": s.sid, "program": program, "today": today.isoformat(), "units": _runner_units_full(rj)}
    data = json.loads(rj.read_text(encoding="utf-8"))
    rep["rereads"] = data.get("names_rereads") or []
    return rep


def compare(sid: str, rec: Dict[str, Any], rep: Dict[str, Any]) -> Dict[str, Any]:
    """Per (file, key, rule): the bot's verdicts against the runner's. Expected differences (the QR-check
    defuse of 5.8) are counted apart."""
    program = str(rec.get("program") or rep.get("program") or "").upper()
    units = {(u["file"], u["key"]): u for u in rep.get("units", [])}
    rows, same, diffs, expected, unmatched, not_cached, left_out = 0, 0, [], [], 0, 0, 0
    for row in rec.get("rows") or []:
        key, fname = row.get("key"), row.get("file") or ""
        if not fname or key in ("cross_check", None):
            continue
        u = units.get((fname, key))
        if u is None:
            if key in LEFT_OUT_KEYS or (key == "income_tax" and program != "MASTER"):
                left_out += 1
            else:
                unmatched += 1
            continue
        mine = [(f["verdict"], f["message"]) for f in u["findings"]]
        if any(f.get("rule") == "doc.unreadable" for f in u["findings"]):
            not_cached += 1
            continue
        a = by_rule(key, parse_detail(row.get("detail", "")), program)
        b = by_rule(key, mine, program)
        for rule in sorted(set(a) | set(b)):
            va, vb = a.get(rule, []), b.get(rule, [])
            if rule == "page.qr" and not va and vb and u.get("bot_qr_skip") is not False:
                expected.append({"sid": sid, "key": key, "rule": rule, "why": "qr_check defused (5.8)",
                                 "bot": va, "runner": vb})
                continue
            if rule == "bank.one_page" and va and not vb and any(m.startswith(_SPLIT_BANK) for _, m in mine):
                expected.append({"sid": sid, "key": key, "rule": rule, "bot": va, "runner": [],
                                 "why": "certificate and statement in separate bank files, judged together (review 2)"})
                continue
            rows += 1
            if va == vb:
                same += 1
            else:
                diffs.append({"sid": sid, "key": key, "rule": rule, "bot": va, "runner": vb, "file": fname,
                              "bot_messages": [m for v, m in parse_detail(row.get("detail", ""))
                                               if rule_of(key, m, program) == rule],
                              "runner_messages": [m for v, m in mine if rule_of(key, m, program) == rule]})
    return {"rows": rows, "same": same, "diffs": diffs, "expected": expected, "unmatched": unmatched,
            "not_cached": not_cached, "left_out": left_out}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="folder_parity")
    ap.add_argument("--mode", choices=("direct", "full"), default="direct")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--sids", default="")
    ap.add_argument("--board-qr", action="store_true", help="read board QRs with the real ladder (slow)")
    ap.add_argument("--child", default="")
    ap.add_argument("--folder", default="")
    ap.add_argument("--program", default="")
    ap.add_argument("--today", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    if a.child:
        out = Path(a.out).resolve()
        if not out.is_relative_to(OUT):
            print("refused: --out must be under", OUT)
            return 2
        return child(a.child, Path(a.folder), a.program.upper(), dt.date.fromisoformat(a.today), out, a.board_qr)

    _guard(block_bot_opens=False)               # the harness reads the bot's caches; it writes only here
    from . import corpus
    t0 = time.perf_counter()
    students = corpus.load_corpus()
    chosen = pick(students, a.n, [x for x in a.sids.split(",") if x])
    results = json.loads(BOT_RESULTS.read_text(encoding="utf-8")).get("documents") or {}
    OUT.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    total = {"rows": 0, "same": 0, "expected": 0, "unmatched": 0, "not_cached": 0, "left_out": 0}
    all_diffs: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    per_rule: Dict[str, List[int]] = {}
    progs: Dict[str, int] = {}
    skipped = []
    for s in chosen:
        cid = Path(s.cache_json).stem
        rec = results.get(cid)
        if not rec or rec.get("rules_version") != BASE_RULES:
            skipped.append((s.sid, "no bot record at " + BASE_RULES))
            continue
        rep = run_student(s, rec, a.mode, a.board_qr, py)
        if rep.get("error"):
            skipped.append((s.sid, rep["error"]))
            print(f"{s.sid}: {rep['error']}")
            continue
        c = compare(s.sid, rec, rep)
        progs[str(rec.get("program"))] = progs.get(str(rec.get("program")), 0) + 1
        for k in total:
            total[k] += len(c[k]) if isinstance(c[k], list) else c[k]
        all_diffs += c["diffs"]
        for d in c["diffs"]:
            per_rule.setdefault(d["rule"], [0, 0])[1] += 1
        for rr in rep.get("rereads") or []:
            eff = str(rr.get("effect", ""))
            if "FAIL" in eff.upper():
                audit.append({"sid": s.sid, "rule": rr.get("rule", ""), "crop": rr.get("crop", ""), "effect": eff})
        print(f"{s.sid} {rec.get('program'):9s} rows {c['rows']:3d} same {c['same']:3d} diffs {len(c['diffs']):2d} "
              f"expected {len(c['expected']):2d} not-cached {c['not_cached']} unmatched {c['unmatched']}"
              + (f" units {len(rep.get('units', []))} {rep.get('seconds', '')}s" if a.mode == "direct" else ""))
    rate = total["same"] / total["rows"] if total["rows"] else 0.0
    (OUT / "diffs.json").write_text(json.dumps(all_diffs, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "reread_audit.json").write_text(json.dumps(
        {"mode": a.mode, "note": ("direct mode makes no re-reads (confirm is 'unclear')" if a.mode == "direct"
                                  else "every FLAG->FAIL caused by a re-read confirmation"),
         "items": audit}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("---")
    print("mode", a.mode, "| students", len(chosen) - len(skipped), "of", len(chosen),
          "| programs", " ".join(f"{k} {v}" for k, v in sorted(progs.items())))
    print(f"compared rows {total['rows']}  equal {total['same']}  rate {rate:.4f}  (floor {FLOOR})  "
          f"expected differences {total['expected']}  not cached {total['not_cached']}  unmatched {total['unmatched']}  "
          f"left out on purpose (birth A3, non-Master's income tax) {total['left_out']}")
    for rule, (_, n) in sorted(per_rule.items()):
        sids = sorted({d["sid"] for d in all_diffs if d["rule"] == rule})
        print(f"  diff {rule}: {n}  sids {','.join(sids)}")
    for sid, why in skipped:
        print(f"  skipped {sid}: {why}")
    print(f"re-read audit items {len(audit)} | {'ok' if rate >= FLOOR else 'MISS'} | "
          f"{time.perf_counter() - t0:.0f}s | details: {OUT}")
    return 0 if rate >= FLOOR else 1


if __name__ == "__main__":
    sys.exit(main())
