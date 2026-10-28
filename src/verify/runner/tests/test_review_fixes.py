"""The fixes of the three 27 Oct reviews (owner rules, correctness, safety), one test per reported defect.
Made-up names, numbers and dates only."""
from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.verify.runner import botimport as B
from src.verify.runner import model as M
from src.verify.runner import strict as S
from src.verify.runner.model import (FAIL, FLAG, MISSING, NOT_JUDGED, NOTE, PASS, AffidavitInfo, DocResult, DocUnit,
                                     Finding, NameRef, PageRead, SplitResult)
from src.verify.runner.tests.test_names import (Case, FakeConfirm, academic, base_case, box, ident_of, page,
                                                 rules, run)

WORK = B.PRIVATE / "tmp" / "test_review_fixes"


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def N():
    from src.verify.runner import names
    return names


# ------------------------------------------------------------------------------------------------ names (review 1)
class TestStrictNames(unittest.TestCase):
    def test_1_a_wholly_different_name_on_two_pages_fails(self):
        c = base_case()
        u = c.add("academic", [academic(mother="OTHERWORD ANOTHERWORD")] * 2, "ACADEMIC")
        res = run(c, ident_of(), FakeConfirm("confirmed"))
        self.assertEqual(rules(res, u.uid), [("names.mother", FAIL)])
        self.assertEqual(res.per_person["mother"], FAIL)

    def test_1_a_wholly_different_name_read_once_is_check_by_eye(self):
        c = base_case()
        u = c.add("academic", [academic(mother="OTHERWORD ANOTHERWORD")], "ACADEMIC")
        res = run(c, ident_of(), FakeConfirm("confirmed"))
        self.assertEqual(rules(res, u.uid), [("names.mother_unread", FLAG)])       # never PASS (A1)
        self.assertEqual(res.per_person["mother"], FLAG)
        line = next(f for f in res.summary if f.rule == "names.mother")
        self.assertEqual(line.verdict, FLAG)
        self.assertIn("not compared", line.message)

    def test_2_an_extra_word_is_a_difference(self):
        for mother in ("DEMO SAMPLE BEGUMX", "OTHERWORD DEMO SAMPLE", "MST DEMO SAMPLE"):
            with self.subTest(mother=mother):
                c = base_case()
                u = c.add("academic", [academic(mother=mother)] * 2, "ACADEMIC")
                res = run(c, ident_of(), FakeConfirm("confirmed"))
                self.assertEqual(rules(res, u.uid), [("names.mother", FAIL)])

    def test_2_identity_is_strict_too(self):
        """_confirmed_elsewhere and _nid_name: a value that adds a word is not the reference (no token subset)."""
        from src.verify.runner import identity as I
        from src.verify.runner.tests import test_identity as TI
        c = Case()
        bc = c.add("birth_cert", ["Name EXAMPLE SAMPLE Father's Name TESTER SAMPLE Mother's Name DEMO SAMPLE"])
        c.add("academic", [academic(mother="DEMO SAMPLE BEGUMX")], "ACADEMIC")
        reads, _ = N().collect_reads(c.units, c.pages, {}, money=True)
        self.assertFalse(I._confirmed_elsewhere("DEMO SAMPLE", bc.uid, bc.pages[0], reads, FakeConfirm("unclear")))
        c2 = Case()
        bc2 = c2.add("birth_cert", ["Name EXAMPLE SAMPLE Father's Name TESTER SAMPLE Mother's Name DEMO SAMPLE"])
        c2.add("academic", [academic(mother="DEMO SAMPLE")], "ACADEMIC")
        reads2, _ = N().collect_reads(c2.units, c2.pages, {}, money=True)
        self.assertTrue(I._confirmed_elsewhere("DEMO SAMPLE", bc2.uid, bc2.pages[0], reads2, FakeConfirm("unclear")))
        t = TI.Case()
        t.add("passport", [TI.passport_text(TI.L1 + "\n" + TI.line2())])
        t.add("mother_nid", ["NATIONAL ID CARD Name DEMO SAMPLE BEGUMX Date of Birth 01 Jan 1975",
                             "Translation Name DEMO SAMPLE BEGUMX Husband TESTER SAMPLE"], "mother", "name")
        ident = I.finish_identity(TI.build(t), t.units, t.pages, {})
        self.assertEqual(ident.mother_nid.written, "DEMO SAMPLE BEGUMX")         # never cut to the reference

    def test_2_an_added_word_read_once_needs_the_reread(self):
        c = base_case()
        u = c.add("academic", [academic(mother="DEMO SAMPLE BEGUMX")], "ACADEMIC")
        self.assertEqual(rules(run(c, ident_of(), FakeConfirm("confirmed")), u.uid), [("names.mother", FAIL)])
        # rev 2.6 (checker): an unclear look never makes an added word the same name (FLAG, check by eye); only a
        # look that shows the reference does ("refuted": page noise, not a name)
        self.assertEqual(rules(run(c, ident_of(), FakeConfirm("unclear")), u.uid), [("names.mother", FLAG)])
        self.assertEqual(rules(run(c, ident_of(), FakeConfirm("refuted")), u.uid), [])
        c = base_case()                                  # an added honorific is always a difference (A1)
        u = c.add("academic", [academic(mother="MST DEMO SAMPLE")], "ACADEMIC")
        self.assertEqual(rules(run(c, ident_of(), FakeConfirm("unclear")), u.uid), [("names.mother", FLAG)])

    def test_3_birth_certificate_tail_confirmed_by_the_reread_joins_the_name(self):
        from src.verify.runner import identity as I
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE Given Names EXAMPLE"])
        bc = c.add("birth_cert", ["Name EXAMPLE SAMPLE Father's Name TESTER SAMPLE KHANO Mother's Name DEMO SAMPLE"])
        c.add("academic", [academic(student="EXAMPLE SAMPLE")] * 2)
        births = {tuple(bc.pages): M.BirthFacts(pages=tuple(bc.pages), english_pages=list(bc.pages))}
        ident = I.build_identity(c.units, c.pages, births, None, WORK, FakeConfirm("confirmed"))
        self.assertEqual((ident.father.written, ident.father.tail), ("TESTER SAMPLE KHANO", ()))
        ident = I.build_identity(c.units, c.pages, births, None, WORK, FakeConfirm("unclear"))
        self.assertEqual((ident.father.written, ident.father.tail), ("TESTER SAMPLE", ("KHANO",)))

    def test_3_a_document_adding_the_unconfirmed_tail_is_flag(self):
        idn = ident_of()
        idn.father.tail = ("KHANO",)
        c = base_case()
        u = c.add("academic", [academic(father="TESTER SAMPLE KHANO")] * 2, "ACADEMIC")
        res = run(c, idn, FakeConfirm("confirmed"))
        self.assertEqual(rules(res, u.uid), [("names.father", FLAG)])
        self.assertTrue(N().adds_tail("MD ALPHAX", "MDALPHAX BRAVOX", ("BRAOVX",)))   # glued and an OCR typo
        self.assertFalse(N().adds_tail("MD ALPHAX", "MD ALPHAX KILOX", ("BRAVOX",)))  # another word

    def test_4_the_passports_own_glued_read_is_no_dot_reference(self):
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE"], "PASSPORT")
        d1 = c.add("academic", [academic(student="M.EXAMPLE SAMPLE")] * 2, "ACADEMIC DEGREE")    # owner 28 Oct: not
        d2 = c.add("academic", [academic(student="MEXAMPLE SAMPLE")] * 2, "ACADEMIC DIPLOMA")    # board papers
        res = run(c, ident_of(student_written="MEXAMPLE SAMPLE", student_parts=("SAMPLE", "MEXAMPLE")),
                  FakeConfirm("unclear"))
        self.assertEqual(rules(res, d1.uid), [])
        self.assertEqual(rules(res, d2.uid), [("names.student_dots", FLAG)])

    def test_5_the_reread_confirms_an_added_honorific(self):
        from src.verify.runner.tests.test_names import FakeEngine
        text = ("BOARD Transcript Name of Student EXAMPLE SAMPLE Father's Name TESTER SAMPLE Mother's Name MST "
                "DEMO SAMPLE Roll 1")
        page_boxes = [(box(10, 100, 120, 130), "Mother's Name", 0.99), (box(130, 100, 180, 130), "MST", 0.97),
                      (box(190, 100, 330, 130), "DEMO SAMPLE", 0.97)]
        crop_boxes = [(box(10, 10, 60, 40), "MST", 0.97), (box(70, 10, 250, 40), "DEMO SAMPLE", 0.97)]
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE Given Names EXAMPLE"], "PASSPORT")
        u = c.add("academic", [text], "ACADEMIC SSC")
        conf = N().make_confirmer(FakeEngine(page_boxes, crop_boxes), c.pages, WORK)
        res = N().evaluate(ident_of(student_written="EXAMPLE SAMPLE", student_parts=("SAMPLE", "EXAMPLE")),
                           c.units, c.pages, AffidavitInfo(), [], {}, conf)
        self.assertEqual([(f.verdict, f.evidence.get("reread")) for f in res.per_unit[u.uid]], [(FAIL, "confirmed")])
        # owner 28 Oct: the closer look's own words in the evidence (the legacy re-read here: "differs")
        self.assertEqual(res.per_unit[u.uid][0].evidence.get("closer_variant"), "differs")

    def test_6_name_of_the_deceased_is_read(self):
        for label in ("Name of the Deceased:", "Name of Deceased:", "Name of the Deceased Person:"):
            with self.subTest(label=label):
                t = f"DEATH REGISTRATION CERTIFICATE {label} OTHERX SAMPLE Father's Name: OLD SAMPLE Date of Death"
                reads = N().extract_nid({"text": t, "pages": [t]}, "father_death_cert")
                self.assertIn("OTHERX SAMPLE", [r.written for r in reads if r.person == "father"])
        t = "FAMILY Name of the Deceased: OTHERX SAMPLE"          # only a death certificate reads it
        self.assertEqual([r.written for r in N().extract_nid({"text": t, "pages": [t]}, "father_nid")
                          if r.src == "Name"], [])

    def test_8_uniformly_glued_reads_are_flag(self):
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE"], "PASSPORT")
        us = [c.add("academic", [academic(student="MEXAMPLE SAMPLE")] * 2, f"AC{k}") for k in range(2)]
        res = run(c, ident_of(student_written="M.EXAMPLE SAMPLE", student_parts=("SAMPLE", "MEXAMPLE")),
                  FakeConfirm("confirmed"))
        self.assertEqual([rules(res, u.uid) for u in us], [[("names.student_dots", FLAG)]] * 2)

    def test_9_language_candidate_enters_the_dot_rule(self):
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE"], "PASSPORT")
        d1 = c.add("academic", [academic(student="MD EXAMPLE SAMPLE")] * 2, "ACADEMIC DEGREE")
        d2 = c.add("academic", [academic(student="MD. EXAMPLE SAMPLE")] * 2, "ACADEMIC DIPLOMA")
        lang = c.add("language_cert", ["IELTS"], "IELTS")
        idn = ident_of(student_written="MD. EXAMPLE SAMPLE")
        res = run(c, idn, FakeConfirm("unclear"), extra=[("student", lang.uid, "MD EXAMPLE SAMPLE", lang.pages[0])])
        self.assertIn(("names.student_dots", FLAG), rules(res, lang.uid))

    def test_9_doccheck_hands_the_ielts_name_on(self):
        from src.verify.runner import doccheck as DC
        from src.verify.runner.tests.test_language import trf
        c = Case()
        u = c.add("language_cert", [trf(family="SAMPLE", first="MD EXAMPLE")], "IELTS")

        class Eng:                                       # no boxes: the page text is read
            def page_image_of(self, *a):
                return None

            def read_boxes(self, img):
                return []
        out = []
        DC.language_rows(u, c.pages, Eng(), ident_of(), "KLP", dt.date(2026, 1, 1), AffidavitInfo(), out)
        self.assertEqual(out, [("student", u.uid, "MD EXAMPLE SAMPLE", u.pages[0])])

    def test_10_titles_are_dropped_only_at_the_start(self):
        self.assertEqual(N()._drop_titles(("MR", "ALPHA", "BRAVO"), ())[0], ("ALPHA", "BRAVO"))
        self.assertEqual(N()._drop_titles(("ALPHA", "MR", "BRAVO"), ())[0], ("ALPHA", "MR", "BRAVO"))

    def test_12_the_assumed_dot_case_is_noted(self):
        c = Case()
        c.add("passport", ["PASSPORT Surname SAMPLE"], "PASSPORT")
        for k in range(2):
            c.add("academic", [academic(student="MD. EXAMPLE SAMPLE")] * 2, f"AC{k}")
        res = run(c, ident_of(student_written="MD EXAMPLE SAMPLE"), FakeConfirm("unclear"))
        self.assertEqual(res.per_person["student"], PASS)
        self.assertIn("names.assumed_dots", [f.rule for f in res.summary])


class TestAssumptionsInTheReport(unittest.TestCase):
    def test_12_the_summary_and_the_excel_list_the_assumptions(self):
        import shutil
        import tempfile
        from openpyxl import load_workbook
        from src.verify.runner import report as RP
        from src.verify.runner.tests.test_report import fixture
        r = fixture()
        self.assertFalse(any(s.startswith("ASSUMED") for _, s in RP.summary_lines(r)))
        r.cross.append(Finding(NOTE, "names.assumed_dots", "student's name has a dot on every other document but "
                                                           "not on the passport — taken as OK (A2 unstated case, Q13)",
                               "runner:names"))
        lines = [s for _, s in RP.summary_lines(r)]
        self.assertIn("ASSUMED (owner to confirm): student's name has a dot on every other document but not on the "
                      "passport — taken as OK (A2 unstated case, Q13)", lines)
        WORK.mkdir(parents=True, exist_ok=True)
        d = Path(tempfile.mkdtemp(prefix="assumptions_", dir=WORK))
        try:
            RP.write_excel(r, d / "report.xlsx")
            cells = [c for row in load_workbook(d / "report.xlsx")["Summary"].iter_rows(values_only=True)
                     for c in row if c]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertIn("assumptions (owner to confirm)", cells)
        for a in RP.ASSUMPTIONS:
            self.assertIn(a, cells)
        self.assertTrue(any(a.startswith(q) for a in RP.ASSUMPTIONS for q in ("Q13", "Q16", "Q18", "Q19")))


class TestBirthDobWindow(unittest.TestCase):
    def test_11_a_bare_date_label_ends_the_dob_window(self):
        from src.verify.runner import birth as BI
        self.assertIsNone(BI.labelled_dob("Date of Birth Date: 15/06/2010 Sex Male"))
        self.assertEqual(BI.labelled_dob("Date of Birth (In Figure): 05/05/2004 Date of Birth (In Words): Fifth"),
                         dt.date(2004, 5, 5))


class TestMissingParentSlots(unittest.TestCase):
    def test_13_one_death_certificate_cannot_fill_both_slots(self):
        from src.verify.runner import split as SP
        units = [DocUnit("D01", "passport", "passport", "PASSPORT", [0]),
                 DocUnit("D02", "death_cert", "death_cert", "DEATH CERTIFICATE", [1])]
        missing, maybe = SP._missing(units, "KLP")
        self.assertIn("parent_id", missing)
        self.assertEqual(set(maybe), {"father_id", "mother_id"})
        from src.verify.runner import verdict as V
        res = SplitResult(units=units, notices=[], missing=missing, stop=False, maybe=maybe)
        lines = V.missing_lines(res, "KLP")
        self.assertIn((MISSING, SP.MISSING_TEXT["parent_id"]), lines)          # one MISSING line, certain
        self.assertEqual(sum(lv == FLAG for lv, _ in lines), 2)                  # and the two "unless" lines
        units.append(DocUnit("D03", "death_cert", "death_cert", "DEATH CERTIFICATE", [2]))
        missing, _ = SP._missing(units, "KLP")
        self.assertNotIn("parent_id", missing)


# ------------------------------------------------------------------------------------------- correctness (review 2)
class TestPageNumbers(unittest.TestCase):
    def tearDown(self):
        M.set_page_numbering(None)

    def test_1_folder_pages_are_numbered_inside_their_file(self):
        from src.verify.runner import bank, split, tin
        pages = [PageRead(0, "A.pdf", 0, "", "ocr", 0, False, 0.0, False, "cpu"),
                 PageRead(1, "A.pdf", 1, "", "ocr", 0, False, 0.0, False, "cpu"),
                 PageRead(2, "BANK.pdf", 0, "", "ocr", 0, False, 0.0, False, "cpu"),
                 PageRead(3, "BANK.pdf", 1, "", "ocr", 0, False, 0.0, False, "cpu")]
        M.set_page_numbering(pages)
        self.assertEqual((M.page_no(2), M.page_no(3)), (1, 2))
        self.assertEqual(bank._p1(2, 3), (1, 2))
        self.assertEqual(tin._p1(3), (2,))
        self.assertEqual(split._range(2, 3), "pages 1-2")
        M.set_page_numbering(None)
        self.assertEqual(M.page_no(2), 3)
        self.assertEqual(split._range(1, 1), "page 2")


class TestTinPagesOfACombinedFile(unittest.TestCase):
    """A file that holds a trade licence and then the TIN certificate (folder mode)."""

    def setUp(self):
        self.pages = [PageRead(0, "INCOME TAX.pdf", 0, "Return of income Taxes Circle-105 Taxes Zone-11", "ocr", 40,
                               False, 0.0, False, "cpu"),
                      PageRead(1, "TRADE LICENSE.pdf", 0, "TRADE LICENCE City Corporation Zone : 05 Owner SAMPLE "
                                                          "OWNER", "ocr", 60, False, 0.0, False, "cpu"),
                      PageRead(2, "TRADE LICENSE.pdf", 1, "National Board of Revenue TIN Certificate This is to "
                                                          "certify that SAMPLE OWNER is a Registered Taxpayer Taxes "
                                                          "Circle-105 Taxes Zone-11", "ocr", 120, False, 0.0, False,
                               "cpu")]
        self.itax = DocUnit("D01", "income_tax", "income_tax", "INCOME TAX", [0], source_file="INCOME TAX.pdf")
        self.tl = DocUnit("D02", "trade_license", "trade_license", "TRADE LICENSE (+ TIN CERTIFICATE)", [1, 2],
                          source_file="TRADE LICENSE.pdf", sub=[(0, 0, "trade_license"), (1, 1, "tin_certificate")])
        M.set_page_numbering(self.pages)

    def tearDown(self):
        M.set_page_numbering(None)

    def test_1_an_unknown_taxpayer_page_is_the_first_tin_page(self):
        from src.verify.runner import financial as F
        tp = F.taxpayer_from_tin({"printed": {"name": "SAMPLE OWNER"}, "findings": [], "page_types": ["", "tin"]},
                                 self.tl)
        self.assertEqual((tp["source"], tp["printed_page"]), ("PRINTED", 2))
        self.assertEqual(M.page_no(tp["printed_page"]), 2)            # page 2 of TRADE LICENSE.pdf

    def test_5_the_tax_office_is_read_from_the_tin_pages_only(self):
        from src.verify.runner import financial, pipeline, tin
        m = SimpleNamespace(tin=tin, financial=financial)
        rs = SimpleNamespace(warnings=[])
        tin_raw = {"D02": {"page_types": ["", "tin"], "printed": {"name": "SAMPLE OWNER"}, "findings": []}}
        _, itax, _ = pipeline._financial(m, rs, [self.itax, self.tl], self.pages, tin_raw, {}, ident_of(),
                                         lambda a, b: False, FakeConfirm("unclear"), "MASTER")
        self.assertEqual([(f.rule, f.verdict) for f in itax["D01"]], [("income_tax.tax_office", PASS)])


class TestPageWording(unittest.TestCase):
    def test_10_one_page_is_page_n_never_n_n(self):
        from src.verify.runner import verdict as V
        from src.verify.runner.tests import test_split as TS
        res, *_ = TS.run(TS.HEAD + TS.MOTHER[:1] + TS.FATHER[:1] + TS.FULL[6:])
        msg = next(n.message for n in res.notices if "not in the order father" in n.message)
        self.assertIn("(the names say: 3 mother, 4 father)", msg)
        self.assertEqual((V.pages_word(2, 2), V.pages_word(1, 3)), ("page 2", "pages 1-3"))
        res, *_ = TS.run(TS.HEAD + TS.MOTHER + TS.FATHER + TS.FULL[6:])
        msg = next(n.message for n in res.notices if "not in the order father" in n.message)
        self.assertIn("(the names say: 3-4 mother, 5-6 father)", msg)


class TestPassportExpiryLabel(unittest.TestCase):
    def test_2_stacked_issue_and_expiry_labels(self):
        from src.verify.runner.tests import test_identity as TI
        c = TI.Case()
        c.add("passport", ["PEOPLE'S REPUBLIC PASSPORT Surname SAMPLE Given Name(s) EXAMPLE Date of Birth 01 JAN "
                           "2000 Date of" + chr(10) + "Issue Date of Expiry 12 FEB 2024 11 FEB 2034"])
        ident = TI.build(c)
        self.assertEqual((ident.expiry, ident.expiry_trusted), (dt.date(2034, 2, 11), True))
        c = TI.Case()
        c.add("passport", ["PASSPORT Surname SAMPLE Date of Issue Date of Expiry 12 FEB 2024"])
        ident = TI.build(c)
        self.assertFalse(ident.expiry_trusted)


class TestSplitBlock(unittest.TestCase):
    def test_3_a_long_nid_block_does_not_hang_and_matches_the_old_search(self):
        import itertools
        import time
        from src.verify.runner import split as SP

        def brute(block):
            best, best_sc, out = None, None, []
            pool = SP.ORDER_ROLES + ["extra"] * len(block)
            for perm in itertools.permutations(range(len(pool)), len(block)):
                roles = [pool[p] for p in perm]
                if any(r != "extra" and roles.count(r) > 1 for r in roles):
                    continue
                if any(d.label == "death_cert" and r == "student" for d, r in zip(block, roles)):
                    continue
                if roles not in out:
                    out.append(roles)
            return out

        block = [SimpleNamespace(label=lab) for lab in ("nid", "death_cert", "nid", "death_cert")]
        self.assertEqual(SP._role_assignments(block), brute(block))
        big = [SimpleNamespace(label="death_cert" if k % 2 else "nid") for k in range(30)]
        t0 = time.perf_counter()
        n = len(SP._role_assignments(big))
        self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertGreater(n, 1000)


class TestVerdictLines(unittest.TestCase):
    def test_4_no_solvency_missing_line_when_another_bank_file_has_it(self):
        from src.verify.runner import verdict as V
        res = SplitResult(units=[], notices=[], missing=[], stop=False, maybe={})
        have = SimpleNamespace(solvency_missing=False, solvency_pages=[3])
        lack = SimpleNamespace(solvency_missing=True, solvency_pages=[])
        self.assertEqual([t for _, t in V.missing_lines(res, "KLP", [have, lack])], [])
        self.assertEqual([lv for lv, _ in V.missing_lines(res, "KLP", [lack])], [MISSING])

    def test_7_headline_counts_names_lines_on_no_judged_document(self):
        from src.verify.runner import verdict as V
        res = SplitResult(units=[], notices=[], missing=[], stop=False, maybe={})
        nc = Finding(FLAG, "names.student", "not compared — check by eye", "runner:names")
        nf = Finding(FLAG, "names.father", "not compared — check by eye", "runner:names")
        self.assertEqual(V.overall([], [], res, [nc, nf], []), (FLAG, "FLAG — 0 FAIL, 1 FLAG, 0 MISSING"))
        u = DocUnit("D01", "academic", "academic", "ACADEMIC", [0])
        fail = Finding(FAIL, "names.mother", "differs", "runner:names", (1,))
        d = DocResult(unit=u, findings=[fail], verdict=NOT_JUDGED, reasons=["fix the pages"])
        result, head = V.overall([d], [], res, [Finding(FAIL, "names.mother", "x", "runner:names")], [])
        self.assertEqual(result, FAIL)
        self.assertTrue(head.startswith("FAIL — 1 FAIL"), head)


class TestSummaryFit(unittest.TestCase):
    def test_9_the_result_line_is_never_joined_into_the_pass_list(self):
        from src.verify.runner import report as RP
        L = [(PASS, "F-0 | KLP | folder | 90 pages | RESULT: PASS"), (PASS, "PASS — 0 FAIL, 0 FLAG, 0 MISSING")]
        L += [(PASS, f"DOC {k} (DOC {k}.pdf): PASS") for k in range(90)]
        out = RP._fit(L)
        self.assertEqual(out[0][1], "F-0 | KLP | folder | 90 pages | RESULT: PASS")
        self.assertLessEqual(len(out), RP.MAX_LINES)


class TestFolderPhoto(unittest.TestCase):
    def test_6_an_unnamed_file_with_little_text_is_not_the_photo(self):
        from src.verify.runner import pagetype as PT
        from src.verify.runner import split as SP
        from src.verify.runner.model import Source, SourceFile
        root = Path(r"C:\made-up-folder")                         # never opened
        sfs = [SourceFile(path=root / "scan001.pdf", name="scan001.pdf", is_image=False, n_pages=1, size=1,
                          mtime=0.0, key_by_name=None)]
        pages = [PageRead(0, "scan001.pdf", 0, "17 characters...", "ocr", 17, False, 0.0, False, "cpu")]
        seg = SP.segment_folder(Source(mode="folder", root=root, files=sfs, case_id="F-0"), pages,
                                PT.type_pages(pages))
        self.assertEqual([d.label for d in seg.docs], ["unrecognised"])


if __name__ == "__main__":
    unittest.main()
