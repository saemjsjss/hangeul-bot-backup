"""Revision 2.3 tests (RUNNER_SPEC "Revision 2.3"): the four runner bugs the owner's first two real folders showed.

  1. folder mode cuts a file that holds several documents (a "FINANCIAL DOCUMENTS" or "ACADEMIC DOCUMENTS" file) by
     the bundle's own rules; no false MISSING; the report names the file and its pages;
  2. a reference spelling read off ONE document does not make the documents that agree with each other FAIL: one
     FLAG on the reference document instead; the birth-certificate QR's parents' names confirm the reference;
  3. the GPU pre-read survives a page that runs out of card memory (retried smaller, else left to the CPU);
  4. a folder file the runner cannot read is listed as not checked, never a document or a slot.

Made-up data only: cue texts from tests/test_pagetype.py, placeholder names ("SAMPLE", "DEMO", "TESTER"), synthetic
files under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp. No personal data, no card, no network.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_fixes23*.py"
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from src.verify.runner import birth as BI
from src.verify.runner import botimport as B
from src.verify.runner import doccheck as DC
from src.verify.runner import gpuread as G
from src.verify.runner import ingest
from src.verify.runner import names as N
from src.verify.runner import pagetype as PT
from src.verify.runner import pipeline as PL
from src.verify.runner import report as RP
from src.verify.runner import split as SP
from src.verify.runner import strict as S
from src.verify.runner.model import (FAIL, FLAG, NOTE, PASS, AffidavitInfo, BirthFacts, DocUnit, Identity,
                                     MachineRead, NameRef, PageRead, Source, SourceFile, set_page_numbering)
from src.verify.runner.tests.test_pagetype import FA, MO, TEXTS, nid_card, nid_tr, page

T = TEXTS
PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
SCRATCH = PRIVATE / "tmp"
_GB = 1024 ** 3


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def tearDownModule():
    set_page_numbering(None)


# ============================================================================================ 1. the folder cut
def folder(files, program="KLP", refs=None):
    """files: [(file name, key_by_name or None, [page texts])] in name order -> (SplitResult, Segmentation, pages)."""
    sfs, pages = [], []
    root = Path(r"C:\made-up-folder")                     # never opened: the split reads no file
    for name, key, texts in files:
        sfs.append(SourceFile(path=root / name, name=name, is_image=False, n_pages=len(texts), size=1, mtime=0.0,
                              key_by_name=key))
        for j, t in enumerate(texts):
            pages.append(page(len(pages), t, file=name, file_page=j))
    src = Source(mode="folder", root=root, files=sfs, case_id="F-0")
    types = PT.type_pages(pages)
    set_page_numbering(pages)
    seg = SP.segment_folder(src, pages, types)
    r = refs if refs is not None else SP.Refs(father=["ALPHA", "SAMPLE"], mother=["BETA", "SAMPLE"],
                                              student=["SAMPLE", "GAMMA"])
    return SP.finish(seg, types, pages, r, program), seg, pages


def layout(res):
    return [(u.key, u.fine, u.level, u.title, u.source_file, u.part) for u in res.units]


class TestFolderCut(unittest.TestCase):
    def test_financial_file_is_cut_into_its_documents(self):
        fin = "9. FINANCIAL DOCUMENTS.pdf"                   # the name gives no key
        res, seg, pages = folder([("1. PASSPORT.pdf", "passport", [T["passport"]]),
                                  (fin, None, [T["solvency"], T["statement"], T["statement"], T["trade"], T["trade"],
                                               T["tin"]])])
        self.assertEqual(layout(res)[1:], [
            ("bank", "bank", "", "BANK SOLVENCY / STATEMENT", fin, "p1-3"),
            ("trade_license", "trade_license", "", "TRADE LICENSE", fin, "p4-5"),
            ("trade_license", "tin_certificate", "", "TIN CERTIFICATE", fin, "p6")])
        self.assertNotIn("owner_doc", res.missing)            # no false MISSING: the documents are in the file
        self.assertNotIn("tin", res.missing)
        self.assertNotIn("bank", res.missing)
        self.assertEqual(res.notices, [])                       # folder mode: no ORDER / STOP
        self.assertTrue(all(not u.notes for u in res.units))
        prov = SP.provisional_units(seg, pages)
        self.assertEqual([u.pages for u in prov], [u.pages for u in res.units])
        self.assertEqual([u.part for u in prov], [u.part for u in res.units])
        # every part's cut file name gives its key back to the bot's classifier (the bot reads the cut)
        classify = B._loaded.dv.classify
        for u in res.units[1:]:
            self.assertEqual(classify(SP.unit_file_name(u)), u.key)

    def test_income_tax_and_an_affidavit_inside_the_file(self):
        fin = "FINANCIAL.pdf"
        res, *_ = folder([(fin, None, [T["solvency"], T["statement"], T["trade"], T["tin"], T["income_tax"],
                                       T["affidavit"]])], program="MASTER")
        self.assertEqual([(u.key, u.fine, u.part) for u in res.units],
                         [("bank", "bank", "p1-2"), ("trade_license", "trade_license", "p3"),
                          ("trade_license", "tin_certificate", "p4"), ("income_tax", "income_tax", "p5"),
                          ("affidavit", "affidavit", "p6")])
        self.assertNotIn("income_tax", res.missing)

    def test_academic_levels_are_cut_with_their_apostilles(self):
        acad = "8 ACADEMIC DOCUMENTS ATTESTED.pdf"
        res, *_ = folder([(acad, "academic", [T["apostille"], T["hsc_cert"], T["hsc_tr"],
                                              T["apostille"], T["ssc_cert"], T["ssc_tr"]])])
        self.assertEqual([(u.key, u.level, u.title, u.part, u.pages) for u in res.units],
                         [("academic", "HSC", "ACADEMIC HSC", "p1-3", [0, 1, 2]),
                          ("academic", "SSC", "ACADEMIC SSC", "p4-6", [3, 4, 5])])
        self.assertNotIn("academic", res.missing)

    def test_a_page_with_no_academic_sign_after_an_apostille_goes_with_it(self):
        blank = "zq xv"                                      # an upside-down certificate reads as noise
        res, *_ = folder([("ACADEMIC.pdf", "academic", [T["apostille"], blank, T["hsc_tr"],
                                                        T["apostille"], blank, T["ssc_tr"]])])
        self.assertEqual([(u.level, u.part) for u in res.units], [("HSC", "p1-3"), ("SSC", "p4-6")])

    def test_the_same_rule_in_a_bundle(self):
        blank = "zq xv"
        texts = [T["apostille"], blank, T["hsc_tr"], T["apostille"], blank, T["ssc_tr"]]
        feats = PT.type_pages([page(i, t) for i, t in enumerate(texts)])
        self.assertEqual(SP.split_academic(feats, 0, 5), [(0, 2, "HSC"), (3, 5, "SSC")])

    def test_one_document_files_are_not_cut(self):
        res, *_ = folder([("ACADEMIC CERTIFICATE.pdf", "academic", [T["hsc_cert"], T["hsc_tr"]]),
                          ("BANK STATEMENT.pdf", "bank", [T["solvency"], T["statement"], T["statement"]]),
                          ("FATHER NID.pdf", "father_nid", [nid_tr(FA, 1), nid_card(FA, 1)]),
                          ("MOTHER NID.pdf", "mother_nid", [nid_card(MO, 2), nid_tr(MO, 2)]),    # card first
                          ("PASSPORT.pdf", "passport", [T["passport"], ""])])                    # a blank back
        self.assertEqual(len(res.units), 5)
        self.assertTrue(all(not u.part for u in res.units))
        by = {u.source_file: u for u in res.units}
        self.assertEqual((by["MOTHER NID.pdf"].key, by["MOTHER NID.pdf"].pages), ("mother_nid", [7, 8]))
        self.assertEqual(by["PASSPORT.pdf"].key, "passport")

    def test_name_vs_content_is_kept_for_a_one_document_file(self):
        res, *_ = folder([("BANK STATEMENT.pdf", "bank", [T["family"]])])
        self.assertEqual([(f.verdict, f.rule) for f in res.units[0].notes], [(FLAG, "split.name_vs_content")])
        self.assertFalse(res.units[0].part)

    def test_a_cut_file_none_of_whose_parts_is_the_named_document(self):
        res, *_ = folder([("BANK STATEMENT.pdf", "bank", [T["trade"], T["tin"]])])
        self.assertEqual([(u.fine, u.part) for u in res.units], [("trade_license", "p1"), ("tin_certificate", "p2")])
        for u in res.units:
            self.assertEqual([(f.verdict, f.rule) for f in u.notes], [(FLAG, "split.name_vs_content")])
        self.assertIn("bank", res.missing)                     # the named document is not in it

    def test_the_named_part_keeps_the_file_name_role(self):
        res, *_ = folder([("FATHER NID.pdf", "father_nid", [nid_tr(FA, 1), nid_card(FA, 1), T["solvency"],
                                                             T["statement"]])])
        self.assertEqual([(u.key, u.role, u.role_how, u.part) for u in res.units],
                         [("father_nid", "father", "file-name", "p1-2"), ("bank", "", "", "p3-4")])

    def test_report_names_the_file_and_its_pages(self):
        u = DocUnit("D02", "trade_license", "tin_certificate", "TIN CERTIFICATE", [7], source_file="F.pdf",
                    part="p6")
        self.assertEqual(RP.where(u, "folder"), "F.pdf p6")
        whole = DocUnit("D03", "passport", "passport", "PASSPORT", [0], source_file="P.pdf")
        self.assertEqual(RP.where(whole, "folder"), "P.pdf")
        self.assertEqual(RP.where(u, "bundle"), "p8")


class TestPartPageNumbers(unittest.TestCase):
    def test_bot_page_numbers_of_a_part_become_the_file_pages(self):
        pages = [PageRead(i, "F.pdf", i, "x", "ocr", 1, False, 0.0, False, "cpu") for i in range(8)]
        u = DocUnit("D05", "academic", "academic", "ACADEMIC SSC", [3, 4, 5], level="SSC", source_file="F.pdf",
                    part="p4-6")
        m = "the pages after the e-Apostille on page 1 are not all for one qualification (page 2: SSC, page 3: HSC)"
        self.assertEqual(DC.part_page_refs(m, u, pages),
                         "the pages after the e-Apostille on page 4 are not all for one qualification "
                         "(page 5: SSC, page 6: HSC)")
        self.assertEqual(DC.part_page_refs("pages 1-2 unreadable", u, pages), "pages 4-5 unreadable")
        whole = DocUnit("D05", "academic", "academic", "ACADEMIC", [3, 4, 5], source_file="F.pdf")
        self.assertEqual(DC.part_page_refs(m, whole, pages), m)           # a whole file: unchanged
        f = DC.bot_findings([("FLAG", m)], "check_academic", u, pages)[0]
        self.assertIn("on page 4", f.message)
        self.assertEqual(f.pages, (4, 5, 6))


class TestCutParts(unittest.TestCase):
    def test_parts_are_cut_from_the_staged_file_not_primed(self):
        # revision 2.4: a folder part is read by the bot itself, as every folder file (no priming)
        calls = {"cut": [], "prime": []}

        class Eng:
            def cut(self, src, plan, out_dir):
                calls["cut"].append((Path(src).name, list(plan), Path(out_dir).name))
                return [Path(out_dir) / name for name, _, _ in plan]

            def prime(self, path, texts):
                calls["prime"].append((Path(path).name, list(texts)))

        pages = [PageRead(0, "P.pdf", 0, "p0", "ocr", 2, False, 0.0, False, "cpu")]
        pages += [PageRead(i + 1, "F.pdf", i, f"t{i}", "ocr", 2, False, 0.0, False, "cpu") for i in range(4)]
        units = [DocUnit("D01", "passport", "passport", "PASSPORT", [0], source_file="P.pdf"),
                 DocUnit("D02", "bank", "bank", "BANK", [1, 2], source_file="F.pdf", part="p1-2"),
                 DocUnit("D03", "trade_license", "tin_certificate", "TIN CERTIFICATE", [3, 4], source_file="F.pdf",
                         part="p3-4")]
        res = SimpleNamespace(units=units)
        staged = {"P.pdf": SCRATCH / "P.pdf", "F.pdf": SCRATCH / "F.pdf"}
        for u in units:
            u.path = staged[u.source_file]
        PL._cut_parts(SimpleNamespace(split=SP), Eng(), res, pages, staged, SCRATCH / "parts")
        self.assertEqual(calls["cut"], [("F.pdf", [("BANK STATEMENT p02-03.pdf", 0, 1),
                                                   ("TIN CERTIFICATE p04-05.pdf", 2, 3)], "parts")])
        self.assertEqual(calls["prime"], [])
        self.assertEqual(units[0].path, staged["P.pdf"])                  # a whole file keeps its staged copy
        self.assertEqual(units[2].path.name, "TIN CERTIFICATE p04-05.pdf")


class TestExamLevel(unittest.TestCase):
    def test_the_examination_names_the_level(self):
        self.assertEqual(PT.level_of("Madrasah Education Board Dakhil Examination 2020 Transcript Name of "
                                     "Institution Sample Alim Madrasah"), ("SSC", 1))
        self.assertEqual(PT.level_of("Higher Secondary Certificate Examinatien 2023 transcript"), ("HSC", 2))
        self.assertEqual(PT.level_of("Secondary School Certificate Examination 2021"), ("SSC", 1))
        self.assertEqual(PT.level_of("University of Sample Bachelor degree; Higher Secondary Certificate "
                                     "Examination passed"), ("degree", 4))
        self.assertEqual(PT.level_of("Sample Alim Madrasah"), ("HSC", 2))       # no examination named: as before


# ============================================================================================ 2. the reference
def reading(mother: str) -> str:
    return (f"BOARD OF EXAMINATIONS Transcript Name of Student MD. EXAMPLE SAMPLE Father's Name TESTER SAMPLE "
            f"Mother's Name {mother} Roll 123456 Result 5.00")


class FakeConfirm:
    def __init__(self, answer="confirmed"):
        self.answer, self.calls, self.log = answer, [], []

    def __call__(self, page_index, variant):
        self.calls.append((page_index, variant))
        return self.answer

    def printed(self, page_index, variant):
        return ""


class RefCase:
    """A birth certificate (the parents' reference, read once) and documents that print the mother's name."""

    def __init__(self, ref_mother="DEMO SAMPLX"):
        self.pages, self.units = [], []
        self.bc = self.add("birth_cert", [f"Birth Registration Certificate Mother's Name {ref_mother} Nationality"])
        self.ref_mother = ref_mother

    def add(self, key, texts, title=""):
        idx = []
        for t in texts:
            self.pages.append(PageRead(len(self.pages), "BUNDLE.pdf", len(self.pages), t, "ocr", len(t), False,
                                       0.0, False, "cpu"))
            idx.append(len(self.pages) - 1)
        u = DocUnit(uid=f"D{len(self.units) + 1:02d}", key=key, fine=key, title=title or key.upper(), pages=idx)
        self.units.append(u)
        return u

    def ident(self, confirmed=True) -> Identity:
        return Identity(
            student=NameRef(parts=("SAMPLE", "MD", "EXAMPLE"), written="MD. EXAMPLE SAMPLE", source="passport MRZ",
                            unit="DPP", pages=(), confirmed=True),
            father=NameRef(parts=S.words("TESTER SAMPLE"), written="TESTER SAMPLE", source="birth certificate",
                           unit=self.bc.uid, pages=tuple(self.bc.pages), confirmed=True),
            mother=NameRef(parts=S.words(self.ref_mother), written=self.ref_mother, source="birth certificate",
                           unit=self.bc.uid, pages=tuple(self.bc.pages), confirmed=confirmed))

    def run(self, confirm=None, machine=(), extra=(), confirmed=True, aff=None):
        facts = BirthFacts(pages=tuple(self.bc.pages), english_pages=list(self.bc.pages))
        return N.evaluate(self.ident(confirmed), self.units, self.pages, aff or AffidavitInfo(), list(machine),
                          {tuple(self.bc.pages): facts}, confirm or FakeConfirm(), list(extra))


def verdicts(res, uid, person="mother"):
    return sorted((f.rule, f.verdict) for f in res.per_unit.get(uid, []) if f.rule.startswith(f"names.{person}"))


class CloserConfirm:
    """A closer-mode confirmer (owner 28 Oct) for tests: pair(page, reference, variant) -> the decision `decide`
    gives for (page, reference, variant); look(page, candidates, kind) -> None (no dots look)."""
    closer = True

    def __init__(self, decide):
        self.decide, self.calls, self.log = decide, [], []

    def pair(self, page, reference, variant, kind="name"):
        self.calls.append((page, reference, variant))
        return self.decide(page, reference, variant)

    def look(self, page, candidates, kind="name"):
        return None

    def __call__(self, page_index, variant, reference=""):
        return "unclear"


class TestNoOutvote(unittest.TestCase):
    """Owner 28 Oct: a misread is never resolved by outvoting with other documents (the revision 2.3 outvote is
    gone); the closer look on the reference's own page decides whether the reference was misread."""

    def test_agreeing_documents_never_outvote_a_confirmed_reference(self):     # legacy re-read: no closer look
        c = RefCase()
        docs = [c.add("academic", [reading("DEMO SAMPLE")] * 2, "ACADEMIC HSC"),
                c.add("family_cert", [reading("DEMO SAMPLE")] * 2, "FAMILY CERTIFICATE"),
                c.add("academic", [reading("DEMO SAMPLE")] * 2, "ACADEMIC SSC")]
        res = c.run(FakeConfirm("confirmed"))
        for u in docs:
            self.assertEqual(verdicts(res, u.uid), [("names.mother", FAIL)], u.title)
        self.assertEqual(verdicts(res, c.bc.uid), [])
        self.assertFalse(hasattr(N, "_outvoted") or hasattr(N, "ref_independently_confirmed"))

    def test_an_added_honorific_confirmed_on_its_page_stays_fail(self):        # the outvote turned this into FLAG
        c = RefCase(ref_mother="DEMO SAMPLE")
        docs = [c.add("academic", [reading("MST DEMO SAMPLE")], "A"), c.add("family_cert", [reading("MST DEMO SAMPLE")], "B")]

        def decide(page, reference, variant):
            if page in c.bc.pages:
                return "same"                               # the birth certificate prints the reference
            return "differs"                                # each document prints the added MST
        res = c.run(CloserConfirm(decide))
        self.assertEqual([verdicts(res, u.uid) for u in docs], [[("names.mother", FAIL)]] * 2)

    def test_the_reference_page_look_corrects_nothing_by_itself_when_unclear(self):
        c = RefCase()
        docs = [c.add("academic", [reading("DEMO SAMPLE")], "A"), c.add("family_cert", [reading("DEMO SAMPLE")], "B")]

        def decide(page, reference, variant):
            return "unclear" if page in c.bc.pages else "differs"
        res = c.run(CloserConfirm(decide), confirmed=True)
        self.assertEqual([verdicts(res, u.uid) for u in docs], [[("names.mother", FLAG)]] * 2)
        f = res.per_unit[docs[0].uid][0]
        self.assertIn("could not confirm", f.message)
        self.assertEqual(f.evidence["closer_reference"], "unclear")

    def test_a_misread_on_the_variant_page_is_no_difference(self):
        c = RefCase(ref_mother="DEMO SAMPLE")
        u = c.add("academic", [reading("DEMO SAMPIE")], "A")
        res = c.run(CloserConfirm(lambda page, reference, variant: "same"))
        self.assertEqual(verdicts(res, u.uid), [])
        line = next(f for f in res.summary if f.rule == "names.mother")
        self.assertEqual(line.verdict, PASS)

    def test_one_other_document_still_fails_against_a_confirmed_reference(self):
        c = RefCase()
        u = c.add("academic", [reading("DEMO SAMPLE")] * 2, "ACADEMIC")
        res = c.run(FakeConfirm("confirmed"))
        self.assertEqual(verdicts(res, u.uid), [("names.mother", FAIL)])
        self.assertEqual(verdicts(res, c.bc.uid), [])

    def test_a_document_printing_the_reference_confirms_it(self):
        c = RefCase()
        a = c.add("academic", [reading("DEMO SAMPLE")], "A")
        c.add("academic", [reading("DEMO SAMPLX")], "C")                   # prints the reference

        def decide(page, reference, variant):
            return "unclear" if page in c.bc.pages else "differs"
        res = c.run(CloserConfirm(decide))
        self.assertEqual(verdicts(res, a.uid), [("names.mother", FAIL)])

    def test_a_qr_machine_read_confirms_the_reference(self):
        c = RefCase()
        a = c.add("academic", [reading("DEMO SAMPLE")], "A")
        mr = MachineRead(unit=c.bc.uid, person="mother", name="DEMO SAMPLX", source="birth QR", page=0)

        def decide(page, reference, variant):
            return "unclear" if page in c.bc.pages else "differs"
        res = c.run(CloserConfirm(decide), machine=[mr])
        self.assertEqual(verdicts(res, a.uid), [("names.mother", FAIL)])

    def test_documents_that_disagree_with_each_other_keep_the_old_rule(self):
        c = RefCase()
        a = c.add("academic", [reading("DEMO SAMPLE")] * 2, "A")
        b = c.add("family_cert", [reading("DEMO SAMPLE")] * 2, "B")
        x = c.add("academic", [reading("DEMO SAMPLZ")] * 2, "C")               # a third, confirmed spelling
        res = c.run(FakeConfirm("confirmed"))
        for u in (a, b, x):
            self.assertEqual(verdicts(res, u.uid), [("names.mother", FAIL)])

    def test_a_third_unconfirmed_spelling_is_at_most_flag(self):
        c = RefCase()
        a = c.add("academic", [reading("DEMO SAMPLE")] * 2, "A")
        x = c.add("academic", [reading("DEMO SAMPLZ")], "C")
        res = c.run(FakeConfirm("unclear"))
        self.assertEqual(verdicts(res, x.uid), [("names.mother", FLAG)])
        self.assertEqual(verdicts(res, a.uid), [("names.mother", FAIL)])       # two pages agree: confirmed
        self.assertNotIn(PASS, [v for _, v in verdicts(res, x.uid)])


class TestBirthQrParents(unittest.TestCase):
    def test_text_payload_fields(self):
        brn = "2000" + "0" * 13                              # a made-up 17-digit number, built at run time
        p = BI.parse_payload("Name: GAMMA SAMPLE; Father's Name: TESTER SAMPLE; Mother's Name: DEMO SAMPLE; "
                             f"BRN {brn}")
        tf = p["text_fields"]
        self.assertEqual((tf.get("name"), tf.get("father"), tf.get("mother")),
                         ("GAMMA SAMPLE", "TESTER SAMPLE", "DEMO SAMPLE"))
        self.assertEqual(tf.get("brn"), brn)
        q = BI.parse_payload("Father Name: TESTER SAMPLE Name: GAMMA SAMPLE")
        self.assertEqual((q["text_fields"].get("name"), q["text_fields"].get("father")),
                         ("GAMMA SAMPLE", "TESTER SAMPLE"))

    def test_url_payload_fields(self):
        p = BI.parse_payload("https://example.invalid/r?name=GAMMA%20SAMPLE&fatherName=TESTER%20SAMPLE"
                             "&mothers_name=DEMO%20SAMPLE")
        tf = p["text_fields"]
        self.assertEqual((tf.get("father"), tf.get("mother")), ("TESTER SAMPLE", "DEMO SAMPLE"))

    def facts(self, father="TESTER SAMPLE", mother="DEMO SAMPLE"):
        f = BirthFacts(pages=(4,), format="bilingual_qr", qr_status="decoded")
        f.qr = {"status": "decoded", "page": 4, "text_fields": {"father": father, "mother": mother}}
        return f

    def test_machine_reads_and_rows(self):
        unit = DocUnit("D05", "birth_cert", "birth_cert", "BIRTH CERTIFICATE", [4])
        reads = BI.qr_parent_reads(self.facts(), unit)
        self.assertEqual([(m.person, m.name, m.source, m.page) for m in reads],
                         [("father", "TESTER SAMPLE", "birth QR", 4), ("mother", "DEMO SAMPLE", "birth QR", 4)])
        ident = Identity(student=NameRef(), father=NameRef(written="TESTER SAMPLE", source="birth certificate",
                                                            unit="D05"),
                         mother=NameRef(written="DEMO SAMPLX", source="birth certificate", unit="D05"))
        out = []
        BI._qr_parent_rows(self.facts(), unit, ident, lambda v, r, m, pg=(), **ev: out.append((r, v)), lambda i: i + 1)
        self.assertEqual(out, [("birth.qr_father", PASS), ("birth.qr_mother", FLAG)])
        undecoded = self.facts()
        undecoded.qr_status = "undecoded"
        self.assertEqual(BI.qr_parent_reads(undecoded, unit), [])

    def test_identity_takes_the_qr_as_confirmation(self):
        from src.verify.runner import identity as ID
        births = {(4,): self.facts()}
        self.assertEqual(ID._qr_parent_names(births, "D05", (4,), "mother"), ["DEMO SAMPLE"])
        self.assertEqual(ID._qr_parent_names(births, "D05", (7,), "mother"), [])
        self.assertEqual(ID._qr_parent_names({(4,): BirthFacts(pages=(4,))}, "D05", (4,), "mother"), [])


# ============================================================================================ 3. card memory
class FakeOOM(RuntimeError):
    pass


FakeOOM.__name__ = "OutOfMemoryError"


class ArrayDV:
    """doc_verifier's reading surface with real arrays: read_pages renders each OCR page as an (h, w, 3) array of
    the given size and calls _ocr_image; the fake OCR runs out of card memory above `limit` pixels (long side)."""

    def __init__(self, sizes, limit, image_limit=None):
        self.sizes, self.limit, self.image_limit = sizes, limit, image_limit
        self.seen = []
        self._ocr_image = self._ocr

    def _ocr(self, arr, turned=None):
        self.seen.append(arr.shape[:2])
        if max(arr.shape[:2]) > self.limit:
            raise FakeOOM("CUDA out of memory. Tried to allocate 870.00 MiB (made up)")
        return f"TEXT {max(arr.shape[:2])}"

    def read_pages(self, path, max_pages=20, sideways=None):
        return [self._ocr_image(np.zeros((h, w, 3), dtype=np.uint8), []) for h, w in self.sizes]

    def read_document(self, path, max_pages=20):
        raise FakeOOM("CUDA out of memory (made up)")

    def _ocr_reader(self):
        dv = self

        class R:
            def readtext(self, arr, detail=0):
                dv.seen.append(arr.shape[:2])
                if max(arr.shape[:2]) > (dv.image_limit or 0):
                    raise FakeOOM("CUDA out of memory (made up)")
                return ["IMAGE", "TEXT"]

        return R()


class TestCardMemory(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="test_fixes23_", dir=str(SCRATCH)))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def src_of(self, n_pages, image=False):
        import pymupdf
        if image:
            from PIL import Image
            p = self.dir / "PHOTO.png"
            Image.new("RGB", (3000, 2400), (255, 255, 255)).save(p)
            sf = SourceFile(path=p, name=p.name, is_image=True, n_pages=1, size=p.stat().st_size, mtime=0.0)
        else:
            p = self.dir / "BUNDLE.pdf"
            d = pymupdf.open()
            for _ in range(n_pages):
                d.new_page()                                   # no text layer: every page is an OCR page
            d.save(str(p))
            d.close()
            sf = SourceFile(path=p, name=p.name, is_image=False, n_pages=n_pages, size=p.stat().st_size, mtime=0.0)
        return Source(mode="bundle" if not image else "folder", root=p, files=[sf], case_id="B-0")

    def test_is_oom(self):
        self.assertTrue(G.is_oom(FakeOOM("x")))
        self.assertTrue(G.is_oom(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")))
        self.assertFalse(G.is_oom(RuntimeError("made-up OCR failure")))
        self.assertFalse(G.is_oom(ValueError("out of memory")))

    def test_downscale(self):
        a = np.zeros((3300, 2550, 3), dtype=np.uint8)
        self.assertEqual(G.downscale(a, 2000).shape[:2], (2000, 1545))
        self.assertIsNone(G.downscale(np.zeros((1200, 900, 3), dtype=np.uint8), 2000))
        self.assertIsNone(G.downscale(7, 2000))

    def test_a_large_page_is_read_smaller(self):
        src = self.src_of(3)
        dv = ArrayDV([(1650, 1275), (3300, 2550), (1650, 1275)], limit=2000)
        frees = []
        with mock.patch.object(G, "_ingest", lambda: ingest):
            d = G.read_all(src, dv, lambda: None, {}, lambda x: None, lambda: frees.append(1))
        pages = d["files"][0]["pages"]
        self.assertTrue(d["complete"])
        self.assertEqual([p["page"] for p in pages], [1, 2, 3])
        self.assertEqual(pages[1]["downscaled"], 2000)
        self.assertEqual(pages[1]["text"], "TEXT 2000")
        self.assertNotIn("downscaled", pages[0])
        self.assertEqual(frees, [1])                           # the cache freed once, after the failed try
        self.assertEqual(dv.seen[1:3], [(3300, 2550), (2000, 1545)])

    def test_a_page_too_large_even_when_smaller_is_left_to_the_cpu(self):
        src = self.src_of(3)
        dv = ArrayDV([(1650, 1275), (3300, 2550), (1500, 1100)], limit=1000)
        saves = []
        with mock.patch.object(G, "_ingest", lambda: ingest):
            d = G.read_all(src, dv, lambda: None, {}, lambda x: saves.append(json.loads(json.dumps(x))), lambda: None)
        e = d["files"][0]
        self.assertFalse(d["complete"])
        self.assertEqual([p["page"] for p in e["pages"]], [])  # pages 1 and 3 do not fit at 1600 px either
        self.assertEqual([s["page"] for s in e["skipped"]], [1, 2, 3])
        self.assertIn("left to the CPU engine", e["skipped"][0]["reason"])
        self.assertEqual(G.counts(d)["skipped"], 3)

    def test_the_other_pages_go_on(self):
        src = self.src_of(3)
        dv = ArrayDV([(1650, 1275), (5000, 3000), (1650, 1275)], limit=1700)
        with mock.patch.object(G, "_ingest", lambda: ingest):
            d = G.read_all(src, dv, lambda: None, {}, lambda x: None)
        e = d["files"][0]
        self.assertEqual([p["page"] for p in e["pages"]], [1, 2, 3])
        self.assertEqual(e["pages"][1]["downscaled"], 1600)
        dv2 = ArrayDV([(1650, 1275), (5000, 3000), (1650, 1275)], limit=1500)
        with mock.patch.object(G, "_ingest", lambda: ingest):
            d2 = G.read_all(src, dv2, lambda: None, {}, lambda x: None)
        e2 = d2["files"][0]
        self.assertEqual([p["page"] for p in e2["pages"]], [])
        dv3 = ArrayDV([(1400, 1000), (5000, 3000), (1400, 1000)], limit=1500)
        with mock.patch.object(G, "_ingest", lambda: ingest):
            d3 = G.read_all(src, dv3, lambda: None, {}, lambda x: None)
        self.assertEqual([p["page"] for p in d3["files"][0]["pages"]], [1, 3])
        self.assertEqual([s["page"] for s in d3["files"][0]["skipped"]], [2])
        # the runner serves what the file holds and reads page 2 on the CPU (a partial file loads)
        out = self.dir / "page_texts.json"
        G.write_json(out, d3)
        with mock.patch.object(ingest, "file_sha256", lambda p: d3["files"][0]["sha256"]):
            from src.verify.runner import engine as E
            given = E.load_page_texts(out, src)
        self.assertEqual(sorted(given.pages), [("BUNDLE.pdf", 0), ("BUNDLE.pdf", 2)])
        self.assertFalse(given.complete)

    def test_another_error_still_stops(self):
        src = self.src_of(2)
        dv = ArrayDV([(100, 100), (100, 100)], limit=2000)

        def broken(arr, turned=None):
            raise RuntimeError("made-up OCR failure")

        dv._ocr_image = broken
        with mock.patch.object(G, "_ingest", lambda: ingest), self.assertRaises(RuntimeError):
            G.read_all(src, dv, lambda: None, {}, lambda x: None)

    def test_an_image_file_is_read_smaller(self):
        src = self.src_of(1, image=True)
        dv = ArrayDV([], limit=0, image_limit=2000)
        with mock.patch.object(G, "_ingest", lambda: ingest):
            d = G.read_all(src, dv, lambda: None, {}, lambda x: None, lambda: None)
        p = d["files"][0]["pages"]
        self.assertEqual((p[0]["text"], p[0]["kind"], p[0]["downscaled"]), ("IMAGE TEXT", "image", 2000))
        dv2 = ArrayDV([], limit=0, image_limit=10)
        with mock.patch.object(G, "_ingest", lambda: ingest):
            d2 = G.read_all(src, dv2, lambda: None, {}, lambda x: None, lambda: None)
        self.assertEqual(d2["files"][0]["pages"], [])
        self.assertEqual(d2["files"][0]["skipped"][0]["page"], 1)

    def test_run_session_frees_the_cache(self):
        src = self.src_of(2)
        dv = ArrayDV([(3300, 2550), (1000, 800)], limit=2000)
        dv.device = "cuda:0"
        dv._ocr_reader = lambda: SimpleNamespace(device="cuda:0")
        empties = []

        class Cuda:
            def is_available(self):
                return True

            def get_device_properties(self, i):
                return SimpleNamespace(total_memory=8 * _GB)

            def set_per_process_memory_fraction(self, f, d=None):
                self.fraction = f

            def max_memory_allocated(self):
                return 2 * _GB

            def max_memory_reserved(self):
                return 2 * _GB

            def empty_cache(self):
                empties.append(1)

        from src.verify.runner.tests.test_gpuread import FakeGuard, captured
        out = self.dir / "runs" / "page_texts.json"
        with mock.patch.object(G, "_ingest", lambda: ingest), captured():
            code = G.run_session(src, out, FakeGuard(), dv, wait_s=1, slot_s=90, done={},
                                 torch_loader=lambda: SimpleNamespace(cuda=Cuda()))
        self.assertEqual(code, G.EXIT_OK)
        self.assertEqual(empties, [1])
        d = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(d["complete"])
        self.assertEqual(d["files"][0]["pages"][0]["downscaled"], 2000)


class TestRunCaseNote(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("run_case_fixes23", str(PRIVATE / "run_case.py"))
        cls.rc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rc)

    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="test_fixes23_rc_", dir=str(SCRATCH)))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.args = SimpleNamespace(out=str(self.dir), input=str(self.dir / "in.pdf"), cpu=False, wait_s=120,
                                    program="KLP", today=None, show=False)

    def write(self, pages, skipped=()):
        d = {"schema": "runner-page-texts/1", "files": [{"name": "in.pdf", "pages": pages,
                                                           "skipped": [{"page": p, "reason": "x"} for p in skipped]}]}
        (self.dir / "page_texts.json").write_text(json.dumps(d), encoding="utf-8")

    def plan(self, code, inspect):
        with mock.patch.object(self.rc, "_inspect", lambda pt, inp: inspect):
            return self.rc.plan(self.args, lambda cmd: code)

    def test_a_crash_after_some_pages_uses_them_and_counts_each_engine(self):
        self.write([{"page": 1, "kind": "ocr"}, {"page": 2, "kind": "layer"}, {"page": 3, "kind": "ocr"}])
        pt, note = self.plan(1, (True, 34, 3))
        self.assertEqual(pt, self.dir / "page_texts.json")
        self.assertEqual(note, "GPU pre-read failed (exit 1) — 2 page(s) read on the card (+1 text layer), "
                               "31 page(s) read on the CPU light engine")

    def test_pages_left_out_for_card_memory(self):
        self.write([{"page": 1, "kind": "ocr"}], skipped=[2])
        pt, note = self.plan(0, (True, 2, 1))
        self.assertIsNotNone(pt)
        self.assertTrue(note.startswith("GPU pre-read left 1 page(s) out (too large for the card's memory cap)"),
                        note)
        self.assertIn("1 page(s) read on the CPU light engine", note)

    def test_nothing_read_on_the_card(self):
        pt, note = self.plan(1, (False, 34, 0))
        self.assertIsNone(pt)
        self.assertEqual(note, "GPU pre-read failed (exit 1) — 0 page(s) read on the card, 34 page(s) read on the "
                               "CPU light engine")


# ============================================================================================ 4. unreadable files
class TestNotChecked(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.dir = SCRATCH / f"test_fixes23-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def pdf(self, name, text="SAMPLE PAGE"):
        import pymupdf
        d = pymupdf.open()
        d.new_page().insert_text((72, 72), text)
        d.save(str(self.dir / name))
        d.close()

    def test_folder_lists_what_it_cannot_read(self):
        self.pdf("9. FINANCIAL DOCUMENTS.pdf")
        self.pdf("1. PASSPORT.pdf")
        (self.dir / "9. FINANCIAL DOCUMENTS.odg").write_bytes(b"PK made-up drawing")
        (self.dir / "BROKEN.pdf").write_bytes(b"this is not a PDF at all")
        src = ingest.open_input(self.dir)
        self.assertEqual([f.name for f in src.files], ["1. PASSPORT.pdf", "9. FINANCIAL DOCUMENTS.pdf"])
        self.assertEqual(src.not_checked, [("9. FINANCIAL DOCUMENTS.odg", ingest.NOT_INPUT),
                                           ("BROKEN.pdf", ingest.NOT_OPENED)])
        joined = "\n".join(f"{f.name}|{f.size}|{int(f.mtime)}" for f in src.files)
        self.assertEqual(src.case_id, ingest.folder_case_id(src.files))
        self.assertIn("|", joined)

    def test_a_folder_whose_only_pdf_will_not_open(self):
        (self.dir / "BROKEN.pdf").write_bytes(b"this is not a PDF at all")
        (self.dir / "notes.odg").write_bytes(b"PK")
        with self.assertRaises(ingest.InputError):
            ingest.open_input(self.dir)

    def test_report_lines(self):
        r = SimpleNamespace(not_checked=[("A.odg", "not a PDF or image"), ("B.docx", "not a PDF or image"),
                                         ("C.pdf", "could not be opened")])
        self.assertEqual(RP.not_checked_lines(r), [("", "Not checked (not a PDF or image): A.odg; B.docx"),
                                                   ("", "Not checked (could not be opened): C.pdf")])
        self.assertEqual(RP.not_checked_lines(SimpleNamespace()), [])


if __name__ == "__main__":
    unittest.main()
