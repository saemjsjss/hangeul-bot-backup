"""T2 sim_split: the split measured on simulated fixed-order bundles (RUNNER_SPEC section 10, Task 2).

A port of the lab's order_split.py lines 708-923 (bundle simulation and scoring) and 925 (check0001), run
on the runner's pagetype.py + split.py. Text level only: no OCR, no PDF, nothing rendered.

    <runner venv python> -I -B -m src.verify.runner.selftest.sim_split              # held-out + tune
    <runner venv python> -I -B -m src.verify.runner.selftest.sim_split --half heldout --show 3
    <runner venv python> -I -B -m src.verify.runner.selftest.sim_split --check0001  # T1 step 3 only
    <runner venv python> -I -B -m src.verify.runner.selftest.sim_split --no-parity

READ-ONLY on the corpus (the verified student folders and the bot's text cache, through
selftest/corpus.py), on the lab's order_split.py (imported for the parity check) and on the test-1 page
texts. It writes only under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\sim_split\\ (the printed
report and a JSON of the numbers). It prints counts, rates, ok/MISS, type labels, sids and page numbers,
never a name, a number or page text.

Bundles: the held-out half (seed 20261027). Each student's files in the owner's fixed order (one file per
bot key, NID pages translation first), plus one-mistake bundles (two adjacent required documents swapped,
one required document moved, the father's and mother's NIDs swapped). Revision 2 places a generic DEATH
CERTIFICATE in the NID block (a death certificate belongs there, section 5.4 change 4); the lab put it
after the birth certificate.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from src.verify.runner import botimport
from src.verify.runner import pagetype as PT
from src.verify.runner import split as SP
from src.verify.runner.model import PageRead, SplitResult
from src.verify.runner.selftest import corpus as C

PRIVATE = botimport.PRIVATE
OUT_DIR = PRIVATE / "selftest" / "sim_split"
LAB_ORDER_SPLIT = PRIVATE / "order_split.py"
TEST1 = PRIVATE / "test1"

# ============================================================== 1. simulated bundles (lab L708-783)
ORDER_KEYS = ["passport", "photo", "father_nid", "father_death_cert", "mother_nid", "mother_death_cert",
              "death_cert", "student_nid", "birth_cert", "family_cert", "affidavit", "academic",
              "language_cert", "bank", "trade_license", "income_tax", "eca", "personal_statement",
              "recommendation", "id_card_cv"]
REQ_KEYS = ["passport", "photo", "birth_cert", "family_cert", "academic", "bank", "trade_license", "income_tax"]
SECTION_OF = {"passport": "passport", "photo": "photo", "father_nid": "nid", "mother_nid": "nid",
              "student_nid": "nid", "father_death_cert": "death_cert", "mother_death_cert": "death_cert",
              "death_cert": "death_cert", "birth_cert": "birth_cert", "family_cert": "family_cert",
              "academic": "academic", "bank": "bank", "trade_license": "financial", "income_tax": "income_tax",
              "affidavit": "affidavit", "language_cert": "language_cert", "eca": "eca",
              "personal_statement": "personal_statement", "recommendation": "recommendation",
              "id_card_cv": "id_card_cv"}
ROLE_OF = {"father_nid": "father", "mother_nid": "mother", "student_nid": "student",
           "father_death_cert": "father", "mother_death_cert": "mother"}
# the lab's slots (the truth the simulated bundles can give): financial = any trade_license-key file
LAB_SLOTS = ["passport", "photo", "father_id", "mother_id", "birth_cert", "family_cert", "academic", "bank",
             "financial"]


@dataclass
class Bundle:
    sid: str
    texts: List[str]
    spans: List[Tuple[int, int, str]]          # truth: (start, end, bot key)
    mistake: str = ""
    mistake_pages: Set[int] = field(default_factory=set)
    program: str = ""


def _file_pages(f: C.CorpusFile) -> List[str]:
    pages = [" ".join(f.pages)] if f.blob else list(f.pages)
    if f.key in ROLE_OF and f.key.endswith("nid"):
        # the owner puts each NID's translation in front of its original (stable)
        tr = [PT.page_features(p).translation for p in pages]
        pages = [p for p, t in zip(pages, tr) if t] + [p for p, t in zip(pages, tr) if not t]
    return pages


def build(stu: C.CorpusStudent, rng: random.Random, mistake: str = "") -> Optional[Bundle]:
    by: Dict[str, C.CorpusFile] = {}
    for f in stu.files:
        by.setdefault(f.key, f)                    # one file per slot in this data
    keys = [k for k in ORDER_KEYS if k in by]
    units = [[k] for k in keys]
    picked = ""
    moved: Set[str] = set()
    if mistake:
        req = [i for i, u in enumerate(units) if u[0] in REQ_KEYS]
        if mistake == "swap_adjacent" and len(req) >= 2:
            pairs = [(a, b) for a, b in zip(req, req[1:])]
            a, b = rng.choice(pairs)
            moved = {units[a][0], units[b][0]}
            units[a], units[b] = units[b], units[a]
            picked = f"swap {units[b][0]}<->{units[a][0]}"
        elif mistake == "move" and len(req) >= 3:
            a = rng.choice(req)
            u = units.pop(a)
            choices = [j for j in range(len(units) + 1) if abs(j - a) >= 2]
            j = rng.choice(choices)
            units.insert(j, u)
            moved = {u[0]}
            picked = f"move {u[0]} {'earlier' if j < a else 'later'}"
        elif mistake == "swap_parents" and "father_nid" in by and "mother_nid" in by:
            i, j = keys.index("father_nid"), keys.index("mother_nid")
            units[i], units[j] = units[j], units[i]
            moved = {"father_nid", "mother_nid"}
            picked = "swap father/mother NID"
        else:
            return None
    texts: List[str] = []
    spans = []
    mp: Set[int] = set()
    for u in units:
        k = u[0]
        s = len(texts)
        texts.extend(_file_pages(by[k]))
        spans.append((s, len(texts) - 1, k))
        if k in moved:
            mp.update(range(s, len(texts)))
    return Bundle(stu.sid, texts, spans, picked, mp, stu.program)


# ============================================================== 2. running the port
@dataclass
class PortRun:
    res: SplitResult
    docs: List[Tuple[int, int, str, bool]]               # joined documents (start, end, label, misplaced)
    roles: Dict[Tuple[int, int], Tuple[str, str]]         # NID / death span -> (role, role_how)


def unit_label(key: str) -> str:
    if key in ("father_nid", "mother_nid", "student_nid", "nid_unassigned"):
        return "nid"
    if key.endswith("death_cert"):
        return "death_cert"
    if key == "trade_license":
        return "financial"
    return key


def run_port(texts: Sequence[str], program: str, image_only: bool = False) -> PortRun:
    pages = [PageRead(index=i, file="bundle.pdf", file_page=i, text=t, kind="ocr", chars=len(t), sideways=False,
                      seconds=0.0, image_only=image_only) for i, t in enumerate(texts)]
    types = PT.type_pages(pages)
    seg = SP.segment(types, program)
    refs = SP.references(texts, seg.docs)
    res = SP.finish(seg, types, pages, refs, program)
    docs: List[List] = []
    roles: Dict[Tuple[int, int], Tuple[str, str]] = {}
    for u in res.units:
        lab = unit_label(u.key)
        a, b = u.pages[0], u.pages[-1]
        if docs and lab in ("academic", "financial") and docs[-1][2] == lab and docs[-1][3] == u.misplaced \
                and docs[-1][1] == a - 1:
            docs[-1][1] = b                          # the lab's one academic / financial document
        else:
            docs.append([a, b, lab, u.misplaced])
        if lab in ("nid", "death_cert"):
            roles[(a, b)] = (u.role, u.role_how)
    return PortRun(res, [tuple(d) for d in docs], roles)


# notice categories (revision-2 mapping of the lab's notes)
def port_category(kind: str, msg: str) -> str:
    if kind in ("HINT", "STOP", "NOTE"):
        return kind
    for cue, cat in (("NIDs are not in the order", "ORDER-nid"), ("look like", "ORDER-misplaced"),
                     ("translation is behind", "ORDER-translation"), ("not newest first", "ORDER-academic"),
                     ("transcript comes before", "ORDER-transcript"),
                     ("statement page comes before", "ORDER-statement"),
                     ("not recognised as any document", "ORDER-unrecognised")):
        if cue in msg:
            return cat
    return "ORDER-other"


def lab_category(kind: str, msg: str) -> str:
    if kind == "ORDER" and "in its place;" in msg:
        return "HINT"
    if kind == "STOP":
        return "STOP"
    for cue, cat in (("NIDs are not in the order", "ORDER-nid"), ("look like", "ORDER-misplaced"),
                     ("translation is behind", "ORDER-translation"), ("not newest first", "ORDER-academic")):
        if cue in msg:
            return cat
    return f"{kind}-other"


NEW_CATEGORIES = {"ORDER-transcript", "ORDER-statement", "ORDER-unrecognised"}
# where documents sit: the lab's ORDER notices without its missing-document hints (now HINT). The floor
# "correct-order bundles with a false ORDER notice" is the study's number on these (section 10, T2).
DOC_ORDER = {"ORDER-nid", "ORDER-misplaced"}
UNREC = {"ORDER-unrecognised"}                  # rev 2: an unrecognised run in the middle (new, reported apart)
WITHIN_DOC = {"ORDER-translation", "ORDER-academic", "ORDER-transcript", "ORDER-statement"}


# ============================================================== 3. scoring (lab L786-923, rev 2)
def score(bundles: List[Bundle], show: int = 0, out=print, parity_lab=None) -> Tuple[collections.Counter, Dict, List[str]]:
    m: collections.Counter = collections.Counter()
    per_type: Dict[str, List[int]] = collections.defaultdict(lambda: [0, 0])
    diffs: List[str] = []
    for bi, b in enumerate(bundles):
        run = run_port(b.texts, b.program)
        res = run.res
        pred = {(s, e): (lab, mis) for s, e, lab, mis in run.docs}
        unrec_pages = {i for s, e, lab, _ in run.docs if lab == "unrecognised" for i in range(s, e + 1)}
        for s, e, k in b.spans:
            sec = SECTION_OF[k]
            d = pred.get((s, e))
            ok = d is not None and d[0] == sec
            ok_l = ok
            if not ok:
                for (ps, pe), (dl, _) in pred.items():
                    if dl == sec and s <= ps <= pe <= e and all(i in unrec_pages for i in range(s, ps)) \
                            and all(i in unrec_pages for i in range(pe + 1, e + 1)):
                        ok_l = True
            m["files"] += 1
            m["exact"] += ok
            m["exact_lenient"] += ok_l
            per_type[k][0] += 1
            per_type[k][1] += ok
            if k in REQ_KEYS or k in ROLE_OF:
                m["req"] += 1
                m["req_exact"] += ok
            if k in ROLE_OF:
                m["persons"] += 1
                role, how = run.roles.get((s, e), ("", ""))
                if ok and role == ROLE_OF[k]:
                    m["person_ok"] += 1
                if ok:
                    m["person_exact"] += 1
                    m["role_given_exact"] += role == ROLE_OF[k]
                    m["how_" + (how or "none")] += 1
                    m["how_ok_" + (how or "none")] += role == ROLE_OF[k]
        # notices
        cats = [(port_category(nt.kind, nt.message), nt) for nt in res.notices]
        orders = [(c, nt) for c, nt in cats if nt.kind == "ORDER"]
        named = [(c, nt) for c, nt in cats if nt.kind in ("ORDER", "HINT")]
        m["bundles"] += 1
        m["stop"] += res.stop
        m["any_order_bundles"] += bool(orders)
        m["doc_order_bundles"] += any(c in DOC_ORDER for c, _ in orders)
        m["unrec_bundles"] += any(c in UNREC for c, _ in orders)
        m["within_doc_bundles"] += any(c in WITHIN_DOC for c, _ in orders)
        m["blocking_bundles"] += any(nt.blocks for _, nt in orders)
        tl = {i: k for s, e, k in b.spans for i in range(s, e + 1)}
        for c, nt in cats:
            m["n_" + c] += 1
            if c in UNREC and not b.mistake:         # what an unrecognised run in a correct bundle truly is
                for k in sorted({tl[i] for i in range(nt.pages[0] - 1, nt.pages[1])}):
                    m["unrec_truth_" + k] += 1
        for c, nt in named:
            hit = bool(b.mistake_pages & set(range(nt.pages[0] - 1, nt.pages[1])))
            m["named_notices"] += 1
            m["named_true"] += hit
        if b.mistake:
            m["mistakes"] += 1
            kind = "parents" if "father/mother" in b.mistake else b.mistake.split()[0]
            m["mk_" + kind] += 1
            det = any(b.mistake_pages & set(range(nt.pages[0] - 1, nt.pages[1])) for _, nt in named)
            m["detected"] += det
            m["det_" + kind] += det
        # missing (required, by the program) vs truth, on the lab's slots
        keys = {k for _, _, k in b.spans}
        have_truth = set()
        for k in keys:
            if k in ROLE_OF and k != "student_nid":
                have_truth.add(ROLE_OF[k] + "_id")
            elif SECTION_OF[k] in LAB_SLOTS + ["income_tax"]:
                have_truth.add(SECTION_OF[k])
        reported = set(res.missing) | set(res.maybe)
        told_lab = {s for s in reported if s not in ("owner_doc", "tin")}
        if "owner_doc" in res.missing and "tin" in res.missing:
            told_lab.add("financial")
        lab_slots = LAB_SLOTS + (["income_tax"] if b.program == "MASTER" else [])
        for slot in lab_slots:
            truly = slot in have_truth
            m["miss_slots"] += 1
            m["miss_false"] += truly and slot in told_lab
            m["miss_true"] += (not truly) and slot in told_lab
            m["miss_absent"] += not truly
        if "financial" in have_truth:
            m["fin_bundles"] += 1
            m["fin_owner_missing"] += "owner_doc" in res.missing
            m["fin_tin_missing"] += "tin" in res.missing
        if parity_lab is not None:
            _parity(parity_lab, b, bi, run, m, diffs)
        if bi < show:
            _show(b, run, out)
    return m, per_type, diffs


def _parity(OS, b: Bundle, bi: int, run: PortRun, m: collections.Counter, diffs: List[str]) -> None:
    lab = OS.run_bundle(b.texts, b.program)
    lspans = [(d.start, d.end, d.label) for d in lab.docs]
    pspans = [(s, e, lab_) for s, e, lab_, _ in run.docs]
    tag = f"{b.sid} {'mistake ' + b.mistake if b.mistake else 'clean'}"
    same_spans = lspans == pspans
    m["par_bundles"] += 1
    m["par_spans_same"] += same_spans
    if not same_spans:
        only_l = [x for x in lspans if x not in pspans]
        only_p = [x for x in pspans if x not in lspans]
        reason = _reason(only_l, only_p, len(b.texts), any("death_cert" in k for _, _, k in b.spans))
        m["par_reason_" + reason.split(" (")[0]] += 1
        diffs.append(f"  spans  {tag}: lab {_fmt(only_l)} | port {_fmt(only_p)} — {reason}")
    lroles = {(d.start, d.end): d.role for d in lab.docs if d.label in ("nid", "death_cert") and d.role}
    roles_same = True
    for span, lrole in lroles.items():
        if span in run.roles:
            m["par_roles_compared"] += 1
            prole = run.roles[span][0] or "extra"
            if prole == lrole:
                m["par_roles_same"] += 1
            else:
                roles_same = False
                diffs.append(f"  role   {tag}: pages {span[0] + 1}-{span[1] + 1} lab {lrole} | port {prole} "
                             f"({run.roles[span][1] or 'none'}) — strict role scoring (5.6)")
    m["par_roles_bundles_same"] += roles_same
    lcat = collections.Counter((lab_category(k, msg), (pg[0] + 1, pg[1] + 1)) for k, pg, msg in lab.notes
                               if lab_category(k, msg) != "STOP")
    pcat = collections.Counter((port_category(nt.kind, nt.message), nt.pages) for nt in run.res.notices
                               if port_category(nt.kind, nt.message) not in NEW_CATEGORIES | {"STOP"})
    m["par_notice_bundles"] += 1
    if lcat == pcat:
        m["par_notices_same"] += 1
    else:
        only_l = sorted((lcat - pcat).elements())
        only_p = sorted((pcat - lcat).elements())
        why = ("follows the span difference" if not same_spans else
               "follows the role difference" if not roles_same else "notice rule")
        m["par_notice_reason_" + why.split()[0]] += 1
        diffs.append(f"  notice {tag}: lab {only_l} | port {only_p} — {why}")
    m["par_stop_lab"] += lab.stop
    m["par_stop_port"] += run.res.stop
    m["par_new_notices"] += sum(1 for nt in run.res.notices if port_category(nt.kind, nt.message) in NEW_CATEGORIES)


def _fmt(spans) -> str:
    return "[" + ", ".join(f"{s + 1}-{e + 1} {lab}" for s, e, lab in spans) + "]"


def _reason(only_l, only_p, n: int, has_death: bool = False) -> str:
    labs = {x[2] for x in only_l} | {x[2] for x in only_p}
    if "death_cert" in labs or (has_death and labs <= {"nid", "death_cert", "unrecognised"}):
        return "death certificate anchored to the NID block (rev 2 change 4)"
    if any(x[2] == "income_tax" and x[1] == n - 1 for x in only_p) and any(x[2] == "unrecognised" for x in only_l):
        return "trailing page kept with income tax (A6 f)"
    if labs <= {"nid"}:
        return "NID person split: strict holder words (S8)"
    return "other (investigate)"


def _show(b: Bundle, run: PortRun, out) -> None:
    out(f"\n  bundle {b.sid} ({b.program}, {len(b.texts)} pages){' mistake: ' + b.mistake if b.mistake else ''}")
    tl = {i: k for s, e, k in b.spans for i in range(s, e + 1)}
    ul = {i: u for u in run.res.units for i in u.pages}
    for i in range(len(b.texts)):
        u = ul[i]
        f = PT.page_features(b.texts[i])
        out(f"    p{i + 1:>3} true={tl[i]:<18} page={f.best:<22}{f.score:5.1f}{' T' if f.translation else '  '}"
            f"  {u.uid} {u.title}{(' /' + u.role + '(' + u.role_how + ')') if u.role else ''}"
            f"{' MISPLACED' if u.misplaced else ''}")
    for nt in run.res.notices:
        out(f"    {nt.kind}{' (blocks)' if nt.blocks else ''} p{nt.pages[0]}-{nt.pages[1]}: "
            f"{port_category(nt.kind, nt.message)}")
    for x in run.res.missing:
        out(f"    MISSING: {SP.MISSING_TEXT[x]}")


def _r(m, a, b) -> str:
    return f"{m[a]}/{m[b]} = {m[a] / max(1, m[b]):.3f}"


def report(name: str, m, per_type, out=print) -> None:
    out(f"\n== {name}: {m['bundles']} bundles, {m['files']} documents")
    out(f"  exact document boundaries (right pages, right type): {_r(m, 'exact', 'files')}")
    out(f"     lenient (unrecognised edge pages forgiven)       : {_r(m, 'exact_lenient', 'files')}")
    out(f"     required documents (incl. every NID / death cert): {_r(m, 'req_exact', 'req')}")
    out(f"  NID / death-cert person: exact span AND right person: {_r(m, 'person_ok', 'persons')}")
    out(f"     right person among exact spans                   : {_r(m, 'role_given_exact', 'person_exact')}")
    out(f"     by strict name: {m['how_name']} (right {m['how_ok_name']}); name-partial: {m['how_name-partial']} "
        f"(right {m['how_ok_name-partial']}); by position: {m['how_position']} (right {m['how_ok_position']})")
    out(f"  missing (lab slots): reported missing but present {m['miss_false']}/{m['miss_slots']}; truly absent "
        f"{m['miss_absent']}, of which reported {m['miss_true']}")
    out(f"     with a financial file: owner_doc MISSING {m['fin_owner_missing']}, tin MISSING {m['fin_tin_missing']} "
        f"of {m['fin_bundles']} (no truth for the parts: information)")
    out(f"  bundles with an ORDER notice: any {m['any_order_bundles']}; where documents sit (misplaced run, NID "
        f"order) {m['doc_order_bundles']}; unrecognised run in the middle (rev 2) {m['unrec_bundles']}; inside a "
        f"document {m['within_doc_bundles']}; blocking {m['blocking_bundles']}")
    if any(k.startswith("unrec_truth_") for k in m):
        out("     the unrecognised runs' true documents (correct-order bundles): " + ", ".join(
            f"{k[12:]} {v}" for k, v in sorted(m.items(), key=lambda kv: -kv[1]) if k.startswith("unrec_truth_")))
    out(f"  STOP {m['stop']}; notices by category: " + ", ".join(
        f"{k[2:]} {v}" for k, v in sorted(m.items()) if k.startswith("n_")))
    if m["mistakes"]:
        out(f"  mistakes named by an ORDER or HINT notice: {_r(m, 'detected', 'mistakes')}")
        for k in ("swap", "move", "parents"):
            if m["mk_" + k]:
                out(f"     {k:<7}: {m['det_' + k]}/{m['mk_' + k]}")
    out(f"  ORDER/HINT notices that point at a moved document: {_r(m, 'named_true', 'named_notices')}")
    out("  per document type (exact): " + ", ".join(f"{k} {v[1]}/{v[0]}" for k, v in sorted(per_type.items())))


FLOORS = [  # (label, value function, floor, higher is better)
    ("exact boundaries, correct order", lambda c, k: c["exact"] / max(1, c["files"]), 0.85, True),
    ("required incl. NID / death certificate", lambda c, k: c["req_exact"] / max(1, c["req"]), 0.90, True),
    ("mistake bundles, exact boundaries", lambda c, k: k["exact"] / max(1, k["files"]), 0.82, True),
    ("NID / death certificate: right pages and person", lambda c, k: c["person_ok"] / max(1, c["persons"]), 0.88, True),
    ("mistakes named by an ORDER or HINT notice", lambda c, k: k["detected"] / max(1, k["mistakes"]), 0.78, True),
    ("correct-order bundles with a false ORDER notice (misplaced run or NID order, as in the study)",
     lambda c, k: c["doc_order_bundles"], 10, False),
    ("STOP on correct-order bundles", lambda c, k: c["stop"], 3, False),
    ("true missing reported (all)", lambda c, k: c["miss_absent"] - c["miss_true"], 0, False),
]


def check_floors(clean, mist, out=print) -> bool:
    ok_all = True
    for label, fn, floor, higher in FLOORS:
        v = fn(clean, mist)
        ok = v >= floor if higher else v <= floor
        ok_all &= ok
        shown = f"{v:.3f}" if isinstance(v, float) else str(v)
        out(f"  {'ok  ' if ok else 'MISS'} {label}: {shown} ({'>=' if higher else '<='} {floor})")
    out(f"     (not in the study's measure: correct-order bundles with an unrecognised-run ORDER notice "
        f"{clean['unrec_bundles']}, with an order break inside a document {clean['within_doc_bundles']}, with any "
        f"ORDER notice {clean['any_order_bundles']}, of {clean['bundles']}; true missing reported "
        f"{clean['miss_true']}/{clean['miss_absent']})")
    return ok_all


# ============================================================== 4. CHECK_0001, text level (T1 step 3)
CHECK0001_TRUTH = [  # (first page, last page, key, fine, level, role); 1-based pages
    (1, 1, "passport", "passport", "", ""), (2, 3, "father_nid", "nid", "", "father"),
    (4, 5, "mother_nid", "nid", "", "mother"), (6, 7, "academic", "academic", "HSC", ""),
    (8, 9, "academic", "academic", "SSC", ""), (10, 10, "trade_license", "employment_certificate", "", ""),
    (11, 11, "trade_license", "tin_certificate", "", ""), (12, 14, "income_tax", "income_tax", "", "")]


def check0001(out=print) -> bool:
    ok_all = True
    for fn in ("pages.json", "pages_cpu.json"):
        p = TEST1 / fn
        if not p.exists():
            out(f"CHECK_0001 {fn}: not found, skipped")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))["pages"]
        texts = [d[k]["text"] for k in sorted(d, key=int)]
        for image_only in (True, False):
            run = run_port(texts, "KLP", image_only=image_only)
            res = run.res
            got = {(u.pages[0] + 1, u.pages[-1] + 1): u for u in res.units}
            out(f"CHECK_0001 {fn} (image_only={image_only}): {len(texts)} pages, {len(res.units)} units")
            for a, b, key, fine, level, role in CHECK0001_TRUTH:
                u = got.get((a, b))
                ok = (u is not None and u.key == key and u.fine == fine and u.level == level
                      and (not role or u.role == role))
                ok_all &= ok
                out(f"  {'ok  ' if ok else 'MISS'} p{a}-{b} {key} {fine} {level} {role}"
                    f"{'' if ok or u is None else f' (got {u.key} {u.fine} {u.level} {u.role})'}"
                    f"{' (role ' + u.role_how + ')' if u is not None and u.role else ''}")
            tax = got.get((12, 14))
            att = tax is not None and any(f.rule == "split.attached_trailing" for f in tax.notes)
            no14 = not any(nt.kind == "ORDER" and nt.pages[0] <= 14 <= nt.pages[1] for nt in res.notices)
            want_missing = ["photo", "birth_cert", "family_cert", "bank"]
            checks = [("page 14 attached to income tax (split.attached_trailing)", att),
                      ("no ORDER notice names page 14", no14),
                      (f"missing == {want_missing}", res.missing == want_missing),
                      ("no STOP", not res.stop)]
            for label, ok in checks:
                ok_all &= ok
                out(f"  {'ok  ' if ok else 'MISS'} {label}")
            for nt in res.notices:
                out(f"  notice {nt.kind} p{nt.pages[0]}-{nt.pages[1]}: {port_category(nt.kind, nt.message)}")
    return ok_all


# ============================================================== 5. main
def load_lab():
    """The lab's order_split.py, imported read-only for the parity check (its loaders are not called)."""
    spec = importlib.util.spec_from_file_location("lab_order_split", LAB_ORDER_SPLIT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                     # its dataclasses look their module up
    saved = list(sys.path)
    sys.path.append(str(LAB_ORDER_SPLIT.parent))     # for its `import order_data`; `src` is already loaded
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved                          # the lab folder never stays on the path
    return mod


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--half", choices=["heldout", "tune", "both"], default="both")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--check0001", action="store_true", help="only the CHECK_0001 text level (T1 step 3)")
    ap.add_argument("--no-parity", action="store_true", help="skip the parity check with the lab's order_split")
    a = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []

    def out(s: str = "") -> None:
        print(s)
        lines.append(s)

    ok0001 = check0001(out)
    out(f"CHECK_0001 text level: {'all ok' if ok0001 else 'MISS'}")
    if a.check0001:
        (OUT_DIR / "check0001.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0 if ok0001 else 1
    studs = C.load_corpus()
    tune, held = C.halves(studs)
    lab = None if a.no_parity else load_lab()
    numbers: Dict[str, Dict[str, int]] = {}
    floors_ok = True
    for half in (["heldout", "tune"] if a.half == "both" else [a.half]):
        chosen = held if half == "heldout" else tune
        out(f"\n######## {half}: {len(chosen)} students (of {len(studs)}; seed {C.SEED})")
        clean = [b for b in (build(s, random.Random(0)) for s in chosen) if b]
        rng = random.Random(C.SEED)
        mist = [b for s in chosen for kind in ("swap_adjacent", "move", "swap_parents")
                for b in [build(s, rng, kind)] if b]
        out(f"bundles: {len(clean)} correct order; {len(mist)} with one order mistake; pages per correct "
            f"bundle {min(len(b.texts) for b in clean)}-{max(len(b.texts) for b in clean)} "
            f"(mean {sum(len(b.texts) for b in clean) / len(clean):.1f})")
        m, pt, d1 = score(clean, a.show if half == "heldout" else 0, out, lab)
        report("CORRECT-ORDER bundles", m, pt, out)
        m2, pt2, d2 = score(mist, 0, out, lab)
        report("ONE-MISTAKE bundles", m2, pt2, out)
        out(f"\n  floors ({half}):")
        ok = check_floors(m, m2, out)
        if half == "heldout":
            floors_ok = ok
        if lab is not None:
            p = m + m2
            out(f"\n  parity with the lab's order_split.run_bundle ({p['par_bundles']} bundles, same bundles):")
            out(f"     spans identical (academic / financial joined): {_r(p, 'par_spans_same', 'par_bundles')}")
            out(f"       differences by reason: " + ", ".join(f"{k[11:]} {v}" for k, v in sorted(p.items())
                                                           if k.startswith("par_reason_")))
            out(f"     roles identical on equal spans: {_r(p, 'par_roles_same', 'par_roles_compared')} "
                f"(bundles with every role equal {p['par_roles_bundles_same']})")
            out(f"     notices identical after the rev-2 mapping (HINT kind, NOTE -> non-blocking ORDER; STOP and "
                f"the new transcript / statement / unrecognised notices left out): "
                f"{_r(p, 'par_notices_same', 'par_notice_bundles')}; "
                + ", ".join(f"{k[18:]} {v}" for k, v in sorted(p.items()) if k.startswith("par_notice_reason_")))
            out(f"     STOP: lab {p['par_stop_lab']}, port {p['par_stop_port']} (rev 2: the NID-person ambiguity "
                f"only); new notices in the port: {p['par_new_notices']}")
            out("     every difference:")
            for line in d1 + d2:
                out(line)
        numbers[half] = {"clean": dict(m), "mistakes": dict(m2)}
    (OUT_DIR / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT_DIR / "numbers.json").write_text(json.dumps(numbers, indent=1, sort_keys=True), encoding="utf-8")
    out(f"\nfloors (held-out): {'all ok' if floors_ok else 'MISS'}; CHECK_0001 text level: "
        f"{'all ok' if ok0001 else 'MISS'}; report under {OUT_DIR}")
    return 0 if (floors_ok and ok0001) else 1


if __name__ == "__main__":
    sys.exit(main())
