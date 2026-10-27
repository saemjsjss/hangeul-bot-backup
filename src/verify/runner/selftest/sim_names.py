"""T2 sim_names (RUNNER_SPEC section 10): the A1/A2 names rule on every corpus student, text level.

Read-only: the corpus (selftest/corpus.py: the student folders' file names and the bot's cached page
texts) and the private audit labels C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\names_scratch\\
audit_labels_private.json. The re-read is OFF: a spelling is confirmed only by two pages of a document
(there are no page images and no board-QR reads at text level). Every file whose name gives a bot key is
one unit (folder mode; NID roles from the file name, role_how "file-name"); birth certificates are read on
the English pages birth.read_birth_certificate() finds without an engine (all pages when Task 7's module
is not importable, which is said).

Prints counts and rates only: students FAIL / FLAG-only / NOTE-only / clean, findings per rule and level,
the dot rule's outcomes, the canonical parent sources (floors: father from the birth certificate >= 184,
mother >= 183), and the FAIL precision against the audit labels (floor 0.75 real). The per-finding list
(it holds names) goes to C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\sim_names\\findings_private.json.

    <runner venv python> -I -B -m src.verify.runner.selftest.sim_names
"""
from __future__ import annotations

import collections
import json
import sys
import time
from pathlib import Path

from src.verify.runner import botimport

OUT = botimport.PRIVATE / "selftest" / "sim_names"
LABELS = botimport.PRIVATE / "names_scratch" / "audit_labels_private.json"
FATHER_FLOOR, MOTHER_FLOOR, PRECISION_FLOOR = 184, 183, 0.75
ROLE = {"father_nid": "father", "mother_nid": "mother", "student_nid": "student", "father_death_cert": "father",
        "mother_death_cert": "mother"}


def _unclear(_p, _v):
    return "unclear"


def student_case(st):
    """(pages, units) of one corpus student, one unit per typed file (folder mode)."""
    from src.verify.runner.model import DocUnit, PageRead
    pages, units = [], []
    for f in st.files:
        texts = list(f.pages or [""])
        idx = []
        for k, t in enumerate(texts):
            pages.append(PageRead(len(pages), f.name, k, t or "", "ocr", len(t or ""), False, 0.0, False, "cache"))
            idx.append(len(pages) - 1)
        role = ROLE.get(f.key, "")
        units.append(DocUnit(uid=f"D{len(units) + 1:02d}", key=f.key, fine=f.key, title=f.key.upper(), pages=idx,
                             role=role, role_how="file-name" if role else "", source_file=f.name))
    return pages, units


def main(argv=None) -> int:
    ns = botimport.load(botimport.DEFAULT_CACHE_ROOT)
    from src.verify.runner import identity as I
    from src.verify.runner import names as N
    from src.verify.runner.selftest import corpus
    try:
        from src.verify.runner import birth as BIRTH
    except Exception:                       # Task 7 not importable: every birth page is read
        BIRTH = None
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / "work"
    labels = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.exists() else {}
    studs = corpus.load_corpus()
    per_student = collections.Counter()
    verd = collections.Counter()
    by_rule = collections.Counter()
    dots = collections.Counter()
    src = collections.Counter()
    audit = collections.Counter()
    kinds = collections.Counter()
    priv = []
    birth_mode = "english pages (birth.py)" if BIRTH is not None else "all pages (birth.py not importable)"
    errors = 0
    for st in studs:
        pages, units = student_case(st)
        births = {}
        for u in units:
            if u.key != "birth_cert":
                continue
            if BIRTH is not None:
                try:
                    births[tuple(u.pages)] = BIRTH.read_birth_certificate(u, pages, None, work, ns.dv.notarised)
                    continue
                except Exception:
                    errors += 1
            from src.verify.runner.model import BirthFacts
            births[tuple(u.pages)] = BirthFacts(pages=tuple(u.pages), english_pages=list(u.pages))
        try:
            ident = I.build_identity(units, pages, births, None, work, _unclear)
            ident = I.finish_identity(ident, units, pages, births, _unclear)
            aff = N.affidavit_info(units, pages)
            res = N.evaluate(ident, units, pages, aff, [], births, _unclear)
        except Exception as exc:
            errors += 1
            print(f"{st.sid} error {type(exc).__name__}", file=sys.stderr)
            continue
        for p in ("father", "mother"):
            src[(p, getattr(ident, p).source or "unknown")] += 1
        src[("student", ident.student.source or "unknown")] += 1
        key_of = {u.uid: u.key for u in units}
        nf = collections.Counter()
        for f in res.findings:
            if f.kind == "DOTS":
                dots[f.verdict] += 1
                by_rule[(f"names.{f.person}_dots", f.verdict)] += 1
            else:
                by_rule[(f"names.{f.person}", f.verdict)] += 1
                kinds[(f.kind, f.verdict)] += 1
            verd[f.verdict] += 1
            nf[f.verdict] += 1
            doc = key_of.get(f.unit, "")
            variant = " ".join(N.nt(t) for t in f.variant.split() if N.nt(t))
            lab = labels.get("|".join([st.cache_json.stem, f.person, doc, variant]))
            if f.verdict == "FAIL" and f.kind != "DOTS":
                audit[lab or "unlabelled"] += 1
            priv.append({"sid": st.sid, "person": f.person, "doc": doc, "variant": f.variant, "kind": f.kind,
                         "verdict": f.verdict, "reason": f.reason, "confirmed": f.confirmed,
                         "contradicted": f.contradicted, "affidavit": f.affidavit, "label": lab or ""})
        for f in res.summary:
            if f.verdict == "PASS":
                dots["person clean"] += 1
        per_student["students"] += 1
        per_student["FAIL"] += nf["FAIL"] > 0
        per_student["FLAG, no FAIL"] += nf["FAIL"] == 0 and nf["FLAG"] > 0
        per_student["NOTE only"] += nf["FAIL"] == 0 and nf["FLAG"] == 0 and nf["NOTE"] > 0
        per_student["clean"] += sum(nf.values()) == 0
    (OUT / "findings_private.json").write_text(json.dumps(priv, indent=1), encoding="utf-8")
    labelled = sum(v for k, v in audit.items() if k != "unlabelled")
    real = audit.get("real", 0)
    prec_all = real / labelled if labelled else None
    sure = labelled - audit.get("unclear", 0)
    prec_sure = real / sure if sure else None
    fb, mb = src[("father", "birth certificate")], src[("mother", "birth certificate")]
    print(f"sim_names: {per_student['students']} students, birth certificates read on {birth_mode}, "
          f"re-read off, {errors} errors, {time.time() - t0:.0f} s")
    print("per student:", dict(per_student))
    print("findings:", dict(verd))
    for k in sorted(by_rule):
        print(f"  {k[0]:22s} {k[1]:5s} {by_rule[k]}")
    print("letters kinds (kind, verdict):", dict(sorted(kinds.items())))
    print("dot rule:", dict(dots))
    print("canonical sources:", {f"{p}:{s}": n for (p, s), n in sorted(src.items())})
    print("audit of letter FAILs (label: count):", dict(audit))
    ok_f, ok_m = fb >= FATHER_FLOOR, mb >= MOTHER_FLOOR
    print(f"{'ok' if ok_f else 'MISS'} father from the birth certificate {fb} (floor {FATHER_FLOOR})")
    print(f"{'ok' if ok_m else 'MISS'} mother from the birth certificate {mb} (floor {MOTHER_FLOOR})")
    if prec_all is None:
        print("n/a FAIL precision: no labelled letter FAIL (labels exist for the revision-1 findings only)")
        ok_p = True
    else:
        ok_p = prec_all >= PRECISION_FLOOR
        print(f"{'ok' if ok_p else 'MISS'} FAIL precision {prec_all:.2f} real of {labelled} labelled "
              f"({prec_sure:.2f} without 'unclear'; floor {PRECISION_FLOOR})")
    return 0 if (ok_f and ok_m and ok_p and not errors) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
