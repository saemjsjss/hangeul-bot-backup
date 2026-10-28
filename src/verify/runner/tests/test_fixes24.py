"""Revision 2.4 tests (RUNNER_SPEC "Revision 2.4"): the lines the by-eye check of the owner's two folders still found
wrong after revision 2.3.

  1. a folder part (one document cut out of a file) is not primed: the bot reads it as every folder file;
  2. the present-only ID card / CV gets no "reads like" FLAG;
  3. a passport name read whose first word is the field's own label ("Name" misread) drops that word;
  4. inside one folder file, blank pages join the document before them, and weakly typed pages between two parts of
     the same kind join that document (a long return of income stays ONE income-tax document);
  5. a family table's name cell that wraps onto a second line is read whole, the row before's address cell is not
     read into it, and a re-read of a variant that drops the reference's last word looks at the line below too;
  6. with an outvoted reference, a document whose other read shows the agreed spelling gets no "not compared" FLAG;
  7. when the bot cannot tell a financial document's kind but the page typing can, the bot's own licence rows run.

Made-up data only: cue texts from tests/test_pagetype.py, placeholder names ("SAMPLE", "DEMO", "EXAMPLE",
"TESTER"). No personal data, no card, no network.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_fixes24*.py"
"""
from __future__ import annotations

import datetime as dt
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.verify.runner import botimport as B
from src.verify.runner import doccheck as DC
from src.verify.runner import names as N
from src.verify.runner import pagetype as PT
from src.verify.runner import split as SP
from src.verify.runner.model import FAIL, FLAG, NOTE, PASS, DocUnit, PageRead, set_page_numbering
from src.verify.runner.tests.test_fixes23 import FakeConfirm, RefCase, folder, reading, verdicts
from src.verify.runner.tests.test_names import FakeEngine, box
from src.verify.runner.tests.test_pagetype import FA, TEXTS, nid_card, page

T = TEXTS
WORK = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\tmp\test_fixes24_work")


def setUpModule():
    B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)


def tearDownModule():
    set_page_numbering(None)
    shutil.rmtree(WORK, ignore_errors=True)


# made-up weak pages of a long return of income (each reads at most partly like any document)
SCHEDULE = "Schedule 3 Investment tax credit Life insurance premium Contribution to deferred annuity Total"
ASSETS = "Statement of Assets and Liabilities Business Capital Non-Agricultural Property Motor Vehicles Total assets"
DECLARE = ("Schedule of family expenditure Personal and fooding expenses Total Expenditure I solemnly declare that the "
           "information given is correct and complete Name and signature of the Assessee")


# ============================================================================================ 2. ID card / CV
class TestPresentOnlyNoContentFlag(unittest.TestCase):
    def test_a_cv_reading_like_an_nid_gets_no_flag(self):
        res, *_ = folder([("10 STUDENT CV.pdf", "id_card_cv", [nid_card(FA, 1), nid_card(FA, 1)])])
        self.assertEqual([(u.key, u.notes) for u in res.units], [("id_card_cv", [])])

    def test_other_files_keep_it(self):
        res, *_ = folder([("BANK STATEMENT.pdf", "bank", [T["family"]])])
        self.assertEqual([(f.verdict, f.rule) for f in res.units[0].notes], [(FLAG, "split.name_vs_content")])


# ============================================================================================ 3. passport label
class TestPassportLabelLead(unittest.TestCase):
    def test_the_label_word(self):
        self.assertTrue(N.passport_label_lead(["Nanle", "EXAMPLE", "SAMPLE"]))
        self.assertTrue(N.passport_label_lead(["Narne:", "EXAMPLE", "SAMPLE"]))
        self.assertFalse(N.passport_label_lead(["NAMQ", "SAMPLE"]))            # a capitalised name word stays
        self.assertFalse(N.passport_label_lead(["Nanle", "Example", "Sample"]))  # the name is not in capitals
        self.assertFalse(N.passport_label_lead(["Sample", "EXAMPLE"]))          # not near "name"
        self.assertFalse(N.passport_label_lead(["Nanle"]))

    def test_personal_page_read(self):
        text = ("PERSONAL DATA AND EMERGENCY CONTACT Nanle EXAMPLE SAMPLE Father's Name TESTER SAMPLE "
                "Mother's Name DEMO SAMPLE")
        reads, _ = N.extract_passport({"pages": [text]})
        got = [r.written for r in reads if r.src == "personal page name"]
        self.assertEqual(got, ["EXAMPLE SAMPLE"])
        plain = "PERSONAL DATA AND EMERGENCY CONTACT Name: EXAMPLE SAMPLE Father's Name TESTER SAMPLE"
        reads, _ = N.extract_passport({"pages": [plain]})
        self.assertEqual([r.written for r in reads if r.src == "personal page name"], ["EXAMPLE SAMPLE"])


# ============================================================================================ 4. inside one file
class TestFileDocuments(unittest.TestCase):
    def test_a_long_return_stays_one_income_tax(self):
        fin = "9. FINANCIAL DOCUMENTS.pdf"
        res, *_ = folder([(fin, None, [T["solvency"], T["statement"], T["trade"], T["tin"], "",
                                       T["income_tax"], "", T["income_tax"], SCHEDULE, ASSETS, DECLARE, DECLARE,
                                       T["income_tax"]])], program="MASTER")
        self.assertEqual([(u.key, u.fine, u.part) for u in res.units],
                         [("bank", "bank", "p1-2"), ("trade_license", "trade_license", "p3"),
                          ("trade_license", "tin_certificate", "p4-5"), ("income_tax", "income_tax", "p6-13")])
        self.assertTrue(all(not u.notes for u in res.units))
        for slot in ("bank", "owner_doc", "tin", "income_tax"):
            self.assertNotIn(slot, res.missing)

    def test_a_clear_affidavit_between_two_income_tax_parts_stays(self):
        res, *_ = folder([("FIN.pdf", None, [T["solvency"], T["statement"], T["income_tax"], T["affidavit"],
                                             T["income_tax"]])], program="MASTER")
        self.assertEqual([(u.key, u.part) for u in res.units],
                         [("bank", "p1-2"), ("income_tax", "p3"), ("affidavit", "p4"), ("income_tax", "p5")])

    def test_a_blank_page_never_joins_a_bank_document(self):
        res, *_ = folder([("FIN.pdf", None, [T["solvency"], "", T["trade"], T["tin"]])])
        self.assertEqual([(u.key, u.part) for u in res.units][0], ("bank", "p1"))       # the bank keeps one page
        self.assertEqual([(u.key, u.part) for u in res.units][1], ("trade_license", "p2-3"))   # bundle edge rule

    def test_helpers(self):
        types = PT.type_pages([page(i, t) for i, t in enumerate([T["income_tax"], SCHEDULE, T["affidavit"], ""])])
        self.assertEqual([SP._weak_page(t) for t in types], [False, True, False, True])
        d = SP.Doc("financial", 0, 1, sub=[(0, 0, "trade_license"), (1, 1, "tin_certificate")])
        SP._extend(d, 3)
        self.assertEqual((d.end, d.sub[-1]), (3, (1, 3, "tin_certificate")))


# ============================================================================================ 5. wrapped cells
FAMILY_WRAPPED = ("Family Certificate This is to certify that the family of GAMMA SAMPLE consists of the following "
                  "members: SL NAME RELATION ID NO BIRTH DATE ADDRESS "
                  "01 GAMMA SELF IDNID: 1234567890 01-06- Vill: Somewhere, Post: Vill: Somewhere Post: SAMPLE 2003 "
                  "Elsewhere,Sadar Elsewhere; Sadar Townname Townname "
                  "02 TESTER FATHER IDNID: 2345678901 01-02 Vill: Somewhere Post: Vil: Somewhere Post: SAMPLE 1967 "
                  "Elsewhere,Sadar Elsewhere,Sadar Townname: Townname, "
                  "DEMO MOTHER ID NID: 28-03- Vil: Somewhere Post: Vi: Somewhere Post: 03 SAMPLE 3456789012345 1976 "
                  "Elsewhere,Sadar Townname. 04 DELTA SISTER ID NID: 4567890123 03-07 Vill: Somewhere Post: "
                  "EXAMPLE 1993 Townname")
FAMILY_FLAT = ("Family Certificate Name GAMMA SAMPLE No Name Relation Age NID Marital Status GAMMA SAMPLE SELF "
               "2004-07-07 1234567890 Unmarried Alive 2 TESTER FATHER 1978-09-10 2345678901 Married Alive "
               "3 DEMO SAMPLE MOTHER 1986-02-08 3456789012 Married Alive Village- TOWNNAME 3701")


class TestWrappedCells(unittest.TestCase):
    def rows(self, text):
        return [(r.person, " ".join([r.written] + list(r.wrap))) for r in N.extract_family_cert({"pages": [text]})
                if r.src == "table row"]

    def test_a_wrapped_name_cell_is_read_whole(self):
        self.assertEqual(self.rows(FAMILY_WRAPPED), [("student", "GAMMA SAMPLE"), ("father", "TESTER SAMPLE"),
                                                     ("mother", "DEMO SAMPLE")])
        reads = [r for r in N.extract_family_cert({"pages": [FAMILY_WRAPPED]}) if r.src == "table row"]
        self.assertEqual([(r.written, r.wrap) for r in reads][1], ("TESTER", ("SAMPLE",)))   # kept apart
        father = reads[1]
        self.assertEqual(N._wrap_choice("TESTER SAMPLE", father, False).written, "TESTER SAMPLE")

    def test_an_address_word_under_a_one_word_name_is_not_taken(self):      # owner checker 28 Oct (made up)
        fam = ("SL NAME RELATION ID NO BIRTH DATE ADDRESS 01 GAMMA SAMPLE SELF 1234567890 01-06-2003 Vill: Somewhere "
               "02 TESTER FATHER 2345678901 01-02- Vill: Somewhere TOWNNAME 1967 DISTRICTX 03 DEMO SAMPLE MOTHER "
               "3456789012 28-03-1976 Vill: Somewhere")
        reads = [r for r in N.extract_family_cert({"pages": [fam]}) if r.src == "table row"]
        father = next(r for r in reads if r.person == "father")
        self.assertEqual((father.written, father.wrap), ("TESTER", ("TOWNNAME",)))
        self.assertEqual(N._wrap_choice("TESTER", father, False).written, "TESTER")          # the name IS one word
        self.assertEqual(N.judge_read("TESTER", N._wrap_choice("TESTER", father, False)).status, "MATCH")
        # the second line within OCR distance of the reference's dropped surname: taken (the closer look decides)
        self.assertEqual(N._wrap_choice("TESTER TOWNNANE", father, False).written, "TESTER TOWNNAME")
        # unrelated to the reference's last word: not taken
        self.assertEqual(N._wrap_choice("TESTER SAMPLE", father, False).written, "TESTER")

    def test_one_line_cells_are_unchanged(self):
        self.assertEqual(self.rows(FAMILY_FLAT), [("student", "GAMMA SAMPLE"), ("father", "TESTER"),
                                                  ("mother", "DEMO SAMPLE")])

    def test_the_tail_helper(self):
        tk = N._tokens("ALPHA FATHER 12-03- Vill: Somewhere BETA 1970 GAMMA MOTHER")
        self.assertEqual(N.wrapped_cell_tail(tk, 1, ["ALPHA"]), ["BETA"])
        self.assertEqual(N.wrapped_cell_tail(tk, 1, ["Alpha"]), [])              # a Title-case first line: none
        tk2 = N._tokens("ALPHA FATHER 12-03-1970 Vill: Somewhere GAMMA MOTHER")
        self.assertEqual(N.wrapped_cell_tail(tk2, 1, ["ALPHA"]), [])             # the next row's name is not it

    def test_the_row_befores_address_is_not_read_into_the_name(self):
        text = "1 ALPHA SAMPLE SELF 1990 Townname: Townname, DEMO SAMPLE MOTHER 1965 Married"
        self.assertIn(("mother", "DEMO SAMPLE"), self.rows(text))

    def test_drops_trailing_words(self):
        self.assertTrue(N.drops_trailing_words("TESTER", "Tester Sample"))
        self.assertTrue(N.drops_trailing_words("MD TESTER", "MD. TESTER SAMPLE"))
        self.assertFalse(N.drops_trailing_words("SAMPLE", "Tester Sample"))     # the first word dropped: not this
        self.assertFalse(N.drops_trailing_words("TESTER SAMPLE", "Tester Sample"))


class CropEngine(FakeEngine):
    """The crop re-read shows the second line only when the crop reaches it."""

    def __init__(self, page_boxes, one_line, two_lines):
        super().__init__(page_boxes, one_line)
        self.one_line, self.two_lines, self.heights = one_line, two_lines, []

    def read_boxes(self, img):
        self.reads += 1
        if img.shape[:2] == (300, 900):
            return self.page_boxes
        self.heights.append(img.shape[0])
        return self.two_lines if img.shape[0] >= 150 else self.one_line


class TestWrappedReread(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(WORK, ignore_errors=True)
        self.pages = [page(0, "TESTER FATHER SAMPLE 1967")]
        self.page_boxes = [(box(100, 100, 200, 130), "TESTER", 0.99), (box(300, 100, 380, 130), "FATHER", 0.99),
                           (box(100, 140, 200, 170), "SAMPLE", 0.99)]
        self.one = [(box(0, 0, 200, 40), "TESTER", 0.97)]
        self.two = [(box(0, 0, 200, 40), "TESTER", 0.97), (box(0, 60, 200, 100), "SAMPLE", 0.96)]

    def tearDown(self):
        shutil.rmtree(WORK, ignore_errors=True)

    def test_a_dropped_last_word_is_looked_for_on_the_line_below(self):
        eng = CropEngine(self.page_boxes, self.one, self.two)
        conf = N.make_confirmer(eng, self.pages, WORK)
        self.assertEqual(conf(0, "TESTER", reference="Tester Sample"), "refuted")
        self.assertTrue(conf.log[-1].get("wrap"))
        self.assertGreaterEqual(eng.heights[-1], 150)

    def test_a_word_like_the_dropped_one_after_the_name_is_no_confirmation(self):
        near = [(box(0, 0, 400, 40), "TESTER SAMPLF 7 Townname", 0.98)]          # one letter of it misread
        eng = CropEngine(self.page_boxes, near, near)
        conf = N.make_confirmer(eng, self.pages, WORK)
        self.assertEqual(conf(0, "TESTER", reference="Tester Sample"), "unclear")
        self.assertEqual(conf.log[-1]["why"], "the re-read shows a word like the dropped one after the name")
        far = [(box(0, 0, 400, 40), "TESTER Father 7 Townname", 0.98)]
        conf2 = N.make_confirmer(CropEngine(self.page_boxes, far, far), self.pages, WORK)
        self.assertEqual(conf2(0, "TESTER", reference="Tester Sample"), "confirmed")

    def test_dropped_words_follow(self):
        boxes = [(box(0, 0, 400, 40), "Tester Samplf n 7", 0.98)]
        self.assertTrue(N.dropped_words_follow(boxes, "TESTER", "TESTERSAMPLE"))
        self.assertFalse(N.dropped_words_follow(boxes, "TESTER", "TESTERQUOKKA"))
        self.assertFalse(N.dropped_words_follow(boxes, "SAMPLE", "TESTERSAMPLE"))  # not the reference's start
        short = [(box(0, 0, 400, 40), "Tester N", 0.98)]
        self.assertFalse(N.dropped_words_follow(short, "TESTER", "TESTERK"))         # a short word: exact only

    def test_other_variants_keep_the_one_line_crop(self):
        eng = CropEngine(self.page_boxes, self.one, self.two)
        conf = N.make_confirmer(eng, self.pages, WORK)
        self.assertEqual(conf(0, "TESTER"), "confirmed")                        # no reference: one line, as before
        self.assertLess(eng.heights[-1], 150)
        self.assertFalse(conf.log[-1].get("wrap"))


# ============================================================================================ 6. not compared
class TestUnreadFlag(unittest.TestCase):
    def test_no_not_compared_flag_where_the_document_shows_the_name(self):
        """Owner 28 Oct: no outvote any more; a document whose other page reads the name (here a confirmed other
        spelling) gets no "not compared" FLAG for a misparsed label on its apostille page."""
        c = RefCase()
        mixed = c.add("academic", ["e-Apostille page Mother zq Qwrtz Plmk", reading("DEMO SAMPLE")], "ACADEMIC HSC")
        others = [c.add("family_cert", [reading("DEMO SAMPLE")] * 2, "FAMILY CERTIFICATE"),
                  c.add("academic", [reading("DEMO SAMPLE")] * 2, "ACADEMIC SSC")]
        res = c.run(FakeConfirm("confirmed"))
        self.assertEqual(verdicts(res, c.bc.uid), [])
        self.assertEqual(verdicts(res, mixed.uid), [("names.mother", FAIL)])
        for u in others:
            self.assertEqual(verdicts(res, u.uid), [("names.mother", FAIL)])

    def test_the_flag_stays_without_an_outvote(self):
        c = RefCase()
        only = c.add("academic", ["e-Apostille page Mother zq Qwrtz Plmk"], "ACADEMIC HSC")
        res = c.run(FakeConfirm("confirmed"))
        self.assertEqual(verdicts(res, only.uid), [("names.mother_unread", FLAG)])


# ============================================================================================ 7. financial kind
class FakeDV:
    TODAY = dt.date(2026, 10, 28)

    def __init__(self, notarised=True):
        self._notarised = notarised

    @staticmethod
    def fiscal_year_check(text):
        return [(FAIL, "licence fiscal year 2025-2026 is not the current fiscal year (2026-2027)")]

    @staticmethod
    def find_dates(text):
        return [dt.date(2025, 1, 2)]

    def notarised(self, text, path):
        return self._notarised


LICENCE = ("UNION PARISHAD TRADE AND BUSINESS LICENSE License No 12 Name of License Holder Nature of Business renewed "
           "for the financial year 2025-2026")
UNSURE = [(FLAG, "could not tell which financial document this is")]


class TestFinancialKind(unittest.TestCase):
    def pages(self, *texts):
        return [PageRead(i, "F.pdf", i, t, "ocr", len(t), False, 0.0, False, "cpu") for i, t in enumerate(texts)]

    def test_a_typed_licence_gets_the_bots_licence_rows(self):
        pages = self.pages(LICENCE, "")
        u = DocUnit("D01", "trade_license", "trade_license", "TRADE LICENSE", [0, 1])
        rows = DC.financial_kind_rows(UNSURE, u, LICENCE, None, FakeDV(), pages)
        self.assertEqual([v for v, _ in rows], [PASS, FAIL, PASS])
        self.assertTrue(rows[0][1].startswith("contains: trade licence (by the page typing"))
        self.assertIn("fiscal year", rows[1][1])
        rows = DC.financial_kind_rows(UNSURE, u, LICENCE + " business start date 02/01/2025", None,
                                      FakeDV(notarised=False), pages)
        self.assertEqual([v for v, _ in rows], [PASS, FAIL, FLAG, FAIL])       # + business start, not notarised

    def test_a_typed_tin_certificate(self):
        t = "TIN Certificale Taxpayers Identification Number National Board of Revenue Taxes Circle Registered Taxpayer"
        u = DocUnit("D02", "trade_license", "tin_certificate", "TIN CERTIFICATE", [0])
        rows = DC.financial_kind_rows(UNSURE, u, t, None, FakeDV(), self.pages(t))
        self.assertEqual([(v, m.split(" (")[0]) for v, m in rows], [(PASS, "contains: TIN certificate")])

    def test_untyped_or_already_known_rows_stay(self):
        noise = "zq xv lorem ipsum"
        u = DocUnit("D03", "trade_license", "trade_license", "TRADE LICENSE", [0])
        self.assertEqual(DC.financial_kind_rows(UNSURE, u, noise, None, FakeDV(), self.pages(noise)), UNSURE)
        known = [(PASS, "contains: trade licence")]
        self.assertEqual(DC.financial_kind_rows(known, u, LICENCE, None, FakeDV(), self.pages(LICENCE)), known)
        self.assertEqual(DC.financial_kind_rows(UNSURE, u, LICENCE, None, FakeDV(), None), UNSURE)


if __name__ == "__main__":
    unittest.main()
