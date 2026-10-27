"""T2 sim_money (RUNNER_SPEC section 10; Task 4): the bank holder, the holder rule, the financial chain and the
TIN QR ladder measured on the 200 real bank cases and the 228 real financial files - read-only, counts only.

    python -I -B -m src.verify.runner.selftest.sim_money            # bank + holder rule + chain (seconds)
    python -I -B -m src.verify.runner.selftest.sim_money --qr       # + the TIN QR re-run (renders 228 files, slow)

Inputs (private, read-only, all under private-runner): bank_cases.json (the bank files' cached text, the other
documents' cached text and the portal values the lab used to label the truth), truth_overrides.json (the
hand-adjudicated holders), tinqr\\work\\inventory.json and qr_results_final.jsonl (the lab's TIN QR run). The
--qr re-run opens the real financial files read-only (PyMuPDF) and renders them in memory.

Measurement only. The portal values are used here ONLY to label the truth (as the lab's eval_holder.py did);
the runner modules measured never see them: the parent references come from names.canonical (the birth
certificate, every document as vocabulary) and the role-confirmed NIDs via identity.finish_identity. A block
labelled "portal references" repeats the decision counts with the portal's parent names, for the shift only.

Output: counts and rates on stdout (no name, number, file name or text); per-case private detail in
private-runner\\selftest\\sim_money\\ (cases.jsonl, qr_rerun.jsonl). No personal data in this file.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import bank as Bk
from .. import botimport
from .. import financial as F
from .. import strict as S
from .. import tin as Q
from ..model import FAIL, FLAG, NOTE, PASS, DocUnit, Identity, NameRef, PageRead

PRIVATE = botimport.PRIVATE
CASES = PRIVATE / "bank_cases.json"
OVERRIDES = PRIVATE / "truth_overrides.json"
INVENTORY = PRIVATE / "tinqr" / "work" / "inventory.json"
LAB_QR = PRIVATE / "tinqr" / "work" / "qr_results_final.jsonl"
OUT = PRIVATE / "selftest" / "sim_money"
FLOOR_HOLDER_READ = 188          # of 200
FLOOR_TIN_QR = 201               # of the 201 files holding a TIN certificate
ROLES = (("F", "Father"), ("M", "Mother"), ("S", "Full Name"))
DOC_UNITS = {                    # other_docs file name -> (bot key, role)
    "BIRTH CERTIFICATE.pdf": ("birth_cert", ""),
    "FATHER NID.pdf": ("father_nid", "father"),
    "MOTHER NID.pdf": ("mother_nid", "mother"),
    "FATHER DEATH CERTIFICATE.pdf": ("father_death_cert", "father"),
    "MOTHER DEATH CERTIFICATE.pdf": ("mother_death_cert", "mother"),
    "TRADE LICENSE.pdf": ("trade_license", ""),
}

# --- the truth label (measurement only; the lab's eval_holder.exclusive_role) ----------------------------
# A holder's role is the role whose portal name owns a word of the holder that no other role has. Titles,
# honorific spellings, initials and two-letter words own nothing here. This labels the truth; it is never a
# runner decision (the runner compares letter for letter, strict.py).
_TRUTH_SKIP = frozenset({"MD", "MST", "MOST", "MR", "MRS", "MISS", "MS", "LATE", "SK", "MOHD"})
_TRUTH_SKIP_RE = re.compile(r"^(?:M[OU]H[A]?M+[AEO]?D|MOS+[AO]M+[AE]T)$")


def _truth_words(name: str) -> List[str]:
    out: List[str] = []
    for w in S.words(name or ""):
        w = re.sub(r"[^A-Z]", "", w)
        if len(w) >= 3 and w not in _TRUTH_SKIP and not _TRUTH_SKIP_RE.match(w) and w not in out:
            out.append(w)
    return out


def exclusive_role(name: str, portal: Dict[str, str]) -> Optional[str]:
    owners = collections.defaultdict(set)
    for r, k in ROLES:
        for t in _truth_words(portal.get(k) or ""):
            owners[t].add(r)
    sole = {next(iter(o)) for t in _truth_words(name) for o in [owners.get(t, set())] if len(o) == 1}
    return next(iter(sole)) if len(sole) == 1 else None


# --- the no-portal identity (names.canonical + role-confirmed NIDs) -------------------------------------
def _unclear(i: int, v: str) -> str:
    return "unclear"


def case_documents(case: Dict[str, Any]) -> Tuple[List[DocUnit], List[PageRead]]:
    """The case's other documents as one-page units (their whole cached text), NIDs role-confirmed by name."""
    units: List[DocUnit] = []
    pages: List[PageRead] = []
    for fn in sorted(case.get("other_docs") or {}):
        if fn not in DOC_UNITS:
            continue
        key, role = DOC_UNITS[fn]
        text = case["other_docs"][fn] or ""
        i = len(pages)
        pages.append(PageRead(i, fn, 0, text, "ocr", len(text), False, 0.0, False))
        units.append(DocUnit(f"D{len(units) + 1:02d}", key, key, key.upper(), [i], role=role,
                             role_how="file-name" if role else ""))
    return units, pages


def case_identity(units: List[DocUnit], pages: List[PageRead]) -> Identity:
    from .. import identity
    ident = Identity(student=NameRef(), father=NameRef(), mother=NameRef())
    return identity.finish_identity(ident, units, pages, {}, _unclear)


def portal_identity(portal: Dict[str, str]) -> Identity:
    def ref(v: str, src: str) -> NameRef:
        return NameRef(parts=S.words(v or ""), written=v or "", source=src, confirmed=bool(v))
    return Identity(student=ref(portal.get("Full Name", ""), "portal"), father=ref(portal.get("Father", ""), "portal"),
                    mother=ref(portal.get("Mother", ""), "portal"))


# --- one case ------------------------------------------------------------------------------------------
def holder_decision(out: Bk.BankOutcome) -> str:
    v = [f.verdict for f in out.findings if f.rule == "bank.holder_parent"]
    if not v:
        return "NO-HOLDER" if not out.solvency_missing else "SOLVENCY-MISSING"
    if PASS in v:
        return PASS
    for x in (FAIL, FLAG, NOTE):
        if x in v:
            return x
    return "OTHER"


def bank_case(case: Dict[str, Any], ident: Identity) -> Bk.BankOutcome:
    texts = list(case.get("pages") or [])
    unit = DocUnit("B01", "bank", "bank", "BANK STATEMENT", list(range(100, 100 + len(texts))))
    return Bk.check_bank_unit(unit, texts, unit.pages, ident, lambda a, b: False, _unclear, lambda i: None)


def chain_case(case: Dict[str, Any], payloads: List[str], ident: Identity, bank_out: Bk.BankOutcome
               ) -> Dict[str, List[Any]]:
    text = (case.get("other_docs") or {}).get("TRADE LICENSE.pdf") or ""
    if not text:
        return {}
    tp = F.tin_taxpayer(text, payloads)
    tin_u = DocUnit("T01", "trade_license", "tin_certificate", "TIN CERTIFICATE", [200]) if (tp.get("name") or payloads) \
        else None
    owners = []
    if re.search(r"(?i)trade\s*licen[cs]e|licen[cs]e\s*n[o0]", text):
        owners.append(DocUnit("T02", "trade_license", "trade_license", "TRADE LICENSE", [201]))
    if re.search(r"(?i)employment|salary\s+certificate|experience\s+certificate|working\s+(?:as|in|at)", text):
        owners.append(DocUnit("T03", "trade_license", "employment_certificate", "EMPLOYMENT CERTIFICATE", [202]))
    bank_u = DocUnit("B01", "bank", "bank", "BANK STATEMENT", list(range(100, 100 + len(case.get("pages") or []))))
    return F.chain_findings(tp, tin_u, owners, {u.uid: text for u in owners}, bank_out.holder, bank_u, ident,
                            lambda a, b: False, _unclear)


# --- the run -------------------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="sim_money")
    ap.add_argument("--qr", action="store_true", help="re-run the TIN QR ladder on the 228 real financial files (slow)")
    ap.add_argument("--limit", type=int, default=0, help="first N bank cases / QR files only (a quick look)")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    botimport.load(botimport._loaded.cache_root if botimport._loaded is not None else botimport.DEFAULT_CACHE_ROOT)
    t0 = time.perf_counter()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    over = {int(k): v for k, v in json.loads(OVERRIDES.read_text(encoding="utf-8")).items()}
    if a.limit:
        cases = cases[:a.limit]
    payloads = _lab_payloads()
    C: collections.Counter = collections.Counter()
    detail = []
    names_ok = True
    for i, c in enumerate(cases):
        p = c["portal"]
        sol = Bk.holders_from_solvency_pages(c["pages"])
        h = sol[0] if sol else None
        truth = over.get(i) or (exclusive_role(h.name, p) if h else None) or "UNLABELLED"
        C["truth:" + truth] += 1
        human = truth in ("F", "M", "S")
        # 1. the extractor (verbatim lab code)
        if h:
            C["holder:read"] += 1
            er = exclusive_role(h.name, p)
            if er and er != truth:
                C["holder:WRONG-PERSON"] += 1
            elif human:
                C["holder:right-person"] += 1
            C["holder:joint"] += bool(h.joint)
            C["holder:ocr-suspect"] += h.ocr_suspect
        elif human:
            C["holder:MISSED(a person can read one)"] += 1
        # 2. the holder rule with the no-portal references
        units, pages = case_documents(c)
        try:
            ident = case_identity(units, pages)
        except Exception as e:                       # names / identity not ready: say so, keep counting
            names_ok = False
            C[f"identity:ERROR {type(e).__name__}"] += 1
            ident = Identity(student=NameRef(), father=NameRef(), mother=NameRef())
        for person in ("father", "mother"):
            ref = getattr(ident, person)
            C[f"ref:{person}:" + (ref.source or "none")] += 1
            C[f"ref:{person}:confirmed"] += bool(ref.confirmed)
            C[f"ref:{person}_nid:" + ("read" if strict_read(getattr(ident, person + "_nid")) else "none")] += 1
        out = bank_case(c, ident)
        d = holder_decision(out)
        C[f"rule:{d}"] += 1
        C[f"rule:{d}|truth={truth}"] += 1
        if truth == "S" and d == PASS:
            C["rule:VIOLATION student-held PASS"] += 1
        if truth in ("F", "M") and d == FAIL:
            kinds = {f.evidence.get("kind") for f in out.findings if f.rule == "bank.holder_parent" and f.verdict == FAIL}
            if kinds <= {"SUBSET", "LETTERS", "OCR", "ORDER"}:
                # A1/A7 (revision 2.1): the parent's own account, but the holder is SPELLED differently from the
                # birth certificate and the NID (an extra or missing word, confirmed twice, no affidavit): the
                # owner's strict rule FAILs it. Counted, not a violation.
                C["rule:parent-held FAIL on a spelling (A1)"] += 1
            else:
                C["rule:VIOLATION parent-held FAIL"] += 1
        if truth in ("F", "M") and d == PASS and out.holder_role and out.holder_role[0].upper() != truth:
            C["rule:PASS on the other parent"] += 1
        for f in out.findings:
            if f.rule == "bank.holder_parent" and f.verdict in (FLAG, FAIL, NOTE):
                C[f"rule:{f.verdict}:{f.evidence.get('kind', '?')}"] += 1
        C["bank:solvency_missing"] += out.solvency_missing
        for f in out.findings:
            if f.rule in ("bank.usd", "bank.seal", "bank.dates", "bank.solvency_present", "bank.holder_read"):
                C[f"{f.rule}:{f.verdict}"] += 1
        # 3. the same rule against the portal's parent names (the shift only; the runner never does this)
        po = bank_case(c, portal_identity(p))
        dp = holder_decision(po)
        C[f"portal-ref:{dp}"] += 1
        if dp != d:
            C[f"shift:{dp}->{d}"] += 1
        # 4. the chain
        pl = payloads.get(c["passport"], [])
        chain = chain_case(c, pl, ident, out)
        seen = set()
        for uid, fs in chain.items():
            for f in fs:
                key = (f.rule, f.verdict, f.evidence.get("kind", ""), f.message)
                if key in seen:
                    continue
                seen.add(key)
                C[f"chain:{f.rule}:{f.verdict}" + (f":{f.evidence['kind']}" if f.evidence.get("kind") else "")] += 1
        detail.append({"i": i, "truth": truth, "holder_read": bool(h), "decision": d, "portal_decision": dp,
                       "role": out.holder_role,
                       "findings": [(f.rule, f.verdict, f.message) for f in out.findings],
                       "chain": {u: [(f.rule, f.verdict, f.message) for f in fs] for u, fs in chain.items()},
                       "refs": {k: (getattr(ident, k).written, getattr(ident, k).source, getattr(ident, k).confirmed)
                                for k in ("father", "mother", "father_nid", "mother_nid")}})
    with open(OUT / "cases.jsonl", "w", encoding="utf-8") as fo:
        for d_ in detail:
            fo.write(json.dumps(d_, ensure_ascii=False) + "\n")
    n = len(cases)
    print(f"sim_money: {n} bank cases, {time.perf_counter() - t0:.1f} s")
    for k in sorted(C):
        print(f"  {k:62s} {C[k]}")
    ok = True
    if not a.limit:
        ok &= _check("holder read", C["holder:read"], f">= {FLOOR_HOLDER_READ}/{n}", C["holder:read"] >= FLOOR_HOLDER_READ)
    ok &= _check("wrong persons", C["holder:WRONG-PERSON"], "== 0", C["holder:WRONG-PERSON"] == 0)
    ok &= _check("student-held account PASS", C["rule:VIOLATION student-held PASS"], "== 0",
                 C["rule:VIOLATION student-held PASS"] == 0)
    ok &= _check("parent-held account FAIL (not a spelling)", C["rule:VIOLATION parent-held FAIL"], "== 0",
                 C["rule:VIOLATION parent-held FAIL"] == 0)
    print(f"  info parent-held account FAIL on a spelling difference (A1, an affidavit would cover it): "
          f"{C['rule:parent-held FAIL on a spelling (A1)']}")
    if not names_ok:
        print("  NOTE: names.py / identity.py raised; the decision counts ran with empty references")
    print("  NOTE: bank_cases.json holds no passport text, so the 'holder is the student' rule cannot fire here "
          "(the student reference is empty); a student-held account is counted by its truth label only")
    if a.qr:
        ok &= qr_rerun(a.limit)
    print("sim_money:", "ok" if ok else "MISS")
    return 0 if ok else 1


def strict_read(ref: NameRef) -> bool:
    return bool(S.letters(ref.written or ref.parts)) if ref is not None else False


def _check(what: str, got: int, want: str, good: bool) -> bool:
    print(f"  {'ok  ' if good else 'MISS'} {what}: {got} (want {want})")
    return good


def _lab_payloads() -> Dict[str, List[str]]:
    """passport -> the QR payloads the lab's TIN QR run read on that student's financial files (private)."""
    if not (INVENTORY.exists() and LAB_QR.exists()):
        return {}
    inv = {it["path"]: it for it in json.loads(INVENTORY.read_text(encoding="utf-8"))["items"]}
    out: Dict[str, List[str]] = collections.defaultdict(list)
    with open(LAB_QR, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            it = inv.get(r["path"])
            if it is None or r.get("key") != "trade_license":
                continue
            out[it["pas"]] += [c for p in r["pages"] for c in p["codes"]]
    return out


def qr_rerun(limit: int = 0) -> bool:
    """The TIN QR ladder (tin.py) on the 228 real financial files, the lab's method: stage A on every page;
    when no TIN QR was read on a file that should hold one (TIN-certificate text, or an income-tax file),
    the deep ladder on the pages without a meaningful payload. Must read 201/201 TIN QRs."""
    items = json.loads(INVENTORY.read_text(encoding="utf-8"))["items"]
    if limit:
        items = items[:limit]
    done: Dict[str, Dict[str, Any]] = {}
    path = OUT / "qr_rerun.jsonl"
    if path.exists():
        for line in open(path, encoding="utf-8"):
            try:
                r = json.loads(line)
                done[r["path"]] = r
            except Exception:
                pass
    t0 = time.perf_counter()
    with open(path, "a", encoding="utf-8") as fo:
        for n, it in enumerate(items):
            if it["path"] in done:
                continue
            t1 = time.perf_counter()
            try:
                imgs = Q.render(it["path"], 250, 20)
            except Exception as e:
                rec = {"path": it["path"], "sid": it["sid"], "key": it["key"], "error": type(e).__name__, "pages": []}
                fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done[it["path"]] = rec
                continue
            pages = []
            for i, img in enumerate(imgs):
                codes, how = Q._stage_a(img)
                pages.append({"page": i, "codes": codes, "stage": how})
            if not any(Q.parse_tin_qr(c) for p in pages for c in p["codes"]) and \
                    (it.get("has_tin_cert_text") or it["key"] == "income_tax"):
                for p in pages:
                    if Q.meaningful(p["codes"]):
                        continue
                    r = Q.read_page(it["path"], p["page"], imgs[p["page"]], deep=True)
                    if r["codes"]:
                        p["codes"] = list(dict.fromkeys(p["codes"] + r["codes"]))
                        p["stage"] = (p["stage"] + ";" if p["stage"] else "") + r["stage"]
                    if any(Q.parse_tin_qr(c) for c in p["codes"]):
                        break
            rec = {"path": it["path"], "sid": it["sid"], "key": it["key"], "npages": len(imgs), "pages": pages,
                   "sec": round(time.perf_counter() - t1, 1)}
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fo.flush()
            done[it["path"]] = rec
            print(f"  qr {n + 1}/{len(items)} {it['sid']} {it['key']} {len(imgs)} pages {rec['sec']} s "
                  f"{'TIN_QR' if any(Q.parse_tin_qr(c) for p in pages for c in p['codes']) else '-'}", flush=True)
    lab_tin = set()
    if LAB_QR.exists():
        for line in open(LAB_QR, encoding="utf-8"):
            r = json.loads(line)
            if any(Q.parse_tin_qr(c) for p in r["pages"] for c in p["codes"]):
                lab_tin.add(r["path"])
    inv = {it["path"] for it in items}
    pop = {p for p in inv if p in lab_tin or next((it for it in items if it["path"] == p), {}).get("has_tin_cert_text")}
    got = {p for p, r in done.items() if p in inv and any(Q.parse_tin_qr(c) for pg in r["pages"] for c in pg["codes"])}
    stages = collections.Counter(pg["stage"].split("/")[0] for r in done.values() if r["path"] in inv
                                 for pg in r["pages"] if any(Q.parse_tin_qr(c) for c in pg["codes"]))
    print(f"  qr re-run: {len(inv)} files, {time.perf_counter() - t0:.0f} s this run; TIN QR read on {len(got)} files; "
          f"the lab's run read {len(lab_tin & inv)}; files that should hold one: {len(pop)}; stages {dict(stages)}")
    print(f"  lost against the lab: {len((lab_tin & inv) - got)}; new: {len(got - lab_tin)}")
    if limit:
        return _check("TIN QRs (partial run)", len(got), f"== the lab's {len(lab_tin & inv)}", got >= (lab_tin & inv))
    return _check("TIN QRs", len(got), f">= {FLOOR_TIN_QR}", len(got) >= FLOOR_TIN_QR)


if __name__ == "__main__":
    sys.exit(main())
